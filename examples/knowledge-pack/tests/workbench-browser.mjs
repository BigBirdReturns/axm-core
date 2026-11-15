import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
const [port,origin,out,source]=process.argv.slice(2);
await fs.mkdir(out,{recursive:true});
const tabs=await(await fetch(`http://127.0.0.1:${port}/json/list`)).json();
const ws=new WebSocket(tabs.find(t=>t.type==='page').webSocketDebuggerUrl);
await new Promise((ok,no)=>{ws.onopen=ok;ws.onerror=no;});
let next=0;const pending=new Map(),errors=[],requests=[],results=[];
ws.onmessage=e=>{const m=JSON.parse(e.data);if(m.method==='Runtime.exceptionThrown')errors.push(m.params.exceptionDetails);if(m.method==='Network.requestWillBeSent')requests.push(m.params.request.url);const p=pending.get(m.id);if(p){pending.delete(m.id);m.error?p.no(Error(JSON.stringify(m.error))):p.ok(m.result);}};
function call(method,params={}){return new Promise((ok,no)=>{const id=++next;pending.set(id,{ok,no});ws.send(JSON.stringify({id,method,params}));setTimeout(()=>{if(pending.has(id)){pending.delete(id);no(Error('CDP timeout '+method));}},30000).unref();});}
async function evaluate(expression){const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value;}
async function wait(expression){for(let i=0;i<300;i++){if(await evaluate(expression))return;await new Promise(r=>setTimeout(r,100));}throw Error('Timeout: '+expression+'\n'+await evaluate('document.body.innerText.slice(-5000)'));}
async function click(selector){await evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`);}
async function fill(selector,value){await evaluate(`document.querySelector(${JSON.stringify(selector)}).value=${JSON.stringify(value)}`);}
async function state(){return await evaluate(`fetch('/api/state').then(r=>r.json())`);}
async function snapshot(name){const s=await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});await fs.writeFile(path.join(out,name+'.png'),Buffer.from(s.data,'base64'));}
function pass(name,detail={}){results.push({name,status:'PASS',...detail});}
let lastCompletedJob=null;
async function waitJobs(){
  // A click schedules fetch asynchronously. Bind to a NEW durable job before
  // accepting terminal state, otherwise a fast runner can observe the old job.
  const previous=lastCompletedJob;
  await wait(`fetch('/api/state').then(r=>r.json()).then(s=>s.jobs.length&&s.jobs[0].id!==${JSON.stringify(previous)}&&s.jobs.every(j=>!['queued','running'].includes(j.state)))`);
  const terminal=await state();
  assert.notEqual(terminal.jobs[0].id,previous);
  assert.equal(terminal.jobs[0].state,'completed',JSON.stringify(terminal.jobs[0]));
  lastCompletedJob=terminal.jobs[0].id;
  await click('#reload');
  await wait(`document.getElementById('connection').textContent==='Local service connected'`);
}
async function reviewAndSeal(){await wait(`document.querySelectorAll('.candidate-select').length>0`);assert.equal(await evaluate(`document.querySelectorAll('.candidate-select:checked').length`),0);await evaluate(`Array.from(document.querySelectorAll('#review-content .toolbar button')).find(b=>b.textContent==='Select all proposals').click()`);await click('#seal');await waitJobs();}
await call('Runtime.enable');await call('Page.enable');await call('Network.enable');
await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1050,deviceScaleFactor:1,mobile:false});
await call('Page.navigate',{url:origin});await wait(`document.getElementById('connection').textContent==='Local service connected'`);
assert.equal((await state()).vaults.length,2);pass('Two registered vaults load in the real application');
await click('#extract');await click('#scan');await waitJobs();await wait(`document.querySelectorAll('.candidate-select').length>0`);
assert.ok((await evaluate(`document.getElementById('review-content').textContent`)).includes('absent or ambiguous'));
assert.equal(await evaluate('window.axmInjected'),undefined);assert.equal(await evaluate(`document.querySelectorAll('#review-content img').length`),0);
await snapshot('review-desktop');pass('Evidence review rejects invented quotes and displays source HTML as text');
await evaluate(`Array.from(document.querySelectorAll('#review-content button')).find(b=>b.textContent==='Inspect source changes').click()`);await wait(`document.getElementById('review-content').textContent.includes('Complete staged original')`);pass('Exact staged source and diff are inspectable before approval');
const original=await fs.readFile(path.join(source,'Protocol.md'));
await reviewAndSeal();assert.equal((await state()).vaults.find(v=>v.id==='research').generation,1);assert.deepEqual(await fs.readFile(path.join(source,'Protocol.md')),original);pass('Explicit UI selection seals a verified edition without source writes');
await click('[data-tab="search"]');await fill('#query','retains_source_evidence_days');await click('#search-form button[type="submit"]');await wait(`document.querySelectorAll('#search-results .hit').length===1`);assert.ok((await evaluate(`document.getElementById('search-results').textContent`)).includes('90'));
await click('#context');await wait(`!document.getElementById('context-output').hidden`);assert.ok((await evaluate(`document.getElementById('context-text').textContent`)).includes('90'));pass('Verified claim search and evidence-bound agent context work through the UI');await snapshot('search-desktop');
await fill('#vault','operations');await evaluate(`document.getElementById('vault').dispatchEvent(new Event('change'))`);await click('[data-tab="review"]');await click('#scan');await waitJobs();await reviewAndSeal();pass('A second vault seals independently');
await click('[data-tab="search"]');await click('#conflicts');await wait(`document.getElementById('search-results').textContent.includes('different approved values')`);pass('Cross-vault competing assertions are surfaced without claiming an automatic truth verdict');
await fill('#vault','research');await evaluate(`document.getElementById('vault').dispatchEvent(new Event('change'))`);await click('[data-tab="review"]');
await fs.writeFile(path.join(source,'Protocol.md'),original.toString().replace('90 days','180 days'));
await click('#scan');await waitJobs();await wait(`document.querySelectorAll('.candidate-select').length>0`);await reviewAndSeal();assert.equal((await state()).vaults.find(v=>v.id==='research').generation,2);pass('Edited notes produce reviewed successor editions');
await click('[data-tab="search"]');await fill('#search-vault','selected');await fill('#query','180 days');await fill('#scope','current');await click('#search-form button[type="submit"]');await wait(`document.getElementById('search-results').textContent.includes('180 days')`);
await fill('#query','90 days');await fill('#scope','history');await click('#search-form button[type="submit"]');await wait(`document.getElementById('search-results').textContent.includes('90 days')`);pass('Current and historical evidence scopes stay separate');
await click('[data-tab="history"]');await click('#load-history');await wait(`document.querySelectorAll('.edition').length===2`);pass('Signed successor lineage renders in history');
await click('[data-tab="graph"]');await click('#load-graph');await wait(`document.querySelectorAll('.edge').length===1`);pass('Retained source wikilinks resolve to real graph edges');
await click('#native');await waitJobs();const native=await fs.readFile(path.join(source,'_AXM-Workbench','Editions.base'));assert.ok(native.length);pass('Native Obsidian views refresh through the UI');
await click('[data-tab="review"]');await click('#scan');await waitJobs();await wait(`document.getElementById('review-content').textContent.includes('No source changes')`);await click('#seal');await waitJobs();assert.equal((await state()).vaults.find(v=>v.id==='research').generation,2);pass('No-change approval reuses the edition');
const beforeWatch=(await state()).plans.length;
await click('#watch');await wait(`fetch('/api/state').then(r=>r.json()).then(s=>s.watching.includes('research'))`);await new Promise(r=>setTimeout(r,500));
await fs.appendFile(path.join(source,'Evidence.md'),'\nWatcher creates a draft only.\n');
await wait(`fetch('/api/state').then(r=>r.json()).then(s=>s.plans.length>${beforeWatch})`);await waitJobs();const watched=await state();assert.equal(watched.vaults.find(v=>v.id==='research').generation,2);assert.equal(watched.plans[0].model_calls,0);await click('#watch');await wait(`fetch('/api/state').then(r=>r.json()).then(s=>s.watching.length===0)`);pass('Real filesystem watcher creates drafts without model calls or automatic signing');
await click('#export');await waitJobs();const exported=await state();const exportResult=JSON.parse(exported.jobs[0].detail);assert.ok(exportResult.download);const downloads=path.join(out,'downloads',crypto.randomUUID());await fs.mkdir(downloads,{recursive:true});await call('Browser.setDownloadBehavior',{behavior:'allow',downloadPath:downloads});await wait(`document.querySelector('.download-link')`);await click('.download-link');let archive;for(let i=0;i<100;i++){const names=await fs.readdir(downloads);archive=names.find(n=>n.endsWith('.zip'));if(archive)break;await new Promise(r=>setTimeout(r,100));}assert.ok(archive);const exportedBytes=await fs.readFile(path.join(downloads,archive));assert.equal(crypto.createHash('sha256').update(exportedBytes).digest('hex'),exportResult.archive_sha256);pass('Browser downloads the exact independently verified signed collection');
await click('[data-tab="settings"]');await fill('#identity-name','browser-test-identity');await click('#create-identity');await wait(`document.getElementById('register-private').value.endsWith('publisher.key')`);pass('Explicit identity creation returns only local key paths');
const extra=path.join(path.dirname(source),'ui-registered');await fs.mkdir(extra);await fs.writeFile(path.join(extra,'Note.md'),'# Browser registration fixture\nA separately registered source.\n');await fill('#register-id','ui-registered');await fill('#register-label','Browser registered');await fill('#register-path',extra);await fill('#register-publisher-id','ui-test');await fill('#register-publisher-name','UI test identity');await evaluate(`document.getElementById('register-form').requestSubmit()`);await wait(`fetch('/api/state').then(r=>r.json()).then(s=>s.vaults.some(v=>v.id==='ui-registered'))`);pass('An existing local directory is registered through the browser');
await fill('#provider-type','none');await evaluate(`document.getElementById('provider-form').requestSubmit()`);await wait(`fetch('/api/state').then(r=>r.json()).then(s=>!s.provider.enabled)`);await click('#reload');await wait(`document.getElementById('extract').disabled`);assert.equal(await evaluate(`document.getElementById('extract').checked`),false);pass('Disabling semantic extraction also clears the browser extraction selection');await snapshot('settings-desktop');
await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});await evaluate('window.scrollTo(0,0)');await new Promise(r=>setTimeout(r,200));const sizes=await evaluate('({inner:innerWidth,scroll:document.documentElement.scrollWidth})');assert.ok(sizes.scroll<=sizes.inner+1,JSON.stringify(sizes));await snapshot('mobile');pass('390px layout remains within viewport',sizes);
assert.deepEqual(errors,[]);assert.deepEqual(requests.filter(u=>/^https?:/.test(u)&&!u.startsWith(origin)),[]);pass('No browser exceptions or external page requests');
await fs.writeFile(path.join(out,'workbench-browser-acceptance.json'),JSON.stringify({status:'PASS',provider:'deterministic browser fixture, not a live model',results,errors,requests},null,2));console.log(JSON.stringify({status:'PASS',checks:results.length}));ws.close();
