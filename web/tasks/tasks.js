import {detailAssistant,detailFields,request,jsonRequest,el} from '/static/intake/details-assistant.js';
import {icon} from '/static/workbench-icons.js';
const $=id=>document.getElementById(id);
const statuses={draft:'待整理',reading:'正在读取',prepared:'已整理',failed:'读取失败'};
const modes={upload:'上传文件',url:'提供网址',idea:'描述新功能'};
const sources={manual:'自己填写',ai:'AI 生成',ai_edited:'AI 生成后编辑'};
const kinds={archive:'工程压缩包',installer:'安装包',source_file:'源码文件',webpage:'网页',attachment:'附件'};
const state={task:null,offset:0,total:0,limit:20,busy:false,dirty:false,listSerial:0,detailSerial:0,timer:null};
const date=value=>new Date(value).toLocaleString('zh-CN',{hour12:false});
const bytes=n=>n>=1048576?(n/1048576).toFixed(1)+' MB':(n/1024).toFixed(1)+' KB';
const assistant=detailAssistant({mount:$('details-assistant'),getSnapshot:()=>({task_id:state.task.id,revision:state.task.revision||0,mode:state.task.mode,url:state.task.url,...fields()}),
  onApply:result=>{for(const key of detailFields)$('edit-'+key).value=result[key];markDirty();},onBusy:setBusy});
function fields(){return Object.fromEntries(detailFields.map(key=>[key,$('edit-'+key).value]));}
function markDirty(){state.dirty=true;$('save-message').textContent='有未保存的修改';}
function error(message){$('page-error').textContent=message;$('page-error').hidden=false;}
function setBusy(value){state.busy=value;document.querySelectorAll('#detail-form input,#detail-form textarea,#detail-form button,#save,#prepare,#discard').forEach(n=>n.disabled=value||state.task?.status==='reading');$('save').textContent=value?'正在处理…':'保存资料';if(!value)$('save').append(icon('arrow'));}
function selectTab(name){document.querySelectorAll('[data-tab]').forEach(b=>{const selected=b.dataset.tab===name;b.setAttribute('aria-selected',String(selected));$('tab-'+b.dataset.tab).hidden=!selected;});}
document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>selectTab(b.dataset.tab));
for(const key of detailFields)$('edit-'+key).oninput=markDirty;

