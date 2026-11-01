# FEA-Bench 官方源码与 TestFix 扩展

## 基底

`third_party/FEA-Bench` 是 [microsoft/FEA-Bench](https://github.com/microsoft/FEA-Bench) 的官方源码快照，版本 `fb3c11274796f6057bf447410540fcf2fa1d90b1`，包含原始 MIT 许可证。来自 GitHub codeload 原始归档；原始 28 个文件与归档逐字节一致。不要直接改动该目录；TestFix 的代码放在项目自己的 `scripts/`、`testfix/` 和 `tests/` 中。更新上游版本应作为明确的版本更新并重新校验。

官方源码负责论文的数据构建、基线推理及评测说明；其中完整的 Standard 数据集需要官方脚本从 GitHub 重建。TestFix 不是官方的多 Agent 实现，也不改变官方源码、参考补丁或裁判。

## 目前接入的能力

`scripts/run_feabench_official_team.py` 是独立适配入口：读取重建后的 Standard 测试集或从它导出的 JSONL，按 `instance_id`、`repo`、`base_commit` 与本机准备的案例核对，只抽取 `problem_statement` 和 `hints_text` 等公开任务信息。`patch`、`test_patch`、`FAIL_TO_PASS`、`PASS_TO_PASS` 不复制给模型。输入的来源和版本应在实验材料另行记录；仅有相同字段不能证明数据来自官方构建。

适配器调用已有 `scripts/run_feabench_pilot.py --team` 的 CodeAgent、WCTP、CP-SAT 分配、员工 Skill 和公开测试反馈。运行结束有候选补丁时，生成官方格式的 `predictions-feabench.jsonl`。独立验收仅在模型任务结束后执行；输出 JSONL 格式相同不等于官方评测已运行。

目前此桥接仍限定于本机已准备的 SQLGlot 案例、Windows 原生裁判和一个模型槽；不支持批量运行任意仓库，也不是论文的单轮 BM25/Oracle 实验或 AgentArts 部署。失败、无补丁与模型服务错误必须保留，不得算作解决。

## 使用

先按官方 README 构建 Standard 数据集和独立的 SWE-Bench/Docker 评测环境。官方 Standard 存盘目录需要 Python `datasets` 包读取；也可以先从官方数据导出 JSONL。官方项目的完整依赖包含 vLLM，平台要求需按官方项目分别确认，不应在现有 Windows 环境中盲目安装整套依赖。

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/run_feabench_official_team.py `
  --standard-dataset <官方Standard数据目录或JSONL> `
  --case-template artifacts/fea-pilot-20261015/public/sqlglot-415/input.json `
  --case-output artifacts/my-study/public-case.json `
  --run-dir artifacts/my-study/run-001 `
  --model gpt-5.6-luna --execute-model
```

不带 `--execute-model` 只做预检，不调用模型；输出路径必须是新路径。完整任务集对比仍需为每项任务准备匹配的基线仓库和隔离运行环境，并使用官方 SWE-Bench 评测器批量判分。

## 本机检查

已从上游固定版本取得完整源码，归档 SHA-256 为 `c4e5dd35a5ab01c9570a228c3599eda6b213461dd03bbdc5750652ae9c5254fd`。源码与原始归档的 28 个文件逐字节比较无改动，核心文件哈希及 200 项 Lite 清单纳入测试。适配器使用基于已知任务元数据和公开 PR 文本构造的 Standard 格式**测试样例**通过预检，模型调用 0 次；该样例不是官方重建后的 Standard 数据，不产生研究成绩。

本机目前没有 `datasets` 包，也没有可用的 Docker 命令，故官方数据构建和 Docker 裁判尚未跑通。模型服务上一轮返回容量不足；上述接口测试不能替代真实模型和官方评测。
