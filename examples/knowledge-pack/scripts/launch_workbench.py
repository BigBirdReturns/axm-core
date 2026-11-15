"""Install the pinned optional runtime in a private environment and open the local application."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, default=Path.home()/'.axm-vault-workbench')
    parser.add_argument('--port', type=int, default=8357)
    parser.add_argument('--demo', action='store_true', help='Initialize clearly labeled synthetic examples only when state is fresh')
    parser.add_argument('--environment', type=Path, help='Explicit isolated runtime location')
    args = parser.parse_args()
    if sys.version_info[:2] < (3, 12):
        raise SystemExit('Python 3.12 or newer is required. This release is qualified on Python 3.12.')
    cache = Path(os.environ.get('LOCALAPPDATA', str(Path.home()/'.cache')))/'axm-vault-workbench'/'0.4.0'
    environment = args.environment or cache/'venv'
    if environment.is_symlink():
        raise SystemExit('Refusing a symlink runtime directory')
    if not environment.exists():
        environment.parent.mkdir(parents=True, exist_ok=True)
        venv.EnvBuilder(with_pip=True).create(environment)
    python = environment/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    requirements = [ROOT/'requirements-test.txt', ROOT/'requirements-workbench.txt', ROOT/'requirements-obsidian.txt']
    fingerprint = hashlib.sha256(b''.join(p.read_bytes() for p in requirements)).hexdigest()
    marker = environment/'axm-runtime.json'
    current = json.loads(marker.read_text()) if marker.exists() else {}
    if current.get('requirements_sha256') != fingerprint:
        commands = [
            [str(python),'-m','pip','install','-r',str(requirements[0]),'-r',str(requirements[1])],
            [str(python),'-m','pip','install','--require-hashes','--only-binary=:all:','-r',str(requirements[2])],
            [str(python),'-m','pip','install','--no-deps',str(ROOT/'vendor/axm-genesis')],
        ]
        for command in commands:
            subprocess.run(command, cwd=ROOT, check=True)
        marker.write_text(json.dumps({'requirements_sha256':fingerprint,'version':'0.4.0'},indent=2)+'\n')
    state = args.state.expanduser().absolute()
    if args.demo:
        config = state/'config.json'
        existing = json.loads(config.read_text()) if config.exists() else {}
        if not existing.get('vaults'):
            subprocess.run([str(python),str(ROOT/'vault_workbench.py'),'--state',str(state),'demo'],cwd=ROOT,check=True)
    print('Local workbench: http://127.0.0.1:'+str(args.port)+'/',flush=True)
    print('Register a vault and choose an extraction provider in Settings. Source capture requires no model.',flush=True)
    return subprocess.call([str(python),str(ROOT/'vault_workbench.py'),'--state',str(state),'serve','--port',str(args.port)],cwd=ROOT)


if __name__=='__main__':
    raise SystemExit(main())