async function loadList(){
  const serial=++state.listSerial;$('loading').textContent='正在读取';$('page-error').hidden=true;
  try{
    const query=new URLSearchParams({q:$('search').value,status:$('filter-status').value,mode:$('filter-mode').value,offset:String(state.offset),limit:String(state.limit)});
    const data=await request('/api/intakes?'+query);if(serial!==state.listSerial)return;
    if(data.total&&state.offset>=data.total){state.offset=Math.floor((data.total-1)/state.limit)*state.limit;await loadList();return;}
    state.total=data.total;$('total').textContent=data.total;
    for(const key of ['all','prepared','draft','failed'])$('count-'+key).textContent=data.counts[key];
    document.querySelectorAll('[data-status]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.status===$('filter-status').value)));
    $('task-list').replaceChildren();
    if(!data.tasks.length){const empty=el('div',undefined,'empty-state');empty.append(el('h2',data.counts.all?'没有匹配的任务':'还没有任务'),el('p',data.counts.all?'试试其他关键词或筛选条件。':'创建任务后，材料和资料会出现在这里。'));$('task-list').append(empty);}
    for(const task of data.tasks){const button=el('button',undefined,'task-row');button.type='button';button.dataset.id=task.id;button.setAttribute('aria-current',String(state.task?.id===task.id));
      const meta=el('div',undefined,'row-meta');meta.append(el('span',statuses[task.status]||task.status,'task-status '+task.status),el('span',`${modes[task.mode]} · ${task.file_count} 个附件`));
      const heading=el('div',undefined,'row-heading'),symbol=el('span',undefined,'task-icon '+task.mode);symbol.append(icon({upload:'code',url:'link',idea:'spark'}[task.mode]));heading.append(symbol,el('strong',task.title),icon('chevron'));
      button.append(heading,el('p',task.description),meta);button.onclick=()=>selectTask(task.id);$('task-list').append(button);}
    $('prev').disabled=state.offset===0;$('next').disabled=state.offset+state.limit>=data.total;$('page-label').textContent=data.total?`${Math.floor(state.offset/state.limit)+1} / ${Math.ceil(data.total/state.limit)}`:'0 / 0';
  }catch(e){if(serial===state.listSerial)error('任务列表读取失败：'+e.message);}
  finally{if(serial===state.listSerial)$('loading').textContent='';}
}
async function selectTask(identifier,{force=false,poll=false}={}){
  if(state.busy&&!force)return;
  if(state.dirty&&!force){error('当前资料尚未保存。请先保存，或点击“还原修改”放弃本次修改。');return;}
  const serial=++state.detailSerial;clearTimeout(state.timer);
  try{
    const task=await request('/api/intakes/'+encodeURIComponent(identifier));if(serial!==state.detailSerial)return;
    state.task=task;state.dirty=false;assistant.reset();$('save-message').textContent='';$('page-error').hidden=true;
    $('empty-detail').hidden=true;$('detail').hidden=false;$('detail-title').textContent=task.title;
    $('task-number').textContent='TASK / '+task.id.slice(0,8).toUpperCase();
    $('task-meta').textContent=`${modes[task.mode]} · 创建于 ${date(task.created_at)} · 更新于 ${date(task.updated_at||task.created_at)}`;
    $('detail-status').textContent=statuses[task.status]||task.status;$('detail-status').className='task-status '+task.status;
    $('detail-symbol').replaceChildren(icon({upload:'code',url:'link',idea:'spark'}[task.mode]));
    $('stage-message').textContent=task.error||task.analysis?.message||(task.status==='reading'?'正在读取材料，完成后可编辑任务资料。':'输入已保存，可继续补充资料和整理材料。');
    $('details-source').textContent=sources[task.details_source]||'自己填写';
    for(const key of detailFields)$('edit-'+key).value=task[key]||'';
    $('handoff').hidden=task.status!=='prepared';$('handoff').href='/api/intakes/'+task.id+'/handoff';
    $('implement').hidden=task.status!=='prepared'||!task.analysis?.source_available||(task.test_library&&!task.test_library.execution_url);$('implement').href=task.test_library?.execution_url||'/implementation?task='+task.id;$('implement').textContent=task.test_library?.execution_url?'使用原始基准任务运行 →':'进入补全与修改 →';
    $('prepare').hidden=task.status!=='draft'||task.mode==='upload'&&!task.files.length;
    $('continue-upload').hidden=task.status!=='draft'||task.mode!=='upload';$('continue-upload').href='/intake?task='+task.id;
    renderMaterials(task);renderEvents(task);
    if(!poll){selectTab('info');document.querySelector('.detail-scroll').scrollTop=0;}setBusy(false);
    window.history.replaceState(null,'','/tasks?task='+task.id);
    document.querySelectorAll('.task-row').forEach(b=>b.setAttribute('aria-current',String(b.dataset.id===task.id)));
    if(task.status==='reading')state.timer=setTimeout(()=>selectTask(task.id,{poll:true}),1000);
    if(!poll&&matchMedia('(max-width:960px)').matches)$('detail').scrollIntoView({behavior:'smooth',block:'start'});
  }catch(e){if(serial===state.detailSerial)error(e.message);}
}
function renderMaterials(task){
  $('file-count').textContent=task.files.length;$('materials').replaceChildren();
  $('source-url-block').hidden=!task.url;$('source-url-value').textContent=task.url||'';
  if(!task.files.length)$('materials').append(el('p','当前没有附件。描述新功能的任务可以只保存需求。','muted'));
  for(const file of task.files){const card=el('article',undefined,'file-evidence'),info=file.inspection;card.append(el('strong',file.name),el('p',`${kinds[info?.kind]||'文件'} · ${bytes(file.size)}`),el('code','SHA-256 '+file.sha256));
    if(info){card.append(el('p',info.message));if(info.file_count)card.append(el('p',`${info.file_count} 个条目 · ${info.source_count||0} 个源码文件`));if(info.manifests?.length)card.append(el('p','工程清单：'+info.manifests.join('、')));if(info.sample_files?.length){const details=el('details'),list=el('ul');details.append(el('summary','查看文件清单'));info.sample_files.forEach(n=>list.append(el('li',n)));details.append(list);card.append(details);}}
    const link=el('a','↓ 下载原文件');link.href='/api/intakes/'+task.id+'/files/'+file.id;link.download=file.name;card.append(link);$('materials').append(card);}
}
function renderEvents(task){
  $('task-events').replaceChildren();const events=[{at:task.created_at,message:'任务创建并保存'},...task.events];
  for(const event of events){const item=el('li',event.message);item.append(el('small',date(event.at)));$('task-events').append(item);}
  $('ai-record').hidden=true;
  request('/api/intake-proposals?task_id='+task.id).then(async data=>{
    if(state.task?.id!==task.id)return;
    const record=$('ai-record');record.replaceChildren();
    if(!data.proposals.length&&!task.details_proposal_id)return;
    record.hidden=false;record.append(el('h3','AI 整理记录'));
    // A new-task preview is bound to the created task by its persisted proposal id.
    let proposals=data.proposals;
    if(task.details_proposal_id&&!proposals.some(p=>p.id===task.details_proposal_id)){
      const bound=await request('/api/intake-proposals/'+task.details_proposal_id);proposals=[bound,...proposals];
    }
    if(state.task?.id!==task.id)return;
    for(const proposal of proposals){record.append(el('p',`${date(proposal.created_at)} · ${proposal.requested_model} · ${proposal.status==='ready'?'草稿已生成':proposal.status==='running'?'正在生成':'生成失败'} · ${proposal.duration_s??'—'} 秒`));
      const expand=el('button','查看生成资料与待确认项','secondary');expand.type='button';expand.onclick=async()=>{
        try{const p=await request('/api/intake-proposals/'+proposal.id);if(state.task?.id!==task.id)return;const text=el('div');
          text.append(el('p',`实际模型：${p.actual_model||'服务未报告'} · 记录 ${p.id}`));
          if(p.result){for(const key of detailFields)text.append(el('p',p.result[key]||'待补充'));text.append(el('p','推断与假设：\n'+p.result.assumptions.join('\n')),el('p','待确认：\n'+p.result.questions.join('\n')));}else text.append(el('p',p.error||'正在生成'));
          expand.replaceWith(text);
        }catch(e){error(e.message);}
      };record.append(expand);}
  }).catch(()=>{if(state.task?.id===task.id){$('ai-record').hidden=false;$('ai-record').replaceChildren(el('p','AI 记录暂时无法读取，请刷新重试。'));}});
}
$('detail-form').onsubmit=async event=>{
  event.preventDefault();if(state.busy||!state.task)return;setBusy(true);$('page-error').hidden=true;
  try{const task=await request('/api/intakes/'+state.task.id,jsonRequest('PATCH',{revision:state.task.revision||0,...fields(),proposal_id:assistant.proposalId}));
    state.dirty=false;await selectTask(task.id,{force:true});$('save-message').textContent='资料已保存 ✓';await loadList();
  }catch(e){$('save-message').textContent=e.message;}
  finally{setBusy(false);}
};
$('discard').onclick=()=>{if(state.task&&!state.busy)selectTask(state.task.id,{force:true});};
$('prepare').onclick=async()=>{if(state.busy||!state.task)return;if(state.dirty){error('请先保存修改后的资料，再整理输入材料。');return;}setBusy(true);
  try{await request('/api/intakes/'+state.task.id+'/prepare',{method:'POST'});await selectTask(state.task.id,{force:true});await loadList();}catch(e){error(e.message);}finally{setBusy(false);}};
let searchTimer;$('search').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{state.offset=0;loadList();},250);};
for(const id of ['filter-status','filter-mode'])$(id).onchange=()=>{state.offset=0;loadList();};
document.querySelectorAll('[data-status]').forEach(b=>b.onclick=()=>{$('filter-status').value=b.dataset.status;state.offset=0;loadList();});
$('prev').onclick=()=>{state.offset=Math.max(0,state.offset-state.limit);loadList();};$('next').onclick=()=>{state.offset+=state.limit;loadList();};
$('refresh').onclick=async()=>{await loadList();if(state.task&&!state.dirty&&!state.busy)await selectTask(state.task.id,{poll:true});};
window.addEventListener('beforeunload',event=>{if(state.dirty||state.busy){event.preventDefault();event.returnValue='';}});
await loadList();const identifier=new URLSearchParams(location.search).get('task');if(identifier)await selectTask(identifier);
