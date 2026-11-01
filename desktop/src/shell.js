function update(value){
  document.querySelector('#status').className=value.ready?'ready':'';
  document.querySelector('#status').replaceChildren();
  document.querySelector('#status').append(document.createElement('i'),document.createTextNode(value.ready?'本地引擎已就绪':value.error?'连接待恢复':'正在启动'));
  document.querySelector('#heading').textContent=value.error?'本地引擎需要恢复':'正在启动本地引擎';
  document.querySelector('#message').textContent=value.error || '载入模型、工作台和历史记录。';
  document.querySelector('#workspace').textContent=value.workspace || '';
  document.querySelector('#recovery').hidden=!value.error;
  document.querySelector('#progress').hidden=!!value.error;
  for(const button of document.querySelectorAll('nav button'))button.classList.toggle('active',button.dataset.action===value.page);
}
document.addEventListener('click',event=>{const button=event.target.closest('[data-action]');if(button)window.desktop.action(button.dataset.action).catch(error=>update({error:error.message}));});
window.desktop.onState(update);window.desktop.ready().then(update);
