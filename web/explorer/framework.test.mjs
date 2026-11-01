import test from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import {parts,primaryParts,childParts,groups,ownerOf,pathToPart,descendantsOf,treePartIds,matchingPartIds,visiblePartIds} from './parts.mjs';
import {connections,modules,modelPose,branchLayout,branchBounds,arrangeLabels,arrangeBranchLabels,clampAmount,relatedConnections} from './framework.mjs';

test('every model module is connected and points at an existing implementation',()=>{
  const ids=new Set(parts.map(part=>part.id));
  assert.equal(ids.size,parts.length);
  assert.equal(primaryParts.length,3);
  assert.deepEqual(primaryParts.map(p=>p.name),['CodeAgent 主控','多 Agent 生成与补全','自动化测试']);
  assert.deepEqual(groups.map(g=>g.id),primaryParts.map(p=>p.id));
  assert.deepEqual(new Set(Object.keys(modules)),ids);
  for (const edge of connections) {
    assert.ok(ids.has(edge.from) && ids.has(edge.to));
    assert.notEqual(edge.from,edge.to);
    assert.ok(edge.label);
  }
  for (const part of parts) {
    assert.ok(relatedConnections(part.id).length);
    assert.ok(primaryParts.some(root=>root.id===ownerOf(part.id)));
    if(part.parent){assert.ok(ids.has(part.parent));assert.equal(ownerOf(part.parent),part.group);assert.equal(part.depth,pathToPart(part.id).length-1);}
    for(const link of part.links||[])assert.ok(ids.has(link.id),link.id);
    for (const path of modules[part.id].files) assert.ok(existsSync(new URL('../../'+path,import.meta.url)),path);
  }
  assert.equal(connections.filter(edge=>edge.from==='verify').length,1);
  assert.equal(connections.find(edge=>edge.from==='verify').to,'controller');
});

test('one module expansion reveals every descendant at every depth',()=>{
  for(const root of primaryParts){
    const children=childParts(root.id);
    assert.equal(children.length,6);
    assert.deepEqual(visiblePartIds(root.id,null),new Set(primaryParts.map(p=>p.id)));
    const expected=new Set([...primaryParts.map(p=>p.id),...descendantsOf(root.id).map(p=>p.id)]);
    assert.deepEqual(visiblePartIds(root.id,root.id),expected);
    assert.deepEqual(treePartIds(root.id,root.id),expected);
    for(const child of descendantsOf(root.id)){
      assert.deepEqual(visiblePartIds(child.id,root.id),expected);
      assert.deepEqual(treePartIds(child.id,root.id),expected);
    }
  }
  assert.equal(ownerOf('scheduler'),'controller');
  assert.equal(ownerOf('gateway'),'controller');
  assert.equal(ownerOf('embedded'),'generation');
  assert.equal(ownerOf('executor'),'tests');
  assert.equal(ownerOf('verify'),'tests');
});

test('search finds a specific capability with its owner instead of unrelated siblings',()=>{
  assert.deepEqual(matchingPartIds('agent-mcu'),new Set(['generation','specialist-team','embedded','agent-mcu']));
  assert.deepEqual(matchingPartIds('  ThReEjS  '),new Set(['generation','specialist-team','frontend','agent-threejs']));
  assert.deepEqual(matchingPartIds('ILP'),new Set(['controller','scheduler']));
  assert.equal(matchingPartIds('unmatched-xyz').size,0);
  assert.equal(matchingPartIds('').size,parts.length);
  assert.deepEqual(matchingPartIds('CodeAgent 主控'),new Set(['controller',...childParts('controller').map(p=>p.id)]));
});

test('detail selection preserves the whole expanded branch until explicit collapse or module switch',()=>{
  assert.deepEqual(childParts('generation').map(p=>p.id),['analysis','team-dispatch','specialist-team','candidate-search','repair','integration-handoff']);
  assert.deepEqual(childParts('candidate-search').map(p=>p.id),['search-state','uct-select','branch-expand','public-score','backpropagate','forest-choose']);
  assert.deepEqual(pathToPart('agent-threejs').map(p=>p.id),['generation','specialist-team','frontend','agent-threejs']);
  const visible=visiblePartIds('agent-threejs','generation');
  assert.equal(visible.size,47);
  for(const id of ['controller','generation','tests','specialist-team','frontend','agent-threejs','agent-frontend','agent-javascript','agent-mcu','backend','uct-select','branch-expand'])assert.ok(visible.has(id),id);
  assert.deepEqual(treePartIds('agent-threejs','generation'),visible);
  assert.deepEqual(visiblePartIds('uct-select','generation'),visible);
  assert.deepEqual(visiblePartIds('generation',null),new Set(primaryParts.map(p=>p.id)));
  assert.ok(!visiblePartIds('scheduler','controller').has('agent-threejs'));
  assert.ok(descendantsOf('specialist-team').some(p=>p.id==='agent-mcu'));
  assert.equal(descendantsOf('specialist-team').filter(p=>p.id.startsWith('agent-')).length,10);
});

