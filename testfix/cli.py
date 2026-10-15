"""Single CLI for import, local deployment and repeatable benchmark execution."""
from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path

from .config import load_settings
from .datasets import DEMO_IDS, import_humaneval
from .orchestrator import execute_run
from .reports import now
from .tasks import import_repository


def main():
    parser = argparse.ArgumentParser(prog="testfix")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("import-humaneval", help="Download pinned official GitHub dataset")
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
    autonomous.add_argument('--model',default='gpt-5.6-sol')
    autonomous.add_argument('--max-rounds',type=int,choices=range(1,6),default=2)
    autonomous.add_argument('--wall-seconds',type=int,help='Defaults to the registered adapter budget')
    autonomous.add_argument('--token-limit',type=int,default=800000)
    autonomous.add_argument('--model-slots',type=int,default=None)
    autonomous.add_argument('--cpu-slots',type=int,default=2)
    autonomous.add_argument('--strategy',choices=['balanced','fast','thorough'],default='balanced')
    requested = autonomous.add_mutually_exclusive_group()
    requested.add_argument('--request', help='Natural-language request for the registered project and its constrained Agent team')
    requested.add_argument('--request-file', type=Path, help='Read the natural-language request from a UTF-8 text file')
    skills = commands.add_parser("skills", help="Validate the versioned Skill catalog and required capability plan; no model or project execution")
    skills.add_argument("--profile", choices=["pure_python", "flaskr_project", "python_package"], default="flaskr_project")
    skills.add_argument("--plan", nargs="+", help="Check this ordered single-round capability plan")
    serve = commands.add_parser("serve")
    serve.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    collaboration_request = None
    if args.command == 'complete-project':
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
    if args.command == 'complete-project':
        from .project_workflow import execute_project_run, default_budget
        from .project_tools import get_project
        wall_seconds = args.wall_seconds if args.wall_seconds is not None else (
            7200 if collaboration_request is not None else default_budget(get_project(settings,args.project)['adapter'])['wall_seconds'])
        run = {'id':uuid.uuid4().hex,'created_at':now(),'project_id':args.project,'provider':args.provider,'model':args.model,
               'max_rounds':args.max_rounds,'strategy':args.strategy,
               'budget':{'wall_seconds':wall_seconds,'token_limit':args.token_limit,
                         'model_slots':args.model_slots if args.model_slots is not None else (2 if collaboration_request is not None else 1),
                         'cpu_slots':args.cpu_slots}}
        if collaboration_request is not None:
            run['collaboration_request'] = collaboration_request
        result = execute_project_run(run,settings)
        print(json.dumps({key:result.get(key) for key in ('id','status','summary','error','download')},ensure_ascii=False,indent=2))
        print(str(settings.artifacts/run['id']/'report.md'))
        raise SystemExit(0 if result['status']=='completed' else 1)
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

