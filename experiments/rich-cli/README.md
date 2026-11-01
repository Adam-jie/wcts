# rich-cli：完整 GitHub 工程的真实模型修复实验

上游：[Textualize/rich-cli](https://github.com/Textualize/rich-cli)，MIT；固定提交 `46f4d2469097be395558768714a5f07ebccf1412`，原始 34 个文件的逐文件 SHA-256 保存在 `source.json`。目标来自 [真实问题 #147](https://github.com/Textualize/rich-cli/issues/147)：CSV 表头和单元格中的 `[red]literal[/red]` 等文本被解释为 Rich 格式，字面值丢失；`[/bold]` 还会导致异常退出。实验没有人为挖空或破坏上游代码。

公开合同 `requirements.json` 包含四项：CSV/TSV 数据逐字保留；文件、stdin、分隔符和 head/tail 行选择保持一致；HTML/SVG 导出保留可见字面文本；保留 JSON、Markdown、参数错误和有意的 `--print` 格式行为。补丁只允许写 `src/rich_cli/__main__.py`，上游源码其他文件和全部测试冻结。

## 评测与真实调用

模型仅收到公开需求、完整公开文本快照及公开测试反馈，没有获得独立验收源码、固定 CLI 检查期望、上游修复或 issue 的修复建议。九项能力包含八项真实模型调用与第九项确定性验证，采用 Skill 1.2.0 的 `python_package` profile 和真实 CP-SAT。调用复用本机 Codex CLI 的 OAuth 登录，请求 `gpt-5.6-sol`；CLI 未回报 actual_model 时保留 null。

- 上游原有 pytest：1 项，原始工程通过。
- 实验前冻结的既有行为回归：13 项，原始工程通过；它们不是上游原有用例。
- 实验前冻结的独立验收：21 项，原始工程 3 通过、18 失败。
- 从实际构建的 wheel 启动 CLI：16 项，原始工程 6 通过、10 失败。
- 生成测试数量、补丁轮数、耗时及用量以各次运行报告为准。

上述验收、回归、CLI 检查在首次模型生成前封存于 `pre-model-seal.json`。Python 3.13 独立环境依赖按 `requirements.lock` 固定。pytest 和 CLI 工具固定 UTF-8、`COLUMNS=200`、`LINES=60`，导入绑定证据保存在每次工具调用中。pytest 从指定的 src 树导入，交付 CLI 从本次 wheel 解包的包导入。

## 保留的失败及环境校准

第一次真实运行 `ef962314c08c4322aee6eca2b675a7da` 耗时 447.361 秒，10 次真实模型请求，最终 rejected，未发布交付包。首轮补丁将生成测试从 3/15 提升到 14/15，回归 14/14；剩余 Markdown 标题用例在 pytest 的默认 80 列终端被裁掉。该次运行没有进入独立验收，不补记为成功。

独立复现确认原始源码和首轮 CSV 补丁均会遇到此宽度差异。仅把测试进程视口与 CLI 一致后，原始源码上的同三份生成测试逐字节不变：Markdown 恢复通过，11 项 CSV 缺陷仍失败，即 4/15；回归保持 14/14。校准不是模型实验，模型调用为 0。

原始失败报告：`artifacts/ef962314c08c4322aee6eca2b675a7da/report.json`。
独立环境复现：`artifacts/probes/package-markdown-viewport-review.json`。
校准摘要：`artifacts/1db4c00a5a0d4a86845407025e79a4f9/environment-calibration.json`。

第二次真实运行 `b92abf04bcbe4ca18eeca5bcd9049ecf` 从相同原始源码重新生成 14 项测试，基线 2/14；首轮补丁使生成测试与回归均为 14/14。但 JUnit 把工作区 baseline/candidate 路径计入了上游测试身份，控制器严格配对拒绝该结果。模型正确指出公开测试全过、拒绝提出无依据的新补丁；该次运行最终 failed，耗时 353.904 秒、10 次模型调用，同样没有发布交付包。

已为每组 pytest 固定收集根目录，不改断言或精确身份门禁；新增真实双根配对及删除、改名、参数 ID 变化的拒绝测试。控制器遇到“全部通过但身份不匹配”立即归为评测或测试完整性故障，停止要求模型修改业务代码。后续新运行使用同一原始上游源码、同一预封存的独立测试与固定 CLI 检查。

## 第三次完整自动运行

运行 `2e350d21b5f04011a90ce5fa2c056656` 已 completed/accepted，耗时 **351.733 秒**，一次补丁轮次，九项能力全部完成。八次真实 Codex CLI 请求均指定 `gpt-5.6-sol`，服务未回报实际模型名，`actual_model` 保留 null；provider 报告 token 合计 **238027**，不作为账单费用证明。生成测试和补丁均由本次模型调用新生成，没有复用前两次补丁。

| 验证 | 原始工程 | 最终候选 |
| --- | --- | --- |
| 本次大模型生成测试 | 3/15 | 15/15 |
| 上游原有测试 | 1/1 | 1/1 |
| 实验前冻结的行为回归 | 13/13 | 13/13 |
| 独立验收 | 3/21 | 21/21 |
| wheel 构建 | — | 1/1 |
| 固定真实 CLI 检查 | 6/16 | 16/16 |

模型执行规划、探索、定位、需求分析、生成 pytest、依据真实失败日志诊断、输出完整允许文件和独立代码评审；宿主执行所有测试、wheel 构建和真实进程验证。模型生成的九项测试步骤、需求覆盖映射、公开请求响应及来源版本均在报告中。测试覆盖 CSV/TSV 的表头与单元格字面值、Unicode/未知或未闭合标记、stdin/文件/行选择、HTML/SVG 可见文本，以及原有 intentional `--print`、JSON、Markdown 等行为。

原始报告：`artifacts/2e350d21b5f04011a90ce5fa2c056656/report.json`；原始交付 ZIP 与报告 SHA 保持原样。候选全仓 SHA-256 为 `781bef3aa59cad4e909e7a117ff8330559aacbad7dac4867925ec645a769b4c1`，只改变 `src/rich_cli/__main__.py`。该文件除 CSV 修复还包含排版与等价清理，保留真实模型补丁；不能称为只有两行的最小修改。离线完整性核验记录为该目录的 `evidence-verification.json`。

交付说明另补固定 UTF-8、200×60 视口、pytest suite root 和锁定 build backend 的命令。`rich-cli-verified-project.zip` 是记录原 ZIP SHA 的派生包，只更新复现 README 并添加核验/安装证据；源码、测试与原始模型记录逐字节不变，派生包 SHA 单独保存在 `verified-project-manifest.json`。

额外创建全新虚拟环境，离线安装同一 wheel 与锁定依赖，直接运行安装生成的 Windows `rich.exe`：CSV 文件字面值、stdin 未闭合标记、intentional print、JSON stdin、help **5/5 通过**。这是独立于上述 16 项门禁的附加检查，不能合并冒称原关卡数；安装后的 `__main__.py` 与候选文件 SHA 完全一致。记录在该运行目录的 `fresh-install/receipt.json` 和派生包 manifest。派生 ZIP SHA-256 为 `3e8462741d66aa8db91c612f72a61aa06a9b23089b9714b171614830f8f8de3e`。

框架最终全量检查：**549 passed，3 skipped**，JUnit 为 `artifacts/rich-cli-framework-tests-ready.xml`。三次运行不是同一评测器版本的统计样本，不据此计算模型成功率。

## 本地重跑

先初始化主 TestFix 环境，然后运行：

```powershell
.\scripts\setup-richcli.ps1
.venv/Scripts/python.exe -X utf8 -m testfix.cli complete-project --project Project/rich-cli --provider codex_cli --model gpt-5.6-sol --max-rounds 2 --wall-seconds 5400 --token-limit 900000
```

setup 校验原始源码和评测封印后更新本机注册路径，不重写历史封印。重跑命令会发起新模型请求。每次工作区、测试、补丁、评审和真实运行日志独立保存在 `artifacts/<run-id>/`，仅最终通过才发布 `completed-project.zip`。

通过只证明所列固定版本与测试域，不代表任意仓库或全部输入的正确性；本地实验不构成 AgentArts 云端部署证据。
