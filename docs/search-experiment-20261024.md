# 多树工程补全本机实测：2026年10月24日

本轮已完成一次真实模型驱动的完整 Web 工程补全，并保存失败、中断、审核、分支与最终验证证据。原始记录为 [实验JSON](../artifacts/search-implementation/experiments.json)，通过记录为 [完整报告](../artifacts/6f91bb3ac2ef4365b5b02d2e1422c5f4/report.json)。

## 项目与功能

使用 Pallets 官方 Flaskr 完整博客示例，来源 [固定 GitHub 提交](https://github.com/pallets/flask/tree/2c1b30d0503cfb064f1cb252e6614a06915a362a/examples/tutorial)，Flask 3.1.2。原始快照含23个文件，保留注册、登录、SQLite、文章增删改、模板、资源与原有测试。

补全文章公开详情、标题字面量搜索、每页五篇分页，方向来自 [Flask官方后续开发建议](https://flask.palletsprojects.com/en/stable/tutorial/next/)。路由、状态码、ASCII页码、排序和特殊字符语义是事先登记的本实验合同。没有人为删除原版代码，也没有把它描述成上游Bug。这是完整示例工程，规模较小，不计入 Uptime Kuma、Paperless-ngx 等大型仓库或 MCU/MPU 实板的成绩。

## 实际执行结果

最终运行 `6f91bb3ac2ef4365b5b02d2e1422c5f4`，状态 `completed`，接受1项工程任务。下表各类检查分别统计，不能相加当成独立工程任务数。

| 检查 | 原始工程 | 交付候选 |
| --- | --- | --- |
| 本次冻结生成测试 | 0/21 | 21/21 |
| 原有回归 | 24/24 | 24/24 |
| 独立验收 | 0/20 | 20/20 |
| wheel构建 | 未作为该列基线 | 成功 |
| 从构建wheel执行真实HTTP，含SQLite重启 | 未作为该列基线 | 40/40 |

三个隔离候选的公开生成测试均21/21、原回归均24/24，奖励均1.0。选择 `node-1`，候选SHA256为 `4e5f881c7d10ae1062a8bc6ad7ecbb878438b825f5fd89634e3d0a1cc5ae6ccf`。只有该候选接受最终独立验收；不能把另两个候选算作独立验收通过。实际搜索深度为1，因三条入口均已通过公开测试而停止；本例没有触发深层回溯，深层选择/回传只另有框架回归证据。

所有10个最终verdict均为真，包括源码与用例身份绑定、回归保持、保护文件未变、评审哈希匹配、构建、HTTP与必需交付检查。

本次恢复运行耗时884.934秒，37次新模型调用，另外复用5项公开前置产物；新调用provider报告1133814 tokens，用量完整。它请求 `gpt-5.6-sol`，CLI未回报实际服务模型名，不能把请求参数说成额外的服务端身份认证。Token不是账单费用，也不包含源检查点此前已消耗的调用。

源检查点 `e9127693d61c40b0a9b16a07b08c1db7` 保持失败状态。恢复只复用公开规划、探索、定位、分析及最后一版测试提案；新鲜模型再次审核和修正测试，然后重新执行基线、搜索、修复、评审及独立验证。不能称为42次新推理或完全冷启动运行。

## 大模型与执行器分别做什么

模型负责公开需求拆解、搜索位置、缺口矩阵、测试代码/步骤、断言审核、诊断、各分支重新派工、员工工具动作/补丁和最终评审。宿主负责文件范围与哈希检查、OR-Tools CP-SAT约束、出度派工、WCTP实际图执行、串行提交、pytest、数值奖励、候选提升和确定性验收。模型自己说通过，不算通过。

实际测试步骤：冻结生成用例 → 核对基线/候选用例身份与导入位置 → 执行同一套生成测试 → 执行原有回归 → 独立验收 → 构建wheel并从wheel启动HTTP应用 → 验证注册登录/CRUD/搜索/分页/权限/转义 → 保留数据库重启并验证持久化。模型步骤文本与实际用例结果在工作台分开显示，未把步骤说明逐条冒充执行记录。

## 失败与中断保留

| 运行ID | 状态 | 新模型调用 | 复用产物 | 已报告tokens | 原因/处理 |
| --- | --- | --- | --- | --- | --- |
| `311786e056b646d48831d4ba7ed72ee8` | interrupted | 36 | 0 | 894510 | 生成测试JUnit路径随候选目录变化，宿主身份映射缺失；中断并保留原证据 |
| `52d79d436c344a42af3ea9cd9060cbe8` | interrupted | 14 | 0 | 367650 | 生成的手工HTMLParser错误收集链接后的作者与正文；中断 |
| `ce50b454b98d4ba4afa4469d8dc13a8c` | failed | 9 | 0 | 231731 | 测试语义审核拒绝错误辅助解析和过度具体断言 |
| `4d0ab5a1d07144dbb5dd958fd1be8f7f` | failed | 4 | 0 | 65816 | 模型定位调用120秒超时；当时尚无一次超时恢复 |
| `e9127693d61c40b0a9b16a07b08c1db7` | failed | 16 | 0 | 447049 | 六轮审核反馈在链接文本和空状态文案上反复；测试未冻结 |
| `6f91bb3ac2ef4365b5b02d2e1422c5f4` | completed | 37 | 5 | 1133814 | 修正审核职责后从公开阶段恢复，最终通过 |

这些是研发调试中不同实现版本的运行，不能计算为固定算法的1/6成功率。前两项中断另有未完成调用用量不可得，JSON的 `interrupted_pending_call_usage` 和 `usage_complete` 单独标明。表中token仅为已保存调用的已报告部分；中断快照的报告时长也不能当作已核实的全部墙钟时间。

## 可操作演示

- 连线工作台：[本次运行](http://127.0.0.1:8765/engineering?run=6f91bb3ac2ef4365b5b02d2e1422c5f4)。工程流程、实际员工依赖、候选搜索树、测试步骤/结果四个视图；可拖动、缩放、展开画布、点击查看原始证据。
- 实际交付应用：[Flaskr](http://127.0.0.1:8774/)。独立演示数据库通过应用路由创建13篇文章，未修改验收数据。
- 浏览器已实际完成：搜索 `engineering 01` 返回1篇；打开 `/1` 显示详情；点击Next到第2页显示08至04五篇并有Previous/Next；无结果查询显示 `No articles match your search.` 且无文章/分页链接。
- 原始源码/测试/补丁/305项归档证据：[交付ZIP](../artifacts/6f91bb3ac2ef4365b5b02d2e1422c5f4/completed-project.zip)。SHA256 `ddace7e7248a544e56336efc9eedabacc54f02ce7f0ff4cc166e678325518fb8` 已复核。包含3个分支的源文件、员工回执及工具日志，并含冻结验证依赖与REPRODUCE.md。wheel本体未塞入ZIP，按其中命令从交付源代码重新构建。

![实际候选搜索图](../artifacts/search-implementation/workbench-forest.png)

![实际补全详情页](../artifacts/search-implementation/flaskr-detail.png)

## 实现与审查口径

机制参考 [SWE-Search ICLR 2025](https://proceedings.iclr.cc/paper_files/paper/2025/hash/a1e6783e4d739196cad3336f12d402bf-Abstract-Conference.html) 和 [Forest-of-Thought ICML 2025](https://proceedings.mlr.press/v267/bi25a.html)。本实现以实际公开测试计值，稀疏激活一条分支，保留宿主约束；不是论文逐项复现。没有运行固定预算的多项目、多随机种子线性/单树/森林对照，因此不报告提升百分比。

本次真实恢复运行在源快照 `1f981e0ce425dc3ad031e1db9a6c69b24a64d3be5500807e5f801b00ea236230` 上启动。之后发现并修正的源审核历史传递、Token证据完整性和阶段收尾状态，通过专项回归与独立源码复审；不回写本次原始JSON或ZIP，不追认为这些修正已在旧运行中执行。旧记录总控raw状态running但active_model_calls=0的显示有单独“已结束”派生及说明，其他原始状态和成绩保留。

最终框架广覆盖检查352 passed、2 skipped（本机符号链接权限）、1依赖弃用提示；状态收尾改动后另有37项相关检查通过。前端44项通过，TypeScript/生产构建通过。这些框架检查含合成边界fixture，与21项真实生成测试分开计数。最终独立复审版本见 [审查处置](../artifacts/search-implementation/review-disposition.json)，散列快照与检查见 `artifacts/search-implementation/`。

本机可信Python执行器不是恶意代码的OS沙箱；哈希防修改不保证文件不可读。任意不可信大型工程、真实设备及AgentArts云端需各自的运行适配与独立验收，本轮不宣称这些已通过。
