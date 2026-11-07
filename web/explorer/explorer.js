import {parts, childParts, ownerOf, pathToPart, descendantsOf, treePartIds, matchingPartIds, visiblePartIds, groups, iconMarkup} from './parts.mjs';
import {connections,modules,views,clampAmount,modelPose,branchBounds,relatedConnections,arrangeLabels,arrangeBranchLabels} from './framework.mjs';

const $ = id => document.getElementById(id);
const partById = new Map(parts.map((part, index) => [part.id, {...part, index}]));
const state = {selected:'generation', expandedRoot:'generation', tab:'overview', view:'layered', explosion:.6, labels:true, connections:true, rotate:false, hover:null};
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
const listButtons = new Map();
let searchQuery='';
let engine = null;

function element(tag, className, text) {
  const result = document.createElement(tag);
  if (className) result.className = className;
  if (text !== undefined) result.textContent = text;
  return result;
}

function buildPartList() {
  const subtrees=[];
  for (const group of groups) {
    const section = element('section','part-group');
    section.dataset.group = group.id;
    const children=element('div','part-subtree');children.id='children-'+group.id;
    for (const part of parts.filter(p => p.group === group.id)) {
      const button = element('button','part-item'+(part.parent?' child-part':' primary-part'));
      button.type = 'button';
      button.dataset.part = part.id;
      button.dataset.depth=String(part.depth);button.style.setProperty('--depth',String(Math.min(3,Math.max(0,part.depth-1))));
      button.style.setProperty('--part-color', part.color);
      button.setAttribute('aria-pressed', String(state.selected === part.id));
      if(childParts(part.id).length)button.setAttribute('aria-expanded',String(state.expandedRoot===part.group));
      const icon = element('span','part-icon'); icon.innerHTML = iconMarkup(part.icon);
      const copy = element('span','part-item-copy');
      copy.append(element('strong','',part.name));
      button.append(icon,copy);
      button.addEventListener('click',() => selectPart(part.id));
      listButtons.set(part.id,button);
      (part.parent?children:section).append(button);
    }
    subtrees.push(children);
    $('parts-list').append(section);
  }
  subtrees.forEach(tree=>$('parts-list').append(tree));
}

function filterPartList(){
  const matched=matchingPartIds(searchQuery);
  const expanded=treePartIds(state.selected,state.expandedRoot);
  const ancestry=new Set(pathToPart(state.selected).map(part=>part.id));
  for(const group of groups){
    const section=document.querySelector(`[data-group="${group.id}"]`),root=partById.get(group.id);
    for(const child of parts.filter(part=>part.parent&&part.group===group.id)){
      const button=listButtons.get(child.id);button.hidden=!matched.has(child.id)||(!searchQuery&&!expanded.has(child.id));
      button.dataset.ancestor=String(ancestry.has(child.id));
      if(childParts(child.id).length)button.setAttribute('aria-expanded',String(expanded.has(child.id)&&state.expandedRoot===child.group));
    }
    section.hidden=!matched.has(root.id);
    section.dataset.active=String(ownerOf(state.selected)===group.id);
    $('children-'+group.id).hidden=searchQuery ? section.hidden : ![...$('children-'+group.id).querySelectorAll('button')].some(button=>!button.hidden);
    listButtons.get(root.id).hidden=false;
  }
  $('search-empty').hidden=[...document.querySelectorAll('.part-group')].some(section=>!section.hidden);
}

function selectPart(id) {
  if (!partById.has(id)) return;
  const changed=state.selected!==id;
  if(searchQuery&&!matchingPartIds(searchQuery).has(id)){searchQuery='';$('part-search').value='';}
  const root=ownerOf(id),opening=state.expandedRoot!==root;
  state.expandedRoot=root;
  state.selected = id;
  if(opening||!partById.get(id).parent){state.view='layered';if(state.explosion===0)state.explosion=.6;engine?.overview();}
  if(changed)setTab('overview');else updateInspector();
  updateSelection();
  $('announcement').textContent = partById.get(id).name+'；所属分支的 '+descendantsOf(root).length+' 项内部能力已全部展开。';
}

function collapseBranch(){state.expandedRoot=null;state.selected=ownerOf(state.selected);state.view='assembled';setExplosion(0);engine?.overview();}
function toggleBranch(){if(state.expandedRoot)collapseBranch();else{state.expandedRoot=ownerOf(state.selected);state.view='layered';setExplosion(.6);updateInspector();engine?.overview();}}

function updateSelection() {
  $('explode-range').value=String(Math.round(state.explosion*100));$('explode-value').textContent=Math.round(state.explosion*100)+'%';
  $('explode-all').querySelector('span').textContent=state.expandedRoot?'一键收拢':'一键展开';
  for (const [id,button] of listButtons){button.setAttribute('aria-pressed',String(id === state.selected));if(childParts(id).length)button.setAttribute('aria-expanded',String(state.expandedRoot===ownerOf(id)));}
  for (const button of $('label-buttons').children) button.setAttribute('aria-pressed',String(button.dataset.part === state.selected));
  $('unpack-part').replaceChildren(document.createTextNode(state.expandedRoot?'一键收拢整棵分支':'一键展开整棵分支'),element('span','','↗'));
  $('scene-mode').replaceChildren(element('i'),document.createTextNode(state.expandedRoot?partById.get(state.expandedRoot).name+' · '+descendantsOf(state.expandedRoot).length+' 项全部展开':'三大模块视图'));
  $('scene').dataset.branchView=String(!!state.expandedRoot);
  for (const button of document.querySelectorAll('[data-view]')) button.setAttribute('aria-pressed',String(button.dataset.view === state.view));
  $('engine-canvas').dataset.view=state.view;
  $('engine-canvas').dataset.selected=state.selected;
  $('engine-canvas').dataset.explosion=String(Math.round(state.explosion*100));
  $('engine-canvas').dataset.owner=ownerOf(state.selected);
  $('engine-canvas').dataset.path=pathToPart(state.selected).map(part=>part.id).join('/');
  $('engine-canvas').dataset.expandedRoot=state.expandedRoot||'';
  $('engine-canvas').dataset.visibleCount=String(visiblePartIds(state.selected,state.expandedRoot).size);
  filterPartList();
}

