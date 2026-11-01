// Controlled transport fixtures only. No model or test-run POST reaches backend.
const {chromium}=require(process.env.TESTFIX_PLAYWRIGHT||'playwright');
const fs=require('node:fs/promises'),assert=require('node:assert/strict');
const base=process.env.TESTFIX_UI_BASE||'http://127.0.0.1:2891';
(async()=>{
 const prior=JSON.parse(await fs.readFile('artifacts/wctp-product-check/fresh-run.json','utf8'));
 const b=await chromium.launch({channel:'msedge',headless:true,args:['--enable-unsafe-swiftshader']});
 const old='a'.repeat(32),fresh='b'.repeat(32),errors=[];let release,heldResolve,posts=0;
 const held=new Promise(r=>heldResolve=r),gate=new Promise(r=>release=r);
 const item=(id,status)=>({id,status,kind:'feature_test',project_id:prior.project_id,created_at:'2026-10-27T00:00:00Z',description:'transport fixture',phase:status==='running'?'analysis':'finished',iterations:1});
 try{
  const p=await b.newPage({viewport:{width:1600,height:1100}});p.on('pageerror',e=>errors.push(e.message));
  let lists=0;
  await p.route('**/api/feature-tests*',async route=>{
   const req=route.request();if(req.method()==='POST'){posts++;return route.fulfill({json:{id:fresh,status:'queued'}});}
   if(new URL(req.url()).searchParams.get('project_id')!==prior.project_id)return route.fulfill({json:{runs:[]}});
   if(++lists===1){heldResolve();await gate;return route.fulfill({json:{runs:[item(old,'completed')]}});}
   return route.fulfill({json:{runs:[item(fresh,'running'),item(old,'completed')]}});
  });
  await p.route('**/api/runs/'+fresh,r=>r.fulfill({json:item(fresh,'running')}));
  await p.route('**/api/runs/'+old,r=>r.fulfill({json:item(old,'completed')}));
  await p.route('**/api/project-runs/'+fresh+'/wctp-tests',r=>r.fulfill({json:{cases:[]}}));
  await p.route('**/api/project-runs/'+old+'/wctp-tests',r=>r.fulfill({json:{cases:[]}}));
  await p.goto(base+'/implementation?'+new URLSearchParams({task:prior.intake_id,run:prior.run_id}));await held;
  await p.waitForFunction(()=>document.querySelector('#feature-target').options.length>1);
  await p.locator('#feature-description').fill('验证中文搜索');await p.locator('#feature-start').click();
  await p.waitForFunction(()=>document.querySelector('#feature-history').value==='b'.repeat(32));
  release();await p.waitForTimeout(800);
  assert.equal(await p.locator('#feature-history').inputValue(),fresh);
  assert.match(await p.locator('#feature-status').innerText(),/执行中/);
  assert.equal(await p.locator('#feature-start').isDisabled(),true);
  await p.locator('#project').selectOption('registered:Project/flaskr');
  await p.waitForFunction(()=>document.querySelector('#project-note').textContent.includes('已准备'));
  await p.waitForTimeout(2000);
  assert.equal(await p.locator('#feature-history').inputValue(),'');
  assert.doesNotMatch(await p.locator('#feature-status').innerText(),/执行中/);
  assert.deepEqual(errors,[]);assert.equal(posts,1);
  const result={status:'passed',kind:'controlled_transport_only',backend_model_or_test_submissions:0,errors,
   checks:['late initial history cannot replace a newly submitted run or its selection','switching projects isolates old polling and histories']};
  await fs.writeFile('artifacts/feature-testing-check/race-validation.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result,null,2));
 }finally{release?.();await b.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
