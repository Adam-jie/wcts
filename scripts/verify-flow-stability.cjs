const {chromium}=require(process.env.TESTFIX_PLAYWRIGHT||'playwright');
const fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict');
const base=process.env.TESTFIX_UI_BASE||'http://127.0.0.1:2890';
const runId=process.env.TESTFIX_CHECK_RUN||'8c66a09e7c8143b8a04d921559f9fdb7';
(async()=>{
  const browser=await chromium.launch({channel:'msedge',headless:true,args:['--enable-unsafe-swiftshader']});
  try{
    const page=await browser.newPage({viewport:{width:1500,height:1000}}),errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    const saved=await (await page.request.get(base+'/api/runs/'+runId)).json();
    await page.route('**/api/runs/'+runId,r=>r.fulfill({json:{...saved,status:'running'}}));
    await page.goto(base+'/implementation?task='+saved.intake_id+'&run='+runId);
    await page.locator('#run-flow').scrollIntoViewIfNeeded();
    await page.waitForFunction(()=>document.querySelector('#run-flow').dataset.renderer==='webgl');
    await page.locator('#flow-expand').click();
    await page.locator('[data-flow-group="module-generation"]').click();
    await page.locator('#flow-canvas').hover();await page.mouse.wheel(0,650);
    await page.waitForTimeout(600);
    const stability=await page.evaluate(async()=>{
      const labels=[...document.querySelectorAll('.flow-label')],previous=new Map(),changes=new Map();
      const keep=document.querySelector('.flow-node-choice');
      const inactive=document.querySelector('.flow-inactive');inactive.open=true;
      let replacements=0;
      for(let i=0;i<100;i++){
        for(const label of labels){
          if(previous.has(label)&&previous.get(label)!==label.hidden)changes.set(label,(changes.get(label)||0)+1);
          previous.set(label,label.hidden);
        }
        if(!keep.isConnected)replacements++;
        await new Promise(r=>setTimeout(r,40));
      }
      return {label_toggles:Object.fromEntries([...changes].map(([label,count])=>[label.dataset.node,count])),
        sidebar_replaced:replacements>0,details_preserved:inactive.isConnected&&inactive.open};
    });
    const out=path.resolve('artifacts/wctp-product-check/ui');await fs.mkdir(out,{recursive:true});
    await fs.writeFile(path.join(out,process.env.TESTFIX_REPRO?'stability-before.json':'stability-after.json'),JSON.stringify({stability,errors},null,2));
    console.log(JSON.stringify({stability,errors}));
    assert.deepEqual(stability.label_toggles,{},'stationary labels must not oscillate');
    assert.equal(stability.sidebar_replaced,false,'poll must preserve existing nodes');
    assert.equal(stability.details_preserved,true);
    assert.deepEqual(errors,[]);
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
