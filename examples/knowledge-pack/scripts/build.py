"""Build the public reference from this source tree, without estate paths or credentials."""
from __future__ import annotations
import argparse,hashlib,json,os,shutil,subprocess,sys,tempfile,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def run(command,*,cwd=None,env=None):
    print('+ '+' '.join(map(str,command)),flush=True)
    subprocess.run(list(map(str,command)),cwd=cwd or ROOT,env=env,check=True)

def write_zip(destination:Path,files:dict[str,Path]):
    with zipfile.ZipFile(destination,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for name,path in sorted(files.items()):
            info=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0))
            info.compress_type=zipfile.ZIP_DEFLATED
            info.external_attr=(0o100755 if name=='axm-pack-reader' else 0o100644)<<16
            z.writestr(info,path.read_bytes())

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--test',action='store_true');parser.add_argument('--web',action='store_true');parser.add_argument('--native',action='store_true');parser.add_argument('--go',default=os.environ.get('GO','go'))
    args=parser.parse_args();go=shutil.which(args.go) or args.go
    version=subprocess.check_output([go,'version'],text=True).strip();print(version,flush=True)
    run([sys.executable,ROOT/'scripts/generate_vectors.py'])
    env=os.environ.copy();env.update(CGO_ENABLED='0',GOTOOLCHAIN='local',GOFLAGS='-buildvcs=false',GOMAXPROCS='2')
    build=ROOT/'build';build.mkdir(exist_ok=True);module=ROOT/'go';native=build/('axm-pack-reader.exe' if os.name=='nt' else 'axm-pack-reader')
    flags=['build','-trimpath','-ldflags=-s -w']
    run([go,*flags,'-o',native,'./cmd/reader'],cwd=module,env=env)
    run([go,*flags,'-o',build/('axm-verify-go.exe' if os.name=='nt' else 'axm-verify-go'),'./cmd/kernel'],cwd=module,env=env)
    if args.test:
        run([go,'test','./...'],cwd=module,env=env)
        run([sys.executable,ROOT/'scripts/conformance.py','--reader',native,'--output',build/'native-conformance.json'])
    web=ROOT/'web';downloads=web/'downloads';downloads.mkdir(exist_ok=True)
    if args.web:
        wasm_env={**env,'GOOS':'js','GOARCH':'wasm'}
        run([go,*flags,'-o',web/'verifier.wasm','./cmd/wasm'],cwd=module,env=wasm_env)
        goroot=Path(subprocess.check_output([go,'env','GOROOT'],text=True).strip());runtime=goroot/'lib/wasm/wasm_exec.js'
        if not runtime.is_file():runtime=goroot/'misc/wasm/wasm_exec.js'
        shutil.copyfile(runtime,web/'wasm_exec.js')
    if args.native:
        for target in ['linux','windows']:
            name='axm-pack-reader'+('.exe' if target=='windows' else '');dest=build/(target+'-'+name)
            run([go,*flags,'-o',dest,'./cmd/reader'],cwd=module,env={**env,'GOOS':target,'GOARCH':'amd64'})
            files={name:dest,'README.md':ROOT/'README.md','LICENSE':ROOT/'LICENSE','example.axmpack.zip':ROOT/'fixtures/example.axmpack.zip','trusted-demo.pub':ROOT/'fixtures/trusted-demo.pub'}
            for p in (ROOT/'third-party-notices').iterdir():
                if p.is_file():files['third-party-notices/'+p.name]=p
            write_zip(downloads/f'axm-pack-reader-{target}-amd64.zip',files)
        if args.test and sys.platform.startswith('linux'):
            with zipfile.ZipFile(downloads/'axm-pack-reader-linux-amd64.zip') as z:
                info=z.getinfo('axm-pack-reader')
                assert (info.external_attr>>16)&0o111,'Linux reader must remain executable after unzip'
                with tempfile.TemporaryDirectory(prefix='axm-shipped-reader-') as temp:
                    directory=Path(temp)
                    for name in ['axm-pack-reader','example.axmpack.zip','trusted-demo.pub']:
                        (directory/name).write_bytes(z.read(name))
                    (directory/'axm-pack-reader').chmod((info.external_attr>>16)&0o777)
                    result=subprocess.run([str(directory/'axm-pack-reader'),'inspect',str(directory/'example.axmpack.zip'),'--trusted-key',str(directory/'trusted-demo.pub')],capture_output=True,text=True,check=True,timeout=20)
                    assert json.loads(result.stdout)['status']=='PASS'
            (build/'shipped-archive-check.json').write_text(json.dumps({'status':'PASS','archive':'axm-pack-reader-linux-amd64.zip','executable_mode_preserved':True,'extracted_reader_verified_pack':True},indent=2)+'\n')
    # Whitelist source areas and exclude local environments, authoring state, keys and caches.
    files={};allowed={'go','scripts','tests','fixtures','web','vendor','third-party-notices'}
    root_files={'README.md','LICENSE','TRANSPORT.md','SOURCE-PROVENANCE.json','INTAKE.json','requirements-test.txt','.gitignore','pack.py','studio.py','build_example.py'}
    excluded={'build','.git','__pycache__','.pytest_cache','downloads','.venv','venv','demo-state','state','identity'}
    for p in sorted(ROOT.rglob('*')):
        if not p.is_file() or p.is_symlink():continue
        rel=p.relative_to(ROOT)
        if len(rel.parts)==1 and rel.name not in root_files:continue
        if len(rel.parts)>1 and rel.parts[0] not in allowed:continue
        if any(x in excluded or x.endswith('.egg-info') for x in rel.parts):continue
        if p.suffix in {'.pyc','.key','.wasm'} or p.name in {'.env','wasm_exec.js','SHA256SUMS.txt','release.json'}:continue
        files['axm-knowledge-pack/'+str(rel)]=p
    write_zip(downloads/'axm-knowledge-pack-source-0.2.0.zip',files)
    hashes={str(p.relative_to(web)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(web.rglob('*')) if p.is_file() and p.name not in {'SHA256SUMS.txt','release.json'}}
    (web/'SHA256SUMS.txt').write_text(''.join(f'{h}  {n}\n' for n,h in hashes.items()))
    (web/'release.json').write_text(json.dumps({'version':'0.2.0','profile':'axm-pack-preview/0.1','toolchain':version,'artifacts':hashes,'build_flags':['-trimpath','-buildvcs=false','-ldflags=-s -w'],'trust_scope':'Demo key is distributed with this reference. Independent publisher authentication is not claimed.'},indent=2)+'\n')
    print(json.dumps({'status':'PASS','toolchain':version,'artifacts':len(hashes),'source_files':len(files)}))
if __name__=='__main__':main()
