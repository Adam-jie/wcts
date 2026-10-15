# TestFix-Agent

面向软件缺失功能的多 Agent 自动化测试与代码补全系统：输入公开需求与注册代码仓库，生成并运行测试，根据失败证据修复代码，经过独立验收、原有回归和真实运行检查后交付工程。

当前支持 Python 函数模块、Pallets Flaskr 博客和 rich-cli 命令行工程；没有声称支持任意仓库或任意语言。总控将需求拆成员工任务图，使用 Skill 合同、OR-Tools CP-SAT、出度优先派工、文件冲突控制与证据哈希约束协作。

## 本地运行

需要 Python >= 3.11 与 uv。以下 Windows PowerShell 命令在仓库根目录执行：

```powershell
uv sync --extra dev --locked
.\.venv\Scripts\python.exe -X utf8 -m testfix.cli import-repo examples/order-feature/task.json
.\.venv\Scripts\python.exe -X utf8 -m testfix.cli run --provider replay --tasks Repo/order-discount
```

回放验证不调用大模型，只验证执行流程和门禁。真实模型不会在失败时回退为回放。可选本地控制台通过 `scripts/start.ps1 -Background` 启动在 `127.0.0.1:8765`；实验可完全使用 CLI，无需网页。

## 完整工程与真实模型

```powershell
.\scripts\setup-richcli.ps1
.\.venv\Scripts\python.exe -X utf8 -m testfix.cli complete-project --project Project/rich-cli --provider codex_cli --model gpt-5.6-sol --max-rounds 2 --wall-seconds 5400 --token-limit 900000
```

该命令发起真实模型调用，需要本机已配置的 Codex CLI 登录与对应模型权限；模型名称可通过参数替换。请求型号与服务端实际回报型号分别记录，服务未回报时不补写。另支持 `openai`、`ollama`、`agentarts` 传输，见 [模型传输](docs/project-model-transports.md)。`.env.example` 仅含空配置，认证由运行者本地提供。不要提交 `.env`、OAuth 文件、模型轨迹或运行产物。

Flaskr 可运行 `scripts/setup-project.ps1` 准备隔离环境，再使用 `--project Project/flaskr`。这两个工程的上游源码、独立验收和依赖锁随仓库提供；运行中的候选与测试保存在新建的 `artifacts/<run-id>/`，不覆盖基线。

## WCTP 外部依赖与并行协作

**公开仓库不包含 WCTP 原源码，也不授予其许可证。** 普通九能力完整工程入口不需要 WCTP；增加 `--request` / `--request-file` 的动态总控与员工协作需要运行者另行获得授权的固定版本 WCTP 源码，并设置环境变量：

```powershell
$env:TESTFIX_WCTP_ROOT = 'D:/dependencies/wctp'
.\.venv\Scripts\python.exe -X utf8 -m testfix.cli complete-project --project Project/rich-cli --request-file examples/rich-cli-parallel-request.txt --provider codex_cli --model gpt-5.6-sol --model-slots 2 --wall-seconds 7200 --token-limit 1000000
```

这是配置路径示例。适配器加载前核对七个依赖文件的固定 SHA-256，缺失或不符时明确失败，没有替代执行器。实际调用原 `GraphExecutor.execute`；独立员工模型决策并行，宿主在批次完成后协调工具与提交。就绪任务按直接出度、声明关键路径、去重后继数和 ID 排序，依赖、槽位、预算与文件冲突均不能被优先级越过。见 [WCTP 接入与边界](docs/wctp-parallel.md)、[总控与员工](docs/requirements-team.md)。

## 检查与证据口径

无需 WCTP 的核心检查：

```powershell
uv run python -X utf8 -m pytest -q tests/test_contracts.py tests/test_scheduling.py tests/test_team_planning.py tests/test_skills.py tests/test_concurrency.py tests/test_project_models.py --basetemp artifacts/public-core-check
```

完整套件包含真实 WCTP 集成测试；运行前需配置上述授权依赖：

```powershell
uv run python -X utf8 -m pytest -q --basetemp artifacts/public-full-check
```

发布前原本地工程完整回归为 **687 passed、3 skipped**。跳过项为 Windows 符号链接权限场景。此历史计数不替代当前机器的重跑结果。

2026-10-15 的 rich-cli 实验在固定上游真实缺陷上完成两名探索员工的并行决策，再由修复员工处理。生成测试 **3/19 → 19/19**，独立验收 **3/21 → 21/21**，上游测试 **1/1**，冻结行为回归 **13/13**，wheel 构建成功，安装后模块入口 CLI 检查 **16/16**。两个批次实际重叠 7.210 秒和 12.807 秒，峰值 2；单次实验不能证明调度加速或算法优越性。补丁含额外格式与 docstring 改写，不称为最小补丁。说明见 [rich-cli fixture](experiments/rich-cli/README.md)。

历史原始模型输入输出、本机路径、聊天记录及运行产物不在公开仓库；本仓库提供重跑代码、固定源码与验收合同。可复现运行会产生新的结果和证据，不保证模型每次生成相同补丁。

## 架构与边界

1. 总控与九项能力：规划、探索、定位、需求分析、测试生成、诊断、修复、评审、确定性验证。
2. 九项项目 Skill 正文与机器合同：`testfix/skill_library/`；借鉴来源与原创边界见 [Skill 设计](docs/skills-design.md)、[来源清单](docs/skill-sources.json)。没有打包第三方 Skill 或声称自写合同来自外部原作。
3. CP-SAT 约束声明时长、Token、依赖与资源，求解状态不等于工程通过；见 [整数模型](docs/ilp-model.md)。
4. 验收、回归与生成测试分开冻结，补丁与评审绑定候选哈希。有限测试通过只覆盖声明的合同与输入域。
5. 华为云 AgentArts SDK 入口和 HTTP 契约已实现；**尚未完成真实云部署**。CodeArts、RDS、Redis 的真实资源调用也未作为已完成成绩。见 [AgentArts 接入](docs/agentarts.md)。

执行已检查的 fixture 使用受控子进程；文件权限与 Skill 不等于操作系统安全沙箱。不应直接运行未经检查的恶意仓库，也不应将本地开发控制台直接暴露公网。

## 许可证

本项目原创代码采用 MIT；第三方 fixture 保持原许可证：rich-cli 为 MIT，Flaskr 为 BSD-3-Clause。WCTP 不随仓库分发。完整范围见 [LICENSE](LICENSE)、[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
