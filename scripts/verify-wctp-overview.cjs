const {chromium}=require('playwright');
const assert=require('node:assert/strict');

(async()=>{
  const base=process.env.TESTFIX_UI_BASE;
  const task=process.env.TESTFIX_TASK_ID;
  const completed=process.env.TESTFIX_COMPLETED_FEATURE;
  const failed=process.env.TESTFIX_FAILED_FEATURE;
  if(!base||!task||!completed||!failed)throw Error('Set TESTFIX_UI_BASE, TESTFIX_TASK_ID, TESTFIX_COMPLETED_FEATURE and TESTFIX_FAILED_FEATURE');
  const browser=await chromium.launch({channel:'msedge',headless:true,args:['--enable-unsafe-swiftshader']});
  try{
    const page=await browser.newPage({viewport:{width:1500,height:900}}),errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    await page.goto(base+'/implementation?'+new URLSearchParams({task}));
    await page.waitForFunction(id=>[...document.querySelector('#feature-history').options].some(o=>o.value===id),completed);
    await page.locator('#feature-history').selectOption(completed);
    await page.waitForFunction(()=>document.querySelector('#feature-wctp-overview-cards')?.children.length===3);
    const cards=page.locator('#feature-wctp-overview-cards .wctp-overview-card');
    await page.waitForFunction(()=>document.querySelector('#feature-wctp-overview-cards')?.innerText.includes('用例 6/6 通过'));
    assert.match(await cards.nth(1).innerText(),/任务生命周期与搜索/);
    assert.match(await cards.nth(1).innerText(),/新增两条中文和两条英文/);
    assert.match(await cards.nth(1).innerText(),/用例 2\/2 通过 · 符合预期/);
    assert.match(await cards.nth(2).innerText(),/用例 3\/3 通过 · 符合预期/);
    await page.locator('#feature-wctp-content .wctp-overview').screenshot({path:'artifacts/wctp-overview-check.png'});
    await page.locator('#feature-wctp-graph').screenshot({path:'artifacts/wctp-graph-check.png'});
    await cards.nth(2).locator('summary').first().click();
    assert.match(await cards.nth(2).innerText(),/任务完成状态及取消完成状态/);
    await page.locator('#feature-wctp-round').selectOption('2');
    assert.match(await page.locator('#feature-wctp-overview-round').innerText(),/第 2 轮/);
    assert.match(await cards.nth(1).innerText(),/用例 2\/2 通过 · 符合预期/);
    await page.locator('#feature-history').selectOption(failed);
    await page.waitForFunction(()=>document.querySelector('#feature-status').textContent.includes('测试发现问题'));
    await page.waitForFunction(()=>document.querySelectorAll('#feature-wctp-overview-cards .wctp-overview-card')[1]?.innerText.includes('本轮未执行'));
    assert.match(await cards.nth(1).innerText(),/本轮未执行/);
    await page.locator('#feature-wctp-round').selectOption('1');
    await page.waitForFunction(()=>document.querySelector('#feature-wctp-overview-cards')?.innerText.includes('用例 1/2 通过 · 未符合预期'));
    assert.match(await cards.nth(2).innerText(),/本轮未执行/);
    assert.match(await page.locator('#feature-wctp-graph [data-step="step_Feature01"]').textContent(),/用例 1\/2 通过 · 未符合预期/);
    assert.doesNotMatch(await page.locator('#feature-wctp-graph').textContent(),/NormalStep|LoopStep/);
    await page.setViewportSize({width:390,height:900});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    assert.deepEqual(errors,[]);
    console.log('真实历史记录：测试内容、关联功能、轮次、通过、失败和未执行显示正确');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
