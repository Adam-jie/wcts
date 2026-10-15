# TestFix-Agent 开源实现与平台接入研究

核查日期：2026-10-14（北京时间）。本页记录实现依据与建议；它不代表已经完成华为云部署，也不替代赛题附件审核。

## 1. 从开源项目提炼的实现机制

| 一手来源 | 已核实机制 | 在本工程中的应用建议 |
| --- | --- | --- |
| [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api) | `StateGraph` 用状态、节点、边定义可循环工作流；节点可以是模型调用或普通代码，状态更新由 reducer 合并；支持 checkpointer、条件边与步数限制。 | 给每次运行维护明确状态；分析、生成测试、补丁、验证分别产出结构化结果；通过条件跳转形成有限修复循环。模型不负责决定测试是否通过。 |
| [LangGraph supervisor 源仓库](https://github.com/langchain-ai/langgraph-supervisor-py) | README 当前推荐以 tools 直接实现 supervisor 模式，大多数新应用无需依赖此 supervisor 库。 | 首版使用可审查的固定流程与角色专用提示词；后续动态调度通过带输入输出 schema 的工具扩展。 |
| [AutoGen 源仓库](https://github.com/microsoft/autogen) | Core、AgentChat、Extensions 分层：消息运行时、团队协作、模型及执行工具分别解耦；当前仓库声明进入维护模式。 | 借鉴消息、模型适配、执行环境三个边界；不将处于维护模式的 AutoGen 作为本项目必需依赖。 |
| [mini-swe-agent 固定版本源码](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/agents/default.py) | `step → query → execute_actions → observation` 构成闭环；独立 Model 与 Environment；记录调用数、费用、终止原因及轨迹；限制步骤、耗时和连续格式错误。 | 将测试实际失败作为下一轮补全的 observation；持久化每阶段证据；遇到预算耗尽或相同失败时明确退出。 |

[SWE-agent 官方仓库](https://github.com/SWE-agent/SWE-agent) 当前建议新用户使用 mini-swe-agent。本项目参考其执行反馈机制；不复用其任意 shell 执行能力。以上“应用建议”是本项目设计判断，不是开源项目对本项目的推荐。

建议主流程：

```text
任务与仓库快照
  → 需求分析 Agent：需求、缺口、可修改范围
  → 测试 Agent：开发测试与可重现的失败
  → 补全 Agent：只提交允许修改文件的候选补丁
  → 受控执行器：语法、开发测试、独立验收、原有回归
  → 裁判：通过 / 反馈重试 / 预算耗尽 / 环境错误
  → 补丁、阶段轨迹、验证报告
```

测试执行器与裁判是确定性代码。独立验收测试由评测侧保管，补全 Agent 的输入、工具及可写目录均不包含验收文件。测试 Agent 生成的开发测试应与验收测试分开记录。不同角色至少有独立提示词、输出结构与权限，角色数量本身不是质量证据。

## 2. GitHub 数据集与实验边界

### HumanEval：首版推荐

[openai/human-eval](https://github.com/openai/human-eval) 提供 Python 函数任务与功能正确性评测。固定源码包含 `data/HumanEval.jsonl.gz`；任务字段为 `task_id`、`prompt`、`entry_point`、`test`、`canonical_solution`。首版可将函数体置为未实现，使流程检测到需求缺口，再根据 prompt 补全。数据读取机制见 [human_eval/data.py](https://github.com/openai/human-eval/blob/6d43fb980f9fee3c892a914eda09951f772ad10d/human_eval/data.py)。

- 推荐下载固定 URL：[HumanEval.jsonl.gz](https://raw.githubusercontent.com/openai/human-eval/6d43fb980f9fee3c892a914eda09951f772ad10d/data/HumanEval.jsonl.gz)。导入时记录下载 URL、commit、文件 SHA-256、任务 ID 和任务数量。
- `prompt` 与缺失实现可进入模型上下文。`test` 由独立裁判执行；`canonical_solution` 不可进入补全上下文，不可作为真实模型模式的答案来源。
- 许可证为仓库 [MIT License](https://github.com/openai/human-eval/blob/6d43fb980f9fee3c892a914eda09951f772ad10d/LICENSE)。分发数据或引用源码时保留相应版权与许可。
- 这是函数级合成任务。它能验证测试驱动补全闭环，不能单独证明跨文件工程能力、HTTP 接口补全能力或真实需求理解能力。

官方 [README](https://github.com/openai/human-eval/blob/6d43fb980f9fee3c892a914eda09951f772ad10d/README.md) 给出的结果包含 `passed`、`failed`、`timed out` 与 pass@k，并明确要求稳健隔离执行模型生成代码。本项目不需要开启其默认被注释的执行函数，可自行提供隔离评测器。

### MBPP：后续扩展

[Google Research MBPP README](https://github.com/google-research/google-research/blob/0a1b0fe7298a58bca6dcb47112379d829bc0c0a5/mbpp/README.md) 描述约一千个基础 Python 任务，每题有需求、解答与自动测试，并提供人工核查的 `sanitized-mbpp.json`。原始划分是测试 ID 11–510、提示示例 ID 1–10、验证 ID 511–600、训练 ID 601–974；sanitized 子集继承相应划分。

数据源：[mbpp.jsonl](https://raw.githubusercontent.com/google-research/google-research/0a1b0fe7298a58bca6dcb47112379d829bc0c0a5/mbpp/mbpp.jsonl)、[sanitized-mbpp.json](https://raw.githubusercontent.com/google-research/google-research/0a1b0fe7298a58bca6dcb47112379d829bc0c0a5/mbpp/sanitized-mbpp.json)。原始与 sanitized 字段名称存在差别，应分别适配，不能假设与 HumanEval 的结构一致。

本次核到 Google Research 仓库根 [Apache-2.0 License](https://github.com/google-research/google-research/blob/0a1b0fe7298a58bca6dcb47112379d829bc0c0a5/LICENSE)，未在该 MBPP 目录核到单独的数据授权声明。打包分发 MBPP 前应再核查数据集的具体许可和引用要求；首版使用许可已明确的 HumanEval。

### 结果应怎样报告

以下是本项目实验建议，不是官方 benchmark 结论：

1. 离线 fixture / 预写补丁用于检查工作流与 UI，报告 `demo` 或 `replay`，不报告大模型求解率。
2. 真实模型实验记录 provider、model、提示词版本、随机种子（如支持）、任务 ID、重试预算、输入输出 token、耗时和终止原因。
3. 有限修复循环报告 `solved_within_budget` 与独立验收通过率。标准 pass@k 需要按官方采样定义计算，不能把多轮反馈修复的通过率直接命名为标准 pass@1。
4. 回归破坏率只对补丁前实际通过的原有回归测试计算；没有原有测试的任务标为不适用，不能用空测试集宣称零破坏。
5. 从固定任务列表取样，保留失败与超时，完整说明样本规模；公开数据上的结果不代表模型从未见过该题。

## 3. AgentArts 已核实的真实接入方式

华为 [AgentArts 产品说明](https://support.huaweicloud.com/productdesc-agentarts/agentarts_03_0002.html) 支持自研和第三方 Agent、MCP Server 托管。建议将核心编排或角色推理服务托管至 AgentArts，并保留本地前端与测试执行器；需要使用云上实际调用日志证明平台承担了核心工作。

### 高代码 Runtime HTTP 契约

官方 [ExecuteRuntime API](https://support.huaweicloud.com/api-agentarts/InvokeRuntime1.html)（页面更新 2026-09-30）规定：

```http
POST https://{gateway_domain}/runtimes/{runtime_name}/invocations?endpoint={access_name}
Content-Type: application/json
Authorization: Bearer {runtime_api_key}
X-Hw-Agentarts-Session-Id: {session_id}
```

`gateway_domain`、运行时名称和可选访问方式从已部署运行时的控制台详情取得。`endpoint` 为可选查询参数，默认 Latest。会话头必填，允许英文字母、数字、连字符与下划线，最长 64 字符。Bearer 示例只适用于部署时选择 API Key 入站认证的运行时；IAM 需要签名 SDK，不能用 AK 直接替代 Bearer token。

官方 [调用概述](https://support.huaweicloud.com/highcode-agentarts/agentarts_10_130.html) 明确指出业务 JSON 字段由 Agent 代码定义。因此本项目应定义并版本化自己的角色调用 schema；API 文档中的 `input/output` 或教程的 `message/reply` 只是示例，不是华为固定 LLM 消息格式。不能将 Runtime 当作未经核实的 `/v1/chat/completions` 服务。

本工程可约定 `role + task + context → JSON 角色结果`，但该约定属于 TestFix-Agent，需要客户端和托管 handler 一起实现。客户端必须拒绝不符合 schema 的响应，保留 HTTP 状态与可读错误原因；401、404、500 均不可静默切换到离线答案。

### 官方 SDK 与部署路径

[huaweicloud/agentarts-sdk-python](https://github.com/huaweicloud/agentarts-sdk-python) 提供 `AgentArtsRuntimeApp` 和 `@app.entrypoint`。已检查固定版本 [runtime/app.py](https://github.com/huaweicloud/agentarts-sdk-python/blob/26a4cf9a06a1c55cb13700e5b3f47f9f4fb84e07/src/agentarts/sdk/runtime/app.py)：它以 Starlette 暴露本地 `POST /invocations`、`GET /ping`、`WS /ws`，并把请求 payload 交给用户 handler。云网关添加 `/runtimes/{runtime_name}` 前缀，本地服务不使用该前缀。

SDK README 给出初始化、开发与部署命令，支持自有框架。SDK 本身为 [Apache-2.0](https://github.com/huaweicloud/agentarts-sdk-python/blob/26a4cf9a06a1c55cb13700e5b3f47f9f4fb84e07/LICENSE)。本地适配可先通过契约测试；真正的云部署还依赖服务开通、授权、镜像、入站认证和账号可用区域，见官方 [通过控制台部署](https://support.huaweicloud.com/highcode-agentarts/agentarts_10_031.html)。未实际部署前不能报告“AgentArts 已跑通”。

官方还有 [部署 MCP Server](https://support.huaweicloud.com/highcode-agentarts/agentarts_10_125.html) 路线。后续可将仓库快照、补丁提交、测试执行封装为 MCP 工具；每个工具仍应由执行器实施路径和修改范围限制。首版 Runtime 调用与后续 MCP 服务是两件独立的接入工作。

## 4. 执行环境与独立验证建议

- 本地可信 fixture 可以使用临时目录与有超时的子进程，应明确标注 `trusted_local`。Python `-I`、AST 检查、进程超时不是安全沙箱。
- 真实模型候选应进入独立容器：禁网、非 root、限制内存与进程数、不传云密钥，只挂载候选副本和评测文件。禁止将主工程或用户资料目录挂载为可写。
- 候选补丁仅允许触及白名单源文件；禁止修改验收、回归测试、评测器和运行配置。应用前检查路径穿越、符号链接与规模；检查失败直接拒绝该候选。
- 验收与原有回归分别保存退出码、输出、耗时与内容哈希。最终通过要求两组实际执行成功，不能以模型自评替代。
- 前端应同时展示失败复现、补丁差异和最终验证，并保留失败轨迹，便于答辩复现。

## 5. 固定版本与许可索引

以下 SHA 通过对应官方 Git 仓库的 `git ls-remote` 在核查日取得，是研究时的分支快照，不等于本工程安装了这些依赖。

| 仓库 | 固定 commit | 已核许可证 | 用途 |
| --- | --- | --- | --- |
| [openai/human-eval](https://github.com/openai/human-eval/tree/6d43fb980f9fee3c892a914eda09951f772ad10d) | `6d43fb980f9fee3c892a914eda09951f772ad10d` | MIT | 首版数据源与评测参考 |
| [google-research/google-research](https://github.com/google-research/google-research/tree/0a1b0fe7298a58bca6dcb47112379d829bc0c0a5/mbpp) | `0a1b0fe7298a58bca6dcb47112379d829bc0c0a5` | 根目录 Apache-2.0；MBPP 专门授权待核 | 后续数据源 |
| [langchain-ai/langgraph](https://github.com/langchain-ai/langgraph/tree/a6731f436700c7d46a9224ccd3a0e0ce3a5bfdb5) | `a6731f436700c7d46a9224ccd3a0e0ce3a5bfdb5` | [MIT](https://github.com/langchain-ai/langgraph/blob/a6731f436700c7d46a9224ccd3a0e0ce3a5bfdb5/LICENSE) | 状态编排参考 |
| [microsoft/autogen](https://github.com/microsoft/autogen/tree/027ecf0a379bcc1d09956d46d12d44a3ad9cee14) | `027ecf0a379bcc1d09956d46d12d44a3ad9cee14` | [代码 MIT、文档 CC-BY-4.0](https://github.com/microsoft/autogen#legal-notices) | 分层协作参考 |
| [SWE-agent/mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent/tree/04d809ceab9df28f9adaed044884180159172930) | `04d809ceab9df28f9adaed044884180159172930` | [MIT](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/LICENSE.md) | 执行反馈与预算参考 |
| [huaweicloud/agentarts-sdk-python](https://github.com/huaweicloud/agentarts-sdk-python/tree/26a4cf9a06a1c55cb13700e5b3f47f9f4fb84e07) | `26a4cf9a06a1c55cb13700e5b3f47f9f4fb84e07` | Apache-2.0 | 官方托管入口参考 |

本研究没有核实本届赛题完整附件、团队报名资格或评分条款。最终参赛材料应对照组委会原件，并补充真实 AgentArts 部署与调用证据。
