const $=id=>document.getElementById(id);
const state={page:1,pageSize:5,query:'',total:0};
async function api(path,options={}){
  const response=await fetch(path,{...options,headers:{'Content-Type':'application/json',...options.headers}});
  const value=await response.json();if(!response.ok)throw new Error(value.error||'请求失败');return value;
}
function fail(error){$('error').textContent=error.message;$('error').hidden=false;}
async function load(){
  $('error').hidden=true;
  const value=await api('/api/tasks?'+new URLSearchParams({page:state.page,page_size:state.pageSize,q:state.query}));
  state.total=value.total;$('total').textContent=value.total+' 项';$('tasks').replaceChildren();
  if(!value.items.length){const empty=document.createElement('li');empty.className='empty';empty.textContent='没有匹配的任务';$('tasks').append(empty);}
  for(const task of value.items){
    const item=document.createElement('li'),label=document.createElement('label'),checkbox=document.createElement('input'),title=document.createElement('span'),remove=document.createElement('button');
    checkbox.type='checkbox';checkbox.checked=task.completed;checkbox.setAttribute('aria-label','完成：'+task.title);title.textContent=task.title;if(task.completed)title.className='done';
    checkbox.onchange=async()=>{try{await api('/api/tasks/'+task.id,{method:'PATCH',body:JSON.stringify({completed:checkbox.checked})});await load();}catch(error){fail(error);}};
    remove.textContent='删除';remove.className='delete';remove.setAttribute('aria-label','删除：'+task.title);remove.onclick=async()=>{try{await api('/api/tasks/'+task.id,{method:'DELETE'});if(state.page>1&&(state.page-1)*state.pageSize>=state.total-1)state.page--;await load();}catch(error){fail(error);}};
    label.append(checkbox,title);item.append(label,remove);$('tasks').append(item);
  }
  $('page-label').textContent=`第 ${state.page} 页 / 共 ${Math.max(1,Math.ceil(value.total/state.pageSize))} 页`;
  $('previous').disabled=state.page===1;$('next').disabled=state.page*state.pageSize>=value.total;
}
$('create-form').onsubmit=async event=>{event.preventDefault();try{await api('/api/tasks',{method:'POST',body:JSON.stringify({title:$('new-title').value})});$('new-title').value='';state.page=1;state.query='';$('search').value='';await load();}catch(error){fail(error);}};
$('search-form').onsubmit=event=>{event.preventDefault();state.query=$('search').value.trim();state.page=1;load().catch(fail);};
$('previous').onclick=()=>{if(state.page>1){state.page--;load().catch(fail);}};
$('next').onclick=()=>{if(state.page*state.pageSize<state.total){state.page++;load().catch(fail);}};
load().catch(fail);
