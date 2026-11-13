#!/bin/bash
# Native CI build only. Never installs into /Applications or the user's runtime.
set -euo pipefail
cd "$(dirname "$0")/.."
[ "$(uname -s)" = Darwin ] || { echo 'macOS is required' >&2; exit 2; }
arch=$(uname -m)
case "$arch" in arm64|x86_64) ;; *) echo "Unsupported native macOS architecture: $arch" >&2; exit 2;; esac
if [ "${BACKPLANE_MACOS_ARCH:-$arch}" != "$arch" ]; then
  echo "Expected native $BACKPLANE_MACOS_ARCH runner, found $arch" >&2
  exit 2
fi
export HOMEBREW_NO_AUTO_UPDATE=1
prefix=$(brew --prefix)
export PATH="$prefix/opt/protobuf@29/bin:$prefix/bin:$PATH"
# KiCad uses the nng 1.x API; Homebrew's nng is 2.0, so CI builds 1.x separately.
nng=${BACKPLANE_NNG_PREFIX:?set BACKPLANE_NNG_PREFIX to an nng 1.x install}
nng_source=${BACKPLANE_NNG_SOURCE:?set BACKPLANE_NNG_SOURCE to its matching nng checkout}
[ ! -e "$prefix/include/nng/nng.h" ] || { echo "Homebrew nng would shadow nng 1.x; brew uninstall nng" >&2; exit 2; }
export PKG_CONFIG_PATH="$nng/lib/pkgconfig:$prefix/lib/pkgconfig:$prefix/opt/curl/lib/pkgconfig"
version=$(tr -d '\r\n' < BACKPLANE_VERSION)
[[ "$version" =~ ^10\.99\.0-(bp\.[0-9]+)$ ]] || { echo 'Invalid Backplane release version' >&2; exit 2; }
version_extra=${BASH_REMATCH[1]}
if [ "${GITHUB_REF_TYPE:-}" = tag ] && [ "$GITHUB_REF_NAME" != "backplane-kicad-v$version" ]; then
  echo 'Release tag does not match BACKPLANE_VERSION' >&2
  exit 2
fi
build="$PWD/build/backplane-macos"
stage="$PWD/stage-macos"
release="$PWD/release/macos"
mkdir -p "$release"
ccache --max-size=2G
cmake -S . -B "$build" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$stage" \
  -DCMAKE_OSX_ARCHITECTURES="$arch" \
  -DCMAKE_PREFIX_PATH="$nng;$prefix;$prefix/opt/protobuf@29;$prefix/opt/curl;$prefix/opt/opencascade" \
  -DCMAKE_C_COMPILER_LAUNCHER=ccache -DCMAKE_CXX_COMPILER_LAUNCHER=ccache \
  -DOCC_INCLUDE_DIR="$(brew --prefix opencascade)/include/opencascade" \
  -DOCC_LIBRARY_DIR="$(brew --prefix opencascade)/lib" \
  -DNGSPICE_LIB_NAME=ngspice -DNGSPICE_ROOT_DIR="$(brew --prefix libngspice)" \
  -DSQLite3_ROOT="$(brew --prefix sqlite)" \
  -DKICAD_BUILD_QA_TESTS=OFF -DKICAD_BUILD_I18N=OFF \
  -DKICAD_USE_SENTRY=OFF -DKICAD_USE_PCH=OFF \
  -DKICAD_VERSION_EXTRA="$version_extra" -DBACKPLANE_FORK_VERSION="$version" \
  -DBACKPLANE_UPSTREAM_COMMIT="$(cat BACKPLANE_UPSTREAM)"
