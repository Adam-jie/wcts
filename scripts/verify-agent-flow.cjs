// UI integration tests; replay uses a real saved run. Live transport cases use
// explicitly truncated snapshots, and never start model inference or repairs.
const {chromium}=require(process.env.TESTFIX_PLAYWRIGHT||'playwright');
const fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict');
const base=process.env.TESTFIX_UI_BASE||'http://127.0.0.1:2890';
const root=path.resolve(__dirname,'..'),out=process.env.TESTFIX_UI_EVIDENCE||path.join(root,'artifacts/agent-flow-check');
(async()=>{
  await fs.mkdir(out,{recursive:true});
  const pointer=JSON.parse(await fs.readFile(process.env.TESTFIX_RUN_POINTER||path.join(root,'artifacts/implementation-check/taskboard-run.json'),'utf8'));
  const url=base+'/implementation?'+new URLSearchParams({task:pointer.intake_id,run:pointer.run_id});
  const browser=await chromium.launch({channel:'msedge',headless:true,args:['--enable-unsafe-swiftshader']});
  const errors=[],checks=[];let modelSubmissions=0;
  try{
    const context=await browser.newContext({viewport:{width:1500,height:1000}});
    context.on('page',p=>{p.on('pageerror',e=>errors.push(e.message));p.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname==='/api/project-runs')modelSubmissions++;});});
    await context.route('**/favicon.ico',r=>r.fulfill({status:204,body:''}));
    const page=await context.newPage();await page.goto(url);
    await page.locator('#run-status').filter({hasText:'已完成并通过验收'}).waitFor();
    await page.locator('#run-flow').scrollIntoViewIfNeeded();
    await page.waitForFunction(()=>document.querySelector('#run-flow').dataset.renderer==='webgl');
    const saved=await (await page.request.get(base+'/api/runs/'+pointer.run_id)).json();
    assert.equal(await page.locator('#flow-position').getAttribute('max'),String(saved.events.length));
    assert.equal(await page.locator('#flow-mode').textContent(),'本次运行已结束');
    checks.push('real saved run and exact event count; terminal state is not labeled live');
    await page.locator('#flow-expand').click();await page.waitForTimeout(200);
    await page.screenshot({path:path.join(out,'overview.png')});
    assert.equal(await page.locator('.flow-label[data-kind="controller"],.flow-label[data-kind="module"]').count(),3);
    await page.locator('[data-flow-group="plan-workflow"]').click();
    assert.equal(await page.locator('.flow-label[data-node="constraint-scheduler"]').count(),1);
    assert.equal(await page.locator('.flow-label[data-kind="employee"]').count(),0);
    await page.locator('[data-flow-group="module-generation"]').click();
    assert.equal(await page.locator('.flow-label[data-kind="employee"]').count(),4);
    assert.equal(await page.locator('.flow-label[data-node="repair-code"]').count(),0);
    await page.screenshot({path:path.join(out,'generation-module.png')});
    checks.push('three module hierarchy: controller tools, scoped employee instances and separate execution base');
    await page.locator('.flow-nodes [data-node="employee:1:explore_completion"]').click();
    assert.match(await page.locator('#flow-node-detail').textContent(),/completed|SQLite/);
    assert.match(await page.locator('#flow-node-files').textContent(),/taskboard\/storage.py/);
    checks.push('employee detail uses actual task and allowed paths');
    const label=page.locator('.flow-label[data-node="plan-workflow"]');
    const initial=await label.getAttribute('style'),canvas=await page.locator('#flow-canvas').boundingBox();
    await page.mouse.move(canvas.x+canvas.width*.5,canvas.y+canvas.height*.3);await page.mouse.down();
    await page.mouse.move(canvas.x+canvas.width*.5+75,canvas.y+canvas.height*.3+18,{steps:8});await page.mouse.up();
    await page.waitForTimeout(150);assert.notEqual(await label.getAttribute('style'),initial);
    await page.mouse.wheel(0,-160);await page.locator('#flow-reset').click();checks.push('3D drag orbit, zoom and camera reset');
    await page.locator('#flow-position').fill('3');
    assert.equal(await page.locator('#run-flow').getAttribute('data-mode'),'replay');
    assert.equal(await page.locator('#flow-mode').textContent(),'回放已暂停');
    await page.locator('#flow-replay').click();await page.waitForTimeout(700);await page.locator('#flow-replay').click();
    const paused=await page.locator('#flow-position').inputValue();assert.ok(Number(paused)>3);
    await page.waitForTimeout(700);assert.equal(await page.locator('#flow-position').inputValue(),paused);
    checks.push('historical play/pause and seeking do not advance while paused');
    const assigned=saved.events.findIndex(e=>e.stage==='employee_assigned');
    await page.locator('#flow-position').fill(String(assigned+2));
    assert.equal(await page.locator('.flow-nodes [data-node="employee:1:explore_completion"]').getAttribute('data-state'),'running');
    assert.equal(await page.locator('.flow-nodes [data-node="employee:1:explore_query_paging"]').getAttribute('data-state'),'running');
    await page.screenshot({path:path.join(out,'parallel-replay.png')});
    await page.locator('#flow-position').fill(String(assigned+1));await page.locator('#flow-speed').selectOption('1');await page.locator('#flow-replay').click();
    await page.waitForTimeout(120);const motionA=await page.locator('#flow-canvas').screenshot();
    await page.waitForTimeout(180);const motionB=await page.locator('#flow-canvas').screenshot();assert.notEqual(Buffer.compare(motionA,motionB),0);
    await page.screenshot({path:path.join(out,'active-transfer.png')});await page.locator('#flow-replay').click();
    checks.push('rendered 3D frames change during actual recorded assignment playback');
    await page.locator('#flow-position').fill(String(saved.events.findIndex(e=>e.agent==='diagnose-results'&&e.stage==='completed')+1));
    assert.match(await page.locator('#flow-message').textContent(),/Storage|搜索|分页/);checks.push('parallel employees and actual diagnostic summary');
    await page.locator('#flow-live').click();assert.equal(await page.locator('#flow-position').inputValue(),String(saved.events.length));
    await page.keyboard.press('Escape');assert.equal(await page.locator('#flow-expand').getAttribute('aria-expanded'),'false');
    await page.setViewportSize({width:390,height:900});await page.locator('#run-flow').scrollIntoViewIfNeeded();
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    await page.screenshot({path:path.join(out,'narrow.png'),fullPage:true});checks.push('return to latest, escape expanded mode and narrow screen');

    const failedHistory=await page.locator('#run-history option').evaluateAll(options=>options.find(o=>o.textContent.includes('执行失败'))?.value);
    if(failedHistory){
      await page.locator('#run-history').selectOption(failedHistory);await page.locator('#flow-mode').filter({hasText:'运行失败'}).waitFor();
      assert.match(await page.locator('#flow-source').textContent(),/停止原因/);
      await page.locator('#run-history').selectOption(pointer.run_id);await page.locator('#run-status').filter({hasText:'已完成并通过验收'}).waitFor();
      checks.push('switch actual saved histories; failed run shows cause and is never presented as accepted');
    }
    // Transport integration with controlled snapshots, not a second experiment.
    let count=assigned+2,fail=false;
    const live=await context.newPage();
    await live.route('**/api/runs/'+pointer.run_id,route=>fail?route.fulfill({status:503,json:{detail:'Controlled disconnect fixture'}}):route.fulfill({json:{...saved,status:'running',events:saved.events.slice(0,count)}}));
    await live.goto(url);await live.locator('#run-flow').scrollIntoViewIfNeeded();
    await live.waitForFunction(()=>document.querySelector('#flow-mode').textContent==='实时执行');
    assert.equal(await live.locator('#flow-position').inputValue(),String(count));
    count+=2;await live.waitForFunction(n=>document.querySelector('#flow-position').value===String(n),count);
    await live.waitForTimeout(1750);assert.equal(await live.locator('#flow-position').inputValue(),String(count));
    await live.locator('#flow-position').fill('2');count+=1;await live.waitForTimeout(1800);
    assert.equal(await live.locator('#flow-position').inputValue(),'2');
    await live.locator('#flow-live').click();assert.equal(await live.locator('#flow-position').inputValue(),String(count));
    fail=true;await live.waitForFunction(()=>document.querySelector('#flow-mode').textContent.includes('连接中断'));
    fail=false;await live.waitForFunction(()=>document.querySelector('#flow-mode').textContent==='实时执行',{},{timeout:10000});
    checks.push('controlled live polling: append, deduplication, paused replay, resume, disconnect and recovery');
    await live.close();
    const fallback=await context.newPage();await fallback.route('**/lab/vendor/three.module.min.js',r=>r.abort());await fallback.goto(url);
    await fallback.locator('#run-flow').scrollIntoViewIfNeeded();await fallback.locator('#flow-fallback').waitFor();
    await fallback.locator('.flow-nodes [data-node="analyze-requirements"]').click();assert.equal(await fallback.locator('#flow-node-title').textContent(),'问题分析');
    checks.push('3D failure preserves accessible actual node details');await fallback.close();
    assert.equal(modelSubmissions,0);assert.deepEqual(errors,[]);
    const result={status:'passed',source_run:pointer.run_id,checks,model_submissions:modelSubmissions,browser_errors:errors,
      evidence:'Saved real TaskBoard run for playback; controlled snapshots for live transport tests. No new LLM experiment performed.'};
    await fs.writeFile(path.join(out,'validation.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result,null,2));
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
