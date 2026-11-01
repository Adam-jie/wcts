// This script submits NEW real Skill model calls unless TESTFIX_FEATURE_REUSE is set.
const {chromium}=require(process.env.TESTFIX_PLAYWRIGHT||'playwright');
const fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict');
const base=process.env.TESTFIX_UI_BASE||'http://127.0.0.1:2891',out=path.resolve('artifacts/feature-testing-check');
(async()=>{
  await fs.mkdir(out,{recursive:true});
  const prior=JSON.parse(await fs.readFile('artifacts/wctp-product-check/fresh-run.json','utf8'));
  const browser=await chromium.launch({channel:'msedge',headless:true,args:['--enable-unsafe-swiftshader']});
  const errors=[],checks=[];let id=process.env.TESTFIX_FEATURE_REUSE||'',submitted=0;
  try{
    const page=await browser.newPage({viewport:{width:1600,height:1050}});
    page.on('pageerror',e=>errors.push(e.message));
    page.on('response',async r=>{if(r.request().method()==='POST'&&new URL(r.url()).pathname==='/api/feature-tests'&&r.status()===202){id=(await r.json()).id;submitted++;await fs.writeFile(path.join(out,'fresh-run.json'),JSON.stringify({run_id:id,source_run_id:prior.run_id,project_id:prior.project_id},null,2));console.log('new feature test',id);}});
    await page.goto(base+'/implementation?'+new URLSearchParams({task:prior.intake_id,run:prior.run_id}));
    await page.waitForFunction(()=>[...document.querySelector('#feature-target').options].some(o=>o.value));
    await page.locator('#feature-testing').scrollIntoViewIfNeeded();
    if(!id){
      await page.locator('#feature-target').selectOption(prior.run_id);
      await page.locator('#feature-review-first').uncheck();
      await page.locator('#feature-description').fill('请测试任务管理系统的这些功能：建立多条中文和英文任务，中文标题子串搜索只返回匹配任务，无匹配关键词时返回零条，清空搜索恢复所有任务；page_size=2 时第一页和第二页不能有重复数据，超出末页返回空列表；勾选完成后重新创建 Storage 仍保存完成状态，取消完成后再打开也保存。使用多组独立数据，包括中文、英文和空结果，保留原有新增和删除功能回归。');
      await page.locator('#feature-iterations').fill('2');
      await page.locator('#feature-start').click();
      await page.waitForFunction(()=>document.querySelector('#feature-status').textContent.includes('执行中'),{},{timeout:30000});
      await page.screenshot({path:path.join(out,'generating.png')});
    }else{
      await page.waitForFunction(id=>[...document.querySelector('#feature-history').options].some(o=>o.value===id),id);
      await page.locator('#feature-history').selectOption(id);
    }
    await page.waitForFunction(()=>/测试通过|测试发现问题|执行异常/.test(document.querySelector('#feature-status').textContent),{},{timeout:900000});
    if(!id)throw Error('missing run id');
    const result=await(await page.request.get(base+'/api/runs/'+id)).json();
    assert.equal(result.status,'completed',JSON.stringify({error:result.error,phase:result.phase}));
    assert.ok(result.model_calls.length>=3&&result.model_calls.length<=5);assert.equal(result.summary.accepted,2);
    assert.equal(result.source_run_id,prior.run_id);assert.ok(result.requirements.length>0&&result.test_plan.steps.length>0);
    assert.ok(result.model_calls.every(c=>c.provider!=='replay'));
    checks.push('actual free-text input, fresh Skill calls, per-file steps, two real WCTP rounds');
    await page.locator('#feature-wctp-content').waitFor();
    const count=await page.locator('#feature-wctp-graph [data-step^="step_Feature"]').count();assert.equal(count,result.test_plan.steps.length);
    await page.locator('#feature-wctp-round').selectOption('2');
    await page.locator('#feature-wctp-graph [data-step="step_Feature01"]').click();
    await page.waitForFunction(()=>document.querySelectorAll('#feature-wctp-results .good').length>0);
    await page.screenshot({path:path.join(out,'steps-results.png')});
    checks.push('native graph steps map to real generated files with per-data JUnit results');
    await page.reload();
    await page.waitForFunction(()=>document.querySelector('#feature-status').textContent.includes('测试通过'));
    assert.equal(await page.locator('#feature-history').inputValue(),id);
    assert.ok(await page.locator('#feature-report').isVisible());
    checks.push('reload restores persisted test history and native WCTP receipts');
    await page.setViewportSize({width:390,height:900});await page.locator('#feature-testing').scrollIntoViewIfNeeded();
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    await page.screenshot({path:path.join(out,'narrow.png')});
    assert.deepEqual(errors,[]);assert.equal(submitted,process.env.TESTFIX_FEATURE_REUSE?0:1);
    const evidence={status:'passed',run_id:id,source_run_id:prior.run_id,checks,errors,new_submissions:submitted,
      steps:result.test_plan.steps.length,requirements:result.requirements.length,summary:result.summary};
    await fs.writeFile(path.join(out,'ui-validation.json'),JSON.stringify(evidence,null,2));console.log(JSON.stringify(evidence,null,2));
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
