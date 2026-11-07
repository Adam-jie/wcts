import {request,jsonRequest,el} from '/static/intake/details-assistant.js';
import {icon} from '/static/workbench-icons.js';
const $=id=>document.getElementById(id);
const datasets={'project-cases':'完整工程样例','requirements':'需求开发模板','swe-bench-lite':'SWE-bench Lite','humaneval':'HumanEval','verilogeval':'VerilogEval'};
const domains={web:'网页系统',software:'软件工程',mcu:'MCU 固件',mpu:'MPU / 网关',rtl:'RTL 硬件设计'};
const operations={completion:'代码补全',modification:'修改 / 修复',development:'按需求开发'};
const origins={controlled_missing_file:'人为缺失文件实验',upstream_issue:'真实上游问题',authored_requirement:'自建需求任务',official_benchmark:'官方基准公开输入'};
let selected=null,offset=0,dataset='',total=0,serial=0,busy=false;
function error(message){$('error').textContent=message;$('error').hidden=!message;}
function tags(entry){const tags=el('div',undefined,'tags');for(const text of [domains[entry.domain],operations[entry.operation],entry.has_bundle?'工程已下载':'需求已收录'])tags.append(el('span',text));return tags;}
async function load(){
  const current=++serial;$('loading').textContent='读取中';error('');
  try{
    const data=await request('/api/test-library?'+new URLSearchParams({q:$('search').value,domain:$('domain').value,operation:$('operation').value,dataset,offset,limit:18}));
    if(current!==serial)return;total=data.total;$('total').textContent='· '+total;$('entries').replaceChildren();$('collections').replaceChildren();
    for(const [key,title,count] of [['','全部',Object.values(data.counts).reduce((a,b)=>a+b,0)],...Object.entries(data.counts).map(([key,count])=>[key,datasets[key]||key,count])]){
      const button=el('button',title);button.append(el('span',String(count),'collection-count'));button.type='button';button.setAttribute('aria-pressed',String(dataset===key));button.onclick=()=>{dataset=key;offset=0;load();};$('collections').append(button);
    }
    for(const entry of data.entries){const button=el('button',undefined,'entry');button.type='button';button.dataset.id=entry.id;button.setAttribute('aria-current',String(selected?.id===entry.id));
      const heading=el('div',undefined,'row-heading'),symbol=el('span',undefined,'task-icon '+entry.domain);symbol.append(icon(['mcu','mpu','rtl'].includes(entry.domain)?'chip':'code'));heading.append(symbol,el('strong',entry.title),icon('chevron'));
      button.append(heading,el('p',entry.description),tags(entry));button.onclick=()=>show(entry);$('entries').append(button);}
    if(!data.entries.length)$('entries').append(el('p','没有匹配的测试任务。'));
    $('prev').disabled=offset===0;$('next').disabled=offset+18>=total;$('page').textContent=total?`${Math.floor(offset/18)+1} / ${Math.ceil(total/18)}`:'0 / 0';
    if(!data.entries.some(entry=>entry.id===selected?.id)){
      if(data.entries.length)show(data.entries[0]);
      else{selected=null;$('detail').hidden=true;$('empty').hidden=false;$('empty').textContent='没有匹配的任务，请调整筛选条件。';}
    }
  }catch(e){error(e.message);}finally{if(current===serial)$('loading').textContent='';}
}
function show(entry){
  if(busy)return;selected=entry;$('empty').hidden=true;$('detail').hidden=false;$('tags').replaceChildren(tags(entry));
  document.querySelector('.detail-scroll').scrollTop=0;$('selected-title').textContent=entry.title;
  $('detail-symbol').replaceChildren(icon(['mcu','mpu','rtl'].includes(entry.domain)?'chip':'code'));
  for(const key of ['title','description','acceptance'])$(key).value=entry[key];
  $('source').replaceChildren();const source=entry.issue_url||entry.source.repository;
  if(source){const link=el('a',source);link.href=source;link.target='_blank';link.rel='noreferrer';$('source').append(link);}
  $('environment').textContent=entry.environment;$('provenance').textContent=origins[entry.origin]+'；'+(entry.source.license||'自建需求');$('commit').textContent=entry.source.commit||'';
  $('execution').textContent=entry.execution;$('download').hidden=!entry.has_bundle;$('download').href='/api/test-library/'+encodeURIComponent(entry.id)+'/bundle';$('download').download=entry.id+'.zip';$('message').textContent='';
  $('run').hidden=!entry.ready_run_url;$('run').href=entry.ready_run_url||'#';
  $('import').textContent=entry.dataset==='swe-bench-lite'?'导入需求与工程下载地址':'导入为任务';
  document.querySelectorAll('.entry').forEach(button=>button.setAttribute('aria-current',String(button.dataset.id===entry.id)));
}
$('detail').onsubmit=async event=>{
  event.preventDefault();if(busy)return;busy=true;$('import').disabled=true;error('');$('message').textContent='正在创建独立任务…';
  try{const task=await request('/api/test-library/'+encodeURIComponent(selected.id)+'/import',jsonRequest('POST',{title:$('title').value,description:$('description').value,acceptance:$('acceptance').value}));location.assign(task.url);}catch(e){error(e.message);$('message').textContent='导入未完成';}finally{busy=false;$('import').disabled=false;}
};
let timer;$('search').oninput=()=>{clearTimeout(timer);timer=setTimeout(()=>{offset=0;load();},250);};
for(const id of ['domain','operation'])$(id).onchange=()=>{offset=0;load();};
$('prev').onclick=()=>{offset=Math.max(0,offset-18);load();};$('next').onclick=()=>{offset+=18;load();};
await load();
