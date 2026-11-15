"""Qualify the shipped source kit in a fresh path, without repository imports."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    archive = ROOT / 'web/downloads/axm-knowledge-pack-source-0.4.0.zip'
    with tempfile.TemporaryDirectory(prefix='axm-workbench-shipped-') as temporary:
        base = Path(temporary)
        with zipfile.ZipFile(archive) as zipped:
            for info in zipped.infolist():
                target = base / info.filename
                if not target.resolve().is_relative_to(base) or info.is_dir():
                    raise ValueError('Unexpected source-kit member')
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zipped.read(info))
                if info.filename.endswith('.sh'):
                    assert (info.external_attr >> 16) & 0o111, 'Unix launcher lost its executable mode'
            assert not any(name.endswith(('.key','.whl')) or '/.venv/' in name for name in zipped.namelist())
        root = base / 'axm-knowledge-pack'
        mapping = json.loads((root/'runtime/SOURCE-MAP.json').read_text())
        for path, item in mapping.items():
            assert hashlib.sha256((root/path).read_bytes()).hexdigest() == item['sha256'], path
        for name in ('vault_workbench.py','workbench_store.py','workbench_collection.py','workbench-web/index.html',
                     'workbench-web/app.js','workbench-web/style.css','WORKBENCH.md','requirements-workbench.txt',
                     'launch-workbench.cmd','launch-workbench.sh'):
            assert (root/name).is_file(), name
        env = dict(os.environ, PYTHONPATH=str(root), PYTHONDONTWRITEBYTECODE='1')
        snippet = '''from pathlib import Path
import workbench_store, axm_forge.vault_extraction, axiom_runtime.engine
root=Path.cwd()
assert Path(axm_forge.vault_extraction.__file__).is_relative_to(root/'runtime/forge')
assert Path(axiom_runtime.engine.__file__).is_relative_to(root/'runtime/spectra')
print('PACKAGED_OWNERS_RESOLVED')
'''
        subprocess.run([sys.executable,'-B','-c',snippet],cwd=root,env=env,check=True,timeout=30)
        tests = subprocess.run([sys.executable,'-B','-m','pytest','-q','tests/test_workbench.py'],cwd=root,
                               env=env,text=True,capture_output=True,timeout=180)
        (ROOT/'build/workbench-package-tests.log').write_text(tests.stdout+tests.stderr)
        tests.check_returncode()
        result = {'status':'PASS','archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
                  'owner_files_checked':len(mapping),'fresh_directory_runtime_imports':True,
                  'private_key_or_wheel_included':False,'unix_launcher_executable':True,
                  'shipped_tests':tests.stdout.strip().splitlines()[-1]}
    (ROOT/'build/workbench-package.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))


if __name__=='__main__':
    main()
