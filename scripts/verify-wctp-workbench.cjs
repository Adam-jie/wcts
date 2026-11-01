const {chromium}=require(process.env.TESTFIX_PLAYWRIGHT||'playwright');
const fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict');
const base=process.env.TESTFIX_UI_BASE||'http://127.0.0.1:2890';
(async()=>{
  const out=path.resolve('artifacts/wctp-product-check/ui');await fs.mkdir(out,{recursive:true});
  const pointer=JSON.parse(await fs.readFile('artifacts/wctp-product-check/fresh-run.json','utf8'));
  const browser=await chromium.launch({channel:'msedge',headless:true,args:['--enable-unsafe-swiftshader']});
  const checks=[],errors=[];let repeats=[],modelPosts=0;
  try{
    const page=await browser.newPage({viewport:{width:1600,height:1050}});
    page.on('pageerror',e=>errors.push(e.message));
    page.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname==='/api/project-runs')modelPosts++;});
    page.on('response',async r=>{if(r.request().method()==='POST'&&r.url().endsWith('/wctp-tests')&&r.status()===202)repeats.push((await r.json()).id);});
    await page.goto(base+'/implementation?'+new URLSearchParams({task:pointer.intake_id,run:pointer.run_id}));
    await page.locator('#wctp-panel').scrollIntoViewIfNeeded();
    await page.locator('#wctp-content').waitFor({timeout:900000});
    await page.screenshot({path:path.join(out,'native-wctp-running.png')});
    await page.locator('#run-status').filter({hasText:'已完成并通过验收'}).waitFor({timeout:900000});
    await page.locator('#wctp-refresh').click();
    await page.waitForFunction(()=>[...document.querySelector('#wctp-cases').options].some(o=>o.textContent.startsWith('verify')));
    const verify=await page.locator('#wctp-cases option').evaluateAll(os=>os.findLast(o=>o.textContent.startsWith('verify')).value);
    await page.locator('#wctp-cases').selectOption(verify);
    await page.locator('#wctp-graph [data-step="step_Delivery"]').click();
    await page.waitForFunction(()=>document.querySelectorAll('#wctp-results tr').length===37);
    assert.equal(await page.locator('#wctp-results .good').count(),37);
    checks.push('real WCTP execution graph, native branch/loop types and 37 product checks');
    await page.locator('#wctp-graph [data-step="step_Acceptance"]').click();
    await page.waitForFunction(()=>document.querySelectorAll('#wctp-results tr').length===12);
    assert.equal(await page.locator('#wctp-results .good').count(),12);
    checks.push('independent acceptance per-case results');
    const yaml=await (await page.request.get(base+await page.locator('#wctp-yaml').getAttribute('href'))).text();
    assert.match(yaml,/required_host_steps:/);assert.match(yaml,/step_Loop -->\|True\|/);
    checks.push('WCTP YAML export declares required host actions and loop branches');
    await page.screenshot({path:path.join(out,'native-wctp-results.png')});
    if(process.env.TESTFIX_REUSE_REPEAT){
      repeats=[process.env.TESTFIX_REUSE_REPEAT];await page.locator('#wctp-cases').selectOption(repeats[0]+':repeat');
      await page.waitForFunction(()=>document.querySelector('#wctp-round').options.length===3);
    }else{
      await page.locator('#wctp-iterations').fill('3');await page.locator('#wctp-repeat').click();
      await page.locator('#wctp-repeat-status').filter({hasText:/排队中|执行中/}).waitFor({timeout:20000});
      await page.screenshot({path:path.join(out,'repeat-running.png')});
      await page.locator('#wctp-repeat-status').filter({hasText:'重测完成'}).waitFor({timeout:300000});
    }
    assert.equal(repeats.length,1);
    const campaign=await (await page.request.get(base+'/api/runs/'+repeats[0])).json();
    assert.equal(campaign.status,'completed',JSON.stringify(campaign));assert.equal(campaign.summary.accepted,3);assert.equal(campaign.source_run_id,pointer.run_id);
    await page.locator('#wctp-round').selectOption('3');
    await page.locator('#wctp-graph [data-step="step_Independent"]').click();
    await page.waitForFunction(()=>document.querySelectorAll('#wctp-results tr').length>40);
    const independentBad=await page.locator('#wctp-results tr').evaluateAll(rs=>rs.filter(r=>!r.cells[0].textContent.startsWith('原版对照 / ')&&r.cells[1]?.textContent!=='通过').map(r=>r.innerText));
    assert.deepEqual(independentBad,[]);
    checks.push('three real iterations, separate datasets/databases, before/after comparison, all candidate checks pass');
    await page.screenshot({path:path.join(out,'repeat-completed.png')});
    await page.setViewportSize({width:390,height:900});await page.locator('#wctp-panel').scrollIntoViewIfNeeded();
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    await page.screenshot({path:path.join(out,'wctp-narrow.png')});
    checks.push('narrow viewport contains graph scrolling inside its panel');
    assert.deepEqual(errors,[]);assert.equal(modelPosts,0);
    const result={status:'passed',run_id:pointer.run_id,retest_id:repeats[0],checks,errors,extra_model_submissions:modelPosts,
      repetition_submission:process.env.TESTFIX_REUSE_REPEAT?'existing_campaign_recheck':'new_real_campaign'};
    await fs.writeFile(path.join(out,'wctp-ui-validation.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result,null,2));
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
