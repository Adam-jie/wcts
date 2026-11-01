"""Run paired linear/single-tree/forest experiments using one frozen test suite."""
import argparse
import csv
import json
from pathlib import Path
import re
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from testfix.config import load_settings
from testfix.experiment_reuse import FrozenFoundationProvider
from testfix.project_models import ProjectModelProvider
from testfix.project_workflow import execute_project_run
from testfix.reports import now


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--foundation',required=True)
    parser.add_argument('--modes',nargs='+',choices=['none','mcts','forest'],default=['none','mcts','forest'])
    parser.add_argument('--repeats',type=int,default=1)
    parser.add_argument('--iterations',type=int,default=6)
    parser.add_argument('--wall-seconds',type=int,default=36000)
    parser.add_argument('--token-limit',type=int,default=4000000)
    args=parser.parse_args()
    if not re.fullmatch('[a-f0-9]{32}',args.foundation) or args.repeats<1 or args.iterations<1:
        parser.error('Use an accepted foundation ID and positive repeats/iterations')
    settings=load_settings()
    foundation=json.loads((settings.artifacts/args.foundation/'report.json').read_text(encoding='utf-8'))
    generated=settings.artifacts/args.foundation/'workspace/generated_tests'
    from testfix.project_tools import files_under
    frozen=files_under(generated)
    campaign=settings.artifacts/('ablation-'+uuid.uuid4().hex)
    campaign.mkdir()
    rows=[]
    for repeat in range(args.repeats):
        for mode in args.modes:
            identity=uuid.uuid4().hex
            run={'id':identity,'created_at':now(),'project_id':foundation['project_id'],
                 'provider':foundation['provider'],'model':foundation['model'],'max_rounds':1,'strategy':'balanced',
                 'budget':{'wall_seconds':args.wall_seconds,'token_limit':args.token_limit,'model_slots':2,'cpu_slots':2},
                 'experiment':{'foundation':args.foundation,'mode':mode,'repeat':repeat,'frozen_generated_hashes':frozen}}
            if mode!='none':
                run['search']={'mode':mode,'trees':1 if mode=='mcts' else 3,'iterations':args.iterations,
                               'width':1,'depth':3,'min_successes':1 if mode=='mcts' else 3}
                run['collaboration_request']=foundation['collaboration_request']
            upstream=ProjectModelProvider(foundation['provider'],settings,model=foundation['model'],
                profile=foundation['execution_profile'],artifact_dir=settings.artifacts/identity/'model_calls')
            provider=FrozenFoundationProvider(upstream,foundation)
            print(json.dumps({'run':identity,'mode':mode,'repeat':repeat}),flush=True)
            result=execute_project_run(run,settings,provider=provider)
            generated_now=settings.artifacts/identity/'workspace/generated_tests'
            paired=generated_now.is_dir() and files_under(generated_now)==frozen
            row={'run':identity,'mode':mode,'repeat':repeat,'status':result['status'],'paired_tests':paired,
                 'accepted':result['summary']['accepted'],'new_model_calls':sum(call['provider']!='frozen_foundation_reuse' for call in result['model_calls']),
                 'reused_capabilities':len(provider.metadata()['reused_capabilities']),
                 'candidate_count':len([node for node in result.get('search_report',{}).get('nodes',[]) if node.get('parent_id')]),
                 'accounted_tokens':result['workflow'].get('budget',{}).get('accounted_tokens'),
                 'elapsed_seconds':result['summary']['duration_s']}
            rows.append(row)
            (campaign/'results.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
            with (campaign/'results.csv').open('w',encoding='utf-8-sig',newline='') as stream:
                writer=csv.DictWriter(stream,fieldnames=list(row));writer.writeheader();writer.writerows(rows)
            print(json.dumps(row),flush=True)
    print(str(campaign),flush=True)


if __name__=='__main__':main()
