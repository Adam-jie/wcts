// Controlled transport regressions. No model or repeat jobs are submitted.
const {chromium}=require(process.env.TESTFIX_PLAYWRIGHT||'playwright');
const fs=require('node:fs/promises'),assert=require('node:assert/strict');
const base=process.env.TESTFIX_UI_BASE||'http://127.0.0.1:2890';
(async()=>{
  const pointer=JSON.parse(await fs.readFile('artifacts/wctp-product-check/fresh-run.json','utf8'));
  const browser=await chromium.launch({channel:'msedge',headless:true});const errors=[],checks=[];
  try{
    const client=await browser.newContext();const source=await (await client.request.get(base+'/api/runs/'+pointer.run_id)).json();
    const other={...source,id:'b'.repeat(32)},campaign='d'.repeat(32);
    for(const boundary of ['post','watch']){
      const page=await client.newPage();page.on('pageerror',e=>errors.push(e.message));let release,arrived;
      const held=new Promise(r=>release=r),entered=new Promise(r=>arrived=r);
      await page.route(base+'/api/runs',r=>r.fulfill({json:{runs:[source,other]}}));
      await page.route(base+'/api/runs/'+other.id,r=>r.fulfill({json:other}));
      await page.route(base+'/api/project-runs/'+other.id+'/wctp-tests',r=>r.fulfill({json:{cases:[],active_repeat:null,latest_repeat:null}}));
      await page.route(base+'/api/project-runs/'+source.id+'/wctp-tests',async r=>{
        if(r.request().method()!=='POST')return r.continue();
        if(boundary==='post'){arrived();await held;}
        await r.fulfill({status:202,json:{id:campaign,status:'queued'}});
      });
      await page.route(base+'/api/runs/'+campaign,async r=>{
        if(boundary==='watch'){arrived();await held;}
        await r.fulfill({json:{id:campaign,status:'completed',source_run_id:source.id}});
      });
      await page.goto(base+'/implementation?'+new URLSearchParams({task:pointer.intake_id,run:source.id}));
      await page.locator('#wctp-repeat:not([disabled])').waitFor();await page.locator('#wctp-repeat').click();await entered;
      await page.locator('#run-history').selectOption(other.id);await page.waitForFunction(id=>document.querySelector('#run-history').value===id,other.id);
      await page.locator('#wctp-empty').waitFor();release();await page.waitForTimeout(1800);
      assert.equal(await page.locator('#wctp-repeat-status').textContent(),'');
      assert.equal(await page.locator('#wctp-repeat').isEnabled(),true);
      assert.equal(await page.locator('#wctp-content').isVisible(),false);
      checks.push('obsolete '+boundary+' response cannot change another selected run');await page.close();
    }
    const reopened=await client.newPage();reopened.on('pageerror',e=>errors.push(e.message));let polls=0;
    const active={id:campaign,status:'running',iterations:3,created_at:source.created_at};
    await reopened.route(base+'/api/project-runs/'+source.id+'/wctp-tests',r=>r.fulfill({json:{cases:[],active_repeat:polls<2?active:null,latest_repeat:{...active,status:polls<2?'running':'completed'}}}));
    await reopened.route(base+'/api/runs/'+campaign,r=>{polls++;return r.fulfill({json:{...active,status:polls<2?'running':'completed',source_run_id:source.id}});});
    await reopened.goto(base+'/implementation?'+new URLSearchParams({task:pointer.intake_id,run:source.id}));
    await reopened.locator('#wctp-repeat-status').filter({hasText:'执行中'}).waitFor();
    assert.equal(await reopened.locator('#wctp-repeat').isDisabled(),true);
    await reopened.locator('#wctp-repeat-status').filter({hasText:'重测完成'}).waitFor({timeout:15000});
    assert.equal(await reopened.locator('#wctp-repeat').isEnabled(),true);
    checks.push('reopening recovers active campaign polling and terminal state');await reopened.close();
    const recovery=await client.newPage();recovery.on('pageerror',e=>errors.push(e.message));let reads=0;
    await recovery.route(base+'/api/project-runs/'+source.id+'/wctp-tests',r=>r.fulfill({json:{cases:[],active_repeat:reads<2?active:null,latest_repeat:{...active,status:reads<2?'running':'completed'}}}));
    await recovery.route(base+'/api/runs/'+campaign,r=>{reads++;return reads===1?r.fulfill({status:503,json:{detail:'Controlled transient read failure'}}):r.fulfill({json:{...active,status:'completed',source_run_id:source.id}});});
    await recovery.goto(base+'/implementation?'+new URLSearchParams({task:pointer.intake_id,run:source.id}));
    await recovery.locator('#wctp-error').filter({hasText:'连接中断'}).waitFor();
    assert.equal(await recovery.locator('#wctp-repeat').isDisabled(),true);
    await recovery.locator('#wctp-repeat-status').filter({hasText:'重测完成'}).waitFor({timeout:15000});
    assert.equal(await recovery.locator('#wctp-error').isVisible(),false);
    assert.equal(await recovery.locator('#wctp-repeat').isEnabled(),true);
    checks.push('transient status read failure keeps repeat locked and recovers without reload');await recovery.close();
    assert.deepEqual(errors,[]);const result={status:'passed',checks,errors,source:'controlled transport fixtures; zero backend job submissions'};
    await fs.writeFile('artifacts/wctp-product-check/ui/race-validation.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result,null,2));
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
