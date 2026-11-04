"""Run creator regressions with a fresh ephemeral key; no author identity is needed."""
from pathlib import Path
import os,subprocess,sys,tempfile
ROOT=Path(__file__).resolve().parents[1]
def main():
 import axm_build  # Real local kernel installation, never a test stub.
 with tempfile.TemporaryDirectory(prefix='axm-public-creator-') as temp:
  state=Path(temp);kernel=ROOT/'vendor/axm-genesis';verifier=ROOT/'build'/('axm-verify-go.exe' if os.name=='nt' else 'axm-verify-go');reader=ROOT/'build'/('axm-pack-reader.exe' if os.name=='nt' else 'axm-pack-reader')
  subprocess.run([sys.executable,str(ROOT/'build_example.py'),'--state',str(state),'--genesis',str(kernel),'--go-verifier',str(verifier)],cwd=ROOT,check=True)
  env=os.environ.copy();env.update(PYTHONPATH=str(ROOT),AXM_TEST_DATA=str(state),AXM_GO_VERIFY=str(verifier),AXM_GO_READER=str(reader),AXM_GENESIS_SNAPSHOT=str(kernel))
  subprocess.run([sys.executable,'-m','pytest',str(ROOT/'tests/test_pack.py'),'-q'],cwd=ROOT,env=env,check=True)
if __name__=='__main__':main()
