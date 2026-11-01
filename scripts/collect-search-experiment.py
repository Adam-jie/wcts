"""Collect existing experiment receipts; never run models or alter their verdicts."""
import argparse
import hashlib
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]


def suite(value):
    return {key:value.get(key) for key in ('passed','total','successful','duration_s')} if value else None


def tokens(call):
    usage=call.get('usage') or {}
    def valid(value):return type(value) is int and value>=0
    total=usage.get('total_tokens')
    if valid(total):return total
    incoming=usage.get('input_tokens',usage.get('prompt_tokens'))
    outgoing=usage.get('output_tokens',usage.get('completion_tokens'))
    return incoming+outgoing if valid(incoming) and valid(outgoing) else None


def collect(identity):
    if not re.fullmatch('[a-f0-9]{32}',identity):
        raise ValueError('Use an existing 32-character run ID')
    path=ROOT/'artifacts'/identity/'report.json'
    report=json.loads(path.read_text(encoding='utf8'))
    task=(report.get('tasks') or [{}])[0]
    calls=report.get('model_calls',[])
    fresh=[call for call in calls if call.get('provider') not in {'frozen_foundation_reuse','public_checkpoint_reuse'}]
    search=report.get('search_report',{})
    nodes=[node for node in search.get('nodes',[]) if node.get('parent_id')]
    quality=report.get('test_quality',{})
    return {'id':identity,'status':report['status'],'project_id':report.get('project_id'),
        'report_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
        'provider':report.get('provider'),'requested_model':report.get('model'),
        'reported_serving_models':sorted({call['actual_model'] for call in fresh if call.get('actual_model')}),
        'serving_model_unconfirmed_calls':sum(not call.get('actual_model') for call in fresh),
        'fresh_calls':len(fresh),'reused_calls':len(calls)-len(fresh),
        'reported_tokens':sum(tokens(call) or 0 for call in fresh),
        'unknown_usage_calls':sum(tokens(call) is None for call in fresh),
        'interrupted_pending_call_usage':report.get('experiment_interruption',{}).get('pending_model_call_usage'),
        'usage_complete':not any(tokens(call) is None for call in fresh)
            and not report.get('experiment_interruption',{}).get('pending_model_call_usage'),
        'duration_s':report.get('summary',{}).get('duration_s'),
        'accepted':report.get('summary',{}).get('accepted',0),
        'error':report.get('error'),
        'quality_passed':quality.get('passed'),'quality_audits':[
            {'attempt':audit['attempt'],'approved':audit['model_audit']['approved'],
             'findings':len(audit['model_audit']['findings'])} for audit in quality.get('audits',[])],
        'candidate_count':len(nodes),'maximum_depth':max((node['depth'] for node in nodes),default=0),
        'public_success_candidates':sum(node.get('evaluation',{}).get('public_pass',False) for node in nodes),
        'selected_id':search.get('selected_id'),'stop_reason':search.get('stop_reason'),
        'candidate_sha256':task.get('candidate_sha256'),
        'generated_baseline':suite(task.get('generated_baseline')),'generated_candidate':suite(task.get('generated')),
        'regression_baseline':suite(task.get('regression_baseline')),'regression_candidate':suite(task.get('regression')),
        'independent_acceptance':suite(task.get('acceptance')),'real_runtime':suite(task.get('extended')),
        'wheel_build':task.get('delivery',{}).get('build',{}).get('passed'),
        'archive':report.get('download'),'paired_experiment':report.get('experiment'),
        'notes':['Provider tokens are not a billing audit; unknown calls are not zero.',
                 'Counts are observed task-specific checks, not universal correctness or algorithm superiority.']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs',nargs='+',required=True)
    args=parser.parse_args()
    records=[collect(identity) for identity in args.runs]
    destination=ROOT/'artifacts/search-implementation/experiments.json'
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(json.dumps({'schema':'testfix.search-experiment-evidence.v1','runs':records},
                                    ensure_ascii=False,indent=2),encoding='utf8')
    print(str(destination))


if __name__=='__main__':main()
