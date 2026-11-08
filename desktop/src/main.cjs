const {app,BrowserWindow,WebContentsView,ipcMain,Menu,dialog,shell,Tray,nativeImage,session,screen,nativeTheme}=require('electron');
nativeTheme.themeSource='dark';
const fs=require('node:fs');
const path=require('node:path');
const crypto=require('node:crypto');
const {pathToFileURL}=require('node:url');
const {Backend,pages,localOrigin,workspaceCandidates,validateWorkspace}=require('./runtime.cjs');

app.setName('无遁 TestFix');app.setAppUserModelId('cn.testfix.wudun.desktop');
const verify=process.argv.includes('--verify-desktop');
if(verify)app.setPath('userData',path.join(app.getPath('appData'),'Wudun-TestFix-Verification'));
const locked=app.requestSingleInstanceLock();
if(!locked){app.quit();}else{
let win,view,tray,backend,workspace,connecting=false,quitting=false,allowClose=false,recoveries=0,quitPending=false,pendingRecovery=null;
const children=new Set();
const preferences={sandbox:true,contextIsolation:true,nodeIntegration:false,webSecurity:true,allowRunningInsecureContent:false};
let state={ready:false,page:'model',error:null,workspace:null};
const userData=app.getPath('userData'),settingsPath=path.join(userData,'workspace.json');
const shellPath=path.join(__dirname,'shell.html'),shellUrl=pathToFileURL(shellPath).href;
const iconPath=path.join(__dirname,'../assets/icon.png');
const diagnostics=[];
function log(message){fs.mkdirSync(userData,{recursive:true});fs.appendFileSync(path.join(userData,'desktop.log'),new Date().toISOString()+' '+message+'\n');}
function send(update={}){state={...state,...update};if(win&&!win.isDestroyed())win.webContents.send('desktop:state',state);}
function showError(error){log('error: '+error.message);if(view&&!view.webContents.isDestroyed())view.setVisible(false);send({ready:false,error:error.message});}
function trusted(event){return win && event.sender===win.webContents && event.senderFrame?.url===shellUrl;}
function viewBounds(){if(win&&view){const [width,height]=win.getContentSize();view.setBounds({x:0,y:62,width,height:Math.max(1,height-62)});}}
function showWindow(){if(!win||win.isDestroyed())return;if(win.isMinimized())win.restore();win.show();win.focus();}
function internalLink(url){return !!backend?.origin&&localOrigin(url,backend.origin);}
function previewLink(url){try{const parsed=new URL(url);return parsed.protocol==='http:'&&parsed.hostname==='127.0.0.1'&&['8770','8771','8772','8773','8774'].includes(parsed.port)&&!parsed.username&&!parsed.password;}catch{return false;}}

async function externalLink(url){
  let parsed;try{parsed=new URL(url);}catch{return;}
  if(parsed.protocol!=='https:'||parsed.username||parsed.password)return;
  const answer=await dialog.showMessageBox(win,{type:'question',buttons:['取消','在系统浏览器中打开'],defaultId:0,cancelId:0,title:'打开外部资料',message:'此链接将离开无遁桌面软件',detail:parsed.href});
  if(answer.response===1)await shell.openExternal(parsed.href);
}
function guard(contents,{preview=false}={}){
  contents.setWindowOpenHandler(({url})=>{if(internalLink(url)||previewLink(url))openChild(url);else void externalLink(url);return{action:'deny'};});
  contents.on('will-navigate',event=>{const url=event.url;if(!internalLink(url)&&!(preview&&previewLink(url))){event.preventDefault();if(previewLink(url))openChild(url);else void externalLink(url);}});
  contents.on('will-redirect',event=>{const url=event.url;if(!internalLink(url)&&!(preview&&previewLink(url)))event.preventDefault();});
  contents.on('will-attach-webview',event=>event.preventDefault());
  contents.on('console-message',details=>{if(details.level==='error'){diagnostics.push({kind:'renderer_error',message:details.message});log('renderer: '+details.message);}if(details.message==='TESTFIX_MODEL_READY'&&contents===view?.webContents&&verify&&details.sourceId===backend?.origin+'/static/explorer/explorer.js')void verifyStartup();});
}
function openChild(url){
  const child=new BrowserWindow({parent:win,width:1140,height:780,autoHideMenuBar:true,title:'无遁 · 查看产物',icon:iconPath,webPreferences:{...preferences,partition:'persist:wudun-content'}});
  children.add(child);child.on('closed',()=>children.delete(child));guard(child.webContents,{preview:previewLink(url)});child.loadURL(url).catch(error=>dialog.showMessageBox(child,{type:'error',message:'产物暂时无法打开',detail:error.message}));
}
async function navigate(page){
  if(!Object.hasOwn(pages,page))throw new Error('未知工作台。');send({page});
  if(!backend?.origin)throw new Error('本地引擎还未就绪。');
  try{await view.webContents.loadURL(backend.origin+pages[page]);view.setVisible(true);send({ready:true,error:null});}catch(error){showError(error);}
}
function recover(instance){
  if(quitting||backend!==instance)return;
  if(connecting){pendingRecovery=instance;return;}
  showError(new Error('本地引擎已停止，正在恢复。历史实验记录仍保存在工程目录。'));
  if(recoveries++<2)setTimeout(()=>{if(backend===instance&&instance.exited)void connect();},1000*recoveries);
}
async function connect({targetWorkspace=workspace}={}){
  if(connecting||quitting)return false;connecting=true;
  const previous=backend;
  let instance;
  try{
    if(previous?.origin&&!previous.exited) {
      try {if(await previous.busy()){dialog.showMessageBoxSync(win,{type:'info',message:'任务仍在执行，请完成后再重新连接。'});return;}}
      catch(error){if(!previous.exited){dialog.showMessageBoxSync(win,{type:'warning',message:'暂时无法确认任务状态，未重启引擎。',detail:error.message});return;}}
    }
    if(previous)await previous.stop();
    workspace=targetWorkspace;send({ready:false,error:null,workspace});view.setVisible(false);
    instance=new Backend(workspace,path.join(userData,'sessions',crypto.randomUUID()),{onExit:()=>recover(instance)});
    backend=instance;await instance.start();log('engine ready on '+instance.origin);
    const navigation=navigate(state.page);
    if(verify&&verificationPhase===1&&!navigationExitSession){
      navigationExitSession=instance.session;log('verification: exit owned engine while initial navigation is pending');instance.child.stdin.end();
      const deadline=Date.now()+10000;
      while(!instance.exited&&Date.now()<deadline)await new Promise(resolve=>setTimeout(resolve,50));
      if(!instance.exited)throw new Error('导航期间的引擎退出验证超时。');
    }
    await navigation;
    return !instance.exited;
  }catch(error){
    if(error.code==='WORK_ACTIVE'){dialog.showMessageBoxSync(win,{type:'info',message:error.message});return false;}
    if(instance){try{await instance.stop();}catch(stopError){log('stop deferred: '+stopError.message);}if(instance.exited&&pendingRecovery!==instance)backend=null;}
    showError(error);return false;
  }
  finally{connecting=false;const pending=pendingRecovery;pendingRecovery=null;if(pending&&backend===pending&&pending.exited)recover(pending);}
}
async function chooseWorkspace(){
  if(backend&&!backend.exited&&await backend.busy())throw new Error('当前任务仍在执行，请完成后再切换工程。');
  const selected=await dialog.showOpenDialog(win,{title:'选择无遁工程文件夹',properties:['openDirectory']});
  if(selected.canceled)return;
  const targetWorkspace=validateWorkspace(selected.filePaths[0]);recoveries=0;
  if(await connect({targetWorkspace})){fs.mkdirSync(userData,{recursive:true});fs.writeFileSync(settingsPath,JSON.stringify({workspace},null,2));}
}
async function closeApp(){
  if(quitPending||allowClose)return;quitPending=true;
  try{
    if(connecting){dialog.showMessageBoxSync(win,{type:'info',message:'本地引擎正在启动，请稍后关闭软件。'});return;}
    if(backend&&!backend.exited&&await backend.busy()){
      dialog.showMessageBoxSync(win,{type:'info',title:'任务在后台继续',message:'任务尚未结束，软件将留在系统托盘继续运行。',detail:'双击托盘里的无遁图标可返回。任务完成后再退出，不会中断测试。'});win.hide();return;
    }
    quitting=true;if(backend)await backend.stop();allowClose=true;
    for(const child of children)child.destroy();if(view&&!view.webContents.isDestroyed())view.webContents.close();tray?.destroy();win.destroy();app.quit();
  }catch(error){quitting=false;if(error.code==='WORK_ACTIVE'){dialog.showMessageBoxSync(win,{type:'info',message:'任务已开始，软件将在托盘继续运行。'});win.hide();}else showError(error);}finally{quitPending=false;}
}
function menu(){Menu.buildFromTemplate([
  {label:'返回架构模型',click:()=>void navigate('model')},{label:'重新连接本地引擎',click:async()=>{try{if(backend&&await backend.busy())throw new Error('有任务执行中，暂不重启。');recoveries=0;await connect();}catch(error){showError(error);}}},
  {label:'选择工程文件夹…',click:()=>void chooseWorkspace().catch(showError)},{label:'打开软件日志',click:()=>void shell.openPath(userData)},
  {type:'separator'},{label:'关于无遁',click:()=>dialog.showMessageBox(win,{type:'info',message:'无遁 TestFix 桌面工程版 '+app.getVersion(),detail:'三维架构、工程协作、自动化测试。引擎随软件自动启动；当前工程：\n'+workspace})},{label:'退出软件',click:()=>void closeApp()},
]).popup({window:win});}
ipcMain.handle('desktop:state',event=>{if(!trusted(event))throw new Error('无效窗口。');return state;});
ipcMain.handle('desktop:action',async(event,action)=>{
  if(!trusted(event)||typeof action!=='string')throw new Error('无效桌面请求。');
  try{if(Object.hasOwn(pages,action))await navigate(action);else if(action==='retry'){recoveries=0;await connect();}else if(action==='workspace')await chooseWorkspace();else if(action==='logs')await shell.openPath(userData);else if(action==='reload')view.webContents.reload();else if(action==='menu')menu();else throw new Error('未知操作。');}catch(error){showError(error);}return state;
});
app.on('second-instance',showWindow);app.on('activate',showWindow);
app.on('before-quit',event=>{if(!allowClose){event.preventDefault();void closeApp();}});
process.on('uncaughtException',error=>{log(error.stack||error.message);showError(error);});
process.on('unhandledRejection',error=>showError(error instanceof Error?error:new Error(String(error))));
let verifying=false,verificationPhase=0,firstSession=null,navigationExitSession=null,verificationStage='waiting_for_model_ready',verificationSettled=false,verificationExiting=false,verificationWatchdog=null;
function verificationReceiptPath(){return path.join(workspace||userData,'artifacts','desktop','launch-check.json');}
function writeVerificationFailure(error,stage){
  const record={status:'failed',reason:'verification_failed',stage,error:error instanceof Error?error.message:String(error),error_name:error instanceof Error?error.name:'Error',packaged:app.isPackaged,electron:process.versions.electron,workspace:workspace||null,engine_pid:backend?.serverPid??null,engine_origin:backend?.origin??null,renderer_url:(()=>{try{return view&&!view.webContents.isDestroyed()?view.webContents.getURL():null;}catch{return null;}})(),diagnostics};
  try{fs.mkdirSync(path.dirname(verificationReceiptPath()),{recursive:true});fs.writeFileSync(verificationReceiptPath(),JSON.stringify(record,null,2));}catch(writeError){try{log('verification receipt write failed: '+(writeError instanceof Error?writeError.message:String(writeError)));}catch{}}
}
async function endVerification(code){
  if(verificationExiting)return false;verificationExiting=true;
  try{if(backend&&!backend.exited)await backend.stop();}catch(stopError){try{log('verification stop deferred: '+(stopError instanceof Error?stopError.message:String(stopError)));}catch{}}
  allowClose=true;app.exit(code);return true;
}
function failVerification(error,stage){
  if(verificationSettled)return;verificationSettled=true;verifying=true;
  if(verificationWatchdog){clearTimeout(verificationWatchdog);verificationWatchdog=null;}
  try{log('verification failed ['+stage+']: '+(error instanceof Error?error.stack||error.message:String(error)));}catch{}
  writeVerificationFailure(error,stage);
  try{showError(error instanceof Error?error:new Error(String(error)));}catch{}
  void endVerification(1);
}
async function verifyStartup(){
  if(verifying||!backend?.origin||backend.session===navigationExitSession)return;verifying=true;
  try{
    verificationStage='wait_for_renderer';
    await new Promise(resolve=>setTimeout(resolve,1300));
    if(verificationSettled)return;
    verificationStage='prepare_artifacts';
    const folder=path.join(workspace,'artifacts','desktop');fs.mkdirSync(folder,{recursive:true});
    verificationStage='capture_page';const picture=await view.webContents.capturePage();
    if(verificationSettled)return;
    verificationStage='write_screenshot';fs.writeFileSync(path.join(folder,'desktop-model.png'),picture.toPNG());
    if(verificationPhase===0){firstSession=backend.session;verificationPhase=1;verifying=false;verificationStage='end_engine_input';log('verification: end owned engine input to exercise automatic recovery');backend.child.stdin.end();verificationStage='await_engine_recovery';return;}
    verificationStage='check_recovery_session';
    if(backend.session===firstSession)throw new Error('恢复后仍使用旧引擎会话。');
    verificationStage='check_navigation_exit_session';
    if(!navigationExitSession||backend.session===navigationExitSession)throw new Error('导航期间退出后未恢复到新会话。');
    verificationStage='check_window';
    if(!win.isVisible()||win.isMinimized())throw new Error('实际桌面窗口不可见，不能判定启动成功。');
    verificationStage='write_receipt';
    const receipt={status:'passed',packaged:app.isPackaged,electron:process.versions.electron,window_visible:win.isVisible(),engine_pid:backend.serverPid,engine_origin:backend.origin,renderer_url:view.webContents.getURL(),webgl_model_initialized:true,automatic_engine_recovery:true,navigation_exit_recovered:true,old_session:firstSession,navigation_exit_session:navigationExitSession,new_session:backend.session,diagnostics,workspace,screenshot:'desktop-model.png'};
    fs.writeFileSync(path.join(folder,'launch-check.json'),JSON.stringify(receipt,null,2));
    verificationSettled=true;if(verificationWatchdog){clearTimeout(verificationWatchdog);verificationWatchdog=null;}
    verificationStage='close_app';
    await closeApp();
  }catch(error){failVerification(error,verificationStage);}
}
app.whenReady().then(async()=>{
  Menu.setApplicationMenu(null);
  fs.mkdirSync(userData,{recursive:true});
  let saved,configured;try{saved=JSON.parse(fs.readFileSync(settingsPath,'utf8')).workspace;}catch{}
  try{configured=JSON.parse(fs.readFileSync(path.join(__dirname,'../workspace-default.json'),'utf8')).workspace;}catch{}
  for(const candidate of workspaceCandidates({saved,configured,appPath:app.getAppPath(),exePath:app.getPath('exe'),cwd:process.cwd()})){try{workspace=validateWorkspace(candidate);break;}catch{}}
  const area=screen.getPrimaryDisplay().workAreaSize;
  win=new BrowserWindow({width:Math.min(1500,area.width-50),height:Math.min(950,area.height-60),minWidth:1000,minHeight:700,backgroundColor:'#0b111c',autoHideMenuBar:true,icon:iconPath,title:'无遁 TestFix · 桌面工作台',webPreferences:{...preferences,preload:path.join(__dirname,'preload.cjs')}});
  win.on('close',event=>{if(!allowClose){event.preventDefault();void closeApp();}});
  win.on('resize',viewBounds);win.webContents.setWindowOpenHandler(()=>({action:'deny'}));win.webContents.on('will-navigate',event=>event.preventDefault());
  const contentSession=session.fromPartition('persist:wudun-content');
  contentSession.setPermissionRequestHandler((_webContents,_permission,callback)=>callback(false));contentSession.setPermissionCheckHandler(()=>false);
  contentSession.on('will-download',(_event,item)=>{item.setSaveDialogOptions({defaultPath:path.join(app.getPath('downloads'),path.basename(item.getFilename()))});});
  view=new WebContentsView({webPreferences:{...preferences,partition:'persist:wudun-content'}});win.contentView.addChildView(view);view.setVisible(false);viewBounds();guard(view.webContents);
  view.webContents.on('render-process-gone',(_event,details)=>{showError(new Error('界面进程已退出：'+details.reason+'。可点击重新连接恢复。'));});
  const image=nativeImage.createFromPath(iconPath);if(!image.isEmpty()){tray=new Tray(image.resize({width:24,height:24}));tray.setToolTip('无遁 TestFix');tray.on('double-click',showWindow);tray.setContextMenu(Menu.buildFromTemplate([{label:'打开无遁',click:showWindow},{label:'退出软件',click:()=>void closeApp()}]));}
  await win.loadFile(shellPath);win.show();
  if(verify){verificationWatchdog=setTimeout(()=>failVerification(new Error('自检在 70 秒内未完成。'),verificationStage),70000);verificationWatchdog.unref();}
  if(workspace)await connect();else showError(new Error('未找到本机工程。请选择包含 testfix、web 和 .venv 的工程文件夹。'));
}).catch(error=>{log(error.stack||String(error));dialog.showErrorBox('无遁启动失败',String(error));allowClose=true;app.quit();});
}
