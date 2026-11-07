const {createRequire}=require('node:module');
const {chromium}=process.env.TESTFIX_PLAYWRIGHT
  ?require(process.env.TESTFIX_PLAYWRIGHT)
  :createRequire(require.resolve('../frontend/package.json'))('playwright');
const fs=require('node:fs/promises'),assert=require('node:assert/strict');
const path=require('node:path');
const base=process.env.TESTFIX_UI_BASE||'http://127.0.0.1:2890';
(async()=>{
  const runId=process.env.TESTFIX_UI_RUN_ID||
    JSON.parse(await fs.readFile('artifacts/wctp-product-check/fresh-run.json','utf8')).run_id;
  const evidence=process.env.TESTFIX_UI_EVIDENCE||'artifacts/wctp-product-check/ui';
  const visible=process.env.TESTFIX_UI_VISIBLE==='1';
  const caseName=process.env.TESTFIX_UI_CASE||'软件自动化测试';
  await fs.mkdir(evidence,{recursive:true});
  const browser=await chromium.launch({...(process.platform==='win32'?{channel:'msedge'}:{}),headless:!visible,slowMo:visible?500:0});
  const checks=[],errors=[],steps=[];
  let preview,currentStep,page;
  try{
    const viewport={width:1280,height:900};
    page=await browser.newPage({
      viewport,
      ...(visible?{recordVideo:{dir:evidence,size:viewport}}:{}),
    });
    page.on('pageerror',e=>errors.push(e.message));
    const response=await page.request.post(base+'/api/project-runs/'+runId+'/preview');
    assert.equal(response.status(),200,await response.text());
    preview=await response.json();
    assert.match(preview.candidate_sha256,/^[a-f0-9]{64}$/,'预览必须返回已验收的源码版本');
    async function mark(label,status){
      if (!visible) return;
      await page.evaluate(({caseName,label,status})=>{
        let badge=document.querySelector('#testfix-browser-step');
        if (!badge){
          badge=document.createElement('aside');
          badge.id='testfix-browser-step';
          badge.setAttribute('role','status');
          badge.style.cssText='position:fixed;right:18px;bottom:18px;z-index:9999;max-width:420px;padding:13px 18px;background:#0b2032;color:#c8f1ff;border:1px solid #72c5df;border-radius:10px;font:15px system-ui;box-shadow:0 6px 24px #0007;pointer-events:none';
          document.body.append(badge);
        }
        badge.textContent=caseName+' · '+status+' · '+label;
      },{caseName,label,status});
    }
    async function step(label,action){
      currentStep={label,status:'running'};
      steps.push(currentStep);
      console.log(caseName+' | '+label);
      await mark(label,'执行中');
      await action();
      currentStep.status='passed';
      await mark(label,'通过');
      if (visible){
        const file=String(steps.length).padStart(2,'0')+'.png';
        await page.screenshot({path:path.join(evidence,file)});
        currentStep.screenshot=file;
      }
    }
    await page.goto(preview.url);
    await step('启动软件并加载任务清单',async()=>{
      await page.locator('#tasks').getByRole('checkbox').first().waitFor();
    });
    const title='界面完整实测_'+Date.now();
    const checkbox=()=>page.getByRole('checkbox',{name:'完成：'+title,exact:true});
    await step('填写中文标题并新增任务',async()=>{
      await page.locator('#new-title').fill(title);
      await page.locator('#create-form button').click();
      await checkbox().waitFor();
    });
    await step('勾选完成，刷新后核对保存状态',async()=>{
      const saved=page.waitForResponse(res=>res.request().method()==='PATCH'&&res.ok());
      await checkbox().check();
      await saved;
      await page.reload();
      await checkbox().waitFor();
      assert.equal(await checkbox().isChecked(),true);
    });
    await step('取消完成，刷新后核对恢复状态',async()=>{
      const saved=page.waitForResponse(res=>res.request().method()==='PATCH'&&res.ok());
      await checkbox().uncheck();
      await saved;
      await page.reload();
      await checkbox().waitFor();
      assert.equal(await checkbox().isChecked(),false);
    });
    checks.push('create through the real frontend; complete and uncomplete survive reload');
    const search=async value=>{
      await page.locator('#search').fill(value);
      await page.locator('#search-form button').click();
    };
    await step('搜索中文任务并核对唯一结果',async()=>{
      await search(title);
      await page.locator('#total').filter({hasText:/^1 项$/}).waitFor();
      assert.deepEqual(await page.locator('#tasks label span').allTextContents(),[title]);
    });
    await step('搜索不存在的关键词并检查空状态',async()=>{
      await search('no-match-'+title);
      await page.locator('#tasks .empty').waitFor();
      assert.equal(await page.locator('#total').textContent(),'0 项');
    });
    await step('清空搜索，翻页并检查列表没有重复',async()=>{
      await search('');
      await checkbox().waitFor();
      const first=await page.locator('#tasks label span').allTextContents();
      await page.locator('#next').click();
      await page.locator('#page-label').filter({hasText:'第 2 页'}).waitFor();
      const second=await page.locator('#tasks label span').allTextContents();
      assert.ok(second.length>0);
      assert.equal(second.filter(t=>first.includes(t)).length,0);
    });
    await page.locator('#previous').click();
    await checkbox().waitFor();
    checks.push('literal Chinese search, no-match state, clear search, distinct next/previous pages');
    await page.screenshot({path:path.join(evidence,'taskboard-running.png')});
    await step('删除本次测试创建的任务并确认移除',async()=>{
      await page.getByRole('button',{name:'删除：'+title,exact:true}).click();
      await checkbox().waitFor({state:'detached'});
    });
    checks.push('delete only the fixture task created by this test');
    assert.equal(await page.locator('#error').isVisible(),false);assert.deepEqual(errors,[]);
    const result={status:'passed',run_id:runId,url:preview.url,candidate_sha256:preview.candidate_sha256,visible,checks,steps,errors};
    await fs.writeFile(path.join(evidence,'taskboard-ui-validation.json'),JSON.stringify(result,null,2));
    console.log(JSON.stringify(result,null,2));
  }catch(error){
    if (currentStep?.status==='running') currentStep.status='failed';
    await fs.writeFile(path.join(evidence,'taskboard-ui-validation.json'),JSON.stringify({
      status:'failed',run_id:runId,candidate_sha256:preview?.candidate_sha256,visible,checks,steps,errors,error:String(error),
    },null,2));
    throw error;
  }finally{
    try{
      const video=page?.video();
      if(video){
        await page.close();
        await fs.rename(await video.path(),path.join(evidence,'browser-run.webm'));
      }
    }finally{await browser.close();}
  }
})().catch(e=>{console.error(e);process.exitCode=1;});
