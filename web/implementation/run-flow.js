import {CORE,MODULES,STATUS,isRunning,timelineOf,frameOf,visibleFrame,publicDetail} from './flow-model.mjs';
const $=id=>document.getElementById(id);
const make=(tag,className,text)=>{const node=document.createElement(tag);node.className=className||'';if(text!==undefined)node.textContent=text;return node;};

export function createRunFlow(){
  let run=null,timeline=[],position=0,following=true,playing=false,playTimer=null,selected='plan-workflow',frame=frameOf(null,0),scene=null,loading=false,disconnected=false,group='auto',nodeSignature='';
  const root=$('run-flow'),stage=$('flow-stage');
  function stop(){clearTimeout(playTimer);playTimer=null;playing=false;}
  function select(id){selected=id;const node=frame.nodes.find(n=>n.id===id);if(node?.kind==='module'||node?.kind==='controller')group=id;else if(node?.parent)group=node.parent;paint(false);}
  function paint(animate=false){
    frame=frameOf(run,position,timeline);
    if(!frame.nodes.some(n=>n.id===selected))selected='plan-workflow';
    const ended={failed:'运行失败 · 已停止',rejected:'验收未通过',budget_exhausted:'预算耗尽 · 已停止',interrupted:'运行已中断'};
    const mode=disconnected?'连接中断 · 保留最后记录':following?(isRunning(run)?'实时执行':ended[run?.status]|| (run?'本次运行已结束':'等待运行')):(playing?'历史回放中':'回放已暂停');
    $('flow-mode').textContent=mode;$('flow-mode').dataset.live=String(following&&isRunning(run)&&!disconnected);
    $('flow-counter').textContent=run?`运行 ${run.id.slice(0,8)} · 第 ${frame.round} 轮`:'尚无执行事件';
    $('flow-position').max=String(timeline.length);$('flow-position').value=String(position);$('flow-position').disabled=!timeline.length;
    $('flow-position-text').textContent=`${position} / ${timeline.length}`;
    $('flow-replay').disabled=!timeline.length;$('flow-replay').textContent=playing?'暂停回放':!following&&position<timeline.length?'继续回放':'回放本次过程';
    $('flow-live').disabled=!run||following;$('flow-live').textContent=isRunning(run)?'回到实时':'回到最新';
    const event=frame.current,byId=new Map(frame.nodes.map(n=>[n.id,n]));
    const name=id=>byId.get(id)?.title||CORE.find(n=>n[0]===id)?.[1]||id;
    $('flow-transfer').textContent=event?(event.source===event.target?name(event.source):`${name(event.source)} → ${name(event.target)}`):'等待主控派工';
    const inputs=frame.edges.filter(e=>!e.planned&&e.eventId===event?.id&&e.kind==='依赖产物（经主控）').map(e=>name(e.source));
    $('flow-message').textContent=event?`${event.kind} · ${event.summary}${inputs.length?'\n输入来源：'+inputs.join('、')+'（经主控交接）':''}`:'开始后，任务分配、问题分析、补丁与测试反馈会在这里实时显示。';
    $('flow-time').textContent=event?.time?new Date(event.time).toLocaleTimeString('zh-CN',{hour12:false}):'—';
    $('flow-source').textContent=!following?'历史逐事件回放 · 按顺序压缩展示，界面操作不会启动模型或修改代码。':disconnected?'进度读取失败，动画已停止；连接恢复后同步最新事件。':run?.error?'停止原因：'+run.error:'依据宿主事件与派工记录；灰线为计划依赖，亮线和移动光点表示实际事件交接。';
    const node=byId.get(selected),detail=node&&publicDetail(node);
    $('flow-node-title').textContent=detail?.title||'';$('flow-node-state').textContent=detail?.state||'';$('flow-node-state').dataset.state=node?.status||'';
    $('flow-node-detail').textContent=detail?`${detail.description}${node?.lastEvent?'\n最近反馈：'+detail.message:''}`:'';
    $('flow-node-files').textContent=detail?.files?'文件：'+detail.files:'';
    $('flow-node-kind').textContent=detail?.kind||'所选节点';
    $('flow-node-contract').textContent=detail?.contract||'';$('flow-node-assignment').textContent=detail?.assignment||'';
    const nextNodes=JSON.stringify(frame.nodes.map(n=>[n.id,n.parent,n.title,n.kind]));
    if(nextNodes!==nodeSignature){
      const opened=$('flow-nodes').querySelector('details')?.open,scroll=$('flow-nodes').parentElement.scrollTop;
      nodeSignature=nextNodes;$('flow-nodes').replaceChildren();
      for(const module of MODULES){const section=make('section','flow-node-group');section.append(make('h5','',module.title));for(const item of frame.nodes.filter(n=>n.id===module.id||n.parent===module.id)){
        const button=make('button','flow-node-choice');button.type='button';button.dataset.node=item.id;button.dataset.kind=item.kind;button.append(make('i'),document.createTextNode(item.title));button.onclick=()=>select(item.id);section.append(button);}$('flow-nodes').append(section);}
      const inactive=make('details','flow-inactive');inactive.open=!!opened;inactive.append(make('summary'),make('p'));$('flow-nodes').append(inactive);$('flow-nodes').parentElement.scrollTop=scroll;
    }
    for(const button of $('flow-nodes').querySelectorAll('[data-node]')){button.dataset.state=byId.get(button.dataset.node)?.status||'pending';button.setAttribute('aria-pressed',String(selected===button.dataset.node));}
    const inactive=$('flow-nodes').querySelector('details');inactive.querySelector('summary').textContent=`未激活专业 ${frame.inactiveSpecialists.length} 类`;inactive.querySelector('p').textContent=frame.inactiveSpecialists.map(s=>s.name+'：'+s.reason).join('\n');
    const visible=visibleFrame(frame,group);scene?.update(visible,selected,animate&&!disconnected,playing||following&&!disconnected);
    $('flow-scope-note').textContent=MODULES.find(m=>m.id===visible.selectedGroup)?.title+' · 职责与员工';
    document.querySelectorAll('[data-flow-group]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.flowGroup===group)));
    root.dataset.mode=following?'latest':'replay';root.dataset.position=String(position);root.dataset.eventCount=String(timeline.length);
    root.dataset.activeNodes=frame.nodes.filter(n=>n.status==='running').map(n=>n.id).join(',');
  }
  function tick(){
    if(!playing)return;
    if(position>=timeline.length){stop();paint();return;}
    position++;selected=timeline[position-1]?.employee||timeline[position-1]?.skill||selected;paint(true);
    playTimer=setTimeout(tick,1100/Number($('flow-speed').value||2));
  }
  $('flow-replay').onclick=()=>{
    if(playing){stop();paint();return;}
    if(!timeline.length)return;
    following=false;playing=true;if(position>=timeline.length)position=0;tick();
  };
  $('flow-position').oninput=()=>{stop();following=false;position=Number($('flow-position').value);selected=timeline[position-1]?.employee||timeline[position-1]?.skill||'plan-workflow';paint(false);};
  $('flow-live').onclick=()=>{stop();following=true;position=timeline.length;paint();};
  $('flow-reset').onclick=()=>scene?.reset();
  document.querySelectorAll('[data-flow-group]').forEach(b=>b.onclick=()=>{group=b.dataset.flowGroup;if(group!=='auto')selected=group;paint(false);});
  function expand(value){root.classList.toggle('expanded',value);$('flow-expand').setAttribute('aria-expanded',String(value));$('flow-expand').textContent=value?'收起视图':'展开视图';document.body.classList.toggle('flow-expanded',value);}
  $('flow-expand').onclick=()=>expand(!root.classList.contains('expanded'));
  document.addEventListener('keydown',event=>{if(event.key==='Escape')expand(false);});
  async function initialize(){
    if(loading||scene)return;loading=true;
    try{const T=await import('/static/lab/vendor/three.module.min.js');scene=createScene(T,stage,$('flow-canvas'),$('flow-labels'),select);root.dataset.renderer='webgl';scene.update(visibleFrame(frame,group),selected,false,false);}
    catch(error){$('flow-canvas').hidden=true;$('flow-fallback').hidden=false;root.dataset.renderer='fallback';}
  }
  const observer=new IntersectionObserver(entries=>{if(entries.some(e=>e.isIntersecting))initialize();},{rootMargin:'200px'});observer.observe(stage);
  paint();
  window.addEventListener('pagehide',()=>{stop();observer.disconnect();scene?.dispose();},{once:true});
  return {update(value){
    const changed=run?.id!==value?.id,prior=timeline.length;run=value;disconnected=false;timeline=timelineOf(value);
    if(changed){stop();following=true;selected='plan-workflow';group='auto';}
    if(following){position=timeline.length;if(timeline.length>prior||changed)selected=timeline.at(-1)?.employee||timeline.at(-1)?.skill||selected;}
    paint(!changed&&following&&timeline.length>prior);
  },disconnected(){disconnected=true;paint();}};
}

function createScene(T,host,canvas,labelHost,onSelect){
  const reduced=matchMedia('(prefers-reduced-motion: reduce)');
  const renderer=new T.WebGLRenderer({canvas,antialias:true,alpha:false,powerPreference:'low-power'});
  renderer.setPixelRatio(Math.min(devicePixelRatio||1,1.7));renderer.setClearColor('#091321');renderer.outputColorSpace=T.SRGBColorSpace;
  const world=new T.Scene(),camera=new T.PerspectiveCamera(39,1,.1,160),objects=new T.Group(),links=new T.Group();
  world.add(objects,links,new T.HemisphereLight(0xc3e9ff,0x101a30,2.4));
  const key=new T.DirectionalLight(0xc3e4ff,3);key.position.set(1,12,5);world.add(key);
  const rim=new T.DirectionalLight(0x5a7fdb,2.4);rim.position.set(-9,4,-6);world.add(rim);
  const grid=new T.GridHelper(44,44,0x20435a,0x132a3b);grid.position.y=-.7;grid.material.transparent=true;grid.material.opacity=.5;world.add(grid);
  const floor=new T.Mesh(new T.PlaneGeometry(50,50),new T.MeshStandardMaterial({color:0x091321,metalness:.35,roughness:.8}));floor.rotation.x=-Math.PI/2;floor.position.y=-.73;world.add(floor);
  const entries=new Map(),arcs=new Map(),raycaster=new T.Raycaster(),pointer=new T.Vector2(),scratch=new T.Vector3();
  let signature='',edgeSignature='',selected='',packets=[],canAnimate=false,visible=false,disposed=false,frameId=0,lastFrame=0,width=1,height=1;
  const orbit={theta:0,phi:.63,radius:29,targetZ:1},defaultOrbit={...orbit};
  const colors={pending:0x405674,running:0x73e3e3,completed:0x83cbb0,handed_off:0x83cbb0,warning:0xf6bf78,failed:0xf3949e,stopped:0x7892ad};
  function free(group){for(const child of [...group.children]){child.traverse(n=>{n.geometry?.dispose();if(Array.isArray(n.material))n.material.forEach(m=>m.dispose());else n.material?.dispose();});group.remove(child);}}
  function mesh(geometry,color,x,y,z,metalness=.55){const m=new T.Mesh(geometry,new T.MeshStandardMaterial({color,roughness:.35,metalness}));m.position.set(x,y,z);return m;}
  function build(nodes){
    free(objects);entries.clear();labelHost.replaceChildren();
    for(const node of nodes){
      const group=new T.Group();group.position.set(...node.xyz);objects.add(group);
      const large=node.kind==='module'||node.kind==='controller',flat=['phase','artifact','algorithm','infrastructure'].includes(node.kind);
      const base=mesh(new T.CylinderGeometry(1.03,1.14,.18,6),0x182d46,0,-.26,0);base.rotation.y=Math.PI/6;group.add(base);
      const body=mesh(new T.BoxGeometry(1.38,.45,1.12),node.id==='plan-workflow'?0x24526a:0x243c5e,0,.1,0);group.add(body);
      const chip=mesh(new T.BoxGeometry(.92,.12,.76),0x426080,0,.4,0);group.add(chip);
      const core=mesh(new T.OctahedronGeometry(node.id==='plan-workflow'?.31:.21),0x9ae9e5,0,.74,0,.4);group.add(core);
      const ring=new T.Mesh(new T.TorusGeometry(1.16,.022,6,64),new T.MeshBasicMaterial({color:colors.pending,transparent:true,opacity:.6}));ring.rotation.x=Math.PI/2;ring.position.y=-.08;group.add(ring);
      for(let j=0;j<4;j++)for(const side of [-1,1])group.add(mesh(new T.BoxGeometry(.15,.08,.1),0x7d9bac,side*.78,.07,(j-1.5)*.24));
      const edges=new T.LineSegments(new T.EdgesGeometry(new T.BoxGeometry(1.4,.47,1.14)),new T.LineBasicMaterial({color:0x6f9aaf,transparent:true,opacity:.5}));edges.position.y=.1;group.add(edges);
      if(large){group.scale.setScalar(1.35);body.material.color.setHex(node.kind==='controller'?0x2f697a:0x344d77);}
      if(flat){group.scale.set(1.25,.42,.85);core.visible=false;ring.visible=false;}
      if(node.kind==='infrastructure'){group.scale.set(7,.22,1.1);body.material.color.setHex(0x20394c);base.visible=false;edges.visible=false;}
      const label=make('button','flow-label');label.type='button';label.dataset.node=node.id;label.dataset.kind=node.kind;label.append(make('i'),make('span','',node.title));label.onclick=()=>onSelect(node.id);labelHost.append(label);
      body.userData.nodeId=node.id;core.userData.nodeId=node.id;
      entries.set(node.id,{group,body,core,ring,label,labelWidth:0,labelHeight:0});
    }
  }
  function rebuildEdges(edges){
    free(links);arcs.clear();
    for(const edge of edges){
      const source=entries.get(edge.source)?.group.position,target=entries.get(edge.target)?.group.position;if(!source||!target)continue;
      const a=source.clone().add(new T.Vector3(0,.52,0)),b=target.clone().add(new T.Vector3(0,.52,0));
      const mid=a.clone().lerp(b,.5);mid.y+=Math.min(2,a.distanceTo(b)*.12);
      const curve=new T.QuadraticBezierCurve3(a,mid,b);
      const material=edge.kind==='模块包含'?new T.LineDashedMaterial({color:0x426079,dashSize:.13,gapSize:.15,transparent:true,opacity:.24}):new T.LineBasicMaterial({color:edge.planned?0x46617c:0x5c95a8,transparent:true,opacity:.19});
      const line=new T.Line(new T.BufferGeometry().setFromPoints(curve.getPoints(36)),material);if(edge.kind==='模块包含')line.computeLineDistances();links.add(line);
      const cone=new T.Mesh(new T.ConeGeometry(.08,.24,6),new T.MeshBasicMaterial({color:0x74c9d3,transparent:true,opacity:.3}));cone.position.copy(curve.getPoint(.82));cone.quaternion.setFromUnitVectors(new T.Vector3(0,1,0),curve.getTangent(.82).normalize());links.add(cone);
      cone.visible=edge.kind!=='模块包含';
      arcs.set(edge.id,{curve,line,cone,edge});
    }
  }
  const particles=Array.from({length:8},()=>{
    const bead=new T.Mesh(new T.SphereGeometry(.10,12,8),new T.MeshBasicMaterial({color:0xc5ffff}));bead.visible=false;world.add(bead);
    const halo=new T.Mesh(new T.SphereGeometry(.23,12,8),new T.MeshBasicMaterial({color:0x6ce3e0,transparent:true,opacity:.16,depthWrite:false}));halo.visible=false;world.add(halo);return {bead,halo};
  });
  function cameraPose(){camera.position.set(orbit.radius*Math.sin(orbit.theta)*Math.cos(orbit.phi),orbit.radius*Math.sin(orbit.phi),orbit.targetZ+orbit.radius*Math.cos(orbit.theta)*Math.cos(orbit.phi));camera.lookAt(0,0,orbit.targetZ);}
  function resize(){const rect=host.getBoundingClientRect();width=Math.max(1,rect.width);height=Math.max(1,rect.height);renderer.setSize(width,height,false);camera.aspect=width/height;camera.updateProjectionMatrix();defaultOrbit.radius=Math.max(26,26/Math.max(.65,camera.aspect),defaultOrbit.spanZ||0);if(!drag)orbit.radius=defaultOrbit.radius;cameraPose();}
  const ro=new ResizeObserver(resize);ro.observe(host);
  const io=new IntersectionObserver(items=>{visible=items.some(i=>i.isIntersecting);if(visible&&!frameId)frameId=requestAnimationFrame(draw);});io.observe(host);
  let drag=null;
  canvas.addEventListener('pointerdown',e=>{if(e.button!==0)return;drag={x:e.clientX,y:e.clientY,startX:e.clientX,startY:e.clientY};canvas.setPointerCapture(e.pointerId);});
  canvas.addEventListener('pointermove',e=>{if(!drag)return;orbit.theta-=(e.clientX-drag.x)*.005;orbit.phi=Math.max(.3,Math.min(1.35,orbit.phi+(e.clientY-drag.y)*.004));drag.x=e.clientX;drag.y=e.clientY;cameraPose();});
  canvas.addEventListener('pointerup',e=>{
    if(!drag)return;const moved=Math.hypot(e.clientX-drag.startX,e.clientY-drag.startY);drag=null;
    if(moved>6)return;const r=canvas.getBoundingClientRect();pointer.set((e.clientX-r.left)/r.width*2-1,-(e.clientY-r.top)/r.height*2+1);raycaster.setFromCamera(pointer,camera);
    const hit=raycaster.intersectObjects([...entries.values()].flatMap(n=>[n.body,n.core]),false)[0];if(hit)onSelect(hit.object.userData.nodeId);
  });
  canvas.addEventListener('pointercancel',()=>{drag=null;});
  canvas.addEventListener('wheel',e=>{e.preventDefault();orbit.radius=Math.max(14,Math.min(58,orbit.radius*Math.exp(e.deltaY*.001)));cameraPose();},{passive:false});
  canvas.addEventListener('webglcontextlost',e=>{e.preventDefault();$('flow-fallback').hidden=false;$('flow-fallback').textContent='三维显示暂时中断；节点详情和实时记录仍可查看。';});
  canvas.addEventListener('webglcontextrestored',()=>{$('flow-fallback').hidden=true;});
  function updateLabels(){
    const occupied=[];
    // Prefer the selected node when projected labels meet during free rotation.
    const ordered=[...entries].sort(([a],[b])=>Number(b===selected)-Number(a===selected));
    for(const [id,item] of ordered){
      scratch.copy(item.group.position).add(new T.Vector3(0,-.6,0)).project(camera);
      const x=(scratch.x*.5+.5)*width,y=(-scratch.y*.5+.5)*height,label=item.label;
      label.style.left=x+'px';label.style.top=y+'px';
      // display:none has zero dimensions. Keep the measured size while culled;
      // otherwise colliding labels alternate between real and fallback widths.
      if(label.offsetWidth){item.labelWidth=label.offsetWidth;item.labelHeight=label.offsetHeight;}
      const w=item.labelWidth||100,h=item.labelHeight||26,rect={x:x-w/2,y:y,w,h};
      const overlap=occupied.some(r=>rect.x<r.x+r.w+3&&rect.x+rect.w+3>r.x&&rect.y<r.y+r.h+3&&rect.y+rect.h+3>r.y);
      const shown=scratch.z<1&&x>w/2&&x<width-w/2&&y>5&&y<height-45&&!overlap;
      if(label.hidden===shown)label.hidden=!shown;if(shown)occupied.push(rect);
    }
  }
  function draw(now){
    frameId=0;if(disposed||!visible)return;frameId=requestAnimationFrame(draw);
    if(document.hidden||now-lastFrame<32)return;lastFrame=now;
    for(const item of entries.values()){
      const running=item.label.dataset.state==='running'&&canAnimate&&!reduced.matches;
      item.core.position.y=.74+(running?Math.sin(now*.003)*.08:0);item.core.rotation.y=running?now*.0007:Math.PI/4;
      item.ring.material.opacity=running?.6+Math.sin(now*.004)*.3:item.label.dataset.selected==='true'?1:.5;
    }
    particles.forEach(({bead,halo},index)=>{const packet=packets[index],elapsed=packet?(now-packet.started)/1400:2,arc=packet&&arcs.get(packet.id);
      bead.visible=halo.visible=!!arc&&canAnimate&&!reduced.matches&&elapsed<1;
      if(bead.visible){bead.position.copy(arc.curve.getPoint(Math.max(0,elapsed)));halo.position.copy(bead.position);}
    });
    renderer.render(world,camera);updateLabels();
  }
  resize();
  return {update(value,id,animate,motion){
    selected=id;canAnimate=motion;
    const next=value.nodes.map(n=>n.id).join('|');if(next!==signature){signature=next;build(value.nodes);edgeSignature='';defaultOrbit.targetZ=value.layout?.centerZ||1;defaultOrbit.spanZ=(value.layout?.spanZ||13)*1.7;resize();Object.assign(orbit,defaultOrbit);cameraPose();}
    const nextEdges=value.edges.map(e=>e.id).join('|');if(nextEdges!==edgeSignature){edgeSignature=nextEdges;rebuildEdges(value.edges);}
    for(const edge of value.edges){const arc=arcs.get(edge.id);if(arc)arc.edge=edge;}
    for(const node of value.nodes){const item=entries.get(node.id),color=colors[node.status]||colors.pending;
      item.label.dataset.state=node.status;item.label.dataset.selected=String(node.id===selected);item.label.setAttribute('aria-pressed',String(node.id===selected));item.label.setAttribute('aria-label',`${node.title}，${STATUS[node.status]||node.status}`);
      item.ring.material.color.setHex(color);item.core.material.color.setHex(color);item.core.material.emissive.setHex(color);item.core.material.emissiveIntensity=node.status==='running'?.6:.12;
    }
    const event=value.current;
    packets=[];
    for(const [key,arc] of arcs){const current=event&&arc.edge.eventId===event.id&&!arc.edge.planned;const relevant=arc.edge.source===selected||arc.edge.target===selected;
      arc.line.material.opacity=current?.95:relevant?.42:.11;arc.line.material.color.setHex(current?0x97e7e3:arc.edge.planned?0x405570:0x5c96ae);arc.cone.material.opacity=current?1:relevant?.55:.12;
      if(animate&&current&&packets.length<particles.length)packets.push({id:key,started:performance.now()});
    }
  },reset(){Object.assign(orbit,defaultOrbit);cameraPose();},dispose(){disposed=true;cancelAnimationFrame(frameId);ro.disconnect();io.disconnect();free(objects);free(links);floor.geometry.dispose();floor.material.dispose();grid.geometry.dispose();grid.material.dispose();for(const {bead,halo} of particles){bead.geometry.dispose();bead.material.dispose();halo.geometry.dispose();halo.material.dispose();}renderer.dispose();}};
}
