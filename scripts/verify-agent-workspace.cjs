const {chromium}=require('playwright');
const assert=require('node:assert/strict');

(async()=>{
  const base=process.env.TESTFIX_UI_BASE,task=process.env.TESTFIX_TASK_ID,runId=process.env.TESTFIX_RUN_ID;
  if(!base||!task||!runId)throw Error('Set TESTFIX_UI_BASE, TESTFIX_TASK_ID and TESTFIX_RUN_ID');
  const browser=await chromium.launch({channel:'msedge',headless:true,args:['--enable-unsafe-swiftshader']});
  try{
    const page=await browser.newPage({viewport:{width:1500,height:950}}),errors=[],models=[];
    page.on('pageerror',e=>errors.push(e.message));
    page.on('console',message=>{if(message.type()==='error'&&/Couldn't load texture|Content Security Policy/.test(message.text()))errors.push(message.text());});
    page.on('response',response=>{if(response.url().includes('/vendor/models/')&&response.url().endsWith('.glb'))models.push(response.status());});
    await page.addInitScript(()=>{
      const Native=window.EventSource;
      window.EventSource=class {
        constructor(url){
          if(!url.includes('/ui-fixture/stream'))return new Native(url);
          this.closed=false;window.testStream=this;
        }
        close(){this.closed=true;}
      };
    });
    await page.goto(base+'/implementation?'+new URLSearchParams({task,run:runId}));
    await page.waitForFunction(()=>document.querySelector('#run-flow').dataset.renderer==='webgl');
    assert.deepEqual(models,[200,200,200,200,200,200],'all six local character models must load');
    await page.waitForFunction(()=>document.querySelectorAll('#flow-team-list .flow-team-item').length>0);
    const saved=await (await page.request.get(base+'/api/runs/'+runId)).json();
    assert.equal(await page.locator('#flow-mode').textContent(),'本次运行已结束');
    assert.equal(await page.locator('#flow-position').inputValue(),String(saved.events.length));
    assert.match(await page.locator('#flow-team-list').innerText(),/Agent/);
    assert.ok((await page.locator('#flow-activity-list li').count())>0);
    await page.locator('#flow-team-list .flow-team-item').first().click();
    assert.notEqual(await page.locator('#flow-node-title').textContent(),'CodeAgent 主控');
    await page.locator('#flow-expand').click();
    await page.screenshot({path:'artifacts/agent-workspace-real.png'});
    await page.locator('#flow-expand').click();
    const assigned=saved.events.findIndex(e=>e.stage==='employee_assigned');
    assert.ok(assigned>=0,'saved run must include real assignment events');
    let count=assigned+1;
    await page.route('**/api/project-runs',r=>r.request().method()==='POST'?r.fulfill({status:202,json:{id:'ui-fixture'}}):r.continue());
    await page.route('**/api/runs/ui-fixture',r=>r.fulfill({json:{...saved,id:'ui-fixture',status:'running',events:saved.events.slice(0,count)}}));
    await page.locator('#goal').fill('测试工作区展示');
    await page.locator('#start').click();
    await page.waitForFunction(()=>document.querySelector('#flow-mode').textContent==='实时执行');
    assert.ok(await page.locator('#flow-team-list [data-state="running"]').count()>0);
    assert.match(await page.locator('#flow-activity-list').innerText(),/主控派工/);
    assert.equal(await page.locator('#run-flow').evaluate(e=>{const r=e.getBoundingClientRect();return r.top<innerHeight&&r.bottom>0;}),true);
    count+=1;
    await page.waitForFunction(()=>window.testStream&&!window.testStream.closed);
    await page.evaluate(value=>window.testStream.onmessage({data:JSON.stringify(value)}),{...saved,id:'ui-fixture',status:'running',events:saved.events.slice(0,count)});
    await page.waitForFunction(n=>document.querySelector('#flow-position').value===String(n),count);
    await page.locator('#flow-expand').click();
    const a=await page.locator('#flow-canvas').screenshot();await page.waitForTimeout(250);const b=await page.locator('#flow-canvas').screenshot();
    assert.notEqual(Buffer.compare(a,b),0,'active 3D frame should change');
    await page.screenshot({path:'artifacts/agent-workspace-live-fixture.png'});
    await page.locator('#flow-expand').click();
    await page.setViewportSize({width:390,height:900});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    const fallback=await browser.newPage({viewport:{width:1500,height:950}});
    await fallback.route('**/lab/vendor/three.module.min.js',route=>route.abort());
    await fallback.goto(base+'/implementation?'+new URLSearchParams({task,run:runId}));
    await fallback.locator('#run-flow').scrollIntoViewIfNeeded();
    await fallback.locator('#flow-fallback').waitFor();
    await fallback.waitForFunction(()=>document.querySelectorAll('#flow-team-list .flow-team-item').length>0);
    await fallback.locator('#flow-team-list .flow-team-item').first().click();
    assert.notEqual(await fallback.locator('#flow-node-title').textContent(),'CodeAgent 主控');
    await fallback.close();
    const missingModel=await browser.newPage({viewport:{width:1500,height:950}});
    await missingModel.route('**/vendor/models/ItEngineer.glb',route=>route.abort());
    await missingModel.goto(base+'/implementation?'+new URLSearchParams({task,run:runId}));
    await missingModel.locator('#run-flow').scrollIntoViewIfNeeded();
    await missingModel.locator('#flow-fallback').waitFor();
    await missingModel.waitForFunction(()=>document.querySelectorAll('#flow-team-list .flow-team-item').length>0);
    await missingModel.close();
    assert.deepEqual(errors,[]);
    console.log('Saved run and controlled EventSource events: 3D workspace, team assignments, progress and auto-scroll verified; no model call.');
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
