#!/usr/bin/env python3
"""Native regression for already-installed dylibs and real basename collisions."""
import importlib.util
import platform
import plistlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

if sys.platform != 'darwin':
    raise SystemExit('requires actual macOS')

spec = importlib.util.spec_from_file_location('relocate', Path(__file__).with_name('backplane-macos-relocate.py'))
relocate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relocate)

with tempfile.TemporaryDirectory(prefix='backplane-dylib-test-') as directory:
    root = Path(directory)
    for name in ('source', 'installed', 'different'):
        (root / name).mkdir()
    source = root / 'source/libsame.dylib'
    target = root / 'installed/libsame.dylib'
    different = root / 'different/libsame.dylib'
    for path, value in ((source, 1), (different, 2)):
        code = path.with_suffix('.c')
        code.write_text(f'int test_marker(void) {{ return {value}; }}\n')
        subprocess.run(['clang', '-dynamiclib', '-Wl,-headerpad_max_install_names', str(code), '-o', str(path)], check=True)
    shutil.copy2(source, target)
    subprocess.run(['install_name_tool', '-id', '@rpath/libsame.dylib', str(target)], check=True)
    subprocess.run(['codesign', '--force', '--sign', '-', str(target)], check=True)
    assert source.read_bytes() != target.read_bytes(), 'fixture must reproduce CMake relocation/signing'
    relocate.require_same_library(source, target)
    print('PASS already-relocated and signed library retains native identity')
    arch = platform.machine()
    relocate.require_architecture(target, arch)
    other_arch = 'arm64' if arch == 'x86_64' else 'x86_64'
    try:
        relocate.require_architecture(target, other_arch)
    except RuntimeError as error:
        assert f'expected {other_arch}' in str(error)
    else:
        raise AssertionError('a dependency missing the runtime architecture was accepted')
    print('PASS native architecture accepted and incompatible architecture rejected')
    try:
        relocate.require_same_library(different, target)
    except RuntimeError as error:
        assert 'conflicting dependency basename' in str(error)
    else:
        raise AssertionError('different library with the same basename was accepted')
    print('PASS different native library with same basename is rejected')
    # Exercise the complete relocator with an absolute build-tree dependency,
    # including the already-rewritten copy that triggered the real CI failure.
    bundle = root / 'Test.app'
    frameworks = bundle / 'Contents/Frameworks'
    macos = bundle / 'Contents/MacOS'
    frameworks.mkdir(parents=True)
    macos.mkdir(parents=True)
    shutil.copy2(target, frameworks / target.name)
    code = root / 'main.c'
    code.write_text('int test_marker(void); int main(void) { return test_marker() == 1 ? 0 : 1; }\n')
    probe = macos / 'probe'
    subprocess.run(['clang', '-Wl,-headerpad_max_install_names', str(code), str(source), '-o', str(probe)], check=True)
    for app, identifier in [(bundle, 'works.backplane.relocation-test'),
                            (bundle / 'Contents/Applications/Nested.app', 'works.backplane.relocation-nested')]:
        executable = app / 'Contents/MacOS/probe'
        executable.parent.mkdir(parents=True, exist_ok=True)
        if executable != probe:
            shutil.copy2(probe, executable)
        (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({
            'CFBundleIdentifier': identifier, 'CFBundleExecutable': 'probe',
            'CFBundlePackageType': 'APPL', 'CFBundleVersion': '1'}))
    peer = macos / 'unsigned-cli'
    shutil.copy2(probe, peer)
    subprocess.run(['codesign', '--remove-signature', str(peer)], check=True)
    relocate.sign_bundle(bundle, [probe, peer, executable, frameworks / target.name])
    print('PASS a primary executable listed first waits for its unsigned CLI peer')
    subprocess.run([sys.executable, str(Path(__file__).with_name('backplane-macos-relocate.py')), str(bundle), '--arch', arch], check=True)
    subprocess.run(['codesign', '--verify', '--deep', '--strict', str(bundle)], check=True)
    print('PASS nested application and outer resource seals validate after relocation')
    source.parent.rename(root / 'removed-source')
    subprocess.run([str(probe)], check=True)
    print('PASS relocated executable runs after its original build libraries disappear')
print('MAC_NATIVE_RELOCATION_IDENTITY_PASS')
