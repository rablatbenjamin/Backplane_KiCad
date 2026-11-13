#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Expose the packaged CLI and matching GUI editors beside one another."""
import argparse
from pathlib import Path


PROGRAMS = {
    'kicad-cli': 'Contents/MacOS/kicad-cli',
    'kicad': 'Contents/MacOS/kicad',
    'pcbnew': 'Contents/Applications/pcbnew.app/Contents/MacOS/pcbnew',
    'eeschema': 'Contents/Applications/eeschema.app/Contents/MacOS/eeschema',
}


def create_launchers(root):
    root = Path(root).resolve(strict=True)
    for relative in PROGRAMS.values():
        program = root / 'KiCad.app' / relative
        if not program.is_file():
            raise FileNotFoundError(f'missing packaged KiCad program: {program}')
    directory = root / 'bin'
    directory.mkdir(exist_ok=True)
    for name, relative in PROGRAMS.items():
        launcher = directory / name
        launcher.write_text('''#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
export KICAD_KIFACE_HOME="$root/KiCad.app/Contents/PlugIns"
export KICAD_STOCK_PLUGIN_HOME="$root/KiCad.app/Contents/PlugIns"
export DYLD_FALLBACK_LIBRARY_PATH="$root/KiCad.app/Contents/Frameworks${DYLD_FALLBACK_LIBRARY_PATH:+:$DYLD_FALLBACK_LIBRARY_PATH}"
exec "$root/KiCad.app/''' + relative + '''" "$@"
''', encoding='utf-8')
        launcher.chmod(0o755)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runtime', type=Path)
    create_launchers(parser.parse_args().runtime)
