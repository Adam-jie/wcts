# AgentArts 托管与实际采用证据

本地首版支持将五种 Agent 的角色推理托管到华为 AgentArts Runtime，本地监督编排和测试执行器逐角色调用云入口。使用官方 [InvokeRuntime](https://support.huaweicloud.com/api-agentarts/InvokeRuntime1.html) 与 [Runtime SDK](https://github.com/huaweicloud/agentarts-sdk-python/tree/26a4cf9a06a1c55cb13700e5b3f47f9f4fb84e07)；SDK 和真实云环境尚未安装或验证，HTTP 契约单元测试使用 mock。

## 业务契约

```json
{
  "contract_version": "testfix.role.v1",
  "role": "developer",
  "context": {
    "task_id": "HumanEval/2",
    "entry_point": "truncate_number",
    "specification": "public specification only",
    "code": "current candidate code",
    "cases": [{"args": [4.25], "expected": 0.25}]
  }
}
```

返回 `{"contract_version":"testfix.role.v1","result":{"code":"...","reason":"..."},"usage":{}}`。其他角色输出 schema 见 `testfix/providers.py`。华为 Runtime 的业务 JSON 由托管程序定义，本契约不是官方固定消息格式。

## 云端准备

1. 把本工程源码和 `agentarts/entry.py` 带入独立构建环境，安装本工程依赖及 `agentarts/requirements.txt` 中固定的 SDK。不要打包 `.env`、运行产物和 HumanEval 参考数据。
2. 在运行时环境设置上游真实模型服务的 `TESTFIX_LLM_BASE_URL`、`TESTFIX_LLM_MODEL` 和凭据，及 `TESTFIX_RUNTIME_PROVIDER=openai`（或 Ollama）。云端禁止选择 replay 或再次选择 agentarts，避免假调用与递归。
3. 运行入口为 `python agentarts/entry.py`，官方 SDK 暴露 `/invocations` 与 `/ping`。在华为控制台创建并部署高代码 Runtime，按平台要求配置监听端口、容器、执行角色、权限和入口。
4. 若使用 API Key 入站认证，获取控制台显示的网关地址、运行时名称及 API Key。本客户端只实现该认证方式；IAM 签名尚未实现。

实际部署参数以对应账号/区域的官方控制台为准。当前没有代你创建云资源或产生云费用。

## 本地配置

设置 `.env`：

```dotenv
TESTFIX_PROVIDER=agentarts
TESTFIX_AGENTARTS_URL=https://<gateway>/runtimes/<runtime>/invocations
TESTFIX_AGENTARTS_API_KEY=<runtime-api-key>
```

按实际需要加可选 `?endpoint=<access-name>`。调用头包含 `Authorization: Bearer ...` 与长度小于 64 的会话 ID。HTTP 错误或错误业务契约会明确使任务失败，不回退离线补丁。

## 参赛前需补齐的证据

- 实际 Runtime 名称/版本、部署时间、配置与健康检查记录。
- 控制台链路观测中五种角色的真实调用；本地对应 run ID、session ID、task ID。
- 实际模型名称、任务列表、修复预算、耗时和独立验收/回归结果。
- 成功和失败样例、可重现启动步骤、源码与依赖锁定版本。
- 赛题完整附件要求的平台使用方式与作品提交清单。

本地网页运行、离线回放通过和 mock 契约测试都不能替代实际 AgentArts 采用证据。

