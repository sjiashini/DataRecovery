#!/usr/bin/env python3
"""Regenerate requirements.lock and requirements-dev.lock from the requirements*.txt files.

    python scripts/lock_deps.py                         relock, keeping current pins
    python scripts/lock_deps.py --upgrade-package anyio bump one indirect package
    python scripts/lock_deps.py --upgrade               bump every indirect package

Both locks are always rebuilt together (the dev lock includes the runtime one). Each is
universal (Windows and Linux, Python 3.10+) and hash-checked, so pip installs it with
--require-hashes. Prints which pins changed. Needs `uv` (python -m pip install uv).
"""

import argparse
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCKS = (('requirements.txt', 'requirements.lock'),
         ('requirements-dev.txt', 'requirements-dev.lock'))
PIN_RE = re.compile(r'^([A-Za-z0-9_.-]+)==([^\s;\\]+)')
HEADER = """# Fully pinned, hash-checked lock of {source} (every indirect package included).
# Valid on Windows and Linux for Python 3.10+. Install with:
#   python -m pip install --require-hashes -r {lock}
# Do not edit by hand. After editing {source}, regenerate both locks with:
#   python scripts/lock_deps.py
"""


def read_pins(path):
    pins = {}
    try:
        with open(path, encoding='utf-8') as handle:
            for line in handle:
                match = PIN_RE.match(line)
                if match:
                    pins[match.group(1).lower()] = match.group(2)
    except OSError:
        pass
    return pins


def describe_changes(old, new):
    lines = []
    for name in sorted(set(old) | set(new)):
        before, after = old.get(name), new.get(name)
        if before == after:
            continue
        if before is None:
            lines.append(f'  + {name} {after}')
        elif after is None:
            lines.append(f'  - {name} {before}')
        else:
            lines.append(f'  ~ {name} {before} -> {after}')
    return lines


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--upgrade', action='store_true',
                        help='move every package to the newest version the inputs allow')
    parser.add_argument('--upgrade-package', action='append', default=[], metavar='NAME',
                        help='move one package to its newest allowed version (repeatable)')
    args = parser.parse_args(argv)

    uv = shutil.which('uv')
    if not uv:
        print('uv is not installed. Install it with: python -m pip install uv')
        return 2

    extra = ['--upgrade'] if args.upgrade else []
    for name in args.upgrade_package:
        extra += ['--upgrade-package', name]

    for source, lock in LOCKS:
        lock_path = os.path.join(ROOT, lock)
        before = read_pins(lock_path)
        # Compiling onto the existing lock keeps its pins unless an upgrade is requested.
        result = subprocess.run(
            [uv, 'pip', 'compile', source, '--universal', '--python-version', '3.10',
             '--generate-hashes', '--no-header', '--quiet', '-o', lock] + extra,
            cwd=ROOT, capture_output=True, text=True, errors='replace')
        if result.returncode != 0:
            print(f'FAILED to lock {source}:\n{result.stderr.strip()}')
            return 1
        with open(lock_path, encoding='utf-8') as handle:
            body = handle.read()
        with open(lock_path, 'w', encoding='utf-8') as handle:
            handle.write(HEADER.format(source=source, lock=lock) + body)
        changes = describe_changes(before, read_pins(lock_path))
        print(f'{lock}: ' + (f'{len(changes)} change(s)' if changes else 'no changes'))
        for line in changes:
            print(line)
    return 0


if __name__ == '__main__':
    sys.exit(main())