function setModelView(view) {
  if (!Object.hasOwn(views,view)) return;
  state.view=view;state.expandedRoot=view==='assembled'?null:ownerOf(state.selected);
  setExplosion(views[view]);
  if (view === 'focused') engine?.focus(state.selected);
  else engine?.overview();
}

function updateInspector() {
  const part = partById.get(state.selected);
  document.querySelector('.inspector').style.setProperty('--accent',part.color);
  $('part-position').textContent = part.parent ? '第 '+part.depth+' 级 '+String(part.sequence).padStart(2,'0')+' / '+String(childParts(part.parent).length).padStart(2,'0') : '主模块 '+String(part.sequence).padStart(2,'0')+' / 03';
  $('part-symbol').innerHTML = iconMarkup(part.icon);
  $('part-title').textContent = part.name;
  $('part-category').textContent = groups.find(g => g.id === part.group).name;
  $('part-english').textContent = part.english;
  $('part-layer').textContent = modules[part.id].layer;
  $('parent-module').hidden=!part.parent;
  $('parent-module').textContent=part.parent?'← 返回 '+partById.get(part.parent).name:'';
  $('parent-module').onclick=()=>selectPart(part.parent);
  const breadcrumbs=$('module-path');breadcrumbs.replaceChildren();breadcrumbs.hidden=!part.parent;
  pathToPart(part.id).slice(0,-1).forEach(ancestor=>{const button=element('button','',ancestor.name);button.type='button';button.addEventListener('click',()=>selectPart(ancestor.id));breadcrumbs.append(button,element('span','','›'));});
  const target = $('detail-content');
  target.replaceChildren();
  if (state.tab === 'overview') {
    const nested=state.expandedRoot===part.group?descendantsOf(part.id):[];
    target.append(element('p','detail-description',part.description));
    if(part.id==='generation')target.append(element('p','sequence-note','所属 Agent、功能与 MCTS 步骤已一次全部展开。点击只查看详情；专业员工在搜索扩展中循环调用，实际执行遵循依赖图。'));
    if(part.id==='candidate-search')target.append(element('p','sequence-note','01 初始化一次；02–05 循环执行；停止后进入 06。未启用搜索时走线性员工流程。'));
    if(part.links?.length){const jumps=element('div','capability-jumps');for(const link of part.links){const button=element('button','',link.label+' ↗');button.type='button';button.addEventListener('click',()=>selectPart(link.id));jumps.append(button);}target.append(jumps);}
    target.append(element('h3','detail-label',nested.length?'完整结构 · 已全部展开':'功能与输出'));
    const children=element('div','model-children full-outline');
    if(nested.length){
      nested.forEach(item=>{const child=element('button');child.type='button';child.dataset.selectPart=item.id;child.style.setProperty('--outline-depth',String(item.depth-part.depth-1));child.append(element('i','step-index',String(item.sequence).padStart(2,'0')),element('strong','',item.name.replace(/^\d{2} /,'')));child.addEventListener('click',()=>selectPart(item.id));children.append(child);});
    }else modules[part.id].children.forEach(name=>{const child=element('span');child.append(element('i'),document.createTextNode(name));children.append(child);});
    target.append(children,element('h3','detail-label','模块职责'));
    const list = element('ul','responsibilities');
    part.duties.forEach(text => list.append(element('li','',text)));
    target.append(list,element('h3','detail-label',part.skills.length ? '关联 SKILL' : '执行方式'));
    const chips = element('div','skill-chips');
    (part.skills.length ? part.skills : [part.id === 'gateway' ? '受控模型传输' : '确定性宿主工具']).forEach(text => chips.append(element('code','',text)));
    target.append(chips);
  } else if (state.tab === 'parameters') {
    const parameters = element('dl','parameters');
    for (const [key,value] of part.parameters) {
      const row = element('div'); row.append(element('dt','',key),element('dd','',value)); parameters.append(row);
    }
    target.append(parameters);
    const files=element('div','model-files');files.append(element('strong','','对应实现'));
    modules[part.id].files.forEach(path=>files.append(element('div','',path)));target.append(files);
  } else {
    const flow = element('div','handoff');
    const steps = [['输入',...part.upstream],['当前模块',part.name,part.short],['输出与交接',...part.downstream]];
    steps.forEach(([label,title,description],index) => {
      const row = element('div','handoff-step'+(index===1?' current':''));
      row.append(element('small','',label),element('strong','',title),element('p','',description));flow.append(row);
    });
    target.append(flow,element('h3','detail-label','关联模块 · 点击跳转'));
    const edges=element('div','connection-list');
    for (const edge of relatedConnections(part.id)) {
      const incoming=edge.to===part.id,other=partById.get(incoming?edge.from:edge.to);
      const button=element('button','connection-item'+(edge.kind==='feedback'?' is-feedback':''));button.type='button';
      button.setAttribute('aria-label','查看 '+other.name);
      button.append(element('small','',incoming?'输入 ←':'输出 →'),element('strong','',other.name),element('span','',edge.label));
      button.addEventListener('click',()=>selectPart(other.id));edges.append(button);
    }
    target.append(edges);
  }
  const note = element('div','technical-note');
  note.append(element('strong','','实现说明'),document.createTextNode(part.note));target.append(note);
  target.scrollTop = 0;
}

function setTab(tab) {
  state.tab = tab;
  for (const button of document.querySelectorAll('[role="tab"]')) {
    const selected = button.dataset.tab === tab;
    button.setAttribute('aria-selected',String(selected));button.tabIndex = selected ? 0 : -1;
  }
  $('detail-content').setAttribute('aria-labelledby','tab-'+tab);
  updateInspector();
}

