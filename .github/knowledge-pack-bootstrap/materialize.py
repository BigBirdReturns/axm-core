"""One-time, hash-bound intake of the public reference source into this repository."""
from __future__ import annotations
import hashlib,io,json,lzma,os,shutil,tarfile,tempfile,urllib.request
from pathlib import Path,PurePosixPath
URL='https://octo-n01.tail932aa0.ts.net/axm-pack-public-20261103/axm-knowledge-pack-public-source-0.2.0.tar.xz'
EXPECTED='e3fa342604856814f5d6790373dd04882d4714aa44314fa40d29ecb4e9051ceb'
ROOT=Path(__file__).resolve().parents[2]
DEST=ROOT/'examples/knowledge-pack'

def main():
    if DEST.exists():
        raise SystemExit('The source already exists. Build from examples/knowledge-pack; do not replay bootstrap.')
    with urllib.request.urlopen(URL,timeout=60) as response:
        data=response.read(2*1024*1024+1)
    if len(data)>2*1024*1024 or hashlib.sha256(data).hexdigest()!=EXPECTED:
        raise SystemExit('Public source archive digest or size mismatch')
    decoder=lzma.LZMADecompressor();expanded=decoder.decompress(data,max_length=8*1024*1024)
    if not decoder.eof or decoder.unused_data:
        raise SystemExit('Source expansion limit or trailing data')
    with tempfile.TemporaryDirectory(prefix='knowledge-pack-intake-') as temp:
        stage=Path(temp)/'source';stage.mkdir();seen=set();hashes={}
        with tarfile.open(fileobj=io.BytesIO(expanded),mode='r:') as archive:
            members=archive.getmembers()
            if len(members)>1000:raise SystemExit('Source inventory limit')
            for item in members:
                path=PurePosixPath(item.name)
                if not item.isfile() or path.is_absolute() or any(p in {'','..','.'} for p in path.parts) or '\\' in item.name or item.name.casefold() in seen:
                    raise SystemExit('Unsafe or duplicate source member')
                if item.name.endswith('.key') or any(p in {'.git','identity','.venv','demo-state'} for p in path.parts):
                    raise SystemExit('Private or runtime state is outside publication scope')
                seen.add(item.name.casefold());blob=archive.extractfile(item).read(1024*1024+1)
                if len(blob)!=item.size or len(blob)>1024*1024:raise SystemExit('Source member size mismatch')
                target=stage.joinpath(*path.parts);target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(blob)
                hashes[item.name]=hashlib.sha256(blob).hexdigest()
        if len(hashes)!=283:raise SystemExit('Source inventory mismatch')
        for required in ['README.md','scripts/build.py','go/go.mod','web/index.html','fixtures/example.axmpack.zip','vendor/axm-genesis/src/axm_build/compiler_generic.py']:
            if required not in hashes:raise SystemExit('Required source missing: '+required)
        DEST.parent.mkdir(parents=True,exist_ok=True);shutil.copytree(stage,DEST)
    receipt={'source_archive_sha256':EXPECTED,'source_files':hashes,'scope':'Public source and fixtures only; no live private signing key. The bootstrap URL is transport, not a runtime dependency.'}
    (DEST/'INTAKE.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps({'status':'PASS','files':len(hashes),'source_archive_sha256':EXPECTED}))
if __name__=='__main__':main()
