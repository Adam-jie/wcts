const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const os=require('node:os');
const crypto=require('node:crypto');
const http=require('node:http');
const {spawn}=require('node:child_process');
const {Backend,validateWorkspace,workspaceCandidates,localOrigin,requestJson}=require('../src/runtime.cjs');

test('only the exact owned loopback origin is an internal link',()=>{
  const origin='http://127.0.0.1:42001';
  assert.ok(localOrigin(origin+'/explorer',origin));
  for(const url of ['http://127.0.0.1:42002/','https://127.0.0.1:42001/','http://127.0.0.1:42001.evil.test/','http://user:pass@127.0.0.1:42001/','file:///C:/secret','javascript:alert(1)'])assert.equal(localOrigin(url,origin),false,url);
});

test('workspace selection checks required files and preserves Unicode paths',()=>{
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'无遁 desktop '));
  assert.throws(()=>validateWorkspace(directory),/缺少/);
  for(const name of ['testfix/desktop_server.py','web/explorer/index.html','pyproject.toml','.venv/Scripts/python.exe']){const file=path.join(directory,name);fs.mkdirSync(path.dirname(file),{recursive:true});fs.writeFileSync(file,'test');}
  assert.equal(validateWorkspace(directory),fs.realpathSync.native(directory));
  assert.throws(()=>validateWorkspace('relative'),/完整/);
  const values=workspaceCandidates({saved:directory,configured:directory,appPath:path.join(directory,'dist','desktop','app.asar')});
  assert.equal(values[0],directory);assert.equal(values.filter(value=>value===directory).length,1);
});

test('JSON requests reject non-success responses',async()=>{
  const server=http.createServer((_request,response)=>{response.writeHead(403);response.end('{}');});
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  try{await assert.rejects(requestJson(`http://127.0.0.1:${server.address().port}/`),/403/);}finally{await new Promise(resolve=>server.close(resolve));}
});

test('Windows workspace uses native filesystem spelling before the Python handshake',{skip:process.platform!=='win32'},()=>{
  const root=path.resolve(__dirname,'../..');
  const alternate=root.toLowerCase();
  assert.equal(validateWorkspace(alternate),fs.realpathSync.native(root));
});

test('busy or unconfirmed shutdown never closes input or kills a process',async()=>{
  const root=path.resolve(__dirname,'../..');
  let shutdownStatus=409,ended=0,kills=0;
  const server=http.createServer((request,response)=>{
    if(request.url.endsWith('/status')){response.writeHead(200);response.end(JSON.stringify({owned_active_runs:0,inflight_requests:1}));}
    else {assert.equal(request.method,'POST');response.writeHead(shutdownStatus);response.end('{}');}
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const engine=new Backend(root,path.join(root,'artifacts','desktop','tests',crypto.randomUUID()),{spawnProcess:()=>{kills++;throw new Error('must not kill');}});
  engine.origin=`http://127.0.0.1:${server.address().port}`;
  engine.child={pid:123,exitCode:null,stdin:{end:()=>ended++}};
  try{
    assert.equal(await engine.busy(),true);
    await assert.rejects(engine.stop(),error=>error.code==='WORK_ACTIVE');
    assert.equal(engine.stopping,false);
    shutdownStatus=500;
    await assert.rejects(engine.stop(),/500/);
    assert.equal(engine.stopping,false);
    assert.equal(ended,0);assert.equal(kills,0);
  }finally{await new Promise(resolve=>server.close(resolve));}
});

test('a stale endpoint is rejected before any process is launched',async()=>{
  const root=path.resolve(__dirname,'../..');
  const directory=path.join(root,'artifacts','desktop','tests',crypto.randomUUID());fs.mkdirSync(directory,{recursive:true});fs.writeFileSync(path.join(directory,'endpoint.json'),'{}');
  let launches=0;
  const engine=new Backend(root,directory,{spawnProcess:()=>{launches++;throw new Error('must not launch');}});
  await assert.rejects(engine.start(),/已存在/);assert.equal(launches,0);
});

test('a failed executable launch stops cleanly without targeting another PID',async()=>{
  const root=path.resolve(__dirname,'../..');
  const directory=path.join(root,'artifacts','desktop','tests',crypto.randomUUID());
  let launches=0;
  const engine=new Backend(root,directory,{spawnProcess:()=>{launches++;return spawn(path.join(directory,'missing-executable.exe'),[],{windowsHide:true,stdio:['pipe','pipe','pipe'],shell:false});}});
  await assert.rejects(engine.start(),/ENOENT/);await engine.stop();assert.equal(launches,1);assert.ok(engine.exited);
});

test('a real engine chooses an ephemeral port, handshakes, serves the model, stops, and reopens', {timeout:100000},async()=>{
  const root=path.resolve(__dirname,'../..');
  const directory=path.join(root,'artifacts','desktop','tests',crypto.randomUUID());
  let prior;
  for(let iteration=0;iteration<2;iteration++){
    const selectedRoot=process.platform==='win32'&&iteration===1?root.toLowerCase():root;
    const engine=new Backend(selectedRoot,path.join(directory,String(iteration)));
    try{
      await engine.start();
      assert.ok(engine.serverPid>0);
      assert.notEqual(engine.origin,'http://127.0.0.1:8765');
      assert.equal(await engine.busy(),false);
      const status=await requestJson(engine.origin+'/api/desktop/status',engine.token);
      assert.equal(status.session,engine.session);
      await assert.rejects(requestJson(engine.origin+'/api/desktop/status','wrong'),/403/);
      const response=await fetch(engine.origin+'/explorer');
      assert.equal(response.status,200);
      assert.match(await response.text(),/id="engine-canvas"/);
      assert.match(response.headers.get('content-security-policy'),/script-src 'self'/);
      if(prior)assert.notEqual(status.session,prior);
      prior=status.session;
    }finally{await engine.stop();}
    assert.equal(fs.existsSync(engine.endpoint),false);
    await assert.rejects(requestJson(engine.origin+'/api/desktop/status',engine.token,1000));
  }
});
