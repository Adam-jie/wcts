import {request,jsonRequest,el} from '/static/intake/details-assistant.js';
const $=id=>document.getElementById(id),NS='http://www.w3.org/2000/svg';
const STATUS={running:'执行中',passed:'通过',failed:'失败',queued:'排队中',completed:'重测完成',interrupted:'已中断'};
const svg=(tag,attrs={})=>{const n=document.createElementNS(NS,tag);for(const [k,v]of Object.entries(attrs))n.setAttribute(k,v);return n;};
export function createWctpTests(prefix='wctp'){
  const $=id=>document.getElementById(id.replace(/^wctp/,prefix));
  let run=null,key='',record=null,selected='',revision='',graphSignature='',overviewSignature='',serial=0,timer=null,repeatId='',busy=false,followingCase=true,designSignature='',epoch=0,watching=false;
  const owns=(source,campaign,generation)=>run?.id===source&&repeatId===campaign&&epoch===generation;
  function error(message){$('wctp-error').textContent=message||'';$('wctp-error').hidden=!message;}
  function controls(){ $('wctp-repeat').disabled=busy||run?.kind!=='autonomous_project'||run?.status!=='completed';$('wctp-iterations').disabled=busy; }
  function nodes(){
    if(!record)return;
    const signature=JSON.stringify(record.case.inline_steps);if(signature===graphSignature)return;graphSignature=signature;
    const graph=$('wctp-graph');graph.replaceChildren();
    const defs=svg('defs'),marker=svg('marker',{id:prefix+'-arrow',viewBox:'0 0 10 10',refX:9,refY:5,markerWidth:6,markerHeight:6,orient:'auto-start-reverse'});marker.append(svg('path',{d:'M 0 0 L 10 5 L 0 10 z',fill:'#739bac'}));defs.append(marker);graph.append(defs);
    const actions=record.case.inline_steps.filter(s=>s.output),height=Math.max(460,actions.length*100+120);
    graph.setAttribute('viewBox',`0 0 900 ${height}`);
    const positions={step_Prepare:[30,35],step_Loop:[30,145],step_Dispatch:[30,255],step_Join:[650,145],step_End:[650,310]};
    actions.forEach((step,i)=>positions[step.step_id]=[335,35+i*100]);
    for(const line of record.case.graph.split('\n')){
      const match=line.trim().match(/^(\w+) -->\s*(?:\|(True|False)\|\s*)?(\w+)$/);if(!match)continue;
      const [x1,y1]=positions[match[1]]||[],[x2,y2]=positions[match[3]]||[];if(x1===undefined||x2===undefined)continue;
      const back=match[1]==='step_Join',vertical=x1===x2;
      const d=back?`M ${x1+110} ${y1+62} C ${x1+110} ${height-20}, 12 ${height-20}, 12 ${y2+31} L ${x2} ${y2+31}`:
        match[2]==='False'?`M ${x1+220} ${y1+31} C 295 ${y1+31}, 285 ${height-50}, 520 ${height-50} S ${x2-25} ${y2+31}, ${x2} ${y2+31}`:
        vertical?`M ${x1+110} ${y1+62} L ${x2+110} ${y2}`:
        `M ${x1+220} ${y1+31} C ${x1+270} ${y1+31}, ${x2-50} ${y2+31}, ${x2} ${y2+31}`;
      graph.append(svg('path',{d,class:'wctp-edge'+(back?' loop':''),'marker-end':`url(#${prefix}-arrow)`}));
      if(match[2]){const text=svg('text',{x:x1+(match[2]==='True'?120:230),y:y1+(match[2]==='True'?85:55),class:'wctp-branch'});text.textContent=match[2]==='True'?'继续循环':'循环结束';graph.append(text);}
    }
    for(const step of record.case.inline_steps){
      const [x,y]=positions[step.step_id];const group=svg('g',{transform:`translate(${x} ${y})`,class:'wctp-node',role:'button',tabindex:0,'data-step':step.step_id,'aria-label':step.step_name});
      group.append(svg('rect',{width:220,height:62,rx:7}));const title=svg('text',{x:12,y:21}),type=svg('text',{x:12,y:39,class:'wctp-type'}),state=svg('text',{x:12,y:54,class:'wctp-node-result'});
      // Long model labels must not expand the SVG hit target beyond its card.
      let width=0,label='';for(const char of step.step_name){width+=char.codePointAt(0)>255?2:1;if(width>27){label+='…';break;}label+=char;}
      const descriptions={step_Prepare:'准备工程和测试输入',step_Loop:'按设定轮数重复执行',step_Dispatch:'开始本轮测试',step_Join:'收集本轮执行结果',step_End:'结束测试流程'};
      title.textContent=label;type.textContent=(step.expected_result||descriptions[step.step_id]||'执行测试步骤').slice(0,19)+(step.expected_result?.length>19?'…':'');
      group.append(title,type,state);group.onclick=()=>{selected=step.step_id;paint();};group.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();selected=step.step_id;paint();}};graph.append(group);
    }
  }
  function dataRows(value,prefix=''){
    if(!value||typeof value!=='object')return [];
    const rows=[];
    for(const c of value.cases||[])rows.push([prefix+c.id.replace(/\\u([a-fA-F0-9]{4})/g,(_,code)=>String.fromCharCode(parseInt(code,16))),c.status,c.message||c.error||'']);
    if(value.successful===false)for(const field of ['parse_error','binding_error'])if(value[field])rows.push([prefix+'执行环境 / '+field,'error',String(value[field])]);
    if(value.timeout)rows.push([prefix+'执行超时','error','执行超过时限，未计为通过']);
    for(const c of value.checks||[])rows.push([prefix+c.name,c.passed?'passed':'failure','']);
    const titles={regression:'原有回归',generated:'生成测试',baseline:'原版对照',acceptance:'修改版验收',delivery:'交付检查',http:'HTTP',cli:'CLI'};
    for(const field of Object.keys(titles))if(value[field])rows.push(...dataRows(value[field],prefix+titles[field]+' / '));
    return rows;
  }
  function overview(iteration){
    const results=record.results||[],actions=record.case.inline_steps.filter(step=>step.output);
    const signature=JSON.stringify([run?.id,key,iteration,selected,actions,results,record.status,record.events,run?.requirements,run?.test_plan?.coverage]);
    if(signature===overviewSignature)return;overviewSignature=signature;
    $('wctp-overview-round').textContent=`第 ${iteration} 轮 / 共 ${record.iterations} 轮`;
    const container=$('wctp-overview-cards');container.replaceChildren();
    for(const [index,step] of actions.entries()){
      const result=results.filter(item=>item.step_id===step.step_id&&item.iteration===iteration).at(-1);
      const executing=record.status==='running'&&record.events.some(e=>e.step_id===step.step_id&&e.event==='started'&&e.iteration===iteration)&&!result;
      const values=Object.values(result?.outputs||{}),rows=values.flatMap(value=>dataRows(value));
      const state=result?(result.status?'passed':'failed'):executing?'running':'pending';
      const stateLabel=result?(rows.length?`${run?.execution_mode==='operations'?'检查':'用例'} ${rows.filter(row=>row[1]==='passed').length}/${rows.length} 通过 · ${result.status?'符合预期':'未符合预期'}`:result.status?'已执行':'执行失败'):executing?'执行中':'本轮未执行';
      const card=el('article',undefined,'wctp-overview-card');card.dataset.state=state;card.dataset.selected=String(step.step_id===selected);
      const heading=el('div',undefined,'wctp-card-heading'),title=el('strong',step.step_name),badge=el('span',stateLabel,'wctp-card-state');heading.append(title,badge);
      card.append(heading,el('p',step.expected_result||'查看该步骤的实际执行记录。','wctp-card-expected'));
      const operationIndex=step.step_id.match(/^step_Operation(\d+)$/),featureIndex=step.step_id.match(/^step_Feature(\d+)$/);
      const plan=run?.test_plan?.steps?.[(operationIndex||featureIndex)?Number((operationIndex||featureIndex)[1])-1:index-(actions[0]?.step_id==='step_Regression'?1:0)];
      const covered=operationIndex?(plan?.requirements||[]):(run?.test_plan?.coverage||[]).filter(item=>plan&&item.tests.some(test=>test.startsWith(plan.file+'::'))).map(item=>item.requirement_id);
      const requirements=(run?.requirements||[]).filter(item=>covered.includes(item.id));
      if(requirements.length){const details=el('details'),list=el('ul');details.append(el('summary',`查看关联功能（${requirements.length} 项）`));for(const item of requirements)list.append(el('li',item.behavior+'；预期：'+item.criterion));details.append(list);card.append(details);}
      if(rows.length){const details=el('details'),list=el('ul');details.append(el('summary',`查看实际用例（${rows.length} 项）`));for(const [name,status,message]of rows){const item=el('li',`${({passed:'通过',failure:'失败',error:'异常',skipped:'跳过'})[status]||status} · ${name}${message?' · '+message:''}`);list.append(item);}details.append(list);card.append(details);}
      const select=el('button','查看节点回执');select.type='button';select.onclick=()=>{selected=step.step_id;paint();$('wctp-step-title').scrollIntoView({behavior:'smooth',block:'center'});};card.append(select);container.append(card);
    }
  }
  function paint(){
    if(!record)return;nodes();
    const results=record.results||[],step=record.case.inline_steps.find(s=>s.step_id===selected)||record.case.inline_steps[0];selected=step.step_id;
    const iteration=Number($('wctp-round').value||1),matching=results.filter(r=>r.step_id===selected&&(r.iteration===iteration||selected==='step_Prepare'||selected==='step_End'));
    const latest=matching.at(-1),started=record.status==='running'&&record.events.some(e=>e.step_id===selected&&e.event==='started'&&e.iteration===iteration);
    for(const group of $('wctp-graph').querySelectorAll('[data-step]')){
      const result=results.filter(r=>r.step_id===group.dataset.step&&(r.iteration===iteration||group.dataset.step==='step_Prepare'||group.dataset.step==='step_End')).at(-1);
      const executing=record.status==='running'&&record.events.some(e=>e.step_id===group.dataset.step&&e.event==='started'&&e.iteration===iteration)&&!result;
      group.dataset.state=result?(result.status||result.control?'passed':'failed'):executing?'running':'pending';group.setAttribute('aria-pressed',String(group.dataset.step===selected));
      const rows=Object.values(result?.outputs||{}).flatMap(value=>dataRows(value));
      group.querySelector('.wctp-node-result').textContent=result?(rows.length?`${run?.execution_mode==='operations'?'检查':'用例'} ${rows.filter(row=>row[1]==='passed').length}/${rows.length} 通过 · ${result.status?'符合预期':'未符合预期'}`:result.control?'流程控制':result.status?'已执行':'未通过'):executing?'执行中':'本轮未执行';
    }
    overview(iteration);
    $('wctp-state').textContent=`${STATUS[record.status]||record.status} · 已完成 ${record.completed_iterations}/${record.iterations} 轮`;
    $('wctp-step-title').textContent=step.step_name;
    const kind=step.step_type==='LoopStep'?'循环控制':step.step_type==='EndStep'?'结束节点':step.output?'测试步骤':'流程节点';
    $('wctp-step-meta').textContent=kind+(step.expected_result?' · 预期：'+step.expected_result:'')+(latest?` · ${latest.control?'流程控制':latest.status?'符合预期':'未满足预期'} · ${Number(latest.duration).toFixed(3)} 秒`:started?' · 执行中':' · 本轮尚未执行');
    $('wctp-step-error').textContent=latest?.error||'';
    const evidence=Object.values(latest?.outputs||{}).find(value=>value&&typeof value==='object'&&(value.request||value.actual));
    $('wctp-step-io').hidden=!evidence;$('wctp-step-io').textContent=evidence?JSON.stringify({输入:evidence.request||{},实际输出:evidence.actual||{},变量:evidence.variables||{}},null,2):'';
    const rows=Object.values(latest?.outputs||{}).flatMap(value=>dataRows(value));
    const signature=JSON.stringify([key,selected,iteration,latest,rows]);if(signature===revision)return;revision=signature;
    const body=$('wctp-results');body.replaceChildren();
    for(const [name,status,message]of rows){const row=el('tr'),value=el('td',({passed:'通过',failure:'失败',error:'异常',skipped:'跳过'})[status]||status);value.className=status==='passed'?'good':'bad';row.append(el('td',name),value,el('td',message));body.append(row);}
    if(!rows.length){const row=el('tr'),cell=el('td',latest?'流程节点已记录；点击测试动作查看逐项结果。':'等待本轮节点执行。');cell.colSpan=3;row.append(cell);body.append(row);}
  }
  async function refresh(value=run){
    run=value;controls();const version=++serial;
    const task=run?.tasks?.[0],design=JSON.stringify([run?.id,task?.test_steps,task?.requirement_coverage]);
    if(design!==designSignature){designSignature=design;const steps=$('wctp-design-steps');steps.replaceChildren();for(const step of task?.test_steps||[])steps.append(el('li',step));if(!steps.children.length)steps.append(el('li','等待本次模型生成并冻结测试。'));$('wctp-coverage').textContent=(task?.requirement_coverage||[]).map(c=>c.requirement_id+' → '+c.tests.join('、')).join('\n');}
    if(!run){record=null;$('wctp-empty').hidden=false;$('wctp-content').hidden=true;$('wctp-cases').replaceChildren();$('wctp-state').textContent='尚无 WCTP 用例';$('wctp-yaml').hidden=true;return;}
    try{
      const listing=await request(`/api/project-runs/${run.id}/wctp-tests`);if(version!==serial)return;
      error('');
      if(listing.active_repeat&&(!repeatId||repeatId===listing.active_repeat.id&&!watching&&!timer)){repeatId=listing.active_repeat.id;busy=true;controls();const source=run.id,campaign=repeatId,generation=epoch;if(!watching&&!timer)timer=setTimeout(()=>watch(source,campaign,generation),0);}
      if(!watching&&listing.latest_repeat){const latest=listing.latest_repeat;$('wctp-repeat-status').textContent=`重测 ${latest.id.slice(0,8)} · ${STATUS[latest.status]||latest.status}${latest.error?' · '+latest.error:''}`;}
      const choices=$('wctp-cases'),prior=key;const listKey=JSON.stringify(listing.cases.map(c=>[c.id,c.status,c.completed_iterations]));
      if(choices.dataset.signature!==listKey){choices.dataset.signature=listKey;choices.replaceChildren();for(const c of listing.cases){const option=el('option',`${c.title} · ${STATUS[c.status]||c.status} · ${c.id.split(':')[0].slice(0,8)}`);option.value=c.id;choices.append(option);}}
      if(!listing.cases.length){$('wctp-empty').hidden=false;$('wctp-content').hidden=true;$('wctp-yaml').hidden=true;$('wctp-state').textContent='尚无 WCTP 用例';return;}
      $('wctp-empty').hidden=true;$('wctp-content').hidden=false;$('wctp-yaml').hidden=false;
      key=!followingCase&&listing.cases.some(c=>c.id===prior)?prior:listing.cases.at(-1).id;
      if(repeatId&&followingCase){const main=listing.cases.find(c=>c.id===repeatId+':repeat');if(main)key=main.id;}
      choices.value=key;
      const next=await request(`/api/project-runs/${run.id}/wctp-tests/case?`+new URLSearchParams({key}));if(version!==serial)return;
      if(key!==prior){selected='';graphSignature='';overviewSignature='';revision='';}
      record=next;const rounds=$('wctp-round'),previous=rounds.value;
      if(rounds.options.length!==record.iterations){rounds.replaceChildren();for(let i=1;i<=record.iterations;i++){const o=el('option',`第 ${i} 轮`);o.value=i;rounds.append(o);}rounds.value=String(Math.min(record.iterations,Math.max(1,Number(previous)||1)));}
      $('wctp-yaml').href=`/api/project-runs/${run.id}/wctp-tests/yaml?`+new URLSearchParams({key});
      error('');paint();
    }catch(e){if(version===serial)error('测试记录读取失败：'+e.message);}
  }
  function follow(value){followingCase=value;$('wctp-follow').setAttribute('aria-pressed',String(value));}
  $('wctp-cases').onchange=()=>{key=$('wctp-cases').value;follow(false);selected='';graphSignature='';revision='';refresh();};
  $('wctp-follow').onclick=()=>{follow(true);refresh();};
  $('wctp-round').onchange=paint;$('wctp-refresh').onclick=()=>refresh();
  async function watch(source=run?.id,campaignId=repeatId,generation=epoch){
    if(!campaignId||!owns(source,campaignId,generation))return;
    clearTimeout(timer);timer=null;watching=true;
    try{
      const campaign=await request('/api/runs/'+campaignId);if(!owns(source,campaignId,generation))return;
      await refresh();if(!owns(source,campaignId,generation))return;
      busy=['running','queued'].includes(campaign.status);$('wctp-repeat-status').textContent=`重测 ${campaignId.slice(0,8)} · ${STATUS[campaign.status]||campaign.status}${campaign.error?' · '+campaign.error:''}`;
      controls();if(busy)timer=setTimeout(()=>watch(source,campaignId,generation),1600);
    }
    catch(e){if(owns(source,campaignId,generation)){error('连接中断，保留测试记录，正在重新读取：'+e.message);busy=true;controls();timer=setTimeout(()=>watch(source,campaignId,generation),3000);}}
    finally{if(owns(source,campaignId,generation))watching=false;}
  }
  $('wctp-repeat').onclick=async()=>{
    if($('wctp-repeat').disabled)return;
    const source=run.id,generation=epoch;busy=true;controls();error('');
    try{const result=await request(`/api/project-runs/${source}/wctp-tests`,jsonRequest('POST',{iterations:Number($('wctp-iterations').value)}));if(run?.id!==source||epoch!==generation)return;repeatId=result.id;follow(true);await watch(source,repeatId,generation);}
    catch(e){if(run?.id===source&&epoch===generation){error(e.message);busy=false;controls();}}
  };
  window.addEventListener('pagehide',()=>clearTimeout(timer),{once:true});
  return {update(value){if(run?.id!==value?.id){epoch++;key='';selected='';record=null;revision='';graphSignature='';repeatId='';clearTimeout(timer);timer=null;watching=false;busy=false;follow(true);$('wctp-content').hidden=true;$('wctp-repeat-status').textContent='';}refresh(value);}};
}
