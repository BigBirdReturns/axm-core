"""Create a real two-edition sample from the retained public Genesis README."""
from pathlib import Path
import argparse, json, shutil, subprocess, hashlib
from pack import compile_pack

def main():
    p=argparse.ArgumentParser();p.add_argument('--state',type=Path,required=True);p.add_argument('--genesis',type=Path,required=True);p.add_argument('--go-verifier',required=True);a=p.parse_args()
    a.state.mkdir(parents=True,exist_ok=True,mode=0o700)
    identity=a.state/'identity';identity.mkdir(mode=0o700,exist_ok=True)
    key=identity/'demo.key';pub=identity/'demo.pub'
    if not key.exists():
        from axm_build.sign import hybrid1_keygen
        public,secret=hybrid1_keygen();key.write_bytes(secret);pub.write_bytes(public);key.chmod(0o600)
    shutil.copyfile(pub,a.state/'trusted-demo.pub')
    raw=(a.genesis/'README.md').read_bytes();source=raw.decode('utf-8')
    quotes=[
        'Genesis compiles and signs; everything else reads. That boundary is the\ninvariant that makes long-term verification possible.',
        'There is no Parquet in the shard. A runtime may build a derived, local,\nrebuildable query cache in any format it likes — outside the shard, never\nMerkle-covered, never normative.',
        'AXM Genesis can be reimplemented in any language from the spec and vectors\nalone, using primitives a stranded implementer can rebuild in 2056:',
    ]
    first,one=compile_pack('AXM portability: the evidence',source,quotes[:1],key.read_bytes(),pub.read_bytes(),go_binary=a.go_verifier)
    second,two=compile_pack('AXM portability: the evidence',source,quotes,key.read_bytes(),pub.read_bytes(),first,a.go_verifier)
    (a.state/'edition-1.axmpack.zip').write_bytes(first)
    (a.state/'example.axmpack.zip').write_bytes(second)
    (a.state/'example-acceptance.json').write_text(json.dumps(two,ensure_ascii=False,indent=2)+'\n')
    (a.state/'SOURCE.json').write_text(json.dumps({'origin':'https://github.com/BigBirdReturns/axm-genesis/blob/main/README.md','source_snapshot_sha256':hashlib.sha256(raw).hexdigest(),'edition_1':one['head'],'edition_2':two['head'],'source_kind':'retained public project documentation','selection':'explicit excerpts; no automatic extraction','identity':'local demonstration publisher; not an authenticated human identity'},indent=2)+'\n')
    print(json.dumps({'status':two['status'],'head':two['head'],'records':len(two['records']),'checks':len(two['checks']),'bytes':len(second)}))
if __name__=='__main__':main()
