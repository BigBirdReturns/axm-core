"""Exercise the public static UI and WebAssembly vectors in an isolated Chrome profile."""
from __future__ import annotations
import argparse,functools,http.server,json,os,shutil,subprocess,tempfile,threading,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--origin');p.add_argument('--output',type=Path,default=ROOT/'build/browser');p.add_argument('--chrome');a=p.parse_args()
 chrome=a.chrome or os.environ.get('CHROME_PATH') or shutil.which('google-chrome') or shutil.which('chromium') or shutil.which('chromium-browser')
 node=shutil.which('node')
 if not chrome or not node:raise SystemExit('Chrome/Chromium and Node 22+ are required for browser acceptance.')
 a.output.mkdir(parents=True,exist_ok=True);server=None
 with tempfile.TemporaryDirectory(prefix='axm-browser-') as temp:
  profile=Path(temp)/'profile';log=(a.output/'chrome.log').open('w')
  if not a.origin:
   server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(http.server.SimpleHTTPRequestHandler,directory=str(ROOT/'web')))
   threading.Thread(target=server.serve_forever,daemon=True).start();origin=f'http://127.0.0.1:{server.server_port}/'
  else:origin=a.origin
  args=[chrome,'--headless=new','--disable-gpu','--disable-dev-shm-usage','--disable-background-networking','--disable-component-update','--no-first-run','--no-default-browser-check','--disable-sync','--metrics-recording-only','--renderer-process-limit=2','--remote-debugging-port=0','--user-data-dir='+str(profile),'about:blank']
  if os.environ.get('CI')=='true':args.insert(1,'--no-sandbox')
  child=subprocess.Popen(args,stdout=log,stderr=subprocess.STDOUT)
  try:
   for _ in range(100):
    portfile=profile/'DevToolsActivePort'
    if portfile.exists():break
    if child.poll() is not None:raise RuntimeError('Chrome exited before readiness; see chrome.log')
    time.sleep(.1)
   else:raise RuntimeError('Chrome readiness timeout')
   port=portfile.read_text().splitlines()[0]
   subprocess.run([node,str(ROOT/'tests/browser.mjs'),port,origin,str(a.output.resolve())],cwd=ROOT,check=True,timeout=180)
  finally:
   child.terminate()
   try:child.wait(10)
   except subprocess.TimeoutExpired:child.kill();child.wait()
   log.close()
   if server:server.shutdown();server.server_close()
 print(json.dumps({'status':'PASS','origin':origin,'output':str(a.output)}))
if __name__=='__main__':main()
