"""Durable evidence; no credentials and no hidden test source in reports."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile


_PUBLISH_ATTEMPTS = 6
_PUBLISH_RETRY_DELAY_SECONDS = 0.02


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def markdown(run: dict) -> str:
    if run["provider"] == "replay":
        mode = "离线回放；以下结果仅证明工程流程，不能作为大模型性能"
    elif run["provider"] == "codex_session":
        mode = f"{run.get('provenance', {}).get('model', '模型')} 本次 Codex 会话生成；本地执行器读取保存输出进行评测，未直接调用 API"
    elif run.get('kind') == 'autonomous_project':
        mode = f"九能力自动工程流程；{run['provider']} 实际模型调用；模型与预算证据见 JSON"
    else:
        mode = "真实模型 API 调用；报告实际执行结果"
    summary = run.get("summary", {})
    project = run.get("runner") == "project_process"
    package = run.get("execution_profile") == "python_package"
    delivery_label = "真实 CLI" if package else "真实 HTTP"
    runner_description = "完整工程在本机受限子进程执行；源码绑定、依赖固定，非通用操作系统沙箱" if project else f"{run['runner']}（process 是受限子集开发验证，非通用操作系统沙箱）"
    lines = ["# TestFix-Agent 验证报告", "", f"运行：`{run['id']}`", f"状态：{run['status']}", f"模式：{mode}",
             f"执行器：{runner_description}", "",
             f"任务验收：{summary.get('accepted', 0)}/{summary.get('total', 0)}；回归破坏：{summary.get('regressions', 0)}；耗时：{summary.get('duration_s', 0)} 秒", "",
             f"pytest 用例按 JUnit 逐条统计；完整工程验收包含原有测试、生成测试、独立验收、构建与{delivery_label}。" if project else "HumanEval 的 total=1 表示该题官方测试套件整体，不表示套件只有一个断言。",
             "本次验证所列固定版本和需求；测试通过只证明这些测试域中的行为。" if project else "修复最多采用固定轮数；本报告是预算内解决数，不等价于官方完整数据集 pass@1。", ""]
    if run["provider"] == "codex_session":
        lines.insert(9, "模型生成发生在评测前；表中耗时仅为本地执行阶段。生成Agent、模型、时间和输出哈希保存在JSON的provenance。")
    if run.get('kind') == 'autonomous_project':
        plan = run.get('plan',{})
        lines.extend(['','## 整数调度与实际执行','',f"求解状态：{plan.get('solver_status','尚未求解')}；最优只针对声明的资源与预算，不是功能通过证明。",
                      '', '```json',json.dumps(run.get('workflow',{}),ensure_ascii=False,indent=2),'```'])
    if run.get('collaboration'):
        team = run['collaboration']
        lines.extend(['','## 总控与员工协作','',f"用户需求：{team['request']}",
                      f"总控规划版本：{len(team['plans'])}；实际执行方式：{team.get('execution','未记录')}。",
                      '员工交接不等于验收通过；最终结果仍由冻结测试、评审、构建和实际运行检查决定。'])
        for revision in team['rounds']:
            lines.extend(['',f"第 {revision['round']} 轮："])
            if revision.get('batches'):
                peak=max(batch.get('max_observed_concurrency',0) for batch in revision['batches'])
                lines.append(f"WCTP 实际执行批次：{len(revision['batches'])}；观测到的最高并发：{peak}；源码哈希和线程时间段见 JSON。")
            for employee in revision['employees']:
                operations = ', '.join(step['action'] for step in employee['steps'])
                lines.append(f"- {employee['agent_id']}：{employee['status']}；工具步骤：{operations}")
    lines.extend(['',f"| 任务 | 初始独立验收 | 生成测试 | 最终独立验收 | {delivery_label}检查 | 回归合计 | 结果 | 评测耗时(s) |" if project else "| 任务 | 初始独立验收 | 生成测试 | 最终独立验收 | 额外性质检查 | 原有回归 | 结果 | 评测耗时(s) |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- |"])
    def score(record):
        return f"{record.get('passed', 0)}/{record.get('total', 0)}" if record else "未执行"
    for task in run.get("tasks", []):
        extended = task.get("extended")
        extended_score = score(extended) + (f"（{extended.get('metrics', {}).get('checks', 0)} 输入）" if extended and extended.get("metrics") else "")
        lines.append(f"| {task['id']} | {score(task.get('baseline'))} | {score(task.get('generated'))} | {score(task.get('acceptance'))} | {extended_score} | {score(task.get('regression'))} | {task['status']} | {task.get('duration_s', 0)} |")
    for task in run.get("tasks", []):
        lines.extend(["", f"## {task['id']} · {task['title']}", "", f"需求：{task.get('requirements', [])}",
                      f"定位：{task.get('diagnosis', '')}", f"评审：{task.get('review', {})}"])
        if task.get("verdict"):
            lines.extend(["", f"判定：{task['verdict']}"])
        if task.get("source"):
            lines.extend(["", "来源与固定版本：", "", "```json", json.dumps(task["source"], ensure_ascii=False, indent=2), "```"])
        if project:
            lines.extend(["", f"大模型生成测试基线：{score(task.get('generated_baseline'))}",
                          f"原工程回归基线：{score(task.get('regression_baseline'))}"])
        if package:
            lines.extend(["", f"上游原有测试：{score(task.get('upstream_regression'))}",
                          f"补全前冻结的既有行为回归：{score(task.get('behavior_regression'))}"])
        if task.get("delivery"):
            lines.extend(["", f"构建与{delivery_label}交付验证：", "", "```json", json.dumps(task["delivery"], ensure_ascii=False, indent=2), "```"])
        if task.get("test_steps"):
            lines.extend(["", "自动化测试步骤：", ""] + [f"{index}. {step}" for index, step in enumerate(task["test_steps"], 1)])
        if task.get("requirement_coverage"):
            lines.extend(["", "需求与开发用例对应：", "", "```json", json.dumps(task["requirement_coverage"], ensure_ascii=False, indent=2), "```"])
        if task.get("test_assumptions"):
            lines.extend(["", "测试域与假设：", ""] + [f"- {item}" for item in task["test_assumptions"]])
        if task.get("error"):
            lines.append(f"错误：{task['error']}")
        lines.extend(["", "```diff", task.get("diff", ""), "```"])
    if run.get("error"):
        lines.extend(["", f"运行错误：{run['error']}"])
    return "\n".join(lines) + "\n"


def _publish_text(target: Path, content: str) -> None:
    """Publish one complete file; sharing denials never truncate its predecessor."""
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent,
                                prefix=f".{target.name}.", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        for attempt in range(_PUBLISH_ATTEMPTS):
            try:
                temporary.replace(target)
                return
            except PermissionError:
                if attempt == _PUBLISH_ATTEMPTS - 1:
                    raise
                # Windows readers or antivirus can briefly deny replacement.
                # Retry only that failure, for at most 0.62 seconds of backoff.
                time.sleep(_PUBLISH_RETRY_DELAY_SECONDS * 2 ** attempt)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def save_run(directory: Path, run: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    # Render both forms before replacing either published file. Each target is
    # atomic independently; the JSON/Markdown pair is not a filesystem transaction.
    document = json.dumps(run, ensure_ascii=False, indent=2, allow_nan=False)
    description = markdown(run)
    _publish_text(directory / "report.json", document)
    _publish_text(directory / "report.md", description)

