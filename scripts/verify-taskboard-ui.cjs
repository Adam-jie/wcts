const {chromium}=require(process.env.TESTFIX_PLAYWRIGHT||'playwright');
const fs=require('node:fs/promises'),assert=require('node:assert/strict');
const base=process.env.TESTFIX_UI_BASE||'http://127.0.0.1:2890';
(async()=>{
  const pointer=JSON.parse(await fs.readFile('artifacts/wctp-product-check/fresh-run.json','utf8'));
  const browser=await chromium.launch({channel:'msedge',headless:true});const checks=[],errors=[];
  try{
    const page=await browser.newPage({viewport:{width:1280,height:900}});page.on('pageerror',e=>errors.push(e.message));
    const response=await page.request.post(base+'/api/project-runs/'+pointer.run_id+'/preview');assert.equal(response.status(),200);
    const preview=await response.json();await page.goto(preview.url);await page.locator('#tasks li input').first().waitFor();
    const title='界面完整实测_'+Date.now();
    await page.locator('#new-title').fill(title);await page.locator('#create-form button').click();
    const checkbox=()=>page.getByRole('checkbox',{name:'完成：'+title,exact:true});
    await checkbox().waitFor();await checkbox().check();await page.waitForFunction(t=>[...document.querySelectorAll('#tasks label')].some(l=>l.textContent===t&&l.querySelector('input').checked),title);
    await page.reload();await checkbox().waitFor();assert.equal(await checkbox().isChecked(),true);
    await checkbox().uncheck();await page.waitForFunction(t=>[...document.querySelectorAll('#tasks label')].some(l=>l.textContent===t&&!l.querySelector('input').checked),title);
    await page.reload();await checkbox().waitFor();assert.equal(await checkbox().isChecked(),false);
    checks.push('create through the real frontend; complete and uncomplete survive reload');
    await page.locator('#search').fill(title);await page.locator('#search-form button').click();await page.locator('#total').filter({hasText:'1 项'}).waitFor();
    assert.deepEqual(await page.locator('#tasks label span').allTextContents(),[title]);
    await page.locator('#search').fill('no-match-'+title);await page.locator('#search-form button').click();await page.locator('#tasks .empty').waitFor();
    assert.equal(await page.locator('#total').textContent(),'0 项');
    await page.locator('#search').fill('');await page.locator('#search-form button').click();await checkbox().waitFor();
    const first=await page.locator('#tasks label span').allTextContents();await page.locator('#next').click();await page.locator('#page-label').filter({hasText:'第 2 页'}).waitFor();
    const second=await page.locator('#tasks label span').allTextContents();assert.equal(second.filter(t=>first.includes(t)).length,0);
    await page.locator('#previous').click();await checkbox().waitFor();
    checks.push('literal Chinese search, no-match state, clear search, distinct next/previous pages');
    await page.screenshot({path:'artifacts/wctp-product-check/ui/taskboard-running.png'});
    await page.getByRole('button',{name:'删除：'+title,exact:true}).click();await checkbox().waitFor({state:'detached'});
    checks.push('delete only the fixture task created by this test');
    assert.equal(await page.locator('#error').isVisible(),false);assert.deepEqual(errors,[]);
    const result={status:'passed',run_id:pointer.run_id,url:preview.url,candidate_sha256:preview.candidate_sha256,checks,errors};
    await fs.writeFile('artifacts/wctp-product-check/ui/taskboard-ui-validation.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result,null,2));
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
