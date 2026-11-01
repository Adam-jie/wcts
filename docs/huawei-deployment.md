# AgentArts 参赛部署与验收

> 方向变更：本文件保留的是此前赛题二的 AgentArts 接入设计，不再作为赛题一的完整部署要求或当前部署状态。赛题一主方案与待完成事项见 [赛题一方案](contest-topic1.md)；下文属于可选接入参考。

赛题要求的是在华为云 AgentArts 上**构建、编排并部署智能体**，展示可视化 Demo，并给出工具、知识、多 Agent 与效果评估证据。单独购买 ECS 不是该要求。当前方案由 AgentArts 低代码多 Agent 应用承担意图路由，高代码 Runtime 承载本项目八个受 Skill 约束的模型能力；第九个 `verify-delivery` 由独立的工程执行器完成。工程执行器和可视化工作台先在本机运行，日后如需评委远程访问再迁到隔离云计算环境。

## 已有与未有

- 账号区域：西南-贵阳一 `cn-southwest-2`。AgentArts 应用 `无遁TestFix Agent` 已创建，但 2026-10-23 控制台显示“开发中”，添加子智能体列表为“暂无数据”；它不是已部署的多 Agent 作品。
- 本仓库有 AgentArts Runtime 业务入口 `agentarts/entry.py`、ARM64 `agentarts/Dockerfile`、客户端与版本化 Skill 契约。`agentarts-check` 可在部署后验证真实 Runtime 地址、模型配置、Skill 哈希一致性；该检查不调用大模型。
- 尚未取得已部署 Runtime 的调用地址/API Key、实际 MaaS 模型配置及 ARM64 镜像构建验证。本机没有 Docker。**本地契约测试不等于云端部署或真实模型调用。**

## 1. 把低代码总控变成可运行应用

先完善并发布一个实际可调用的子工作流，再回到 `无遁TestFix Agent` 的“子智能体”列表选择它，设置清楚意图与输出。当前“需求澄清”工作流可以作为首个子流程；只有其试运行通过且已发布，才能确认在列表中可选。随后加入“工程补全与验证”“证据报告”等真正接入执行器的流程。总控提示词不能代替子流程的工具调用和实际执行。

在多智能体应用完成调试后提交版本并[部署为 API](https://support.huaweicloud.com/lowcode-agentarts/agentarts_05_0108.html)，保存版本号、部署状态、一次真实 API 调用及链路日志。低代码 [HTTP 节点](https://support.huaweicloud.com/lowcode-agentarts/http_node.html) 有单次等待上限，完整工程测试应提交异步任务并轮询结果，不能在一个节点里等待数十分钟。只有实际调用工程执行器、获得 run ID 和验收报告后，才可把该流程称为“工程补全与验证”。

## 2. 托管八个模型 Skill 到 AgentArts Runtime

在可用 ARM64 Linux 构建环境中、从不含 `.env` 的源码根目录构建：

```bash
docker build --platform linux/arm64 -f agentarts/Dockerfile -t testfix-agentarts:local .
docker image inspect testfix-agentarts:local --format '{{.Architecture}}'
```

应得到 `arm64`。本地容器先检查官方 SDK 的 `GET /ping`，再向 `POST /invocations` 发送 `{"contract_version":"testfix.runtime-info.v1"}`，确认返回 `ready=true` 和与本机相同的 Skill 目录哈希。该响应只说明 Runtime 配置与代码版本，**不证明模型已实际推理**。

在容器环境设置 `TESTFIX_RUNTIME_PROVIDER=openai`、`TESTFIX_LLM_BASE_URL`、`TESTFIX_LLM_MODEL`、`TESTFIX_LLM_API_KEY`，分别对应实际开通的 MaaS 兼容端点和模型。凭据放在受控环境变量或密钥服务，不放入镜像、Git、报告或聊天。当前适配器会在 Base URL 后拼接 `/chat/completions`，并发送严格 JSON Schema 输出格式；要用实际 MaaS 模型试一次 Skill 调用才能确认兼容性。

将验证过的 ARM64 镜像推至 SWR，在 AgentArts“托管与运行 → 智能体运行时”创建 Runtime，配置镜像、端口 `8080`、模型环境与受控入站认证。参见[官方 Runtime 部署](https://support.huaweicloud.com/highcode-agentarts/agentarts_10_031.html)和[快速开始](https://support.huaweicloud.com/highcode-agentarts/agentarts_10_040.html)。记录镜像摘要、Runtime 版本、健康状态和调用日志。低代码总控里选择 DeepSeek 模型，并不会自动部署本仓库的 Python Skill。

## 3. 本机工程执行器调用云端 Runtime

工作台仍可在本机 `127.0.0.1:8765` 运行，无需先买 ECS。在本机 `.env` 填入 Runtime **invocations** 地址和仅供该 Runtime 调用的 API Key：

```dotenv
TESTFIX_AGENTARTS_URL=https://<gateway>/runtimes/<runtime>/invocations
TESTFIX_AGENTARTS_API_KEY=<runtime-api-key>
```

不要把低代码应用 API 地址误填为上述高代码 Runtime 地址。两者业务协议不同。先执行：

```bash
uv run python -m testfix.cli agentarts-check --model "实际部署的模型ID"
```

检查成功应返回 `ready: true`、模型 ID 与 `skill_catalog_matches: true`；失败会明确返回认证、端点、模型或目录不匹配，不会退回离线回放。工作台也提供 `POST /api/agentarts/check` 供主动检查。随后运行一个注册工程：

```bash
uv run python -m testfix.cli complete-project --project Project/flaskr --provider agentarts --model "实际部署的模型ID" --max-rounds 1
```

CLI 会先验证 Runtime。执行器在本机创建隔离工程副本、执行基线和生成测试、应用补丁、回归与独立验收；云端 Runtime 处理模型 Skill。API 请求中的模型名必须与 Runtime 配置一致。完整测试结果以生成的 run ID、补丁、回归、独立验收和实际运行证据为准。

## 4. 参赛演示判定

应现场展示：AgentArts 已部署版本与调用链路 → 输入需求和目标工程 → 总控分配子 Agent/Skill → 模型生成测试步骤与补丁 → 独立验收及回归 → 修复后的实际程序运行。记录 AgentArts 调用 ID、项目 run ID、模型名与用量、原始版本和补全版本的行为差异。模型调用成功、构建成功、功能符合需求和原功能未回归要分别判断，不用单一“成功”代替。

报名表还要求体现华为云码道 CodeArts 代码智能体在需求理解、代码生成、构建、问题修复或部署环节的使用价值。参赛版需保存它的实际操作与产物证据；本仓库当前使用 Codex CLI 的历史记录不能冒充 CodeArts 使用记录。

若需要评委从互联网访问工作台，可再选择隔离的云工作机或其他托管方式。云端工程执行需要专门的代码执行安全边界；不能因 Runtime 能调用模型就把任意仓库构建和测试直接放入低代码对话节点。[赛题要求中的 AgentArts 部署](https://support.huaweicloud.com/agentarts/index.html)仍须完成，外部工作台的放置地点不是该要求的替代物。
