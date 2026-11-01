const {chromium} = require('playwright');
const assert = require('node:assert/strict');

(async () => {
  const {TESTFIX_UI_BASE: base, TESTFIX_TASK_ID: task, TESTFIX_OPERATION_REVIEW: review,
    TESTFIX_OPERATION_RUN: run} = process.env;
  if (!base || !task || !review || !run) throw Error('Set UI base, task, review and operation run IDs');
  const browser = await chromium.launch({channel: 'msedge', headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1500, height: 900}}), errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(base + '/implementation?' + new URLSearchParams({task}));
    await page.waitForFunction(id => [...document.querySelector('#feature-history').options].some(o => o.value === id), review);
    await page.locator('#feature-history').selectOption(review);
    await page.waitForFunction(() => document.querySelector('#feature-status').textContent.includes('等待确认测试计划'));
    await page.locator('#feature-editor summary').first().click();
    assert.equal(await page.locator('.feature-edit-step').count(), 7);
    assert.match(await page.locator('.feature-plan-preview').innerText(), /新增.*读取.*已完成/s);
    assert.equal(await page.locator('[aria-label="步骤 1 操作方法"]').inputValue(), 'POST');
    assert.match(await page.locator('[aria-label="步骤 1 响应断言 JSON"]').inputValue(), /title/);
    await page.locator('#feature-history').selectOption(run);
    await page.waitForFunction(() => document.querySelector('#feature-status').textContent.includes('测试通过'));
    await page.waitForFunction(() => document.querySelectorAll('#feature-wctp-graph [data-step^="step_Operation"]').length === 7);
    assert.match(await page.locator('#feature-wctp-graph').textContent(), /新增逐步骤验证任务/);
    await page.locator('#feature-wctp-graph [data-step="step_Operation01"]').click();
    assert.match(await page.locator('#feature-wctp-step-io').textContent(), /POST/);
    assert.match(await page.locator('#feature-wctp-step-io').textContent(), /逐步骤验证任务/);
    assert.match(await page.locator('#feature-wctp-step-io').textContent(), /201/);
    await page.locator('#feature-wctp-round').selectOption('2');
    assert.match(await page.locator('#feature-wctp-overview-round').textContent(), /第 2 轮/);
    assert.deepEqual(errors, []);
    await page.locator('#feature-wctp-content').screenshot({path: 'artifacts/operation-flow-ui.png'});
    console.log('真实模型生成计划、可编辑步骤、WCTP 连线、请求响应与两轮记录显示正常');
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
