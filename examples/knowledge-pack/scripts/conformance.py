"""Run the public vector manifest against a standalone reader; no private keys."""
from pathlib import Path
import argparse,json,subprocess,sys,hashlib
ROOT=Path(__file__).resolve().parents[1]
def main():
 p=argparse.ArgumentParser();p.add_argument('--reader',required=True,type=Path);p.add_argument('--output',type=Path);a=p.parse_args();index=ROOT/'web/conformance';manifest=json.loads((index/'index.json').read_text());checks=[]
 for case in manifest['cases']:
  source=index/case['file'];assert hashlib.sha256(source.read_bytes()).hexdigest()==case['sha256']
  command='vector' if case['operation']=='vector' else 'inspect'
  r=subprocess.run([str(a.reader.resolve()),command,str(source),'--trusted-key',str(index/case['key'])],capture_output=True,text=True,timeout=20)
  v=json.loads(r.stdout);expected=case['expected_status'];assert v['status']==expected,(case['name'],v)
  codes=sorted({x['code'] for x in v.get('errors',[])}) if command=='vector' else sorted({x['code'] for c in v.get('checks',[]) for x in c['errors']})
  if expected!='ERROR':assert codes==sorted(case['expected_codes']),(case['name'],codes,case['expected_codes'])
  if command=='vector':
   assert v['profiles_checked']==case['profiles_checked'];assert v['profiles_unchecked']==case['profiles_unchecked']
  assert r.returncode==case['expected_exit'],(case['name'],r.returncode)
  checks.append({'name':case['name'],'status':'PASS','observed':v['status']})
 report={'status':'PASS','cases':len(checks),'checks':checks}
 if a.output:a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps(report))
if __name__=='__main__':main()
