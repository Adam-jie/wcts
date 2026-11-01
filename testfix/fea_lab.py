"""Fixed, public SQLGlot evidence and bounded installed-library demonstrations.

This adapter does not accept paths, Python, SQL programs, model prompts, or shell
commands. Independent acceptance source and raw model transports are never read.
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import re
import time
import uuid
import xml.etree.ElementTree as ET

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import Settings
from .package_tools import _run_bounded, run_package_suite
from .project_eval import _mapping_sha256
from .reports import now


PILOT = "artifacts/fea-pilot-20261015"
FINAL = "model-trial-03-continuation"
PARENT = "model-trial-02"
TEST_FILE = "test_sort_cluster_builders.py"
TEST_SHA = "9b7fe455275911cf60087c4d068c512a67e2309dd5818f993a422ca58ceb6e27"
CANDIDATE_SHA = "5d735e4c7c6a84649344ba45881494de80b1dc5de061b46c14bab78d88ec97c9"
PARENT_SHA = "16230c6ac50b4f06faecd843c23372119808e938b760963b009b1a44f59738d7"
BASE_SHA = "12fd2b8eae96d7bedff59e6b0b8d2ad35716d8e2bbfef0b071c084635a137cc0"
RUNTIME_NOTE = "真实运行 SQLGlot Python 库并生成 SQL 文本；不连接数据库，也不执行数据库查询。"
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}(?:\.[A-Za-z_][A-Za-z0-9_]{0,63})?\Z")
_CLAUSE = re.compile(r"([A-Za-z_][A-Za-z0-9_]{0,63}(?:\.[A-Za-z_][A-Za-z0-9_]{0,63})?)(?: +(ASC|DESC))?\Z", re.I)


class FEAProbeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: str = "sort_by"
    columns: list[str] = Field(default_factory=lambda: ["a"], min_length=1, max_length=5)
    table: str = "tbl"
    clauses: list[str] = Field(default_factory=lambda: ["a DESC"], min_length=1, max_length=5)

    @field_validator("operation")
    @classmethod
    def operation_allowed(cls, value):
        if value not in {"sort_by", "cluster_by", "order_by"}:
            raise ValueError("Only sort_by, cluster_by and order_by are supported")
        return value

    @field_validator("table")
    @classmethod
    def table_allowed(cls, value):
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("Use a simple table identifier")
        return value

    @field_validator("columns")
    @classmethod
    def columns_allowed(cls, values):
        if any(not _IDENTIFIER.fullmatch(value) for value in values):
            raise ValueError("Columns must be simple identifiers")
        return values

    @field_validator("clauses")
    @classmethod
    def clauses_allowed(cls, values):
        normalized = []
        for value in values:
            match = _CLAUSE.fullmatch(value)
            if not match:
                raise ValueError("A clause is an identifier optionally followed by ASC or DESC")
            normalized.append(match[1] + (" " + match[2].upper() if match[2] else ""))
        return normalized


def _safe(root: Path, path: Path, *, exists: bool = True) -> Path:
    root, path = root.absolute(), path.absolute()
    if not path.is_relative_to(root):
        raise ValueError("Evidence path escaped the fixed workspace")
    for item in (path, *path.parents):
        if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
            raise ValueError("Linked evidence paths are forbidden")
        if item == root:
            break
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()) or exists and not resolved.exists():
        raise ValueError("Required fixed evidence is unavailable")
    return resolved


def _read(root: Path, path: Path, limit: int = 4_000_000) -> str:
    path = _safe(root, path)
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError("Evidence must be a bounded regular file")
    return path.read_text(encoding="utf-8")


def _json(root: Path, path: Path) -> dict:
    value = json.loads(_read(root, path))
    if not isinstance(value, dict):
        raise ValueError("Evidence must be a JSON object")
    return value


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree(root: Path, source: Path, *, runtime: bool = False) -> dict:
    source = _safe(root, source)
    files = (source / "sqlglot").rglob("*.py") if runtime else source.rglob("*")
    result = {}
    for file in sorted(files):
        relative = file.relative_to(source)
        if any(part in {".git", "__pycache__", ".pytest_cache"} for part in relative.parts) or file.suffix == ".pyc":
            continue
        _safe(root, file)
        if file.is_file():
            result[relative.as_posix()] = _digest(file)
    if not result:
        raise ValueError("Evidence source tree is empty")
    return result


def _suite(value: dict, identifier: str, label: str, messages: dict | None = None) -> dict:
    cases = [{"id": str(item.get("id", "")), "status": str(item.get("status", "unknown"))}
             for item in value.get("cases", [])]
    for item in cases:
        if messages and item["id"] in messages:
            item["message"] = messages[item["id"]]
    return {"id": identifier, "label": label, "passed": value.get("passed", 0),
            "total": value.get("total", 0), "failed": sum(item["status"] in {"failure", "failed"} for item in cases),
            "errors": value.get("collection_errors", 0) + sum(item["status"] == "error" for item in cases),
            "ok": value.get("successful") is True, "cases": cases,
            "duration_s": value.get("duration_s", 0),
            "binding_verified": value.get("binding", {}).get("success") is True and not value.get("binding_error")}


def _public_failures(root: Path, suite: dict, allowed: Path) -> dict:
    """Only short exception headlines from a fixed public generated-suite JUnit."""
    path = _safe(root, Path(suite.get("junit_path", "")))
    if not path.is_relative_to(allowed.resolve()) or path.suffix != ".xml":
        raise ValueError("Public generated result does not bind its fixed evidence directory")
    result = {}
    for case in ET.fromstring(_read(root, path, 1_000_000)).iter("testcase"):
        for error in list(case):
            if error.tag not in {"failure", "error"}:
                continue
            text = error.get("message", "")
            match = re.search(r"(?:AttributeError|TypeError|AssertionError|ValueError):[^\r\n]{0,350}", text)
            if match:
                identifier = case.get("classname", "") + "::" + case.get("name", "")
                result[identifier] = match[0]
    return result


def _evidence(settings: Settings) -> dict:
    root = settings.root.resolve()
    pilot = _safe(root, root / PILOT)
    final, parent = pilot / FINAL, pilot / PARENT
    summary = _json(root, final / "retest-summary.json")
    report = _json(root, final / "report.json")
    old = _json(root, parent / "report.json")
    case = _json(root, pilot / "public/sqlglot-415/input.json")
    if any(value.get("instance_id") != "tobymao__sqlglot-415" for value in (summary, report, old, case)):
        raise ValueError("Fixed SQLGlot evidence identity mismatch")
    if not summary.get("success") or not report.get("accepted"):
        raise ValueError("Final accepted experiment evidence is missing")
    review = _json(root, final / "capabilities/review-patch-2.json")
    if (report.get("candidate_sha256") != CANDIDATE_SHA
            or report.get("review") != review or review.get("approved") is not True
            or review.get("candidate_sha256") != CANDIDATE_SHA):
        raise ValueError("Final review receipt does not bind the accepted candidate")
    generated = _safe(root, final / "workspace/generated_tests")
    frozen = _tree(root, generated)
    if frozen != {TEST_FILE: TEST_SHA} or report.get("generated_tests", {}).get("sha256") != frozen:
        raise ValueError("Frozen generated test bytes changed")
    candidate = _safe(root, final / "workspace/candidate")
    candidate_tree = _tree(root, candidate)
    if _mapping_sha256(candidate_tree) != CANDIDATE_SHA or summary.get("candidate_sha256") != CANDIDATE_SHA:
        raise ValueError("Reviewed final candidate bytes changed")
    baseline = _safe(root, pilot / "public/sqlglot-415/base")
    failed = _safe(root, parent / "workspace/candidate")
    if _mapping_sha256(_tree(root, baseline)) != BASE_SHA or _mapping_sha256(_tree(root, failed)) != PARENT_SHA:
        raise ValueError("Baseline or failed candidate bytes changed")
    if Path(case.get("baseline_root", "")).resolve() != baseline:
        raise ValueError("Public baseline location changed")
    delivery = _json(root, final / "delivery/delivery-report.json")
    manifest = _json(root, final / "delivery/wheel-runtime-hashes.json")
    expected_runtime = _tree(root, candidate, runtime=True)
    if delivery.get("candidate_runtime_sha256") != expected_runtime or manifest != expected_runtime:
        raise ValueError("Delivery receipt does not bind the candidate")
    wheel_raw = Path(delivery.get("wheel", {}).get("path", ""))
    wheel = _safe(root, wheel_raw)
    if not wheel.is_relative_to((final / "delivery/wheels").resolve()) or _digest(wheel) != summary.get("wheel_sha256"):
        raise ValueError("Wheel bytes differ from the accepted experiment")
    sites = list((final / "delivery").glob("installed-venv*/Lib/site-packages"))
    sites += list((final / "delivery").glob("installed-venv*/lib/python*/site-packages"))
    if len(sites) != 1:
        raise ValueError("Exactly one installed SQLGlot wheel is required")
    installed = _safe(root, sites[0])
    if _tree(root, installed, runtime=True) != expected_runtime:
        raise ValueError("Installed runtime differs from the reviewed wheel")
    # Interpreter location is fixed by the reconstructed local case, never an API input.
    python = _safe(root, Path(case["python"]))
    expected_python = (pilot / "evaluator-only/sqlglot-415/venv/Scripts/python.exe").resolve()
    if python != expected_python:
        raise ValueError("Unregistered local test interpreter")
    installed_python = _safe(root, installed.parent.parent / "Scripts/python.exe")
    return dict(root=root, pilot=pilot, final=final, parent=parent, summary=summary,
                report=report, old=old, case=case, generated=generated, candidate=candidate,
                baseline=baseline, failed=failed, installed=installed, python=python,
                installed_python=installed_python, runtime=expected_runtime, frozen=frozen)


_TEST_DESCRIPTIONS = [
    ("字符串 SORT BY", "构建查询 → 调用 sort_by('x DESC') → 检查 Sort 节点和 SQL 文本"),
    ("字符串 CLUSTER BY", "构建查询 → 调用 cluster_by('x') → 检查 Cluster 节点和 SQL 文本"),
    ("表达式对象输入", "输入 column 表达式 → 分别调用两种构建器 → 检查 SQL 文本"),
    ("SORT 追加与替换", "首次排序 → 默认追加 → append=False 替换 → 检查三版 SQL"),
    ("CLUSTER 追加与替换", "首次聚簇 → 默认追加 → append=False 替换 → 检查三版 SQL"),
    ("SORT 复制语义", "默认复制保留原对象 → copy=False 原地更新 → 检查对象与 SQL"),
    ("CLUSTER 复制语义", "默认复制保留原对象 → copy=False 原地更新 → 检查对象与 SQL"),
    ("方言参数传递", "解析 MySQL 反引号列名 → 通用方言生成 → 检查引用和排序"),
    ("原有解析兼容性", "解析已有 SORT/CLUSTER SQL → 检查节点类型 → 重新生成同一文本"),
]
_STAGE_LABELS = {
    "plan-workflow": ("总控规划", "根据需求确定阶段、先后顺序、预算及验收边界。"),
    "explore-repository": ("仓库探索", "识别目录、SQLGlot 库入口和已有工程测试。"),
    "ground-code": ("代码定位", "把需求关联到 Select、构建辅助函数、解析器及生成器。"),
    "analyze-requirements": ("需求分析", "形成行为清单：字符串、表达式、追加、复制、方言和兼容性。"),
    "generate-tests": ("生成测试", "LLM 生成 9 项 pytest 用例和执行步骤；生成后立即冻结字节。"),
    "diagnose-results": ("失败诊断", "依据公开生成测试的失败结果分析缺口，不读取独立验收源码。"),
    "repair-code": ("代码补全", "生成受约束补丁，执行器验证作用域、旧文本匹配及源码哈希。"),
    "review-patch": ("独立审查", "另一轮模型核对候选源码、行为和失败反馈，不能修改测试。"),
    "verify-delivery": ("验收与交付", "宿主执行独立验收、回归、构建安装及安装后真实 API 检查。"),
}


def demo(settings: Settings) -> dict:
    e = _evidence(settings)
    summary, report, old, root = e["summary"], e["report"], e["old"], e["root"]
    generated = _json(root, e["parent"] / "capabilities/generate-tests-1.json")
    source = _read(root, e["generated"] / TEST_FILE, 100_000)
    functions = [node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")]
    if len(functions) != len(_TEST_DESCRIPTIONS):
        raise ValueError("Frozen generated case inventory changed")
    stages = []
    for phase, values in (("parent", old.get("stages", [])), ("continuation", report.get("stages", []))):
        for item in values:
            identifier = item.get("skill_id")
            if identifier not in _STAGE_LABELS or identifier == "verify-delivery":
                continue
            label, detail = _STAGE_LABELS[identifier]
            attempt = item.get("attempt", 1)
            artifact_file = f"capabilities/{identifier}-{attempt}.json"
            directory = e["parent"] if phase == "parent" else e["final"]
            # Return only declared public artifact fields, never a raw transport record.
            artifact = _json(root, directory / artifact_file)
            allowed = {"plan-workflow": ("goals", "skill_ids", "rationale"), "explore-repository": ("candidates", "exhausted"),
                       "ground-code": ("locations",), "analyze-requirements": ("requirements",),
                       "generate-tests": ("steps", "coverage"), "diagnose-results": ("summary", "failures"),
                       "repair-code": ("reason", "baseline_sha256"), "review-patch": ("approved", "summary", "findings", "candidate_sha256")}
            public = {key: artifact[key] for key in allowed[identifier] if key in artifact}
            stages.append({"id": f"{phase}-{identifier}-{attempt}", "skill_id": identifier,
                           "label": label, "role": identifier, "status": item.get("status", "completed"),
                           "summary": detail, "phase": phase, "attempt": attempt, "llm": True,
                           "inherited": phase == "parent", "artifact": public,
                           "artifact_path": (directory / artifact_file).relative_to(root).as_posix()})
    stages.append({"id": "host-verify-delivery", "skill_id": "verify-delivery", "label": "验收与交付",
                   "role": "可信宿主执行器", "status": "completed", "summary": _STAGE_LABELS["verify-delivery"][1],
                   "phase": "host", "llm": False, "inherited": False,
                   "artifact": {"checks": summary["checks"]}})
    test_stages = [_suite(old["generated_baseline"], "base", "原始工程",
                          _public_failures(root, old["generated_baseline"], e["parent"] / "public-tests")),
                   _suite(old["generated_candidate"], "failed", "首次补全",
                          _public_failures(root, old["generated_candidate"], e["parent"] / "public-tests")),
                   _suite(report["generated_candidate"], "final", "修复后工程")]
    checks = summary["checks"]
    return {"schema": "testfix.fea-visual-demo.v1",
            "meta": {"title": "SQLGlot · 真实工程补全实验", "repo_url": "https://github.com/tobymao/sqlglot",
                     "task_url": summary["upstream_task"], "description": e["case"]["requirement"]["description"],
                     "requested_model": summary["model"]["requested"], "actual_model": None,
                     "run_id": FINAL, "instance_id": summary["instance_id"], "base_commit": summary["base_commit"],
                     "program_kind": "Python SQL 构建库", "runtime_note": RUNTIME_NOTE},
            "metrics": {"generated_before": {"passed": 1, "total": 9}, "generated_failed": {"passed": 2, "total": 9},
                        "generated_after": {"passed": 9, "total": 9}, "official_before": {"passed": 0, "total": 1},
                        "official_after": checks["independent_feature"], "regression_after": checks["original_regression"],
                        "installed_generated": checks["installed_generated"], "installed_regression": checks["installed_regression"],
                        "installed_api": checks["installed_api"], "build": checks["compile_build_install"]},
            "stages": stages,
            "generated_tests": {"filename": TEST_FILE, "source": source, "steps": generated.get("steps", []),
                                "frozen_sha256": TEST_SHA, "coverage": generated.get("coverage", []),
                                "cases": [{"name": node.name, "description": detail, "label": label,
                                           "line": node.lineno, "source": ast.get_source_segment(source, node)}
                                          for node, (label, detail) in zip(functions, _TEST_DESCRIPTIONS)],
                                "stages": test_stages},
            "patch": {"diff": _read(root, e["final"] / "candidate.patch", 100_000),
                      "before_summary": "原始 Select 缺少两个构建器；首次补全增加方法，却漏注册 Sort/Cluster 字符串解析器。",
                      "after_summary": "在 expressions.py 增加两个构建器，在 parser.py 注册两个延迟解析回调。",
                      "files": ["sqlglot/expressions.py", "sqlglot/parser.py"]},
            "provenance": {"requested_model": summary["model"]["requested"], "actual_identity_verified": False,
                           "new_real_calls": summary["model"]["new_real_calls"],
                           "cumulative_real_calls": summary["model"]["cumulative_real_calls"],
                           "cumulative_reported_tokens": summary["model"]["cumulative_reported_tokens"],
                           "orchestration": "串行 Skill 调用；继承原实验产物，续跑修复与审查。"},
            "integrity": {"verified": True, "candidate_sha256": CANDIDATE_SHA,
                          "wheel_sha256": summary["wheel_sha256"], "frozen_tests_sha256": TEST_SHA},
            "limits": [RUNTIME_NOTE, "历史流程回看不是正在重新调用模型；下方探针和重测会真实执行本地 Python。",
                       "本轮是 Windows 本地原生实验，不是官方 Docker 完整榜单成绩。",
                       "已完成固定 10 项任务中的 1 项；两个先前失败实验保留。",
                       "请求模型 gpt-5.6-sol；服务未返回可核实的实际模型标识。",
                       "仅展示公开生成用例和独立验收汇总；不公开裁判源码或原始模型请求。"]}


_PROBE = r'''import hashlib,json,pathlib,sys
request=json.loads(sys.stdin.read())
source=pathlib.Path(request["source"]).resolve()
sys.path.insert(0,str(source))
import sqlglot
result={"ok":False,"sql":None,"error":None}
try:
    query=sqlglot.select(*request["columns"]).from_(request["table"])
    query=getattr(query,request["operation"])(*request["clauses"])
    result.update(ok=True,sql=query.sql())
except Exception as error:
    result["error"]=type(error).__name__+": "+str(error)
modules={}
for name,module in sorted(sys.modules.items()):
    if name!="sqlglot" and not name.startswith("sqlglot."): continue
    raw=getattr(module,"__file__",None)
    if not raw: continue
    file=pathlib.Path(raw).resolve()
    if not file.is_relative_to(source) or file.suffix!=".py":
        raise RuntimeError("SQLGlot import escaped fixed source binding")
    relative=file.relative_to(source).as_posix()
    checksum=hashlib.sha256(file.read_bytes()).hexdigest()
    if request["runtime"].get(relative)!=checksum: raise RuntimeError("Imported SQLGlot module bytes changed")
    modules[name]={"relative":relative,"sha256":checksum}
if "sqlglot" not in modules: raise RuntimeError("SQLGlot package was not imported")
result.update(binding_verified=True,modules=modules)
print(json.dumps(result,ensure_ascii=False))
'''


def probe(settings: Settings, request: FEAProbeRequest) -> dict:
    # Revalidate even callers bypassing FastAPI/Pydantic construction.
    request = FEAProbeRequest.model_validate(request.model_dump())
    e, started = _evidence(settings), time.monotonic()
    payload = request.model_dump()
    versions = []
    seals = {}
    for identifier, label, source, python in (("base", "原始工程", e["baseline"], e["python"]),
                                             ("failed", "首次补全工程", e["failed"], e["python"]),
                                             ("installed", "最终安装包", e["installed"], e["installed_python"])):
        runtime = _tree(e["root"], source, runtime=True)
        seals[identifier] = (source, runtime)
        process = _run_bounded([str(python), "-I", "-B", "-X", "utf8", "-c", _PROBE],
                               cwd=e["root"], timeout_s=10,
                               stdin=json.dumps({**payload, "source": str(source), "runtime": runtime}))
        if process["exit_code"] != 0 or process["timeout"] or process["output_limit_exceeded"]:
            raise ValueError("Bounded SQLGlot probe failed its execution or import-binding guard")
        try:
            result = json.loads(process["stdout"])
        except ValueError:
            raise ValueError("SQLGlot probe returned invalid evidence") from None
        if result.get("binding_verified") is not True:
            raise ValueError("SQLGlot probe did not verify imported runtime bytes")
        versions.append({"id": identifier, "label": label, "ok": result["ok"], "sql": result["sql"],
                         "error": result["error"], "binding_verified": True,
                         "source_sha256": _mapping_sha256(runtime), "duration_s": process["duration_s"]})
    if any(_tree(e["root"], source, runtime=True) != seal for source, seal in seals.values()):
        raise ValueError("Probe changed a bound runtime source")
    keyword = {"sort_by": "SORT BY", "cluster_by": "CLUSTER BY", "order_by": "ORDER BY"}[request.operation]
    expected = f"SELECT {', '.join(request.columns)} FROM {request.table} {keyword} {', '.join(request.clauses)}"
    args = ", ".join(repr(value) for value in request.columns)
    clauses = ", ".join(repr(value) for value in request.clauses)
    identifier = uuid.uuid4().hex
    result = {"schema": "testfix.fea-probe.v1", "id": identifier, "artifact_id": identifier, "executed_at": now(),
            "request": payload, "code": f"sqlglot.select({args}).from_({request.table!r}).{request.operation}({clauses}).sql()",
            "expected_sql": expected, "versions": versions, "source_unchanged": True,
            "duration_s": round(time.monotonic() - started, 3), "runtime_note": RUNTIME_NOTE,
            "model_calls": 0}
    output = _safe(e["root"], e["root"] / "artifacts/fea-visual-checks" / identifier, exists=False)
    output.mkdir(parents=True, exist_ok=False)
    (output / "probe-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def retest(settings: Settings) -> dict:
    started, e = time.monotonic(), _evidence(settings)
    # Validate the already prepared fixture data; never write into sealed delivery.
    installed_receipt = _json(e["root"], e["final"] / "installed-tests/installed-suites-report.json")
    baseline_fixtures = e["baseline"] / "tests/fixtures"
    installed_fixtures = e["installed"] / "tests/fixtures"
    fixture_seal = _tree(e["root"], baseline_fixtures)
    if (_tree(e["root"], installed_fixtures) != fixture_seal
            or installed_receipt.get("fixtures", {}).get("target_sha256") != fixture_seal):
        raise ValueError("Installed regression fixture data changed")
    identifier = uuid.uuid4().hex
    output = _safe(e["root"], e["root"] / "artifacts/fea-visual-checks" / identifier, exists=False)
    output.mkdir(parents=True, exist_ok=False)
    result = {"schema": "testfix.fea-interactive-retest.v1", "id": identifier, "artifact_id": identifier,
              "executed_at": now(), "model_calls": 0, "ok": False, "suites": []}
    raw = {}
    try:
        for name, label, tests in (("generated", "冻结生成测试", e["generated"]),
                                   ("regression", "原工程回归测试", e["baseline"] / "tests")):
            raw[name] = run_package_suite(e["python"], e["installed"], tests, output,
                                          "visual-" + name, {"source_root": ".", "package": "sqlglot"})
            result["suites"].append(_suite(raw[name], name, label))
        unchanged = (_tree(e["root"], e["installed"], runtime=True) == e["runtime"]
                     and _tree(e["root"], e["generated"]) == e["frozen"]
                     and _tree(e["root"], installed_fixtures) == fixture_seal
                     and _mapping_sha256(_tree(e["root"], e["candidate"])) == CANDIDATE_SHA
                     and _mapping_sha256(_tree(e["root"], e["baseline"])) == BASE_SHA)
        paired = all(set(raw[name].get("case_ids", [])) == set(installed_receipt["suites"][name]["case_ids"])
                     and len(raw[name].get("case_ids", [])) == len(set(raw[name].get("case_ids", [])))
                     for name in raw)
        result["integrity"] = {"source_unchanged": unchanged, "case_ids_match_frozen_run": paired,
                               "frozen_tests_sha256": TEST_SHA, "candidate_sha256": CANDIDATE_SHA,
                               "installed_runtime_verified": unchanged, "independent_judge_rerun": False}
        result["ok"] = unchanged and paired and all(item["ok"] for item in result["suites"])
    finally:
        result["duration_s"] = round(time.monotonic() - started, 3)
        (output / "public-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        # Full local binding receipts remain disk evidence, not a public HTTP route.
        (output / "local-suite-receipts.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
