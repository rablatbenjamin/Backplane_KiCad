#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check editor routing and argument preservation without opening a GUI."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile

spec = importlib.util.spec_from_file_location('launchers', Path(__file__).with_name('backplane-macos-launchers.py'))
launchers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launchers)

with tempfile.TemporaryDirectory(prefix='backplane-launchers-') as directory:
    root = Path(directory) / 'runtime with spaces é'
    root.mkdir()
    for name, relative in launchers.PROGRAMS.items():
        program = root / 'KiCad.app' / relative
        program.parent.mkdir(parents=True, exist_ok=True)
        program.write_text('#!/bin/sh\nprintf "%s\\n" "$0" "$KICAD_KIFACE_HOME" "$KICAD_STOCK_PLUGIN_HOME" "$@"\n')
        program.chmod(0o755)
    launchers.create_launchers(root)
    arguments = ['project with spaces.kicad_pro', '--marker', '$(echo literal)']
    for name, relative in launchers.PROGRAMS.items():
        lines = subprocess.check_output([str(root / 'bin' / name), *arguments], text=True).splitlines()
        assert lines == [str(root / 'KiCad.app' / relative),
                         str(root / 'KiCad.app/Contents/PlugIns'),
                         str(root / 'KiCad.app/Contents/PlugIns'), *arguments], lines
        print(f'PASS {name}: bundled program, private plugin paths and literal arguments')
    missing = root / 'KiCad.app' / launchers.PROGRAMS['eeschema']
    missing.unlink()
    try:
        launchers.create_launchers(root)
    except FileNotFoundError as error:
        assert str(missing) in str(error)
    else:
        raise AssertionError('a runtime missing its matching schematic editor was accepted')
    print('PASS incomplete GUI runtime rejected')
print('MAC_NATIVE_LAUNCHERS_PASS')
