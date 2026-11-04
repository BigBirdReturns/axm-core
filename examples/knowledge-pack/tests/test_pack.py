from pathlib import Path
import io,json,os,stat,subprocess,zipfile
import pytest
import pack

DATA=Path(os.environ['AXM_TEST_DATA'])
GO=os.environ.get('AXM_GO_VERIFY')
READER=os.environ.get('AXM_GO_READER')
@pytest.fixture
def archive():return (DATA/'example.axmpack.zip').read_bytes()
@pytest.fixture
def key():return (DATA/'trusted-demo.pub').read_bytes()

def zip_of(entries):
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:
        for name,data in entries:z.writestr(name,data)
    return out.getvalue()

def test_roundtrip(archive,key):
    v=pack.inspect_pack(archive,key,GO)
    assert v['status']=='PASS' and len(v['records'])==2
    assert len(v['records'][-1]['claims'])==3
    assert v['records'][-1]['supersedes']==[v['records'][0]['id']]
    assert len(v['checks'])==4

@pytest.mark.parametrize('mode,code',[('source','E_MERKLE_MISMATCH'),('ed25519','E_SIG_INVALID'),('mldsa','E_SIG_INVALID'),('wrong-key','E_SIG_INVALID')])
def test_real_tamper(archive,key,mode,code):
    before=pack.digest(archive);v=pack.challenge(archive,key,mode,GO)
    assert v['status']=='FAIL' and v['records']==[]
    assert v['challenge']['original_preserved'] and pack.digest(archive)==before
    assert any(code in [e['code'] for e in c['errors']] for c in v['checks'])
    assert any(c['status']=='FAIL' and c['implementation'].startswith('Go') for c in v['checks'])

@pytest.mark.parametrize('size',[0,32,1343,1345])
def test_missing_or_wrong_length_key(archive,size):
    with pytest.raises(ValueError,match='separate'):pack.inspect_pack(archive,bytes(size),GO)

@pytest.mark.parametrize('name',['../escape','/absolute','C:/Windows/x','a\\b','a/../b','pack.json/child','CON','a/NUL.txt','a/x.','a//b'])
def test_path_rejection(name):
    with pytest.raises(ValueError):pack.unpack(zip_of([(name,b'x')]))

def test_duplicate_member():
    with pytest.raises(ValueError):pack.unpack(zip_of([('pack.json',b'{}'),('pack.json',b'{}')]))

def test_case_collision():
    with pytest.raises(ValueError):pack.unpack(zip_of([('pack.json',b'{}'),('PACK.JSON',b'{}')]))

def test_symlink():
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:
        info=zipfile.ZipInfo('pack.json');info.external_attr=(stat.S_IFLNK|0o777)<<16;z.writestr(info,b'/etc/passwd')
    with pytest.raises(ValueError):pack.unpack(out.getvalue())

def test_zip_bomb():
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED) as z:z.writestr('pack.json',b'x'*2_000_000)
    with pytest.raises(ValueError,match='ratio'):pack.unpack(out.getvalue())

def test_duplicate_json_key():
    with pytest.raises(ValueError,match='Duplicate JSON'):pack.loads(b'{"x":1,"x":2}')

def test_missing_history(archive,key):
    files=pack.unpack(archive);meta=pack.loads(files['pack.json']);head=meta['head']
    files={k:v for k,v in files.items() if k=='pack.json' or k.startswith(f'shards/{head}/')}
    meta['shards']=[head];files['pack.json']=json.dumps(meta).encode()
    with pytest.raises(ValueError,match='incomplete'):pack.inspect_pack(pack.pack_files(files),key,GO)

def test_unsigned_head_cannot_lie(archive,key):
    files=pack.unpack(archive);meta=pack.loads(files['pack.json']);meta['head']=next(s for s in meta['shards'] if s!=meta['head']);files['pack.json']=json.dumps(meta).encode()
    with pytest.raises(ValueError,match='head'):pack.inspect_pack(pack.pack_files(files),key,GO)

def test_original_is_byte_exact(archive):
    files=pack.unpack(archive);meta=pack.loads(files['pack.json'])
    source=(Path(os.environ['AXM_GENESIS_SNAPSHOT'])/'README.md').read_bytes()
    assert files[f"shards/{meta['head']}/content/original.txt"]==source

def test_predecessor_bytes_unchanged(archive):
    first=pack.unpack((DATA/'edition-1.axmpack.zip').read_bytes());second=pack.unpack(archive)
    assert all(second[n]==v for n,v in first.items() if n!='pack.json')

def test_unicode_crlf_and_original(key):
    text='A café decision.\r\nKeep e\u0301 evidence and Thai ภาษาไทย.\r\n'
    raw,v=pack.compile_pack('Unicode custody',text,['Keep e\u0301 evidence and Thai ภาษาไทย.'],(DATA/'identity/demo.key').read_bytes(),key,go_binary=GO)
    files=pack.unpack(raw)
    assert files[f"shards/{v['head']}/content/original.txt"]==text.encode()
    e=v['records'][0]['claims'][0]['evidence'][0]
    canonical=files[f"shards/{v['head']}/content/source.txt"]
    assert canonical[e['byte_start']:e['byte_end']].decode()==e['text']

@pytest.mark.parametrize('source,quotes',[('same same',['same']),('one',['missing']),('', ['x']),('one',['one','one'])])
def test_bad_candidate_refused(key,source,quotes):
    with pytest.raises(ValueError):pack.compile_pack('Bad candidate',source,quotes,(DATA/'identity/demo.key').read_bytes(),key,go_binary=GO)

def test_independent_reader_exact_parity(archive,key,tmp_path):
    if not READER:pytest.skip('standalone reader not built yet')
    p=tmp_path/'pack.zip';p.write_bytes(archive);k=tmp_path/'trust.pub';k.write_bytes(key)
    r=subprocess.run([READER,'inspect',str(p),'--trusted-key',str(k)],capture_output=True,text=True,check=True)
    go=json.loads(r.stdout);py=pack.inspect_pack(archive,key,GO)
    for field in ['head','records','archive_sha256','trusted_key_sha256','status']:assert py[field]==go[field],field
