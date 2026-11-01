import {parts,primaryParts,childParts,descendantsOf,groups} from './parts.mjs';
// Three primary modules own every other capability; edges are architecture, not run status.
export const modelVersion = '04';
export const views = { assembled: 0, layered: .6, focused: .6 };
const rootFiles={
  controller:['testfix/project_workflow.py','testfix/team_planning.py','testfix/team_runtime.py','testfix/scheduling.py','testfix/search_workflow.py','testfix/agentarts_runtime.py'],
  generation:['testfix/team_runtime.py','testfix/specialists.py','testfix/specialist_library/catalog.json','testfix/skill_library/repair-code/SKILL.md'],
  tests:['testfix/test_quality.py','testfix/wctp_runtime.py','testfix/product_campaign.py','testfix/project_tools.py','testfix/hardware.py','testfix/reports.py'],
};
export const connections = [
  {from:'controller',to:'generation',label:'分配实现任务、专业与修改范围',kind:'control'},
  {from:'controller',to:'tests',label:'下发测试目标、验收要求与预算',kind:'control'},
  {from:'generation',to:'tests',label:'候选代码、补丁与评审回执',kind:'handoff'},
  {from:'tests',to:'controller',label:'公开失败反馈 → 二次分配',kind:'feedback'},
  ...parts.filter(part=>part.parent).map(part=>({from:part.parent,to:part.id,label:part.short,kind:'internal'})),
  {from:'search',to:'candidate-search',label:'主控配置；在生成补全中执行候选搜索',kind:'control'},
  {from:'uct-select',to:'branch-expand',label:'选中父节点后扩展候选',kind:'handoff'},
  {from:'branch-expand',to:'public-score',label:'实际员工修改与固定候选',kind:'handoff'},
  {from:'public-score',to:'backpropagate',label:'有效公开奖励',kind:'handoff'},
  {from:'backpropagate',to:'uct-select',label:'更新统计后进入下一轮选择',kind:'feedback'},
  {from:'branch-expand',to:'specialist-team',label:'每次扩展调用专业员工，不是独立的后置阶段',kind:'handoff'},
  {from:'verify',to:'controller',label:'独立验收最终结论；不回流验收内容',kind:'delivery'},
  {from:'delivery',to:'controller',label:'报告、工程包与证据交付',kind:'delivery'},
];
export const modules=Object.fromEntries(parts.map(part=>[part.id,{
  layer:String(groups.findIndex(g=>g.id===part.group)+1).padStart(2,'0')+' / '+(part.parent?'第 '+part.depth+' 级内部能力':'主模块'),
  children:childParts(part.id).length?childParts(part.id).map(child=>child.name):part.duties,
  files:part.files||rootFiles[part.id],
}]));

export function clampAmount(value) {
  const amount = Number(value);
  return Number.isFinite(amount) ? Math.max(0,Math.min(1,amount)) : 0;
}

const branchLayouts=new Map();
export function branchLayout(rootId){
  if(branchLayouts.has(rootId))return branchLayouts.get(rootId);
  const layout=new Map(),other=primaryParts.filter(root=>root.id!==rootId);
  layout.set(rootId,[0,4,0]);other.forEach((root,i)=>layout.set(root.id,[i===0?-6.5:6.5,4,-1.5]));
  const stages=childParts(rootId);
  stages.forEach((stage,index)=>{
    const x=(index-(stages.length-1)/2)*5;
    layout.set(stage.id,[x,1.6,0]);
    descendantsOf(stage.id).forEach((part,slot)=>layout.set(part.id,[x+(slot%2?1.35:-1.35),-.3-Math.floor(slot/2)*1.9,(part.depth-1)*.28]));
  });
  branchLayouts.set(rootId,layout);return layout;
}

export function branchBounds(rootId){
  const positions=[...branchLayout(rootId).values()];
  return {min:[0,1,2].map(i=>Math.min(...positions.map(p=>p[i]))-1.1),max:[0,1,2].map(i=>Math.max(...positions.map(p=>p[i]))+(i===1?3:1.1))};
}

