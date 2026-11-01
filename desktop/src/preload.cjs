const {contextBridge,ipcRenderer}=require('electron');
contextBridge.exposeInMainWorld('desktop',Object.freeze({
  action:(action)=>ipcRenderer.invoke('desktop:action',action),
  onState:(callback)=>{if(typeof callback!=='function')return;ipcRenderer.on('desktop:state',(_event,value)=>callback(value));},
  ready:()=>ipcRenderer.invoke('desktop:state'),
}));
