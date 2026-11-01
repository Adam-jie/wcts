"""Attach captured role chronology to a verified report without changing verdicts."""
import argparse
from pathlib import Path
import json
import zipfile

from testfix.config import load_settings
from testfix.reports import save_run


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id")
    args = parser.parse_args()
    settings = load_settings()
    base = Path(__file__).resolve().parent
    directory = settings.artifacts / args.run_id
    run = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    if run["schema"] != "testfix.project.v1" or run["summary"]["accepted"] != 1:
        raise ValueError("Receipt requires an independently accepted engineering run")
    developer = json.loads((base / "developer-output.json").read_text(encoding="utf-8"))
    run["tasks"][0]["diagnosis"] = developer["diagnosis"]
    run["tasks"][0]["attempts"] = 2
    events = []
    for filename, agent, message in (
        ("tester-output-v1.json", "tester", "GPT-5.6 分析官方需求并生成23个工程测试。"),
        ("developer-output-v1.json", "developer", "GPT-5.6 修改完整工程的路由、模板和CSS；原有与生成测试通过。"),
        ("reviewer-output-v1.json", "reviewer", "独立评审拒绝首版：非ASCII字面搜索与超长页码存在偏差。"),
        ("developer-output.json", "developer", "GPT-5.6 修复两项边界问题，保持原有功能与所有测试断言。"),
        ("reviewer-output.json", "reviewer", "独立评审通过最终候选，绑定13个运行资源的SHA-256。"),
        ("tester-output.json", "tester", "测试Agent修正目标工程导入配置；25个功能断言保持不变。"),
    ):
        output = json.loads((base / filename).read_text(encoding="utf-8"))
        events.append({"task_id": "Project/flaskr", "agent": agent, "stage": "captured_generation",
                       "time": output.get("captured_at", output.get("generated_at_utc")), "message": message})
    events.sort(key=lambda item: item["time"] or "")
    events.extend([{"task_id": "Project/flaskr", "agent": "supervisor", "stage": "local_evaluation",
                    "time": run["created_at"], "message": "读取已保存输出；六组独立子进程分别绑定原工程与候选源码，执行用例与文件完整性门禁。"},
                   {"task_id": "Project/flaskr", "agent": "verifier", "stage": "delivery",
                    "time": run["finished_at"], "message": "候选25个生成测试、20个独立验收及24个原回归通过；wheel构建和40项真实HTTP验证通过。"}])
    run["events"] = events
    run["coverage_evidence"] = "experiments/flaskr/coverage.json"
    save_run(directory, run)
    (directory / "patch.diff").write_text(run["tasks"][0]["diff"], encoding="utf-8")
    (base / "latest-run.json").write_text(json.dumps({"id": run["id"], "summary": run["summary"], "candidate_sha256": run["provenance"]["candidate_mapping_sha256"]}, indent=2), encoding="utf-8")
    archive_path = settings.artifacts / "flaskr-completed-project.zip"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted((base / "candidate").rglob("*")):
            if path.is_file() and not {"__pycache__", ".pytest_cache", "instance"}.intersection(path.parts):
                archive.write(path, path.relative_to(base / "candidate"))
        for folder in ("generated_tests", "acceptance_tests"):
            for path in (base / folder).glob("*.py"):
                archive.write(path, path.relative_to(base))
        archive.write(base / "requirements.lock", "requirements.lock")
        archive.write(directory / "patch.diff", "TESTFIX-patch.diff")
        archive.write(directory / "report.md", "TESTFIX-report.md")
    print(str(archive_path))


if __name__ == "__main__":
    main()