grep -Fqx "KICAD_VERSION_EXTRA:STRING=$version_extra" "$build/CMakeCache.txt" || {
  echo 'CMake lost the exact Backplane version suffix; refusing to build' >&2
  exit 2
}
# Verify generated/native protocol bindings before the expensive compile.
python scripts/backplane-ipc-smoke.py --check-bindings
cmake --build "$build" --parallel 3
cmake --install "$build"
bundle="$stage/KiCad.app"
# Upstream installs the GUI bundle; explicitly retain its matched CLI as well.
cli=$(find "$build" -type f -name kicad-cli -perm -111 | head -n 1)
[ -n "$cli" ]
cp "$cli" "$bundle/Contents/MacOS/kicad-cli"
python scripts/backplane-macos-relocate.py "$bundle" --arch "$arch" | tee "$release/relocation.log"
root="$release/backplane-kicad-darwin-$arch-$version"
mkdir -p "$root/bin" "$root/share/backplane" "$root/licenses"
cp -R "$bundle" "$root/KiCad.app"
# Backplane's packaged IPC helper loads the runtime's nng from root/lib.
# Alias the whole directory so dependent @loader_path names keep working.
ln -s KiCad.app/Contents/Frameworks "$root/lib"
cp "$build/api/api.desc" "$root/share/backplane/api.desc"
python scripts/backplane-macos-launchers.py "$root"
cp LICENSE* COPYING* AUTHORS* "$root/licenses/" 2>/dev/null || true
mkdir -p "$root/licenses/nng"
cp "$nng_source/LICENSE.txt" "$root/licenses/nng/"
git -C "$nng_source" rev-parse HEAD > "$root/licenses/nng/SOURCE_COMMIT"
git -C "$nng_source" archive --format=tar.gz --output="$root/licenses/nng/matching-source.tar.gz" HEAD
git archive HEAD | gzip > "$root/licenses/matching-source.tar.gz"
printf '%s\n' "$(git rev-parse HEAD)" > "$root/SOURCE_COMMIT"
cp BACKPLANE_UPSTREAM "$root/UPSTREAM_COMMIT"
# Exercise the relocated package from a different working directory with fresh state.
runtime_state="$RUNNER_TEMP/backplane-kicad-macos-$arch-state"
export XDG_CONFIG_HOME="$runtime_state/config" XDG_CACHE_HOME="$runtime_state/cache" KICAD_CONFIG_HOME="$runtime_state/kicad"
mkdir -p "$XDG_CONFIG_HOME" "$XDG_CACHE_HOME" "$KICAD_CONFIG_HOME"
"$root/bin/kicad-cli" --version | tee "$release/version.log"
"$root/bin/kicad-cli" api-server --describe | tee "$root/share/backplane/runtime.json" "$release/runtime.json"
python - "$root" "$arch" <<'MANIFEST'
import json
from pathlib import Path
import sys
root = Path(sys.argv[1])
manifest = json.loads((root / 'share/backplane/runtime.json').read_text())
manifest['platform'] = 'darwin-' + sys.argv[2]
(root / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
MANIFEST
python scripts/backplane-ipc-smoke.py "$root/bin/kicad-cli" --expected-version "$version" | tee "$release/ipc.log"
python scripts/backplane-document-ipc-regression.py "$root/bin/kicad-cli" | tee "$release/documents.log"
python scripts/backplane-glb-concurrency-test.py "$root/bin/kicad-cli" | tee "$release/glb-concurrency.log"
# Preserve library licenses alongside every dependency from Homebrew.
brew info --json=v2 --installed > "$root/licenses/homebrew-packages.json"
for formula in "$prefix/Cellar"/*; do
  [ -d "$formula" ] || continue
  name=$(basename "$formula")
  mkdir -p "$root/licenses/homebrew/$name"
  find "$formula" -type f \( -iname '*license*' -o -iname '*copying*' -o -path '*/.brew/*.rb' \) -exec cp {} "$root/licenses/homebrew/$name/" \;
done
archive="$(basename "$root").tar.gz"
tar -C "$release" -czf "$release/$archive" "$(basename "$root")"
(cd "$release" && shasum -a 256 "$archive" > SHA256SUMS)
