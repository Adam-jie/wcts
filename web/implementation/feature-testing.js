import {request,jsonRequest,el} from '/static/intake/details-assistant.js';
import {createWctpTests} from './wctp-tests.js';
const $=id=>document.getElementById(id);
const active=r=>['queued','running'].includes(r?.status);
const status={queued:'排队中',running:'执行中',awaiting_review:'等待确认测试计划',completed:'测试通过',rejected:'测试发现问题',failed:'执行异常',interrupted:'已中断'};
const phase={queued:'准备任务',analysis:'分析功能条件',generation:'生成步骤和测试数据',review:'尚未执行',execution:'WCTP 正在执行',finished:'执行结束'};
export function createFeatureTesting(){
  // Reuse the same native graph/receipt viewer with independent IDs and state.
  const copy=$('wctp-panel').cloneNode(true);
  for(const node of [copy,...copy.querySelectorAll('[id]')])if(node.id)node.id=node.id.replace(/^wctp/,'feature-wctp');
  copy.querySelector('h3').textContent='本次功能测试 · 连线与结果';
  copy.querySelector('.wctp-repeat-row').hidden=true;
  copy.querySelector('.wctp-hint').hidden=true;
  copy.querySelector('.wctp-expectation').textContent='每个功能步骤执行实际生成的测试文件；下表显示各组数据的结果。未运行、失败或异常均保留记录。';
  copy.querySelector('#feature-wctp-empty').textContent='功能分析和测试生成完成后，这里显示实际 WCTP 执行流程。';
  $('feature-wctp-view').append(copy);
  const graph=createWctpTests('feature-wctp');
  let context={},projectId='',run=null,timer=null,epoch=0,fetchId=0,choice=0,historySerial=0,submitting=false,signature='',listSignature='';
  function error(text){$('feature-error').textContent=text||'';$('feature-error').hidden=!text;}
  function controls(){
    const running=submitting||active(run),provider=context.providers?.find(p=>p.id===$('provider').value);
    $('feature-start').disabled=running||context.busy||active(context.run)||!context.project||!provider?.configured||!$('feature-description').value.trim();
    $('feature-start').textContent=running?'正在处理…':$('feature-review-first').checked?'Agent 生成测试步骤':'Agent 生成并运行测试';
    for(const id of ['feature-description','feature-target','feature-mode','feature-iterations','feature-review-first'])$(id).disabled=running;
    $('feature-history').disabled=submitting;
    $('feature-rerun').disabled=running||context.busy||active(context.run);
  }
  function editPlan(value){
    const draft=structuredClone(value),container=$('feature-step-editor'),json=$('feature-plan-json');
    const sync=()=>{json.value=JSON.stringify(draft,null,2);};
    function draw(){
      container.replaceChildren();
      const flow=el('div',undefined,'feature-plan-preview');flow.setAttribute('aria-label','测试步骤顺序预览');
      const names=['原有回归',...draft.steps.map(step=>step.title),'汇总本轮结果'];
      names.forEach((name,index)=>{if(index)flow.append(el('span','→','feature-plan-arrow'));flow.append(el('span',name,'feature-plan-node'));});
      container.append(el('p','步骤顺序预览 · 循环次数由上方设置 · 尚未表示执行结果'),flow);
      draft.steps.forEach((step,index)=>{
        const box=el('section',undefined,'feature-edit-step'),operation=run?.execution_mode==='operations',heading=el('h4',`步骤 ${index+1} · ${operation?step.method+' '+step.path:step.file}`);
        const name=el('input');name.value=step.title;name.setAttribute('aria-label',`步骤 ${index+1} 名称`);
        const expected=el('textarea');expected.rows=2;expected.value=step.expected;expected.setAttribute('aria-label',`步骤 ${index+1} 预期结果`);
        const file=draft.files?.find(item=>item.path===step.file),source=el('textarea');source.rows=8;source.spellcheck=false;source.value=file?.source||'';source.setAttribute('aria-label',`步骤 ${index+1} 测试代码`);
        name.oninput=()=>{step.title=name.value;flow.querySelectorAll('.feature-plan-node')[index+1].textContent=name.value;sync();};expected.oninput=()=>{step.expected=expected.value;sync();};
        source.oninput=()=>{if(file)file.source=source.value;sync();};
        const up=el('button','上移'),down=el('button','下移');up.type=down.type='button';up.disabled=index===0;down.disabled=index===draft.steps.length-1;
        up.onclick=()=>{[draft.steps[index-1],draft.steps[index]]=[draft.steps[index],draft.steps[index-1]];draw();};
        down.onclick=()=>{[draft.steps[index+1],draft.steps[index]]=[draft.steps[index],draft.steps[index+1]];draw();};
        const nameLabel=el('label','步骤名称'),expectedLabel=el('label','预期结果');
        nameLabel.append(name);expectedLabel.append(expected);box.append(heading,nameLabel,expectedLabel);
        if(operation){
          for(const [key,label] of [['method','操作方法'],['path','接口路径'],['query_json','查询参数 JSON'],['body_json','请求正文 JSON'],['expected_status','预期状态码'],['capture_name','保存变量名'],['capture_field','保存响应字段']]){
            const wrapper=el('label',label),input=el('input');input.value=String(step[key]);input.setAttribute('aria-label',`步骤 ${index+1} ${label}`);input.oninput=()=>{step[key]=key==='expected_status'?Number(input.value):input.value;sync();};wrapper.append(input);box.append(wrapper);
          }
          const checksLabel=el('label','响应断言 JSON'),checks=el('textarea');checks.rows=4;checks.value=JSON.stringify(step.checks,null,2);checks.setAttribute('aria-label',`步骤 ${index+1} 响应断言 JSON`);
          checks.oninput=()=>{try{step.checks=JSON.parse(checks.value);checks.setCustomValidity('');sync();}catch{checks.setCustomValidity('断言必须是有效 JSON');}};checksLabel.append(checks);box.append(checksLabel);
        }else{const sourceLabel=el('label','测试代码');sourceLabel.append(source);box.append(sourceLabel);}
        box.append(up,down);container.append(box);
      });
      sync();
    }
    draw();
  }
  function render(value){
    const previous=run?.id;run=value;
    if(value?.id!==previous&&value?.iterations&&!active(value))$('feature-iterations').value=String(value.iterations);
    controls();graph.update(value);
    copy.querySelector('.wctp-expectation').textContent=value?.execution_mode==='operations'?'每个操作节点执行真实本地 HTTP 请求，重启节点重启应用；下表显示该节点的检查结果。':'每个功能步骤执行实际生成的测试文件；下表显示各组数据的结果。未运行、失败或异常均保留记录。';
    $('feature-status').textContent=value?`${status[value.status]||value.status} · ${phase[value.phase]||''} · 模型调用 ${value.model_calls?.length||0} 次 · 检查与回归通过 ${value.summary?.passed||0}/${value.summary?.tested||0} · 完成 ${value.summary?.accepted||0}/${value.iterations} 轮`:'输入功能描述，Agent 会生成可执行步骤和测试。';
    error(value?.error||'');$('feature-report').hidden=!value;$('feature-report').href=value?`/api/runs/${value.id}/report?format=md`:'#';
    const key=JSON.stringify([value?.id,value?.test_plan,value?.events?.filter(e=>e.message)]);
    if(key===signature)return;signature=key;
    $('feature-events').replaceChildren();for(const event of value?.events?.filter(e=>e.message)||[])$('feature-events').append(el('li',event.message));
    const plan=$('feature-plan-content');plan.replaceChildren();$('feature-plan').hidden=!value?.test_plan;
    $('feature-editor').hidden=!value?.test_plan||active(value);
    if(value?.test_plan&&!active(value))editPlan(value.test_plan);
    if(!value?.test_plan)return;
    const conditions=el('ul');for(const item of value.requirements||[])conditions.append(el('li',item.id+'：'+item.behavior+'；预期：'+item.criterion));plan.append(conditions);
    const steps=el('ol');for(const step of value.test_plan.steps)steps.append(el('li',step.title+' · '+(value.execution_mode==='operations'?step.method+' '+step.path:step.file)+' · '+step.expected));plan.append(steps);
    for(const assumption of [...value.analysis_assumptions||[],...value.test_plan.assumptions||[]])plan.append(el('p','测试假设：'+assumption));
    for(const file of value.test_plan.files||[]){const detail=el('details'),pre=el('pre',file.source);detail.append(el('summary',file.path),pre);plan.append(detail);}
  }
  async function history(generation=epoch){
    if(!projectId)return;
    const requestId=++historySerial;
    const data=await request('/api/feature-tests?'+new URLSearchParams({project_id:projectId}));if(generation!==epoch||requestId!==historySerial)return;
    const select=$('feature-history'),key=JSON.stringify(data.runs.map(r=>[r.id,r.status]));
    if(key!==listSignature){listSignature=key;select.replaceChildren();for(const r of data.runs){const o=el('option',new Date(r.created_at).toLocaleString('zh-CN',{hour12:false})+' · '+status[r.status]+' · '+r.description.slice(0,38));o.value=r.id;select.append(o);}if(!data.runs.length){const o=el('option','尚无自动测试记录');o.value='';select.append(o);}}
    if(run&&data.runs.some(r=>r.id===run.id))select.value=run.id;
    return data.runs;
  }
  async function poll(id,generation=epoch){
    clearTimeout(timer);const ticket=++fetchId;
    try{
      const value=await request('/api/runs/'+id);if(generation!==epoch||ticket!==fetchId)return;
      render(value);await history(generation);if(generation!==epoch||ticket!==fetchId)return;
      if(active(value))timer=setTimeout(()=>poll(id,generation),1600);
    }catch(e){if(generation===epoch&&ticket===fetchId){error('进度读取中断，正在重试：'+e.message);timer=setTimeout(()=>poll(id,generation),3000);}}
  }
  $('feature-start').onclick=async()=>{
    if($('feature-start').disabled)return;
    const generation=epoch,selectedProject=projectId;
    choice++;fetchId++;clearTimeout(timer);
    submitting=true;controls();error('');
    try{
      const provider=context.providers.find(p=>p.id===$('provider').value);
      const result=await request('/api/feature-tests',jsonRequest('POST',{project_id:selectedProject,
        description:$('feature-description').value,source_run_id:$('feature-target').value||null,
        provider:provider.id,iterations:Number($('feature-iterations').value),review_before_execution:$('feature-review-first').checked,execution_mode:$('feature-mode').value}));
      if(generation!==epoch)return;
      render({...result,kind:'feature_test',phase:'queued',iterations:Number($('feature-iterations').value)});
      await poll(result.id,generation);
    }catch(e){if(generation===epoch)error(e.message);}
    finally{if(generation===epoch){submitting=false;controls();}}
  };
  $('feature-rerun').onclick=async()=>{
    if($('feature-rerun').disabled||!run?.test_plan)return;
    const invalid=$('feature-step-editor').querySelector(':invalid');if(invalid){invalid.reportValidity();return;}
    let plan;try{plan=JSON.parse($('feature-plan-json').value);}catch{error('测试计划不是有效的 JSON，请检查后重试。');return;}
    const generation=epoch,parent=run.id;choice++;fetchId++;clearTimeout(timer);submitting=true;controls();error('');
    try{
      const result=await request('/api/feature-tests/'+parent+'/edited',jsonRequest('POST',{plan,iterations:Number($('feature-iterations').value)}));
      if(generation!==epoch)return;
      render({...result,kind:'feature_test',phase:'queued',iterations:Number($('feature-iterations').value)});
      await poll(result.id,generation);
    }catch(e){if(generation===epoch)error(e.message);}
    finally{if(generation===epoch){submitting=false;controls();}}
  };
  $('feature-history').onchange=()=>{choice++;fetchId++;clearTimeout(timer);if($('feature-history').value)poll($('feature-history').value);};
  $('feature-refresh').onclick=async()=>{const generation=epoch,selection=++choice;try{const rows=await history(generation);if(generation!==epoch||selection!==choice)return;if(run)await poll(run.id,generation);else if(rows?.length)await poll(rows[0].id,generation);}catch(e){if(generation===epoch&&selection===choice)error(e.message);}};
  $('feature-description').oninput=controls;
  $('feature-review-first').onchange=controls;
  window.addEventListener('pagehide',()=>{epoch++;clearTimeout(timer);},{once:true});
  return {update(value){
    context=value;
    if(value.project?.id!==projectId){
      projectId=value.project?.id;epoch++;fetchId++;submitting=false;clearTimeout(timer);timer=null;run=null;signature='';listSignature='';render(null);
      const supported=value.project?.adapter==='python_application'&&value.project?.public_runtime?.package==='taskboard';
      $('feature-mode').querySelector('option[value="operations"]').disabled=!supported;$('feature-mode').value=supported?'operations':'pytest';
      $('feature-target').replaceChildren(new Option('原始工程',''));
      if(projectId){const generation=epoch,selection=choice;history(generation).then(rows=>{if(generation===epoch&&selection===choice&&!run&&!submitting&&rows?.length)poll(rows[0].id,generation);}).catch(e=>{if(generation===epoch&&selection===choice)error(e.message);});}
    }
    const target=$('feature-target'),accepted=value.run;
    if(accepted?.status==='completed'&&accepted.summary?.accepted===1&&accepted.project_id===projectId&&!target.querySelector(`option[value="${accepted.id}"]`)){
      target.append(new Option('已补全版本 · '+accepted.id.slice(0,8),accepted.id));if(!active(run))target.value=accepted.id;
    }
    controls();
  }};
}