function setExplosion(value) {
  state.explosion = clampAmount(value);
  if(state.explosion===0){state.expandedRoot=null;state.selected=ownerOf(state.selected);updateInspector();}
  $('explode-range').value = String(Math.round(state.explosion*100));
  $('explode-value').textContent = Math.round(state.explosion*100)+'%';
  $('explode-all').querySelector('span').textContent = state.expandedRoot ? '一键收拢' : '一键展开';
  updateSelection();
}

const directCount=parts.filter(part=>part.depth===1).length,detailCount=parts.filter(part=>part.depth>1).length;
$('capability-summary').textContent='3 个主模块 · '+directCount+' 项直属能力 · '+detailCount+' 项细分';
buildPartList(); updateInspector(); setExplosion(state.explosion);
for (const button of document.querySelectorAll('[data-view]')) button.addEventListener('click',()=>setModelView(button.dataset.view));
$('part-search').addEventListener('input',event => {
  searchQuery=event.target.value.trim().toLocaleLowerCase();filterPartList();
});
document.querySelectorAll('[role="tab"]').forEach((button,index,buttons) => {
  button.addEventListener('click',()=>setTab(button.dataset.tab));
  button.addEventListener('keydown',event => {
    if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length-1 : (index+(event.key==='ArrowRight'?1:-1)+buttons.length)%buttons.length;
    buttons[next].focus();setTab(buttons[next].dataset.tab);
  });
});
$('unpack-part').addEventListener('click',toggleBranch);
$('explode-range').addEventListener('input',event=>{state.view=Number(event.target.value)===0?'assembled':'layered';if(Number(event.target.value)>0)state.expandedRoot=ownerOf(state.selected);setExplosion(Number(event.target.value)/100);engine?.overview();});
$('explode-all').addEventListener('click',toggleBranch);
$('toggle-connections').addEventListener('click',()=>{state.connections=!state.connections;$('toggle-connections').setAttribute('aria-pressed',String(state.connections));});
$('toggle-labels').addEventListener('click',()=>{
  state.labels=!state.labels;$('toggle-labels').setAttribute('aria-pressed',String(state.labels));$('scene-labels').hidden=!state.labels;
});
$('toggle-rotate').addEventListener('click',()=>{
  state.rotate=!state.rotate;$('toggle-rotate').setAttribute('aria-pressed',String(state.rotate));
});
$('zoom-in').addEventListener('click',()=>engine?.zoom(.85));
$('zoom-out').addEventListener('click',()=>engine?.zoom(1.15));
$('reset-view').addEventListener('click',()=>engine?.reset());
$('retry-webgl').addEventListener('click',()=>location.reload());
$('fullscreen').addEventListener('click',async()=>{
  try {
    if (document.fullscreenElement) await document.exitFullscreen();
    else if (document.querySelector('.viewport-panel').requestFullscreen) await document.querySelector('.viewport-panel').requestFullscreen();
    else $('announcement').textContent='当前浏览器不支持全屏，请使用浏览器全屏功能。';
  } catch { $('announcement').textContent='浏览器没有允许全屏，可继续在当前视图操作。'; }
});
document.addEventListener('fullscreenchange',()=>{
  $('fullscreen').setAttribute('aria-label',document.fullscreenElement?'退出全屏':'全屏查看三维模型');
  $('fullscreen').title=document.fullscreenElement?'退出全屏':'全屏';
});
const guide = $('guide-dialog');
$('guide-open').addEventListener('click',()=>guide.showModal());
for (const id of ['guide-close','guide-start']) $(id).addEventListener('click',()=>guide.close());
guide.addEventListener('click',event=>{if(event.target===guide){const r=guide.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)guide.close();}});

function showFallback(message) {
  $('scene-loading').hidden=true;$('scene-fallback').hidden=false;$('scene-labels').hidden=true;
  if(message)$('fallback-description').textContent=message;
  for(const id of ['explode-all','explode-range','toggle-labels','toggle-connections','toggle-rotate','zoom-in','zoom-out','reset-view','fullscreen','view-assembled','view-layered','view-focused'])$(id).disabled=true;
  $('unpack-part').disabled=true;
  $('scene-mode').textContent='部件说明模式';
}

try {
  const THREE = await import('../lab/vendor/three.module.min.js');
  engine = createEngine(THREE);
  console.info('TESTFIX_MODEL_READY');
} catch (error) {
  console.error('Three.js scene initialization failed',error);
  showFallback('三维引擎未能启动。请使用支持 WebGL 2 的浏览器；部件清单和全部详情仍然可用。');
}

