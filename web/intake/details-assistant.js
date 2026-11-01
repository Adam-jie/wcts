export const detailFields=['title','description','acceptance','environment'];
export const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
export async function request(url,options={}){
  const response=await fetch(url,{cache:'no-store',...options}),data=await response.json();
  if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:Array.isArray(data.detail)?data.detail.map(x=>x.msg).join('；'):'请求未完成，请重试');
  return data;
}
export const jsonRequest=(method,body)=>({method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});

export function detailAssistant({mount,getSnapshot,onApply,onBusy=()=>{}}){
  let applied=null,working=false,serial=0;
  mount.classList.add('detail-assistant');
  const header=el('div',undefined,'assistant-header'),label=el('div','详细资料'),choices=el('div',undefined,'assistant-choices');
  label.append(el('small','你填写，或让 AI 帮你整理'));
  const manual=el('button','自己填写'),ai=el('button','✧ AI 生成');
  for(const button of [manual,ai]){button.type='button';button.setAttribute('aria-pressed','false');choices.append(button);}
  header.append(label,choices);
  const panel=el('div',undefined,'assistant-panel');panel.hidden=true;
  const promptLabel=el('label','告诉 AI 想补充什么（一句话即可）', 'field'),prompt=el('textarea');prompt.rows=2;prompt.maxLength=6000;prompt.placeholder='例如：根据我的描述整理功能需求，补充多数据、循环测试和回归验收标准';promptLabel.append(prompt);
  const tools=el('div',undefined,'assistant-tools'),provider=el('select');provider.setAttribute('aria-label','AI 模型服务');
  const generate=el('button','生成详细资料','secondary');generate.type='button';
  const message=el('p','模型根据当前描述和材料生成草稿，采用前可检查。','field-help');message.setAttribute('role','status');message.setAttribute('aria-live','polite');
  const preview=el('section',undefined,'assistant-preview');preview.hidden=true;
  tools.append(provider,generate);panel.append(promptLabel,tools,message,preview);mount.append(header,panel);
  function choose(value){manual.setAttribute('aria-pressed',String(value==='manual'));ai.setAttribute('aria-pressed',String(value==='ai'));panel.hidden=value!=='ai';if(value==='manual')applied=null;}
  choose('manual');manual.onclick=()=>choose('manual');ai.onclick=()=>choose('ai');
  async function models(){
    try{const result=await request('/api/intake-models');provider.replaceChildren();
      for(const model of result.providers){const option=el('option',model.label+(model.configured?'':' · 未配置'));option.value=model.id;option.disabled=!model.configured;provider.append(option);}
      const configured=[...provider.options].find(x=>!x.disabled);if(configured)provider.value=configured.value;else{generate.disabled=true;message.textContent='还没有可用的模型服务，请先配置模型，也可以自己填写。';}
    }catch{message.textContent='模型配置暂时无法读取，点击 AI 生成可重试加载。';generate.disabled=true;}
  }
  ai.addEventListener('click',()=>{if(!provider.options.length)models();});
  async function generateDraft(parent=null,answers=[]){
    if(working)return;working=true;const token=++serial;onBusy(true);
    for(const control of [manual,ai,prompt,provider,generate])control.disabled=true;
    generate.textContent='AI 正在整理…';message.textContent='正在调用真实模型。通常需要几十秒，当前表单保持原样。';
    try{
      let proposal=await request('/api/intake-proposals',jsonRequest('POST',{...getSnapshot(),prompt:parent?'请根据已提供的回答完善任务资料，仅询问尚未明确的关键问题。':prompt.value,provider:provider.value,
        ...(parent?{parent_proposal_id:parent,answers}:{})}));
      const start=Date.now();
      while(proposal.status==='running'){
        if(Date.now()-start>190000)throw new Error('等待模型超时。任务资料已保留，请稍后重试。');
        await new Promise(resolve=>setTimeout(resolve,1000));
        if(token!==serial)return;
        proposal=await request('/api/intake-proposals/'+proposal.id);
      }
      if(proposal.status!=='ready')throw new Error(proposal.error||'模型未返回可用草稿');
      preview.replaceChildren(el('h3','草稿预览 · 尚未写入任务'));
      const names=['任务名称','功能描述','验收标准','运行环境'];
      detailFields.forEach((key,i)=>{const section=el('div');section.append(el('h4',names[i]),el('p',proposal.result[key]||'待补充'));preview.append(section);});
      if(proposal.result.assumptions?.length){const list=el('ul');proposal.result.assumptions.forEach(s=>list.append(el('li',s)));preview.append(el('h4','推断与假设'),list);}
      if(proposal.result.questions?.length){
        preview.append(el('h4','请回答影响实现的问题'));
        const inputs=proposal.result.questions.map((question,i)=>{
          const label=el('label',`${i+1}. ${question}`,'clarification-question'),input=el('textarea');
          input.rows=2;input.maxLength=4000;input.placeholder='填写你的回答';label.append(input);preview.append(label);return input;
        });
        const next=el('button','提交回答并继续分析','secondary');next.type='button';
        next.onclick=()=>{const answers=inputs.map(input=>input.value.trim());if(answers.some(answer=>!answer)){message.textContent='请先回答每一个问题，再继续分析。';return;}generateDraft(proposal.id,answers);};
        preview.append(next);
      }
      preview.append(el('small',`请求模型：${proposal.requested_model} · 实际模型：${proposal.actual_model||'服务未报告'} · ${proposal.duration_s} 秒`));
      const apply=el('button',proposal.result.questions?.length?'暂按草稿填写（仍有待确认问题）':'采用草稿并填入表单','primary');apply.type='button';
      apply.onclick=()=>{applied=proposal.id;onApply(proposal.result);message.textContent='草稿已填入表单。可继续修改，点击保存后生效。';apply.textContent='已填入表单 ✓';apply.disabled=true;};
      preview.append(apply);preview.hidden=false;message.textContent=proposal.result.questions?.length?'请回答问题，继续完善需求；也可以先将草稿填入表单。':'草稿已生成。先核对功能与验收条件，再选择采用。';
    }catch(error){message.textContent=error.message;}
    finally{if(token===serial){working=false;onBusy(false);for(const control of [manual,ai,prompt,provider,generate])control.disabled=false;generate.textContent='重新生成草稿';}}
  }
  generate.onclick=()=>generateDraft();
  return {get proposalId(){return applied;},get working(){return working;},reset(){serial++;applied=null;working=false;prompt.value='';preview.hidden=true;preview.replaceChildren();choose('manual');}};
}
