import { describe,it,expect } from 'vitest';
import { graphOf } from './engineering-graph';
describe('engineering evidence graphs', () => {
  it('shows planned core gates without inventing completed execution', () => {
    const graph = graphOf(null,'workflow');
    expect(graph.nodes).toHaveLength(9);
    expect(graph.nodes.every(node => node.data.status === 'pending')).toBe(true);
    expect(graph.edges.some(edge => edge.id === 'retry-loop')).toBe(true);
  });
  it('preserves employee dependencies rather than drawing a fixed sequence', () => {
    const graph = graphOf({collaboration:{plans:[{plan:{work_items:[
      {id:'a',title:'a',skill_id:'repair-code',depends_on:[]},
      {id:'b',title:'b',skill_id:'repair-code',depends_on:[]},
      {id:'c',title:'c',skill_id:'repair-code',depends_on:['a','b']},
    ]}}]}},'employees');
    expect(graph.edges.map(edge => edge.id)).toEqual(['manager-a','manager-b','a-c','b-c']);
    expect(graph.nodes.find(node => node.id === 'c')!.position.x).toBeGreaterThan(graph.nodes.find(node => node.id === 'a')!.position.x);
  });
  it('keeps generated step text separate from measured pytest case statuses', () => {
    const graph=graphOf({tasks:[{test_steps:['check data'],generated:{successful:true,cases:[{id:'test.py::test_data',status:'passed'}]}}]},'tests');
    expect(graph.nodes.find(node=>node.id==='step-0')!.data.status).toBe('defined');
    expect(graph.nodes.find(node=>node.id==='case-0')!.data.status).toBe('passed');
    expect(graph.nodes.find(node=>node.id==='pytest-suite')!.data.status).toBe('passed');
  });
  it('uses actual search parent edges and selected evidence', () => {
    const graph=graphOf({search_report:{selected_id:'n1',nodes:[
      {id:'root-0',tree:0,depth:0,parent_id:null},
      {id:'n1',tree:0,depth:1,parent_id:'root-0',iteration:1,visits:1,evaluation:{reward:.5,public_pass:true}},
    ]}},'search');
    expect(graph.edges[0].source).toBe('root-0');
    expect(graph.nodes[1].data.status).toBe('selected');
  });
  it('shows live branch assignments and requires host events for commit status', () => {
    const run={search_report:{nodes:[{id:'node-1',status:'expanding'}]},
      model_calls:[{skill_id:'plan-workflow',request:{context:{search_context:{node_id:'node-1'}}},result:{team_plan:{work_items:[
        {id:'backend',title:'Backend',skill_id:'repair-code',depends_on:[]}]}}},
        {skill_id:'repair-code',request:{context:{assigned_task:{id:'backend'},search_context:{node_id:'node-1'}}},result:{action:'apply_patch'}}],
      events:[{time:'2026-01-01T00:00:00Z',stage:'search_expanding',message:'搜索 node-1：树 1'}]};
    expect(graphOf(run,'employees').nodes.find(n=>n.id==='backend')!.data.status).toBe('pending');
    const observed={...run,events:[...run.events,{time:'2026-01-01T00:00:01Z',stage:'employee_tool',message:'backend: apply_patch 完成'}]};
    expect(graphOf(observed,'employees').nodes.find(n=>n.id==='backend')!.data.status).toBe('running');
    const rejected={...run,events:[...observed.events,{time:'2026-01-01T00:00:02Z',stage:'handoff_checked',task_id:'backend',ok:false}]};
    expect(graphOf(rejected,'employees').nodes.find(n=>n.id==='backend')!.data.status).toBe('warning');
    const approved={...run,events:[...rejected.events,{time:'2026-01-01T00:00:03Z',stage:'employee_handoff',task_id:'backend'}]};
    expect(graphOf(approved,'employees').nodes.find(n=>n.id==='backend')!.data.status).toBe('handed_off');
  });
  it('labels historical stale running states as ended only with final evidence and no active call', () => {
    const capability={skill_id:'plan-workflow',status:'running',active_model_calls:0,artifact:{sha256:'receipt'}};
    const run={status:'completed',tasks:[{verdict:{satisfies_tested_requirements:true}}],workflow:{capabilities:[capability]}};
    expect(graphOf(run,'workflow').nodes[0].data.status).toBe('settled');
    expect(capability.status).toBe('running');
    expect(graphOf({...run,status:'failed'},'workflow').nodes[0].data.status).toBe('running');
    expect(graphOf({...run,workflow:{capabilities:[{...capability,active_model_calls:1}]}},'workflow').nodes[0].data.status).toBe('running');
  });
});
