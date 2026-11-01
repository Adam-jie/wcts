"""Single CLI for import, local deployment and repeatable benchmark execution."""
from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path

from .config import CODEX_PROJECT_MODELS, DEFAULT_CODEX_MODEL, load_settings
from .datasets import DEMO_IDS, import_humaneval
from .orchestrator import execute_run
from .reports import now
from .tasks import import_repository


def main():
    parser = argparse.ArgumentParser(prog="testfix")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("import-humaneval", help="Download pinned official GitHub dataset")
    study = commands.add_parser('study', help='Freeze and run a paired fixed-budget study on registered projects')
    study.add_argument('spec', type=Path, nargs='?', help='UTF-8 JSON study specification')
    study.add_argument('--prepare-only', action='store_true', help='Freeze inputs and show the schedule without model calls')
    study.add_argument('--execute-prepared', help='Run a previously prepared study ID')
    repo = commands.add_parser("import-repo", help="Snapshot a single-module Python feature task")
    repo.add_argument("manifest", type=Path)
    run = commands.add_parser("run")
    run.add_argument("--provider", choices=["replay", "openai", "ollama", "agentarts"])
    run.add_argument("--runner", choices=["process", "docker"])
    run.add_argument("--tasks", nargs="+", default=list(DEMO_IDS))
    run.add_argument("--max-rounds", type=int, default=2, choices=range(1, 6))
    session = commands.add_parser("evaluate-session", help="Verify outputs from a captured live Codex generation session")
    session.add_argument("bundle", type=Path)
    session.add_argument("--runner", choices=["process", "docker"], default="process")
    project = commands.add_parser("evaluate-project", help="Build and verify a sealed complete Flaskr project")
    project.add_argument("manifest", type=Path)
    autonomous = commands.add_parser('complete-project',help='Run all nine capabilities with real models, integer scheduling and independent delivery checks')
    autonomous.add_argument('--project',default='Project/flaskr')
    autonomous.add_argument('--provider',choices=['codex_cli','openai','ollama','agentarts'],default='codex_cli')
    autonomous.add_argument('--model',help='Exact model ID; AgentArts discovers it from the deployed Runtime when omitted')
    autonomous.add_argument('--max-rounds',type=int,choices=range(1,6),default=2)
    autonomous.add_argument('--wall-seconds',type=int,help='Defaults to the registered adapter budget')
    autonomous.add_argument('--token-limit',type=int,default=800000)
    autonomous.add_argument('--model-slots',type=int,default=None)
    autonomous.add_argument('--cpu-slots',type=int,default=2)
    autonomous.add_argument('--strategy',choices=['balanced','fast','thorough'],default='balanced')
    autonomous.add_argument('--search', choices=['none','mcts','forest'], default='none')
    autonomous.add_argument('--search-iterations', type=int, default=6)
    autonomous.add_argument('--search-trees', type=int, default=3)
    autonomous.add_argument('--search-depth', type=int, default=3)
    autonomous.add_argument('--search-width', type=int, default=2)
    autonomous.add_argument('--search-min-successes', type=int, default=3)
    autonomous.add_argument('--test-quality-attempts', type=int, default=6)
    autonomous.add_argument('--resume-public-run', help='Reuse the five public outputs of a failed pre-repair test-quality run; never reuse acceptance')
    requested = autonomous.add_mutually_exclusive_group()
    requested.add_argument('--request', help='Natural-language request for the registered project and its constrained Agent team')
    requested.add_argument('--request-file', type=Path, help='Read the natural-language request from a UTF-8 text file')
    skills = commands.add_parser("skills", help="Validate the versioned Skill catalog and required capability plan; no model or project execution")
    skills.add_argument("--profile", choices=["pure_python", "flaskr_project", "python_package"], default="flaskr_project")
    skills.add_argument("--plan", nargs="+", help="Check this ordered single-round capability plan")
    specialists = commands.add_parser('specialists', help='Preview required domain Skills and missing adapters; no inference or execution')
    specialists.add_argument('--project', default='Project/flaskr', help='Registered project ID')
    agentarts_check = commands.add_parser("agentarts-check", help="Verify the deployed AgentArts Runtime contract, model configuration and Skill version")
    agentarts_check.add_argument("--model", help="Require this exact deployed model ID")
    serve = commands.add_parser("serve")
    serve.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    collaboration_request = None
    if args.command == 'complete-project':
        if args.resume_public_run and args.search == 'none':
            parser.error('Public checkpoint recovery requires --search mcts or forest so tests receive a fresh quality audit')
        if args.request is not None:
            collaboration_request = args.request
        elif args.request_file is not None:
            try:
                with args.request_file.open(encoding='utf-8-sig') as request_file:
                    collaboration_request = request_file.read(20_001)
            except (OSError, UnicodeError) as exc:
                parser.error(f'Cannot read request file as UTF-8: {exc}')
        if collaboration_request is not None:
            if not collaboration_request.strip() or len(collaboration_request) > 20_000:
                parser.error('Request must contain 1-20000 characters and cannot be blank')
            collaboration_request = collaboration_request.strip()
    settings = load_settings()
    if args.command == 'study':
        from .study import StudySpec, execute_study, prepare_study, save_study
        from .project_models import ProjectModelError, project_provider_status
        try:
            if args.execute_prepared:
                import re
                if args.spec or args.prepare_only or not re.fullmatch('[a-f0-9]{32}',args.execute_prepared):
                    raise ValueError('请单独传入有效的 --execute-prepared 研究 ID')
                path = settings.artifacts/'studies'/args.execute_prepared/'study.json'
                value = json.loads(path.read_text(encoding='utf-8'))
                if value['status'] != 'prepared':
                    raise ValueError('只能执行尚未运行的研究计划')
                spec = StudySpec.model_validate(value['spec'])
                if prepare_study(spec, settings)['cases'] != value['cases']:
                    raise ValueError('工程或验收数据与冻结计划不一致，请重新准备')
            else:
                if not args.spec:
                    raise ValueError('请提供研究配置文件或 --execute-prepared')
                spec = StudySpec.model_validate_json(args.spec.read_text(encoding='utf-8-sig'))
                value = prepare_study(spec, settings)
            if not args.prepare_only and spec.provider not in {p['id'] for p in project_provider_status(settings) if p['configured']}:
                raise ValueError('所选模型服务未配置或未登录')
            if not args.prepare_only and spec.provider == 'codex_cli' and spec.model not in CODEX_PROJECT_MODELS:
                raise ValueError('请选择受支持的 Codex 工程模型')
            if not args.prepare_only and spec.provider == 'agentarts':
                from .agentarts_probe import check_runtime
                check_runtime(settings, expected_model=spec.model)
            save_study(settings.artifacts/'studies'/value['id'], value)
            print(json.dumps({'study_id':value['id'], 'status':value['status'], 'jobs':len(value['jobs'])}, ensure_ascii=False), flush=True)
            if not args.prepare_only:
                value = execute_study(value, settings)
                print(json.dumps({'study_id':value['id'], 'status':value['status'], 'jobs':value['jobs']}, ensure_ascii=False))
        except (ValueError, OSError, ProjectModelError) as exc:
            parser.error(str(exc))
    elif args.command == 'agentarts-check':
        from .agentarts_probe import check_runtime
        from .project_models import ProjectModelError
        try:
            print(json.dumps(check_runtime(settings, expected_model=args.model), ensure_ascii=False, indent=2))
        except ProjectModelError as exc:
            print(json.dumps({"ready": False, "code": exc.code, "error": str(exc)}, ensure_ascii=False, indent=2))
            raise SystemExit(1) from None
    elif args.command == 'complete-project':
        from .project_workflow import execute_project_run, default_budget
        from .project_tools import get_project
        if args.provider == 'agentarts':
            from .agentarts_probe import check_runtime
            from .project_models import ProjectModelError
            try:
                args.model = check_runtime(settings, expected_model=args.model)['model']
            except ProjectModelError as exc:
                parser.error(str(exc))
        else:
            args.model = args.model or (DEFAULT_CODEX_MODEL if args.provider == 'codex_cli' else 'gpt-5.6-sol')
        wall_seconds = args.wall_seconds if args.wall_seconds is not None else (
            36000 if args.search != 'none' else 7200 if collaboration_request is not None else default_budget(get_project(settings,args.project)['adapter'])['wall_seconds'])
        run = {'id':uuid.uuid4().hex,'created_at':now(),'project_id':args.project,'provider':args.provider,'model':args.model,
               'max_rounds':args.max_rounds,'strategy':args.strategy,
               'budget':{'wall_seconds':wall_seconds,'token_limit':args.token_limit,
                         'model_slots':args.model_slots if args.model_slots is not None else (2 if collaboration_request is not None else 1),
                         'cpu_slots':args.cpu_slots}}
        if collaboration_request is not None:
            run['collaboration_request'] = collaboration_request
        if args.search != 'none':
            from .repair_search import SearchConfig
            try:
                configuration = SearchConfig(trees=1 if args.search == 'mcts' else args.search_trees,
                    iterations=args.search_iterations,depth=args.search_depth,width=args.search_width,
                    min_successes=args.search_min_successes,quality_attempts=args.test_quality_attempts)
            except ValueError as exc:
                parser.error(str(exc))
            from dataclasses import asdict
            run['search'] = {'mode':args.search, **asdict(configuration)}
            if '--token-limit' not in __import__('sys').argv:
                run['budget']['token_limit'] = 4_000_000
            if args.model_slots is None:
                run['budget']['model_slots'] = 2
        if args.resume_public_run:
            from .experiment_reuse import FrozenFoundationProvider
            from .project_models import ProjectModelProvider
            identity=args.resume_public_run
            if len(identity)!=32 or any(character not in '0123456789abcdef' for character in identity):
                parser.error('Public checkpoint must be a 32-character run ID')
            try:
                foundation=json.loads((settings.artifacts/identity/'report.json').read_text(encoding='utf8'))
                if (foundation['project_id']!=run['project_id'] or foundation['provider']!=run['provider']
                        or foundation['model']!=run['model']):
                    raise ValueError('Public checkpoint project/provider/model differs')
                if collaboration_request is None and foundation.get('collaboration_request'):
                    run['collaboration_request']=foundation['collaboration_request']
                upstream=ProjectModelProvider(run['provider'],settings,model=run['model'],
                    profile=foundation['execution_profile'],artifact_dir=settings.artifacts/run['id']/'model_calls')
                provider=FrozenFoundationProvider(upstream,foundation,public_checkpoint=True)
            except (OSError,ValueError,KeyError) as exc:
                parser.error(str(exc))
            run['public_checkpoint']={'source_run_id':identity,'reuse_is_new_inference':False}
            result=execute_project_run(run,settings,provider=provider)
        else:
            result = execute_project_run(run,settings)
        print(json.dumps({key:result.get(key) for key in ('id','status','summary','error','download')},ensure_ascii=False,indent=2))
        print(str(settings.artifacts/run['id']/'report.md'))
        raise SystemExit(0 if result['status']=='completed' else 1)
    elif args.command == 'specialists':
        from .specialists import preview_project
        try:
            result = preview_project(settings, args.project)
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "skills":
        from .skills import load_catalog
        catalog = load_catalog()
        skill_ids = args.plan or catalog.catalog["profiles"][args.profile]["required_skills"]
        try:
            checked = catalog.validate_plan(args.profile, skill_ids)
        except ValueError as exc:
            parser.error(str(exc))
        print(json.dumps({"catalog": catalog.snapshot(), "plan": checked, "execution_performed": False}, ensure_ascii=False, indent=2))
    elif args.command == "import-humaneval":
        print(json.dumps(import_humaneval(settings.dataset), ensure_ascii=False, indent=2))
    elif args.command == "import-repo":
        print(import_repository(args.manifest, settings))
    elif args.command == "evaluate-project":
        from .project_delivery import verify_delivery
        from .project_eval import evaluate_project, _validate_hashes
        from .reports import save_run
        result = evaluate_project(args.manifest, settings)
        try:
            delivery = verify_delivery(args.manifest, settings, result)
        except Exception as exc:
            delivery = {"build": {"passed": False}, "http": {"passed": 0, "total": 0, "successful": False},
                        "duration_s": 0, "execution_error": True, "error": f"{type(exc).__name__}: {exc}"}
        task = result["tasks"][0]
        task["delivery"] = delivery
        task["build"] = {**delivery["build"], "passed": int(delivery["build"]["passed"]), "total": 1}
        task["extended"] = delivery["http"]
        task["verdict"].update(build_succeeds=delivery["build"]["passed"],
                               application_runs_over_http=delivery["http"]["successful"])
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        integrity = True
        try:
            _validate_hashes(args.manifest.resolve().parent, manifest["protected_files"], settings.root, "post_execution_integrity")
        except ValueError as exc:
            integrity = False
            task["error"] = str(exc)
        task["verdict"]["protected_files_unchanged"] = integrity
        accepted = task["verdict"]["test_suites_pass"] and integrity and delivery["build"]["passed"] and delivery["http"]["successful"]
        task["verdict"]["satisfies_tested_requirements"] = accepted
        task["status"] = "accepted" if accepted else "rejected"
        if delivery.get("error"):
            task["error"] = delivery["error"]
        task["duration_s"] = round(task["duration_s"] + delivery["duration_s"], 3)
        result["summary"].update(accepted=int(accepted), duration_s=task["duration_s"])
        result["kind"] = "project"
        result["status"] = "completed" if accepted else "failed" if delivery.get("execution_error") else "rejected"
        result["finished_at"] = now()
        result["delivery_finished_at"] = now()
        save_run(settings.artifacts / result["id"], result)
        print(json.dumps({key: result[key] for key in ("id", "status", "summary")}, ensure_ascii=False, indent=2))
        print(str(settings.artifacts / result["id"] / "report.md"))
        raise SystemExit(0 if accepted else 1)
    elif args.command == "evaluate-session":
        import hashlib
        from .session import SessionProvider
        provider = SessionProvider(args.bundle)
        expected_suites = {item["task_id"]: item["sha256"] for item in provider.metadata()["additional_suites"]}
        extras = {}
        for task_id, path in provider.additional_suite_paths.items():
            data = Path(path).read_bytes()
            if hashlib.sha256(data).hexdigest() != expected_suites[task_id]:
                parser.error("Additional suite changed after session validation")
            extras[task_id] = data.decode("utf-8")
        run = {"id": uuid.uuid4().hex, "created_at": now(), "status": "queued", "task_ids": list(provider.task_ids),
               "provider": "codex_session", "runner": args.runner, "max_rounds": 1}
        result = execute_run(run, settings, provider=provider, extra_suites=extras)
        print(json.dumps({key: result[key] for key in ("id", "status", "provider", "runner", "summary", "provenance")}, ensure_ascii=False, indent=2))
        print(str(settings.artifacts / run["id"] / "report.md"))
        raise SystemExit(0 if result["status"] == "completed" else 1)
    elif args.command == "run":
        if not 1 <= len(args.tasks) <= 164 or len(set(args.tasks)) != len(args.tasks):
            parser.error("Select 1-164 unique tasks")
        run = {"id": uuid.uuid4().hex, "created_at": now(), "status": "queued", "task_ids": args.tasks,
               "provider": args.provider or settings.provider, "runner": args.runner or settings.runner, "max_rounds": args.max_rounds}
        result = execute_run(run, settings)
        print(json.dumps({key: result[key] for key in ("id", "status", "provider", "runner", "summary")}, ensure_ascii=False, indent=2))
        print(str(settings.artifacts / run["id"] / "report.md"))
        raise SystemExit(0 if result["status"] == "completed" else 1)
    else:
        import uvicorn
        from .api import create_app
        uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()

