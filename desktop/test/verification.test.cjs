const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const crypto=require('node:crypto');

const source=fs.readFileSync(path.join(__dirname,'../src/main.cjs'),'utf8');
const verification=source.slice(source.indexOf('let verifying=false'),source.indexOf('app.whenReady()'));

function harness({captureError,blockedArtifacts=false}={}){
  const workspace=path.resolve(__dirname,'../../artifacts/desktop/verification-tests',crypto.randomUUID());
  fs.mkdirSync(workspace,{recursive:true});
  if(blockedArtifacts)fs.writeFileSync(path.join(workspace,'artifacts'),'not a directory');
  const exits=[],logs=[],timers=[],calls={stop:0,close:0,endInput:0,capture:0};
  const context={fs,path,Error,workspace,userData:workspace,diagnostics:[],verifying:false,
    allowClose:false,quitting:false,process:{versions:{electron:'45.0.0'}},
    log:message=>logs.push(message),showError(){},
    app:{isPackaged:false,exit:code=>exits.push(code)},
    backend:{origin:'http://127.0.0.1:1',session:'first',serverPid:123,exited:false,
      async stop(){calls.stop++;this.exited=true;},child:{stdin:{end(){calls.endInput++;}}}},
    view:{webContents:{isDestroyed:()=>false,getURL:()=>'/explorer',async capturePage(){
      calls.capture++;if(captureError)throw captureError;return {toPNG:()=>Buffer.from('capture fixture')};
    }}},win:{isVisible:()=>true,isMinimized:()=>false},
    async closeApp(){calls.close++;},
    setTimeout(callback,delay){const timer={callback,delay,cleared:false,unref(){}};timers.push(timer);
      if(delay===1300)queueMicrotask(callback);return timer;},
    clearTimeout(timer){timer.cleared=true;}
  };
  vm.createContext(context);vm.runInContext(verification,context);
  return {context,calls,exits,logs,timers,receipt:path.join(workspace,'artifacts/desktop/launch-check.json')};
}

test('capture failure preserves its actual stage and stops only the owned engine',async()=>{
  const h=harness({captureError:new Error('UnknownVizError')});
  await h.context.verifyStartup();await new Promise(setImmediate);
  const receipt=JSON.parse(fs.readFileSync(h.receipt,'utf8'));
  assert.equal(receipt.status,'failed');assert.equal(receipt.stage,'capture_page');
  assert.equal(receipt.error,'UnknownVizError');assert.notEqual(receipt.reason,'model_readiness_timeout');
  assert.equal(h.calls.stop,1);assert.deepEqual(h.exits,[1]);assert.equal(h.calls.close,0);
  const first=fs.readFileSync(h.receipt,'utf8');
  await h.context.verifyStartup();await new Promise(setImmediate);
  assert.equal(fs.readFileSync(h.receipt,'utf8'),first);assert.equal(h.calls.capture,1);
  assert.equal(h.calls.stop,1);assert.deepEqual(h.exits,[1]);
});

test('an unwritable artifact directory still stops and exits with failure',async()=>{
  const h=harness({blockedArtifacts:true});
  await h.context.verifyStartup();await new Promise(setImmediate);
  assert.equal(h.calls.capture,0);assert.equal(h.calls.stop,1);assert.deepEqual(h.exits,[1]);
  assert.ok(h.logs.some(line=>/prepare_artifacts/.test(line)));
});

test('a late capture cannot resume recovery after verification has already failed',async()=>{
  const h=harness();let finishCapture;
  h.context.view.webContents.capturePage=()=>new Promise(resolve=>{finishCapture=resolve;});
  const pending=h.context.verifyStartup();await new Promise(setImmediate);
  h.context.failVerification(new Error('capture deadline exceeded'),'capture_page');
  await new Promise(setImmediate);
  const first=fs.readFileSync(h.receipt,'utf8');
  finishCapture({toPNG:()=>Buffer.from('late capture')});await pending;
  assert.equal(fs.readFileSync(h.receipt,'utf8'),first);
  assert.equal(fs.existsSync(path.join(path.dirname(h.receipt),'desktop-model.png')),false);
  assert.equal(h.calls.endInput,0);assert.equal(h.calls.stop,1);assert.deepEqual(h.exits,[1]);
});

test('successful recovery retains its original successful receipt and close path',async()=>{
  const h=harness();
  await h.context.verifyStartup();
  assert.equal(h.calls.endInput,1);assert.equal(h.calls.close,0);assert.equal(h.exits.length,0);
  h.context.backend.session='recovered';
  vm.runInContext("navigationExitSession='navigation-exit'",h.context);
  await h.context.verifyStartup();
  const receipt=JSON.parse(fs.readFileSync(h.receipt,'utf8'));
  assert.equal(receipt.status,'passed');assert.equal(receipt.old_session,'first');
  assert.equal(receipt.new_session,'recovered');assert.equal(receipt.screenshot,'desktop-model.png');
  assert.equal(h.calls.close,1);assert.equal(h.calls.stop,0);assert.deepEqual(h.exits,[]);
});
