const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const crypto=require('node:crypto');

// Exercise the shipped orchestration functions without a mock Electron runtime.
const source=fs.readFileSync(path.join(__dirname,'../src/main.cjs'),'utf8');
const functions=source.slice(source.indexOf('function recover(instance)'),source.indexOf('async function chooseWorkspace()'));
function harness(stage){
  const timers=[],instances=[];
  const context={path,crypto,backend:null,workspace:'root',userData:'data',connecting:false,quitting:false,pendingRecovery:null,recoveries:0,
    verify:false,state:{page:'model'},view:{setVisible(){}},send(){},showError(){},log(){},win:{},dialog:{showMessageBoxSync(){}},
    setTimeout(callback){timers.push(callback);},
    Backend:class {
      constructor(_root,_directory,options){this.options=options;instances.push(this);}
      async start(){if(stage==='handshake'){this.exited=true;this.options.onExit();throw new Error('engine exited before readiness');}this.origin='http://127.0.0.1:1';}
      async stop(){}
    },
    async navigate(){if(stage==='navigation'){const instance=instances.at(-1);instance.exited=true;instance.options.onExit();}}
  };
  vm.createContext(context);vm.runInContext(functions,context);
  return {context,timers,instances};
}

for(const stage of ['handshake','navigation'])test(`an unexpected ${stage} exit schedules recovery after connection cleanup`,async()=>{
  const {context,timers,instances}=harness(stage);
  await context.connect();
  assert.equal(context.connecting,false);
  assert.equal(context.backend,instances[0]);
  assert.equal(timers.length,1);
  assert.equal(context.recoveries,1);
});

test('a shutdown refusal keeps the existing engine and does not launch a replacement',async()=>{
  const {context,instances,timers}=harness('normal');
  const previous={origin:'http://127.0.0.1:1',exited:false,async busy(){return false;},async stop(){throw Object.assign(new Error('work admitted during idle check'),{code:'WORK_ACTIVE'});}};
  context.backend=previous;
  assert.equal(await context.connect(),false);
  assert.equal(context.backend,previous);
  assert.equal(instances.length,0);assert.equal(timers.length,0);
});
