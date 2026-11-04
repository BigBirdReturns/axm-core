from pathlib import Path
import json,zipfile,io,hashlib,shutil,re
REF=Path(__file__).resolve().parents[1]
KERNEL=REF/'vendor/axm-genesis'
OUT=REF/'web/conformance';OUT.mkdir(parents=True,exist_ok=True)
def archive(files):
 out=io.BytesIO()
 with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED) as z:
  for n,b in sorted(files.items()):
   i=zipfile.ZipInfo(n,date_time=(1980,1,1,0,0,0));i.compress_type=zipfile.ZIP_DEFLATED;i.external_attr=0o100644<<16;z.writestr(i,b)
 return out.getvalue()
def shard_zip(p):return archive({str(f.relative_to(p)):f.read_bytes() for f in p.rglob('*') if f.is_file()})
cases=[]
def add(name,raw,key,status,codes=None,kind='vector',checked=None,unchecked=None):
 filename=name+'.zip';(OUT/filename).write_bytes(raw)
 cases.append({'name':name,'operation':kind,'file':filename,'key':key,'expected_status':status,'expected_codes':codes or [],'profiles_checked':checked or [],'profiles_unchecked':unchecked or [],'sha256':hashlib.sha256(raw).hexdigest()})
shutil.copy2(KERNEL/'tests/keys/ci_test_publisher.pub',OUT/'ci-test.pub');shutil.copy2(KERNEL/'keys/canonical_publisher.pub',OUT/'canonical.pub');shutil.copy2(REF/'fixtures/trusted-demo.pub',OUT/'demo.pub')
add('kernel-gold',shard_zip(KERNEL/'shards/gold/fm21-11-hemorrhage-v2'),'canonical.pub','PASS')
for line in (KERNEL/'tests/vectors/shards/EXPECTED.md').read_text().splitlines():
 if not line.startswith('|'):continue
 cols=[x.strip() for x in line.strip().strip('|').split('|')]
 if len(cols)<7 or cols[2] not in ['0','1','2']:continue
 name,path,exitcode,status,codes,checked,unchecked=cols[:7]
 split=lambda s:[] if s=='-' else re.split('[;,]',s)
 add('kernel-'+name.replace('/','-'),shard_zip(KERNEL/'tests/vectors/shards'/path),'ci-test.pub',status,split(codes),checked=split(checked),unchecked=split(unchecked))
raw=(REF/'fixtures/example.axmpack.zip').read_bytes();add('pack-valid',raw,'demo.pub','PASS',kind='open')
with zipfile.ZipFile(io.BytesIO(raw)) as z:files={n:z.read(n) for n in z.namelist()}
meta=json.loads(files['pack.json']);head=meta['head']
for name,rel,offset,code in [('source','content/source.txt',0,'E_MERKLE_MISMATCH'),('ed25519','sig/manifest.sig',0,'E_SIG_INVALID'),('mldsa','sig/manifest.sig',64,'E_SIG_INVALID')]:
 copy=dict(files);p=f'shards/{head}/{rel}';changed=bytearray(copy[p]);changed[offset]^=1;copy[p]=bytes(changed);add('pack-tamper-'+name,archive(copy),'demo.pub','FAIL',[code],kind='open')
add('pack-wrong-key',raw,'ci-test.pub','FAIL',['E_SIG_INVALID'],kind='open')
copy=dict(files);badmeta=dict(meta);badmeta['head']=next(s for s in meta['shards'] if s!=head);copy['pack.json']=json.dumps(badmeta).encode();add('pack-false-head',archive(copy),'demo.pub','ERROR',kind='open')
copy={k:v for k,v in files.items() if k=='pack.json' or k.startswith('shards/'+head+'/')};badmeta=dict(meta);badmeta['shards']=[head];copy['pack.json']=json.dumps(badmeta).encode();add('pack-missing-history',archive(copy),'demo.pub','ERROR',kind='open')
add('pack-traversal',archive({'../outside.txt':b'not executable'}),'demo.pub','ERROR',kind='open')
add('pack-duplicate-json',archive({'pack.json':b'{"format":"x","format":"y"}'}),'demo.pub','ERROR',kind='open')
for case in cases:
 case['expected_exit']=0 if case['expected_status']=='PASS' else 2 if case['expected_status']=='ERROR' or (case['operation']=='vector' and case['expected_codes'] and set(case['expected_codes'])<={'E_LAYOUT_MISSING','E_SCHEMA_MISSING','E_SIG_MISSING'}) else 1
(OUT/'index.json').write_text(json.dumps({'profile':'axm-pack-preview/0.1','version':'0.2.0','cases':cases,'scope':'Kernel and transport interoperability fixtures. CI and demo public keys authenticate test data only; no production publisher identity is claimed.'},indent=2)+'\n')