function createEngine(T) {
  const host=$('scene'),canvas=$('engine-canvas');
  const renderer=new T.WebGLRenderer({canvas,antialias:true,alpha:true,powerPreference:'high-performance'});
  renderer.setPixelRatio(Math.min(window.devicePixelRatio||1,1.75));
  renderer.outputColorSpace=T.SRGBColorSpace;renderer.toneMapping=T.ACESFilmicToneMapping;renderer.toneMappingExposure=1.18;
  const scene=new T.Scene();scene.fog=new T.FogExp2(0x0b121e,.024);
  const camera=new T.PerspectiveCamera(36,1,.1,100);
  const home={theta:.70,phi:.93,radius:14.7,x:0,y:1.38,z:0};
  const orbit={...home},desired={...home};
  const raycaster=new T.Raycaster(),pointer=new T.Vector2(),hitMeshes=[],entries=new Map(),allGeometries=new Set(),allMaterials=new Set(),textures=new Set();
  let width=1,height=1,failed=false,frameId=0,last=performance.now(),elapsed=0,explosion=0,layoutScaleX=1;
  const geometryCache=new Map();
  const registerGeometry=g=>{allGeometries.add(g);return g;};
  const standard=(color,extra={})=>{const m=new T.MeshStandardMaterial({color,roughness:.4,metalness:.55,...extra});allMaterials.add(m);return m;};
  const basic=(color,extra={})=>{const m=new T.MeshBasicMaterial({color,...extra});allMaterials.add(m);return m;};
  const boxGeometry=(w,h,d)=>{
    const key=[w,h,d].join(':');if(geometryCache.has(key))return geometryCache.get(key);
    const r=Math.min(.09,w*.12,d*.12),s=new T.Shape(),x=-w/2,y=-d/2;
    s.moveTo(x+r,y);s.lineTo(x+w-r,y);s.quadraticCurveTo(x+w,y,x+w,y+r);s.lineTo(x+w,y+d-r);s.quadraticCurveTo(x+w,y+d,x+w-r,y+d);s.lineTo(x+r,y+d);s.quadraticCurveTo(x,y+d,x,y+d-r);s.lineTo(x,y+r);s.quadraticCurveTo(x,y,x+r,y);
    const g=registerGeometry(new T.ExtrudeGeometry(s,{depth:h,bevelEnabled:true,bevelSize:.018,bevelThickness:.018,bevelSegments:1,steps:1,curveSegments:3}));
    g.rotateX(-Math.PI/2);g.translate(0,-h/2,0);geometryCache.set(key,g);return g;
  };
  function mesh(g,m,parent,x=0,y=0,z=0,partId=null){const o=new T.Mesh(g,m);o.position.set(x,y,z);parent.add(o);if(partId){o.userData.partId=partId;hitMeshes.push(o);}return o;}
  const box=(w,h,d,m,parent,x=0,y=0,z=0,id=null)=>mesh(boxGeometry(w,h,d),m,parent,x,y,z,id);
  function line(points,color,opacity,parent){const geometry=registerGeometry(new T.BufferGeometry().setFromPoints(points.map(p=>new T.Vector3(...p))));const material=new T.LineBasicMaterial({color,transparent:true,opacity});allMaterials.add(material);const object=new T.Line(geometry,material);parent.add(object);return object;}
  function ring(radius,tube,m,parent,y=0){const object=mesh(registerGeometry(new T.TorusGeometry(radius,tube,6,80)),m,parent,0,y);object.rotation.x=Math.PI/2;return object;}
  const shadowCanvas=document.createElement('canvas');shadowCanvas.width=shadowCanvas.height=128;
  const shadowContext=shadowCanvas.getContext('2d'),shadowGradient=shadowContext.createRadialGradient(64,64,0,64,64,64);shadowGradient.addColorStop(0,'rgba(0,0,0,0.7)');shadowGradient.addColorStop(1,'rgba(0,0,0,0)');shadowContext.fillStyle=shadowGradient;shadowContext.fillRect(0,0,128,128);
  const shadowTexture=new T.CanvasTexture(shadowCanvas);textures.add(shadowTexture);
  const shadow=mesh(registerGeometry(new T.PlaneGeometry(11,9)),basic(0x000000,{map:shadowTexture,transparent:true,opacity:.65,depthWrite:false}),scene,0,-.49);shadow.rotation.x=-Math.PI/2;
  const grid=new T.GridHelper(38,60,0x2b465b,0x1b2c40);grid.position.y=-.55;grid.material.transparent=true;grid.material.opacity=.21;scene.add(grid);allGeometries.add(grid.geometry);allMaterials.add(grid.material);
  for(const radius of [4.7,5.1,6.8]){const r=mesh(registerGeometry(new T.RingGeometry(radius,radius+.009,128)),basic(0x53798b,{transparent:true,opacity:.15,side:T.DoubleSide}),scene,0,-.52);r.rotation.x=-Math.PI/2;}
  for(let i=0;i<60;i++){const a=i/60*Math.PI*2,r=5.1,l=i%5===0?.12:.05;line([[Math.cos(a)*r,-.51,Math.sin(a)*r],[Math.cos(a)*(r+l),-.51,Math.sin(a)*(r+l)]],0x5e8499,.28,scene);}
  scene.add(new T.HemisphereLight(0xc0e2f5,0x283247,2.1));
  const keyLight=new T.DirectionalLight(0xc2eaf2,3.5);keyLight.position.set(3,8,6);scene.add(keyLight);
  const rimLight=new T.DirectionalLight(0x8b9ddd,2.1);rimLight.position.set(-4,5,-5);scene.add(rimLight);
  const fill=new T.PointLight(0x6adbe9,30,18,2);fill.position.set(-4,4,5);scene.add(fill);
  const warm=new T.PointLight(0xd2b6e9,18,15,2);warm.position.set(4,4,-4);scene.add(warm);

  // A shared platform is visual support, not a fourth architecture module.
  const platform=new T.Group();platform.position.y=-.25;scene.add(platform);
  const platformBody=standard(0x1d2b42),platformTrim=standard(0x526c89),platformLight=basic(0x739eae,{transparent:true,opacity:.55});
  box(7.8,.22,5.0,platformBody,platform);box(8.0,.10,5.2,platformTrim,platform,0,-.19);
  for(const z of [-2.48,2.48])box(7.4,.025,.02,platformLight,platform,0,.03,z);
  for(const x of [-3.65,3.65])for(const z of [-2.25,2.25])mesh(registerGeometry(new T.CylinderGeometry(.06,.06,.07,10)),platformTrim,platform,x,.15,z);
  for(const x of [-1,1])line([[0,.13,-.8],[x*1.2,.13,-.8],[x*1.2,.13,.85],[x*2.1,.13,.85]],0x526d8e,.4,platform);

  function glowTexture(){const c=document.createElement('canvas');c.width=c.height=128;const ctx=c.getContext('2d'),g=ctx.createRadialGradient(64,64,0,64,64,64);g.addColorStop(0,'rgba(255,255,255,0.42)');g.addColorStop(.25,'rgba(255,255,255,0.13)');g.addColorStop(1,'rgba(255,255,255,0)');ctx.fillStyle=g;ctx.fillRect(0,0,128,128);const t=new T.CanvasTexture(c);textures.add(t);return t;}
  const auraTexture=glowTexture();
  function topMark(text,color,parent,x,y,z,size=.6){const c=document.createElement('canvas');c.width=256;c.height=128;const ctx=c.getContext('2d');ctx.font='500 57px Consolas, monospace';ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillStyle=color;ctx.fillText(text,128,64);const t=new T.CanvasTexture(c);t.colorSpace=T.SRGBColorSpace;textures.add(t);const p=mesh(registerGeometry(new T.PlaneGeometry(size,size*.5)),basic(0xffffff,{map:t,transparent:true,depthWrite:false,toneMapped:false}),parent,x,y,z);p.rotation.x=-Math.PI/2;return p;}
  function partMaterials(part){const body=standard(0x26364b,{emissive:part.color,emissiveIntensity:0});const dark=standard(0x121e2d,{roughness:.55,metalness:.5});const metal=standard(0x6e8a9e,{roughness:.25,metalness:.72});const accent=standard(part.color,{emissive:part.color,emissiveIntensity:.55,metalness:.18,roughness:.3});const glass=new T.MeshPhysicalMaterial({color:part.color,roughness:.2,metalness:.2,transparent:true,opacity:.16,depthWrite:false,side:T.DoubleSide});allMaterials.add(glass);return {body,dark,metal,accent,glass};}
  for(const part of parts){
    const group=new T.Group();group.position.fromArray(part.base);scene.add(group);
    const mats=partMaterials(part),entry={part,group,mats,unpack:0,movable:[],height:.85};entries.set(part.id,entry);
    const addBox=(w,h,d,material,x=0,y=0,z=0,parent=group)=>box(w,h,d,material,parent,x,y,z,part.id);
    const move=(object,offset)=>{entry.movable.push({object,start:object.position.clone(),offset:new T.Vector3(...offset)});return object;};
    const glowMaterial=basic(part.color,{map:auraTexture,transparent:true,opacity:.18,blending:T.AdditiveBlending,depthWrite:false,side:T.DoubleSide});
    const aura=mesh(registerGeometry(new T.PlaneGeometry(part.geometry==='base'?8:3.5,part.geometry==='base'?7:3.5)),glowMaterial,group,0,-.13);aura.rotation.x=-Math.PI/2;entry.aura=aura;
    if(part.geometry==='base'){
      addBox(6.6,.22,5.3,mats.dark,0,-.11);addBox(6.85,.12,5.55,mats.body,0,-.27);
      const board=move(new T.Group(),[0,.45,0]);group.add(board);addBox(6.35,.09,5.03,mats.body,0,.065,0,board);
      for(const x of [-3.05,3.05])for(const z of [-2.4,2.4]){mesh(registerGeometry(new T.CylinderGeometry(.065,.065,.07,10)),mats.metal,group,x,.15,z,part.id);}
      for(const side of [-1,1]){addBox(5.6,.025,.024,mats.accent,0,-.13,side*2.68);for(let i=0;i<12;i++)addBox(.16,.075,.025,mats.metal,-2.5+i*.45,-.25,side*2.80);}
      for(const x of [-1,1])for(const z of [-1,1]){line([[0,.125,0],[x*.7,.125,0],[x*.7,.125,z*1.2],[x*2.7,.125,z*1.2]],0x4e8293,.5,board);line([[x*.2,.125,0],[x*.9,.125,0],[x*.9,.125,z*1.45],[x*2.5,.125,z*1.45]],0x456377,.35,board);}
      topMark('T E S T F I X', '#81a7bd',board,0,.13,2.17,1.55);entry.height=.16;
    }else if(part.geometry==='controller'){
      addBox(2.02,.16,1.93,mats.dark,0,0);ring(1.33,.016,mats.accent,group,-.08);
      const chip=move(new T.Group(),[0,.35,0]);group.add(chip);addBox(1.56,.62,1.48,mats.body,0,.43,0,chip);
      for(const sign of [-1,1])for(let i=0;i<9;i++){addBox(.035,.12,.12,mats.metal,sign*.84,.28,-.63+i*.16,chip);addBox(.12,.12,.035,mats.metal,-.63+i*.16,.28,sign*.81,chip);}
      for(const z of [-.77,.77]){addBox(1.42,.024,.018,mats.accent,0,.59,z,chip);for(let i=0;i<5;i++)addBox(.17,.025,.028,mats.metal,-.5+i*.25,.4,z,chip);}
      const lid=move(new T.Group(),[0,.96,0]);group.add(lid);addBox(1.9,.085,1.83,mats.glass,0,.85,0,lid);addBox(1.03,.055,1.0,mats.dark,0,.89,0,lid);topMark('CA',part.color,lid,0,.95,0,.98);
      const corners=[[-.82,-.79],[-.82,.79],[.82,-.79],[.82,.79]];for(const [x,z]of corners)addBox(.15,.028,.15,mats.accent,x,.902,z,lid);
      entry.height=.96;
    }else if(part.geometry==='scheduler'){
      const frame=move(new T.Group(),[0,.22,0]);group.add(frame);
      for(const z of [-1.80,1.80])addBox(4.4,.105,.13,mats.metal,0,0,z,frame);
      for(const x of [-2.16,2.16])addBox(.13,.105,3.6,mats.metal,x,0,0,frame);
      addBox(1.45,.105,1.4,mats.dark,0,0,0,frame);
      const track=move(new T.Group(),[0,.55,0]);group.add(track);ring(1.62,.026,mats.accent,track,.04);ring(1.77,.013,mats.metal,track,.04);
      for(let i=0;i<8;i++){const a=i/8*Math.PI*2;addBox(.13,.035,.13,mats.accent,Math.cos(a)*1.77,.04,Math.sin(a)*1.77,track);}
      for(const x of [-1.95,1.95])for(const z of [-1.56,1.56]){addBox(.27,.08,.27,mats.body,x,.1,z,frame);addBox(.1,.012,.1,mats.accent,x,.15,z,frame);line([[x,.04,z],[x*.63,.04,z],[x*.63,.04,0]],0x6c88b4,.5,frame);}
      topMark('ILP',part.color,frame,0,.079,0,.9);entry.height=.21;
    }else if(part.geometry==='module'||part.geometry==='submodule'){
      addBox(1.48,.19,1.35,mats.dark,0,0);addBox(1.38,.29,1.25,mats.body,0,.20);
      for(const sign of [-1,1])for(let i=0;i<6;i++)addBox(.045,.07,.10,mats.metal,sign*.77,.07,-.5+i*.2);
      const chip=move(new T.Group(),[0,.26,0]);group.add(chip);addBox(.81,.08,.75,mats.dark,0,.39,0,chip);
      topMark(part.mark,part.color,chip,0,.46,0,.65);
      for(const z of [-.52,.52])addBox(1.07,.016,.026,mats.accent,0,.365,z);
      const cover=move(new T.Group(),[0,.64,0]);group.add(cover);addBox(1.49,.05,1.35,mats.glass,0,.49,0,cover);
      for(const x of [-.63,.63])addBox(.025,.018,1.12,mats.metal,x,.524,0,cover);
      for(let i=0;i<3;i++)addBox(.11,.02,.06,mats.accent,-.18+i*.18,.53,.49,cover);
      entry.height=.6;
    }else if(part.geometry==='gateway'){
      mesh(registerGeometry(new T.CylinderGeometry(.50,.57,.17,6)),mats.dark,group,0,.04,0,part.id);
      const core=move(new T.Group(),[0,.4,0]);group.add(core);
      mesh(registerGeometry(new T.CylinderGeometry(.33,.33,.55,6)),mats.body,core,0,.4,0,part.id);
      for(const y of [.18,.38,.6])ring(.38,.018,mats.accent,core,y);
      const cap=move(new T.Group(),[0,.7,0]);group.add(cap);mesh(registerGeometry(new T.OctahedronGeometry(.28)),mats.glass,cap,0,.92,0,part.id);ring(.57,.012,mats.metal,cap,.87);
      entry.height=1.12;
    }
    if(part.parent)group.scale.setScalar(.43);else if(part.geometry==='module')group.scale.setScalar(1.2);
    entry.anchor=new T.Object3D();entry.anchor.position.y=entry.height;group.add(entry.anchor);
  }

  const arrowGeometry=registerGeometry(new T.ConeGeometry(.045,.13,5));
  const edges=connections.map(edge=>{
    const color=edge.kind==='feedback'?0xc5ae88:0x79b9c7;
    const g=registerGeometry(new T.BufferGeometry());g.setAttribute('position',new T.BufferAttribute(new Float32Array(96),3));const m=new T.LineBasicMaterial({color,transparent:true,opacity:.28,depthWrite:false});allMaterials.add(m);const o=new T.Line(g,m);scene.add(o);
    const arrow=mesh(arrowGeometry,basic(color,{transparent:true,opacity:.6,depthWrite:false}),scene);
    return{...edge,object:o,arrow};
  });
  const labels=parts.map(part=>{
    const button=element('button','scene-label');button.type='button';button.dataset.part=part.id;button.dataset.depth=String(part.depth);button.title=part.name;button.style.setProperty('--part-color',part.color);button.setAttribute('aria-pressed',String(part.id===state.selected));
    button.append(element('strong','',part.name));button.addEventListener('click',()=>selectPart(part.id));$('label-buttons').append(button);
    const path=document.createElementNS('http://www.w3.org/2000/svg','polyline');path.setAttribute('fill','none');path.classList.add('label-line');$('label-lines').append(path);
    const dot=document.createElementNS('http://www.w3.org/2000/svg','circle');dot.setAttribute('r','2');dot.classList.add('label-point');$('label-lines').append(dot);
    return{part,button,path,dot};
  });
  const scratch=new T.Vector3(),start=new T.Vector3(),end=new T.Vector3(),arrowPoint=new T.Vector3(),direction=new T.Vector3(),axis=new T.Vector3(0,1,0);
  function updateLabels(){
    if(!state.labels)return;
    const shown=visiblePartIds(state.selected,state.expandedRoot);
    const projected=labels.filter(label=>shown.has(label.part.id)).map(label=>{
      entries.get(label.part.id).anchor.getWorldPosition(scratch);scratch.project(camera);
      return{id:label.part.id,depth:label.part.depth,px:(scratch.x*.5+.5)*width,py:(-scratch.y*.5+.5)*height,side:label.part.label[0]};
    });
    const placed=new Map((state.expandedRoot?arrangeBranchLabels(projected,width,height,state.expandedRoot):arrangeLabels(projected,width,height,state.selected)).map(item=>[item.id,item]));
    for(const label of labels){
      const box=placed.get(label.part.id);label.button.hidden=!box;label.path.style.display=box?'':'none';label.dot.style.display=box?'':'none';
      if(!box)continue;
      const {x,y,px,py,width:labelWidth,height:labelHeight}=box;
      label.button.style.width=labelWidth+'px';
      label.button.style.height=state.expandedRoot?labelHeight+'px':'';
      label.button.style.transform=`translate(${Math.round(x)}px, ${Math.round(y)}px)`;
      const ax=px>x+labelWidth/2?x+labelWidth:x,ay=y+labelHeight/2;
      label.path.setAttribute('points',`${px.toFixed(1)},${py.toFixed(1)} ${(ax+(px-ax)*.24).toFixed(1)},${ay.toFixed(1)} ${ax.toFixed(1)},${ay.toFixed(1)}`);
      label.dot.setAttribute('cx',px.toFixed(1));label.dot.setAttribute('cy',py.toFixed(1));
      label.path.classList.toggle('selected',state.selected===label.part.id);label.dot.classList.toggle('selected',state.selected===label.part.id);
    }
  }
  function resize(){const rect=host.getBoundingClientRect();width=Math.max(1,rect.width);height=Math.max(1,rect.height);renderer.setSize(width,height,false);camera.aspect=width/height;camera.updateProjectionMatrix();overview();}
  const observer=new ResizeObserver(resize);observer.observe(host);resize();
  function updateCamera(){
    const adaptive=width<450?1.18:width<650?1.12:1;
    const r=(orbit.radius+(state.expandedRoot?0:explosion*3.1))*adaptive;
    const targetY=orbit.y+(state.expandedRoot?0:explosion*.7);
    camera.position.set(orbit.x+r*Math.sin(orbit.phi)*Math.sin(orbit.theta),targetY+r*Math.cos(orbit.phi),orbit.z+r*Math.sin(orbit.phi)*Math.cos(orbit.theta));camera.lookAt(orbit.x,targetY,orbit.z);
  }
  function pick(event){const rect=canvas.getBoundingClientRect();pointer.set((event.clientX-rect.left)/rect.width*2-1,-(event.clientY-rect.top)/rect.height*2+1);raycaster.setFromCamera(pointer,camera);const shown=visiblePartIds(state.selected,state.expandedRoot);return raycaster.intersectObjects(hitMeshes.filter(mesh=>shown.has(mesh.userData.partId)),false)[0]?.object.userData.partId||null;}
  const pointers=new Map();let gesture=null,hoverStamp=0;
  canvas.addEventListener('contextmenu',event=>event.preventDefault());
  canvas.addEventListener('pointerdown',event=>{
    if(event.button!==0&&event.button!==2)return;
    canvas.focus({preventScroll:true});canvas.setPointerCapture(event.pointerId);pointers.set(event.pointerId,{x:event.clientX,y:event.clientY});
    gesture={x:event.clientX,y:event.clientY,startX:event.clientX,startY:event.clientY,moved:false,pan:event.shiftKey||event.button===2,distance:0};
    if(pointers.size===2){const [a,b]=[...pointers.values()];gesture.distance=Math.hypot(a.x-b.x,a.y-b.y);gesture.moved=true;}
    canvas.classList.add('dragging');state.hover=null;
  });
  canvas.addEventListener('pointermove',event=>{
    if(!pointers.has(event.pointerId)){
      if(performance.now()-hoverStamp>60){state.hover=pick(event);canvas.classList.toggle('hovering',!!state.hover);hoverStamp=performance.now();}return;
    }
    pointers.set(event.pointerId,{x:event.clientX,y:event.clientY});
    if(pointers.size===2){const[a,b]=[...pointers.values()],distance=Math.hypot(a.x-b.x,a.y-b.y);if(gesture.distance>0)desired.radius=T.MathUtils.clamp(desired.radius*gesture.distance/Math.max(1,distance),7,state.expandedRoot?110:25);gesture.distance=distance;gesture.moved=true;return;}
    const dx=event.clientX-gesture.x,dy=event.clientY-gesture.y;
    if(Math.hypot(event.clientX-gesture.startX,event.clientY-gesture.startY)>5)gesture.moved=true;
    if(gesture.pan){const scale=desired.radius*.0014;desired.x+=(-dx*Math.cos(desired.theta)+dy*Math.sin(desired.theta))*scale;desired.y+=dy*scale*.7;desired.z+=(dx*Math.sin(desired.theta)+dy*Math.cos(desired.theta))*scale;desired.x=T.MathUtils.clamp(desired.x,-30,30);desired.z=T.MathUtils.clamp(desired.z,-30,30);}
    else{desired.theta-=dx*.006;desired.phi=T.MathUtils.clamp(desired.phi-dy*.005,.16,Math.PI-.16);}
    gesture.x=event.clientX;gesture.y=event.clientY;
  });
  function finishPointer(event,cancelled=false){
    if(!pointers.has(event.pointerId))return;
    const click=!cancelled&&gesture&&!gesture.moved&&pointers.size===1&&event.button===0;
    pointers.delete(event.pointerId);if(canvas.hasPointerCapture(event.pointerId))canvas.releasePointerCapture(event.pointerId);
    if(click){const id=pick(event);if(id)selectPart(id);}
    if(pointers.size===0){gesture=null;canvas.classList.remove('dragging');}else{const[a]=[...pointers.values()];gesture={x:a.x,y:a.y,startX:a.x,startY:a.y,moved:true,pan:false,distance:0};}
  }
  canvas.addEventListener('pointerup',event=>finishPointer(event));canvas.addEventListener('pointercancel',event=>finishPointer(event,true));
  canvas.addEventListener('dblclick',event=>{const id=pick(event);if(id){state.selected=id;updateInspector();setModelView('focused');}});
  canvas.addEventListener('lostpointercapture',event=>finishPointer(event,true));
  canvas.addEventListener('pointerleave',()=>{state.hover=null;canvas.classList.remove('hovering');});
  canvas.addEventListener('wheel',event=>{event.preventDefault();desired.radius=T.MathUtils.clamp(desired.radius*Math.exp(T.MathUtils.clamp(event.deltaY,-150,150)*.0015),7,state.expandedRoot?110:25);},{passive:false});
  canvas.addEventListener('keydown',event=>{
    if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','+','=','-','_','Home','Enter','Escape'].includes(event.key))return;
    event.preventDefault();
    if(event.key==='ArrowLeft')desired.theta-=.12;if(event.key==='ArrowRight')desired.theta+=.12;
    if(event.key==='ArrowUp')desired.phi=Math.max(.16,desired.phi-.10);if(event.key==='ArrowDown')desired.phi=Math.min(Math.PI-.16,desired.phi+.10);
    if(['+','='].includes(event.key))zoom(.9);if(['-','_'].includes(event.key))zoom(1.1);
    if(event.key==='Home')reset();if(event.key==='Enter')selectPart(state.selected);if(event.key==='Escape')collapseBranch();
  });
  function zoom(factor){desired.radius=T.MathUtils.clamp(desired.radius*factor,7,state.expandedRoot?110:25);}
  function overview(){
    if(!state.expandedRoot){layoutScaleX=1;Object.assign(desired,home);return;}
    const bounds=branchBounds(state.expandedRoot),spanX=bounds.max[0]-bounds.min[0],spanY=bounds.max[1]-bounds.min[1];
    const visibleFraction=Math.max(.35,(height-150)/Math.max(1,height)),tangent=Math.tan(camera.fov*Math.PI/360);
    layoutScaleX=Math.max(1,Math.min(3.5,(camera.aspect/visibleFraction)/(spanX/spanY)));
    const radius=Math.max(spanX*layoutScaleX/(2*tangent*Math.max(.5,camera.aspect)),spanY/(2*tangent*visibleFraction))*1.10;
    Object.assign(desired,{theta:0,phi:1.28,radius:Math.min(105,radius),x:(bounds.max[0]+bounds.min[0])/2,y:(bounds.max[1]+bounds.min[1])/2+spanY*(1-visibleFraction)/(2*visibleFraction),z:0});
  }
  function focus(id){const entry=entries.get(id);if(!entry)return;desired.x=entry.group.position.x;desired.z=entry.group.position.z;desired.y=entry.group.position.y+.5;desired.radius=entry.part.parent?7:10;}
  function reset(){state.rotate=false;$('toggle-rotate').setAttribute('aria-pressed','false');setModelView('layered');}
  canvas.addEventListener('webglcontextlost',event=>{event.preventDefault();failed=true;cancelAnimationFrame(frameId);showFallback('浏览器暂停了三维图形上下文。点击重新加载可尝试恢复，部件详情仍可查看。');});
  function animate(now){
    if(failed)return;frameId=requestAnimationFrame(animate);const dt=Math.min((now-last)/1000,.05);last=now;
    if(document.hidden)return;elapsed+=dt;
    const blend=reducedMotion.matches?1:1-Math.exp(-dt*7),quick=reducedMotion.matches?1:1-Math.exp(-dt*9);
    if(state.rotate&&pointers.size===0)desired.theta+=dt*.12;
    for(const key of Object.keys(home))orbit[key]+=(desired[key]-orbit[key])*quick;
    explosion+=(state.explosion-explosion)*blend;
    const shown=visiblePartIds(state.selected,state.expandedRoot);
    platform.visible=grid.visible=shadow.visible=!state.expandedRoot;scene.fog.density=state.expandedRoot ? .006 : .024;
    for(const entry of entries.values()){
      entry.group.visible=shown.has(entry.part.id);
      const size=entry.part.parent?(state.expandedRoot&&entry.part.depth===1?.66:.43):(entry.part.geometry==='module'?1.2:1)*(state.expandedRoot?1.8:1);
      entry.group.scale.lerp(scratch.setScalar(size),blend);
      entry.unpack+=((state.expandedRoot===entry.part.group?1:0)-entry.unpack)*blend;
      entry.group.position.lerp(scratch.fromArray(modelPose(entry.part,explosion,entry.unpack,state.expandedRoot,layoutScaleX)),blend);
      for(const item of entry.movable)item.object.position.copy(item.start).addScaledVector(item.offset,entry.unpack*.9+explosion*.28);
      const active=state.selected===entry.part.id||(!entry.part.parent&&ownerOf(state.selected)===entry.part.id),hovered=state.hover===entry.part.id;
      entry.mats.body.emissiveIntensity=active?.13:hovered?.07:.005;
      entry.mats.accent.emissiveIntensity=active?1.25: hovered?.95:.45;
      entry.mats.glass.opacity=active?.25:.14;
      entry.aura.material.opacity=(active?.38:.14)+entry.unpack*.12;
    }
    for(const edge of edges){
      edge.object.visible=edge.arrow.visible=state.connections&&shown.has(edge.from)&&shown.has(edge.to);
      if(!edge.object.visible)continue;
      start.copy(entries.get(edge.from).group.position);start.y+=.12;end.copy(entries.get(edge.to).group.position);end.y+=.1;
      const array=edge.object.geometry.attributes.position.array;
      const bend=edge.kind==='feedback'?.18:-.10;
      function point(t,out){const wave=Math.sin(t*Math.PI);return out.set(T.MathUtils.lerp(start.x,end.x,t)+wave*bend,T.MathUtils.lerp(start.y,end.y,t)+wave*.28,T.MathUtils.lerp(start.z,end.z,t)-wave*bend);}
      for(let i=0;i<32;i++){point(i/31,scratch);scratch.toArray(array,i*3);}
      edge.object.geometry.attributes.position.needsUpdate=true;edge.object.geometry.computeBoundingSphere();
      const related=state.selected===edge.from||state.selected===edge.to;edge.object.material.opacity=related?.58:.10;
      point(.82,edge.arrow.position);point(.84,arrowPoint);direction.subVectors(arrowPoint,edge.arrow.position).normalize();edge.arrow.quaternion.setFromUnitVectors(axis,direction);edge.arrow.material.opacity=related?.85:.17;
    }
    updateCamera();renderer.render(scene,camera);updateLabels();
  }
  updateCamera();renderer.render(scene,camera);$('scene-loading').hidden=true;
  frameId=requestAnimationFrame(animate);
  window.addEventListener('pagehide',event=>{if(event.persisted)return;failed=true;cancelAnimationFrame(frameId);observer.disconnect();renderer.dispose();allGeometries.forEach(g=>g.dispose());allMaterials.forEach(m=>m.dispose());textures.forEach(t=>t.dispose());});
  return{zoom,reset,focus,overview};
}
