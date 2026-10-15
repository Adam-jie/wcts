# 参赛测试数据与评测协议

本文件记录 2026-10-15 实际采集状态。**下载任务清单、能够执行一个工程、实际修复成功，是三个不同状态。** 现场结果必须来自本次运行产生的日志、补丁、验收记录和交付包，不能用任务总数冒充测试成绩。

## 已准备的数据

| 集合 | 实际任务数 | 范围 | 当前状态 | 来源与许可证 |
| --- | ---: | --- | --- | --- |
| rich-cli issue #147 | 1 | 完整 CLI 工程，4 项需求合同 | 已注册，可启动真实实验 | [Textualize/rich-cli](https://github.com/Textualize/rich-cli/issues/147)，MIT |
| Flaskr 详情、搜索、分页 | 1 | 完整 Web/SQLite 工程，4 项需求合同 | 已注册，可启动真实实验 | [Flask 官方扩展建议](https://flask.palletsprojects.com/en/stable/tutorial/next/)，BSD-3-Clause |
| SWE-bench Lite test | 300 | 12 个仓库的真实 issue 修复 | 已下载，待项目环境与评测适配；本次未运行模型修复 | [官方 Princeton 数据集](https://huggingface.co/datasets/princeton-nlp/SWE-bench_Lite)；[SWE-bench 框架 MIT](https://github.com/SWE-bench/SWE-bench/blob/02e7a74ffd0b707aab73d203fe87bdc7c76afc8e/LICENSE)，各源仓库保留各自许可 |
| BugsInPy | 501 | 17 个 Python 工程的缺陷元数据 | 已下载，待问题描述与历史环境适配；本次未运行模型修复 | [官方仓库](https://github.com/soarsmu/BugsInPy/tree/11c5f1eea954a42132cfd06bf257766a7963e0fd)；该固定快照未发现 LICENSE |
| HumanEval | 164 | 函数级补全辅助集 | 原工程已采集；不能计入完整工程修复成绩 | [openai/human-eval](https://github.com/openai/human-eval/tree/6d43fb980f9fee3c892a914eda09951f772ad10d)，MIT |

rich-cli 是未注入缺陷的真实上游问题。Flaskr 是官方扩展建议形成的明确实验需求，不能称为上游 Bug。HumanEval 中部分演示使用本工程人为构造的缺失实现，也不能称为原仓库真实缺陷。

SWE-bench Lite 数据卡仍写 11 个项目；本次固定 test 文件按 `repo` 实际去重是 **12** 个，下面提供分布。BugsInPy 早期论文报告 493 项；本次固定官方仓库中的 `bug.info` 实数是 **501**，不是把论文数字直接搬过来。

## 固定版本与采集证据

运行方式：

```powershell
.venv/Scripts/python.exe -X utf8 scripts/collect_benchmarks.py --dataset all
```

没有安装或执行下载仓库的任何代码，也没有调用模型。脚本只使用 Python 标准库进行有界下载、解析和校验。重复执行会检查已有目录哈希并返回；不会覆盖历史采集证据。校验失败须保留原目录并选择新的采集目的地调查。

固定值：

| 文件/集合 | 版本或 SHA-256 |
| --- | --- |
| SWE-bench Lite revision | `6ec7bb89b9342f664a54a6e0a6ea6501d3437cc2` |
| SWE-bench Lite 原始 test Parquet | `7a21f37b8bc179c7db5beeb14e88ac538ba283455c776e6b2535bbfb6e3551b4` |
| SWE-bench Lite 公开任务 JSONL | `18f7885c754e387d074927d3e7d196a02e903d2db3d32fcc96257b2c0d4dcb71` |
| BugsInPy revision | `11c5f1eea954a42132cfd06bf257766a7963e0fd` |
| BugsInPy 原始 ZIP | `88bc8fbaa2f347e318c5c585f89ec844ca27f7dd6761c3e66d63f4261a08fad1` |
| BugsInPy 公开任务 JSONL | `d3f1e8cf8a6e8c7ffb7393aea6d0e6f257bbf248cd28205cab717e20d0a7d38d` |

目录为 `data/benchmarks/<dataset>/<revision>/`，每个目录都有 `manifest.json`，记录全部文件 SHA-256、来源 URL、采集时间、任务数和仓库分布。SWE-bench 的三页官方 JSON 响应额外保存 `x-revision` 回执；与固定版本不一致、分页缺失或字段截断，采集立即失败。Parquet 原始文件独立保留，JSON 页面来自同一固定版本；当前工具不解析 Parquet。

公开文件只有 `public/tasks.jsonl` 和 SWE-bench 的 `public/stratified-pilot.json`。金标准补丁、测试补丁、测试名称、原始 Parquet/JSON、BugsInPy 含修复版本的原始归档放在 `evaluator-only/`。这个目录划分不是操作系统安全沙箱；当前 Agent 工具只读取已注册工程的公开源文件，不开放采集目录。未来 Docker 适配时，必须仅挂载公开任务和基线仓库，金标目录仅挂载到独立评测容器。

`list_benchmark_datasets(settings)` 只返回 JSON 安全摘要，不读取金标文件、不返回补丁或测试代码。`ready` 表示本地有注册适配器和运行环境；`collected` 表示公开任务索引已采集且校验通过；`environment_required` 和 `integrity_failed` 都禁止算成已验证成功。

## 代表性试验队列

SWE-bench Lite 采集后，在任何模型调用之前按仓库分层，每仓库选择 `SHA256("testfix-v1:" + instance_id)` 最小的两项，共 **24 项**。清单已落盘，选择规则可复算，用于后续适配和小规模对照实验；它不是 24 项已经测完的成绩。不要因为首次失败而悄悄替换任务。

实际分布：

| 仓库 | 项数 | 仓库 | 项数 |
| --- | ---: | --- | ---: |
| django/django | 114 | sympy/sympy | 77 |
| matplotlib/matplotlib | 23 | scikit-learn/scikit-learn | 23 |
| pytest-dev/pytest | 17 | sphinx-doc/sphinx | 16 |
| astropy/astropy | 6 | psf/requests | 6 |
| pylint-dev/pylint | 6 | pydata/xarray | 5 |
| mwaskom/seaborn | 4 | pallets/flask | 3 |

第一层：现场先用已注册 rich-cli（CLI 可见输出）和 Flaskr（真实 HTTP、SQLite 持久化）演示。任务选择在模型执行前固定，同时展示基线失败与修复后结果。

第二层：按上述 24 项清单适配 Linux/Docker、安装依赖、复现基线失败并封存环境。SWE-bench 的官方评测基于仓库测试，参考[官方评测流程](https://www.swebench.com/SWE-bench/faq/)；当前 TestFix 还没有这些任务的正式适配器，网页不能直接把它们当作可运行工程。

第三层：有稳定环境后扩展到全部 300 项，并额外保留不参与 Skill、调度权重、提示词调优的最终留出集。按 `repo` 分组划分调试/验证/留出，避免相同项目同类缺陷跨组泄漏。SWE-bench Lite 是公开基准，无法保证基础模型预训练未见过问题；不得宣称“无污染盲测”。

BugsInPy 用于增加工程多样性。其 501 项中 **168 项**上游 `buggy_commit_id` 是缩写，执行前必须在目标仓库解析并封存完整 40 位提交。还需补公开自然语言问题描述、Python 3.6/3.7/3.8 等历史环境和第三方依赖。不能把缺陷元数据直接当成已可运行的模型任务。上游许可未声明，原始材料不随本工程公开源码包分发。

## 每次实验怎么判定

1. **封存输入**：任务 ID、issue/需求、完整基线 commit、依赖锁与镜像摘要、独立验收/回归测试哈希、模型请求名、Skill 版本、调度策略、预算、随机种子和一次运行 ID。先写任务清单和排除规则，再开始推理。
2. **验证基线**：缺失功能的目标测试确实失败；原有回归先有基线结果。环境启动失败、依赖下载失败、已修复任务、测试无效分别记录；必须报告总选中数、可执行数、排除原因，不能用排除方式抬高成功率。
3. **运行总控与员工**：保留实时任务 DAG、出度/入度、分配原因、依赖交接、模型动作与工具回执、资源/预算消耗。动画只显示日志中已经发生的事件。
4. **独立判断**：生成测试可见，参考修复、独立验收、冻结回归不可见且不可修改。补丁必须能应用、代码能导入/构建、程序能真实启动、需求验收通过、原有回归保持；“模型说完成”不是通过条件。
5. **交付与复现**：保存补丁 diff、候选 SHA、安装包/工程包 SHA、完整日志、JUnit、真实 CLI/HTTP 输出。脱离开发工作目录，在干净环境重新安装并运行验收。失败尝试也保留。

主指标按**任务**计算：`Resolved@1 = 首次完整通过独立验收且无回归破坏的任务数 / 预先固定的可评测任务数`。同时报告总选中数和环境可运行率。允许修复多轮时另列固定轮数/预算下的最终成功率，不混写为首次成功率。

辅助指标按**断言/测试项**计算：目标验收通过数/总数、原有回归通过数/总数、构建成功、真实启动成功、CLI/HTTP 场景通过数，以及受保护测试被修改的次数。不得把 21 条验收算成 21 个独立工程，也不得用大量简单断言掩盖一个核心需求失败。

效率指标：端到端墙钟、模型调用次数、提供方实际报告的 token、测试耗时、峰值模型并发和重试轮数。缺少实际计费单价时报告 token 与耗时，不编造节约金额。出度/CP-SAT 创新效果需要相同模型、相同任务、相同预算下的串行、无出度、完整策略对照，至少重复三次并报告中位数与离散程度；现有两个工程的成功不证明算法全面优越。

## 现场建议展示

现场选择一个预先公开的失败任务，展示原程序症状、用户需求、3D 流程中的真实派工与交接、第一次失败测试、补丁差异、独立验收、安装后输出，最后下载本次日志与工程包。准备一份标明“历史真实运行回放”的离线备份，网络故障时明确切换，不能把回放当实时推理。

比赛平台采用程度、提交附件与答辩规则以最终赛题细则为准。验证重点应包含 AgentArts/CodeArts 的实际调用与部署证据；本地动画和基准成绩不能代替指定华为技术的真实采用。
