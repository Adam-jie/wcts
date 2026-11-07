import {detailAssistant,detailFields} from '/static/intake/details-assistant.js';
const $=id=>document.getElementById(id);
const state={mode:'upload',files:[],busy:false,timer:null,resume:null};
const fields=()=>({title:$('task-title').value,description:$('description').value,acceptance:$('acceptance').value,environment:$('environment').value});
const assistant=detailAssistant({mount:$('details-assistant'),getSnapshot:()=>({mode:state.mode,...fields(),url:state.mode==='url'?$('source-url').value.trim():'',attachment_names:state.files.map(f=>f.name),...(state.resume?{task_id:state.resume.id,revision:state.resume.revision||0}:{})}),onApply:result=>{for(const key of detailFields)$(key==='title'?'task-title':key).value=result[key];},onBusy:value=>busy(value,'AI 正在整理…')});
const statuses={draft:'待整理',reading:'正在读取',prepared:'已整理',failed:'读取失败'};
const kinds={installer:'安装包',archive:'工程压缩包',source_file:'源码文件',attachment:'附件',webpage:'网页'};
const size=bytes=>bytes>=1048576?(bytes/1048576).toFixed(1)+' MB':(bytes/1024).toFixed(1)+' KB';
const node=(tag,text,className)=>{const element=document.createElement(tag);if(text!==undefined)element.textContent=text;if(className)element.className=className;return element;};
async function api(url,options={}){
  const response=await fetch(url,{cache:'no-store',...options});
  const data=await response.json();
  if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:Array.isArray(data.detail)?data.detail.map(item=>item.msg).join('；'):'请求失败，请稍后重试');
  return data;
}
function showError(error){$('form-error').textContent=error.message;$('form-error').hidden=false;}
function setMode(mode){
  if(state.busy)return;
  if(state.resume&&state.resume.mode!==mode){showError(new Error('继续已有任务时保留原输入方式；如需更换，请创建下一个任务。'));return;}
  state.mode=mode;
  document.querySelectorAll('[data-mode]').forEach(button=>{const active=button.dataset.mode===mode;button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;$('panel-'+button.dataset.mode).hidden=!active;});
  $('source-url').required=mode==='url';
  $('form-error').hidden=true;
}
function renderFiles(){
  $('file-list').replaceChildren();
  state.files.forEach((file,index)=>{const item=node('li');const remove=node('button','×');remove.type='button';remove.setAttribute('aria-label','移除 '+file.name);remove.disabled=state.busy;remove.onclick=()=>{state.files.splice(index,1);renderFiles();};item.append(node('strong',file.name),node('small',size(file.size)),remove);$('file-list').append(item);});
}
function addFiles(files){
  if(state.busy)return;
  const selected=[...state.files];
  for(const file of files){
    if(!file.size){showError(new Error('不能上传空文件：'+file.name));return;}
    if(file.size>1024**3){showError(new Error('文件超过 1 GB：'+file.name));return;}
    if(!selected.some(existing=>existing.name===file.name&&existing.size===file.size&&existing.lastModified===file.lastModified))selected.push(file);
  }
  if(selected.length+(state.resume?.files.length||0)>20){showError(new Error('每个任务最多接收 20 个附件（含已经上传的文件）'));return;}
  state.files=selected;renderFiles();$('form-error').hidden=true;
}
function upload(identifier,file,number,total){
  return new Promise((resolve,reject)=>{
    const xhr=new XMLHttpRequest();xhr.open('PUT','/api/intakes/'+identifier+'/files?filename='+encodeURIComponent(file.name));xhr.setRequestHeader('Content-Type','application/octet-stream');
    xhr.upload.onprogress=event=>{if(event.lengthComputable){const percent=Math.round(event.loaded/event.total*100);$('transfer-label').textContent=`上传 ${number}/${total}：${file.name} · ${percent}%`;$('transfer-progress').value=percent;}};
    xhr.onload=()=>{let value;try{value=JSON.parse(xhr.responseText);}catch{reject(new Error('上传响应无效'));return;}if(xhr.status>=200&&xhr.status<300)resolve(value);else reject(new Error(typeof value.detail==='string'?value.detail:'上传失败'));};
    xhr.onerror=()=>reject(new Error('文件上传中断，请检查本地引擎连接'));xhr.send(file);
  });
}
function renderTask(task){
  $('result').hidden=false;$('result-title').textContent=task.title;$('result-status').textContent=statuses[task.status]||task.status;$('result-id').textContent=task.id;
  $('result-message').textContent=task.error||task.analysis?.message||(task.status==='reading'?'正在读取文件与工程结构…':'材料与需求已保存。');
  $('download-handoff').hidden=task.status!=='prepared';$('download-handoff').href='/api/intakes/'+task.id+'/handoff';
  $('view-in-center').href='/tasks?task='+task.id;
  $('implement-task').hidden=task.status!=='prepared'||!task.analysis?.source_available;
  $('implement-task').href='/implementation?task='+encodeURIComponent(task.id);
  $('result-files').replaceChildren();
  for(const file of task.files){
    const card=node('div',undefined,'file-evidence'),info=file.inspection;
    card.append(node('strong',file.name),node('p',(kinds[info?.kind]||'文件')+' · '+size(file.size)),node('code','SHA-256 '+file.sha256));
    const original=node('a','↓ 下载原文件');original.href='/api/intakes/'+task.id+'/files/'+file.id;original.setAttribute('download',file.name);card.append(original);
    if(info){card.append(node('p',info.message));if(info.source_count)card.append(node('p',`${info.file_count} 个条目 · ${info.source_count} 个源码文件`));
      if(info.page_title)card.append(node('p','网页标题：'+info.page_title));
      for(const link of info.download_links||[]){try{const url=new URL(link,file.url||task.url);if(!['http:','https:'].includes(url.protocol))continue;const button=node('a','使用下载链接：'+url.pathname.split('/').pop());button.href='#task-form';button.onclick=()=>{if(state.busy)return;setMode('url');$('source-url').value=url.href;$('source-url').focus();};card.append(button);}catch{/* Ignore malformed page links. */}}
    }$('result-files').append(card);
  }
  $('result-requirements').replaceChildren();
  for(const [label,values] of [['需求原文',task.analysis?.requirements],['验收标准',task.analysis?.acceptance_criteria]])if(values?.length){const list=node('ul');values.forEach(text=>list.append(node('li',text)));$('result-requirements').append(node('h3',label),list);}
  $('events').replaceChildren();for(const event of task.events)$('events').append(node('li',event.message));
}
async function history(){
  try{const {tasks}=await api('/api/intakes');$('history').replaceChildren();if(!tasks.length)$('history').append(node('p','还没有任务，创建第一项吧。','muted'));
    for(const task of tasks){const button=node('button',task.title);button.type='button';button.append(node('small',(statuses[task.status]||task.status)+' · '+new Date(task.created_at).toLocaleDateString('zh-CN')));button.onclick=()=>{if(!state.busy)location.href='/tasks?task='+task.id;};$('history').append(button);}
  }catch(error){$('history').replaceChildren(node('p','记录暂时无法读取。点击上方刷新重试。','muted'));}
}
async function openTask(identifier){
  clearTimeout(state.timer);
  const task=await api('/api/intakes/'+encodeURIComponent(identifier));renderTask(task);window.history.replaceState(null,'','/intake?task='+task.id);
  if(task.status==='reading')state.timer=setTimeout(()=>openTask(identifier).catch(showError),900);
  else await history();
  return task;
}
function busy(value,message='正在整理…'){state.busy=value;document.querySelectorAll('#task-form input,#task-form textarea,#task-form button,[data-mode],#new-task').forEach(element=>element.disabled=value);$('submit').textContent=value?message:state.resume?'保存并整理任务 ↗':'创建并整理任务 ↗';renderFiles();}
$('task-form').addEventListener('submit',async event=>{
  event.preventDefault();if(state.busy)return;$('form-error').hidden=true;
  if(state.mode==='upload'&&!state.files.length&&!state.resume?.files.length){showError(new Error('请先选择安装包、工程源码或其他附件'));return;}
  if(!$('task-title').value.trim()||!$('description').value.trim()){showError(new Error('请填写任务名称和功能描述'));return;}
  busy(true);$('transfer').hidden=false;$('transfer-label').textContent='正在保存需求…';$('transfer-progress').value=0;
  try{
    clearTimeout(state.timer);
    const resuming=!!state.resume;
    const task=await api(resuming?'/api/intakes/'+state.resume.id:'/api/intakes',{method:resuming?'PATCH':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...fields(),proposal_id:assistant.proposalId,...(resuming?{revision:state.resume.revision||0}:{mode:state.mode,url:state.mode==='url'?$('source-url').value.trim():''})})});
    state.resume=task;assistant.reset();
    renderTask(task);window.history.replaceState(null,'','/intake?task='+task.id);
    if(state.mode==='upload')while(state.files.length){await upload(task.id,state.files[0],1,state.files.length);state.files.shift();state.resume=await api('/api/intakes/'+task.id);}
    $('transfer-label').textContent=state.mode==='url'?'正在读取网址并下载材料…':'正在识别文件并整理需求…';$('transfer-progress').removeAttribute('value');
    let prepared=await api('/api/intakes/'+task.id+'/prepare',{method:'POST'});renderTask(prepared);
    while(prepared.status==='reading'){
      await new Promise(resolve=>setTimeout(resolve,900));
      prepared=await api('/api/intakes/'+task.id);renderTask(prepared);
    }
  }catch(error){showError(error);}
  finally{$('transfer').hidden=true;if(state.resume){state.resume=await api('/api/intakes/'+state.resume.id).catch(()=>state.resume);if(state.resume.status!=='draft'){state.resume=null;}}busy(false);await history();}
});
document.querySelectorAll('[data-mode]').forEach(button=>{button.onclick=()=>setMode(button.dataset.mode);button.onkeydown=event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();const modes=['upload','url','idea'];const index=event.key==='Home'?0:event.key==='End'?2:(modes.indexOf(state.mode)+(event.key==='ArrowRight'?1:2))%3;setMode(modes[index]);$('tab-'+modes[index]).focus();};});
$('files').onchange=event=>{addFiles(event.target.files);event.target.value='';};
for(const name of ['dragover','dragleave','drop'])$('dropzone').addEventListener(name,event=>{event.preventDefault();$('dropzone').classList.toggle('dragging',name==='dragover');if(name==='drop')addFiles(event.dataTransfer.files);});
$('refresh-history').onclick=history;
$('new-task').onclick=()=>{if(state.busy)return;clearTimeout(state.timer);$('task-form').reset();state.files=[];state.resume=null;assistant.reset();renderFiles();setMode('upload');$('result').hidden=true;window.history.replaceState(null,'','/intake');$('submit').textContent='创建并整理任务 ↗';$('task-title').focus();};
await history();
const identifier=new URLSearchParams(location.search).get('task');if(identifier){try{const task=await openTask(identifier);if(task.status==='draft'){setMode(task.mode);state.resume=task;for(const key of detailFields)$(key==='title'?'task-title':key).value=task[key]||'';$('source-url').value=task.url||'';$('submit').textContent='保存并整理任务 ↗';}else location.replace('/tasks?task='+task.id);}catch(error){showError(error);}}