export function modelPose(part, explosion, unpack=0,expandedRoot=null,scaleX=1) {
  if(expandedRoot){const pose=branchLayout(expandedRoot).get(part.id)||part.base;return [pose[0]*scaleX,pose[1],pose[2]];}
  const amount = clampAmount(explosion) + clampAmount(unpack)*.25;
  return part.base.map((value,index) => value+part.spread[index]*amount);
}

// Whole-branch mode keeps every node label; zoom and the full sidebar remain
// available in small windows instead of silently dropping deeper Agents.
export function arrangeBranchLabels(projected,width,height,rootId){
  const stages=childParts(rootId),slots=new Map(),stageCount=Math.max(1,stages.length);
  let maxRows=1;
  stages.forEach((stage,lane)=>{
    slots.set(stage.id,{lane,stage:true});
    const descendants=descendantsOf(stage.id);maxRows=Math.max(maxRows,Math.ceil(descendants.length/2));
    descendants.forEach((part,index)=>slots.set(part.id,{lane,column:index%2,row:Math.floor(index/2)}));
  });
  const margin=14,laneWidth=(width-margin*2)/stageCount,detailTop=246,rowHeight=Math.max(8,(height-detailTop-18)/maxRows);
  const layout=branchLayout(rootId);
  const rootOrder=[...primaryParts].sort((a,b)=>(layout.get(a.id)?.[0]||0)-(layout.get(b.id)?.[0]||0));
  return projected.map(item=>{
    const slot=slots.get(item.id);
    if(!slot){const boxWidth=Math.min(112,(width-margin*2)/3-8);return {...item,width:boxWidth,height:38,x:margin+(rootOrder.findIndex(root=>root.id===item.id)+.5)*(width-margin*2)/3-boxWidth/2,y:164};}
    const boxWidth=slot.stage?Math.min(128,laneWidth-6):Math.max(10,laneWidth/2-5);
    const x=margin+slot.lane*laneWidth+(slot.stage?.5:(slot.column+.5)/2)*laneWidth-boxWidth/2;
    return {...item,width:boxWidth,height:slot.stage?34:Math.max(6,Math.min(30,rowHeight-3)),x,y:slot.stage?208:detailTop+slot.row*rowHeight};
  });
}

export function relatedConnections(id) {
  return connections.filter(edge => edge.from === id || edge.to === id);
}

// Keep labels in two outer gutters. A narrow canvas only labels the selection,
// leaving the actual model unobscured; the complete part list remains available.
export function arrangeLabels(projected,width,height,selected) {
  if (width < 180 || height < 230) return [];
  const narrow = width < 540;
  let visible = narrow ? projected.filter(item => item.id === selected) : projected;
  const top = 160, bottom = height-30, boxHeight=44, gap=9;
  const capacity = Math.max(1,Math.floor((bottom-top+gap)/(boxHeight+gap)));
  const boxWidth = 155;
  const sides = [-1,1].map(side => visible.filter(item => (item.side < 0 ? -1 : 1) === side));
  const result=[];
  for (let sideIndex=0;sideIndex<sides.length;sideIndex++) {
    let items=sides[sideIndex];
    if (items.length > capacity) items=[...items].sort((a,b)=>Number(b.id===selected)-Number(a.id===selected)).slice(0,capacity);
    items=[...items].sort((a,b)=>a.py-b.py);
    const x=sideIndex===0 ? 18 : Math.max(18,width-boxWidth-48);
    const positions=[];
    for (let i=0;i<items.length;i++) positions.push(Math.max(top,Math.min(bottom-boxHeight,items[i].py-20),i ? positions[i-1]+boxHeight+gap : top));
    if (positions.length && positions.at(-1) > bottom-boxHeight) {
      positions[positions.length-1]=bottom-boxHeight;
      for (let i=positions.length-2;i>=0;i--) positions[i]=Math.min(positions[i],positions[i+1]-boxHeight-gap);
    }
    items.forEach((item,index)=>result.push({...item,x,y:positions[index],width:boxWidth,height:boxHeight}));
  }
  return result;
}