test('exploded branch layout gives every visible node a distinct position and keeps all labels',()=>{
  for(const root of primaryParts){
    const visible=visiblePartIds(root.id,root.id),layout=branchLayout(root.id),bounds=branchBounds(root.id);
    assert.deepEqual(new Set(layout.keys()),visible);
    assert.equal(new Set([...layout.values()].map(p=>p.join(','))).size,layout.size);
    for(const [id,pose] of layout){
      assert.deepEqual(modelPose(parts.find(p=>p.id===id),.6,1,root.id),pose);
      for(let i=0;i<3;i++)assert.ok(pose[i]>=bounds.min[i]&&pose[i]<=bounds.max[i]);
    }
    const projected=[...visible].map((id,index)=>({id,depth:parts.find(p=>p.id===id).depth,px:80+(index%12)*65,py:140+Math.floor(index/12)*60}));
    for(const [width,height] of [[1000,560],[900,450],[1150,570],[650,400]]){
      const labels=arrangeBranchLabels(projected,width,height,root.id);
      assert.equal(labels.length,visible.size);
      assert.deepEqual(new Set(labels.map(label=>label.id)),visible);
      for(const label of labels){
        assert.ok(label.x>=0&&label.y>=164&&label.x+label.width<=width&&label.y+label.height<=height);
        for(const other of labels)if(label!==other)assert.ok(label.x+label.width<=other.x||other.x+other.width<=label.x||label.y+label.height<=other.y||other.y+other.height<=label.y,label.id+' overlaps '+other.id);
      }
    }
  }
});

test('MCTS explanatory parameters and edges stay grounded and acceptance remains isolated',()=>{
  const ids=new Set(childParts('candidate-search').map(p=>p.id));
  for(const from of ['uct-select','branch-expand','public-score'])assert.ok(connections.some(edge=>edge.from===from&&ids.has(edge.to)));
  assert.ok(connections.some(edge=>edge.from==='backpropagate'&&edge.to==='uct-select'&&edge.kind==='feedback'));
  assert.ok(connections.some(edge=>edge.from==='branch-expand'&&edge.to==='specialist-team'));
  assert.ok(!connections.some(edge=>edge.from==='verify'&&(ids.has(edge.to)||edge.to==='generation')));
  for(const id of ids)assert.ok(modules[id].files.some(file=>file==='testfix/repair_search.py'||file==='testfix/search_workflow.py'));
  assert.match(parts.find(p=>p.id==='uct-select').parameters[0][1],/parent_visits \+ 1/);
});

test('assembly returns exact base coordinates and explosion is deterministic',()=>{
  for (const part of parts) {
    assert.deepEqual(modelPose(part,0),part.base);
    assert.deepEqual(modelPose(part,1),part.base.map((value,i)=>value+part.spread[i]));
    assert.ok(modelPose(part,.6,1).every(Number.isFinite));
  }
  assert.equal(clampAmount(Infinity),0);
  assert.equal(clampAmount(NaN),0);
  assert.equal(clampAmount(-1),0);
  assert.equal(clampAmount(5),1);
});

const projected=primaryParts.map((part,index)=>({id:part.id,px:300,py:220+index*10,side:part.label[0]}));
for (const [width,height] of [[800,530],[560,480],[390,420]]) {
  test(`labels remain bounded and do not overlap at ${width}x${height}`,()=>{
    const placed=arrangeLabels(projected,width,height,'controller');
    assert.equal(placed.length,width<540?1:3);
    assert.ok(placed.some(item=>item.id==='controller'));
    for (const item of placed) {
      assert.ok(item.x>=0 && item.y>=0);
      assert.ok(item.x+item.width<=width && item.y+item.height<=height);
      for (const other of placed) if (item!==other) assert.ok(item.x+item.width<=other.x || other.x+other.width<=item.x || item.y+item.height<=other.y || other.y+other.height<=item.y);
    }
  });
}

test('compact containers keep the selection and avoid clipped labels',()=>{
  assert.deepEqual(arrangeLabels(projected,100,100,'controller'),[]);
  assert.deepEqual(arrangeLabels(projected,600,210,'controller'),[]);
  const labels=arrangeLabels(projected,600,300,'tests');
  assert.ok(labels.some(item=>item.id==='tests'));
  assert.ok(labels.every(item=>item.y>=160 && item.y+item.height<=270));
});
