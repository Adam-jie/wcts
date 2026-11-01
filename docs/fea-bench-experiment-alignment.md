# FEA-Bench 实验输入输出对齐

## 目的和范围

与 [FEA-Bench 论文](https://aclanthology.org/2025.acl-long.839/)及[官方实现](https://github.com/microsoft/FEA-Bench)对齐任务、补丁和评测接口。格式一致不代表已经完成官方评测，也不代表不同方法使用了相同的信息、预算或运行环境。

## 输入

正式对比应固定官方数据集版本、Lite 200 个任务及各任务的 `instance_id`、仓库基线提交、需求描述和允许提供的上下文。论文表 2 的 Brief 提供新增组件签名，Detailed 还提供文档与非 Python 修改信息；Oracle 包含参考补丁涉及文件，BM25 按 PR 内容检索文件。选择具体一列时应明确这些设置；不得将标准答案代码或测试补丁作为模型输入。论文表 2 使用单轮生成，Agent 系统多轮修改属于不同方法设置，需要单独标注调用次数及预算。

目前本机预实验使用 `tobymao__sqlglot-415`，该任务属于官方 Lite，但只提供 PR 文字及原版代码，不提供论文中的新增组件提示；在 Windows 原生环境执行，不能称为表 2 的 BM25 或 Oracle 实验。已有预先固定的 10 项抽样清单，但其余 9 项尚未运行。

## 输出

运行完成的原生实验可以转换为官方预测 JSONL，每行包含 `instance_id`、`model_name_or_path`、`full_output` 和 `model_patch`。`model_name_or_path` 记录请求型号；服务若未返回实际型号，不视为验证了实际型号。`full_output` 在本适配器中留空，真实调用记录仍保存在原运行目录。补丁不存在时导出报错，不能将缺失补丁假装为正常结果。

```powershell
.\.venv\Scripts\python.exe scripts/export_feabench_predictions.py `
  --run-dir artifacts/fea-pilot-20261015/luna-trial-20261029 `
  --output artifacts/fea-pilot-20261015/luna-trial-20261029/predictions-feabench.jsonl
```

同一实例的不同方法或重复运行应输出到不同文件。为了保留原始结果，输出文件已存在时程序不会覆盖。Windows 上历史补丁中的 CRLF 在导出时转换成 LF；新的原生运行直接生成 LF 补丁。

## 独立评测与统计

正式实验按[官方说明](https://github.com/microsoft/FEA-Bench#4-running-model-evaluation)准备数据与固定版本的 SWE-Bench 评测器，在 Docker 环境执行，保存 `total_instances`、`submitted_instances`、`completed_instances`、`resolved_instances`、`unresolved_instances`、`empty_patch_instances` 和 `error_instances`。主指标为 resolved 数除以固定任务总数，失败、无补丁和环境错误必须保留并分别报告，不能只统计运行成功的样本。论文还报告补丁应用率；本研究可另外报告回归、调用次数、耗时和 token，但不能用这些替代官方 resolved 指标。

若采用论文的 Brief/Detailed 提示，生成代理只能访问官方设置允许的信息；用于独立判分的测试和参考补丁不能通过日志或反馈泄漏给代理。所有方法须固定相同任务与评测环境；模型、提示、工具、预算、重试和交互轮数应分别记录，论文的单轮方法与我们的多轮方法不能描述成相同推理条件。

## 当前验证与缺口

`luna-trial-20261029/predictions-feabench.jsonl` 已从真实运行生成；其 `model_patch` 已对 SQLGlot 原版仓库运行 `git apply --check` 通过。该运行在本机独立目标测试中 0/1、原有回归 140/140；未执行官方 Docker harness，不能算作官方 Lite 的 resolved。输出 JSONL 的一致性测试与原有 FEA 驱动测试共 51 项通过。

原生 pilot 已增加 `--team` 模式，通过 WCTP 执行员工任务，由 CP-SAT 按依赖和资源分配，员工交接须经 CodeAgent 检查；后续独立裁判仍在模型工作结束后执行。此模式目前使用一个模型槽，串行实际模型请求；它不代表已经在 AgentArts 上运行，也不是官方评测。默认不加 `--team` 的历史实验仍为串行 Skills。

第一次真实 `--team` 试跑，总控生成了仓库调查和实现两个依赖任务，CP-SAT 和 WCTP 确实执行；调查员工连续搜索后用尽步数，未交接，运行失败。为此增加最终步骤交接提示；第二次试跑发现步骤上限与 Skill 合同不一致而停止，现调整为合同允许的五次。原始失败记录保留在 `luna-team-trial-20261029/report.json` 和 `luna-team-handoff-20261029/report.json`。此前另一条试跑在第二轮诊断中给出带行号的路径，被误判为越权，现仅在去掉行号后仍匹配授权路径时接受，原始模型输出仍保留。

第三次试跑中调查员工按步骤交接，但主控认为源码定位证据不充分而拒绝，运行未完成；记录位于 `luna-team-contract-20261029/report.json`。随后增加一次工具操作搜索多个字面关键词，并将此前实际的代码定位、需求分析和公开测试反馈提供给员工及主控；这些改动不将主控拒绝改为自动通过。

第四次 `luna-team-multisearch-20261029` 真实调用中，调查员工完成交接且总控批准，随后启动补全员工；请求 `gpt-5.6-luna` 的 Codex CLI 返回模型服务容量不足，补全未产生代码改动。记录显示 16 次模型调用、提供方报告 293930 tokens，未返回可核实的实际模型名称；空补丁和失败报告保留，不视为解决任务，也不能计算官方榜单得分。

该运行结束后独立本机核验了未修改候选：目标测试 0/1，原有回归 140/140；结果位于 `luna-team-multisearch-20261029/post-run-evaluation/evaluation.json`。此后验结果没有反馈给模型。

包含 PR 标题后的串行预实验 `luna-title-test-correction-20261029` 曾产出候选，冻结的模型生成测试由基线 1/7 变为候选 3/7；第二轮诊断因带行号路径而停止，未能继续模型修改。模型工作停止后，独立执行的本机检查为目标测试 0/1、原有回归 140/140，验收结果见同目录 `post-run-evaluation/evaluation.json`。该检查未作为模型反馈，仍不是官方 Docker 得分。

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/run_feabench_pilot.py `
  --case artifacts/fea-pilot-20261015/public/sqlglot-415/input.json `
  --run-dir artifacts/fea-pilot-20261015/my-team-run `
  --model gpt-5.6-luna --team --execute-model
```

还需构建官方 Standard 数据与评测环境、导入相应 Brief/Detailed 与 Oracle/BM25 提示，在不同仓库批量运行并与对照方法使用同一评测器。当前本机无可用 Docker 命令；单案例原生结果不能作为完整 Lite 的 resolved。
