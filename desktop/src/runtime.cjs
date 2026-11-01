const fs=require('node:fs');
const path=require('node:path');
const crypto=require('node:crypto');
const http=require('node:http');
const {spawn}=require('node:child_process');

const pages=Object.freeze({model:'/explorer',engineering:'/engineering',hardware:'/hardware',web:'/web-testing',home:'/'});

function validateWorkspace(directory) {
  if(typeof directory!=='string' || !path.isAbsolute(directory)) throw new Error('请选择工程的完整文件夹路径。');
  const root=fs.realpathSync.native(directory);
  for(const relative of ['testfix/desktop_server.py','web/explorer/index.html','pyproject.toml','.venv/Scripts/python.exe']) {
    if(!fs.statSync(path.join(root,relative),{throwIfNoEntry:false})?.isFile()) throw new Error(`工程缺少 ${relative}。请选择已配置好的无遁工程文件夹。`);
  }
  return root;
}

function workspaceCandidates({saved,appPath,exePath,configured,cwd}) {
  const candidates=[saved,process.env.TESTFIX_WORKSPACE,configured];
  for(const initial of [appPath,exePath,cwd]) {
    if(!initial)continue;
    let current=path.resolve(initial);
    for(let level=0;level<7;level++) {candidates.push(current);const parent=path.dirname(current);if(parent===current)break;current=parent;}
  }
  return [...new Set(candidates.filter(Boolean))];
}

function requestJson(url,token,timeout=2000,method='GET') {
  return new Promise((resolve,reject)=>{
    const request=http.request(url,{method,headers:token?{'X-TestFix-Desktop':token}:{},timeout},response=>{
      let body='';response.setEncoding('utf8');response.on('data',chunk=>{body+=chunk;if(body.length>4*1024*1024){response.destroy();reject(new Error('本地引擎响应过大。'));}});
      response.on('end',()=>{try{if(response.statusCode!==200){const error=new Error(`本地引擎返回 ${response.statusCode}`);error.statusCode=response.statusCode;throw error;}resolve(JSON.parse(body));}catch(error){reject(error);}});
      response.on('error',reject);
    });request.on('error',reject);request.on('timeout',()=>request.destroy(new Error('本地引擎未及时响应。')));request.end();
  });
}

function localOrigin(url,expectedOrigin) {
  try {const parsed=new URL(url);return parsed.protocol==='http:' && parsed.origin===expectedOrigin && parsed.hostname==='127.0.0.1' && !parsed.username && !parsed.password;} catch{return false;}
}

class Backend {
  constructor(root,sessionDirectory,{spawnProcess=spawn,timeout=45000,onExit=()=>{}}={}) {
    this.root=validateWorkspace(root);this.directory=sessionDirectory;this.spawnProcess=spawnProcess;this.timeout=timeout;this.onExit=onExit;
    this.token=crypto.randomBytes(32).toString('hex');this.session=crypto.randomUUID();this.stopping=false;this.child=null;this.origin=null;
  }
  async start() {
    fs.mkdirSync(this.directory,{recursive:true});
    this.endpoint=path.join(this.directory,'endpoint.json');
    // Never reuse a stale PID or attach to the browser's fixed-port service.
    if(fs.existsSync(this.endpoint)) throw new Error('桌面引擎会话目录已存在，请重新启动软件。');
    const logPath=path.join(this.directory,'engine.log');
    this.log=fs.createWriteStream(logPath,{flags:'a'});
    const env={...process.env,TESTFIX_DESKTOP_TOKEN:this.token,PYTHONUTF8:'1',PYTHONUNBUFFERED:'1'};
    delete env.ELECTRON_RUN_AS_NODE;
    this.child=this.spawnProcess(path.join(this.root,'.venv/Scripts/python.exe'),['-X','utf8','-m','testfix.desktop_server','--root',this.root,'--endpoint',this.endpoint,'--session',this.session,'--parent-pid',String(process.pid)],{cwd:this.root,env,windowsHide:true,stdio:['pipe','pipe','pipe'],shell:false});
    this.child.stdout.pipe(this.log,{end:false});this.child.stderr.pipe(this.log,{end:false});
    this.spawnError=null;
    this.child.once('error',error=>{this.spawnError=error;this.exited=true;this.log.end();});
    this.child.once('exit',(code,signal)=>{this.exited=true;this.log.end();if(!this.stopping)this.onExit({code,signal,logPath});});
    const deadline=Date.now()+this.timeout;
    while(Date.now()<deadline) {
      if(this.spawnError)throw this.spawnError;
      if(this.exited)throw new Error(`本地引擎启动失败。日志：${logPath}`);
      try {
        const info=JSON.parse(fs.readFileSync(this.endpoint,'utf8'));
        const origin=new URL(info.origin);
        if(info.session!==this.session || origin.hostname!=='127.0.0.1' || origin.protocol!=='http:' || origin.pathname!=='/' || origin.username || origin.password || !origin.port) throw new Error('本地引擎身份不匹配。');
        const ready=await requestJson(origin.origin+'/api/desktop/status',this.token);
        if(ready.session!==this.session || ready.pid!==info.pid || ready.workspace!==this.root || ready.status!=='ready') throw new Error('本地引擎握手失败。');
        this.origin=origin.origin;this.serverPid=info.pid;return this;
      } catch(error) {this.lastError=error;}
      await new Promise(resolve=>setTimeout(resolve,150));
    }
    throw new Error(`本地引擎在 45 秒内未就绪。日志：${logPath}`);
  }
  async busy() {
    if(!this.origin)return false;
    const result=await requestJson(this.origin+'/api/desktop/status',this.token);
    return (result.owned_active_runs ?? result.active_runs)>0 || result.inflight_requests>0;
  }
  async stop() {
    const child=this.child;
    if(!child || this.exited)return;
    this.stopping=true;
    let idleAccepted=false;
    try {
      if(this.origin) {
        const result=await requestJson(this.origin+'/api/desktop/shutdown',this.token,3000,'POST');
        if(result.status!=='stopping'||result.session!==this.session)throw new Error('引擎停止确认无效。');
        idleAccepted=true;
      }else{
        child.stdin.on('error',()=>{});child.stdin.end();
      }
    }catch(error){
      this.stopping=false;
      if(error.statusCode===409){error.code='WORK_ACTIVE';error.message='任务仍在执行，已保留引擎。';}
      if(this.exited)return;
      throw error;
    }
    const deadline=Date.now()+8000;
    while(!this.exited && Date.now()<deadline)await new Promise(resolve=>setTimeout(resolve,100));
    // The live ChildProcess handle belongs to this launch. No PID files or
    // processes found on a port are ever used as termination targets.
    if(!this.exited && child.exitCode===null && idleAccepted) {
      if(process.platform==='win32')await new Promise(resolve=>{const killer=this.spawnProcess('taskkill.exe',['/PID',String(child.pid),'/T','/F'],{windowsHide:true,stdio:'ignore',shell:false});killer.once('error',resolve);killer.once('exit',resolve);});
      else child.kill('SIGTERM');
    }
    if(!this.exited&&!idleAccepted)throw new Error('引擎仍在完成已接收的任务，请稍后重试。');
  }
}

module.exports={Backend,pages,localOrigin,requestJson,validateWorkspace,workspaceCandidates};
