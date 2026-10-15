# 完整工程的模型传输与调用证据

完整工程使用 `testfix.project-skill.v1`，执行配置为 `flaskr_project`。入口是 `ProjectModelProvider.invoke(skill_id, context, output_schema)`；调用方可以为每步传入 `timeout_s` 和 `token_cap`。实现位于 `testfix/project_models.py`。

九项能力中，前八项调用模型：规划、仓库探索、代码定位、需求分析、测试生成、失败诊断、工程补丁、独立评审。第九项 `verify-delivery` 由受信执行器运行测试、构建 wheel、启动真实 HTTP 服务并验证 SQLite 重启持久性。模型不能替代该执行器，也不能自行宣称验收通过。

## 复用本机 Codex 登录

先运行 `codex login status`。已登录 ChatGPT 时，`codex_cli` 直接复用本机登录，无需为这条调用链另建 API Key。Python 传输层不读取 `auth.json`，也不导出 OAuth token；认证由 Codex CLI 自己完成。

当前本机已验证 `gpt-5.6-sol` 的真实 JSON 调用。每次都显式传入 `--model gpt-5.6-sol`，不自动改用其他模型。模型是否可用仍取决于当前账号、服务和网络；失败会保留错误并停止。

CLI 在不含工程源码的临时目录中运行。公开源码快照通过 stdin 提供，目录内只放输出 schema 和最终响应文件。主要参数为：

```text
codex --no-daemon exec --ignore-user-config --ignore-rules --ephemeral
  --skip-git-repo-check --model gpt-5.6-sol --sandbox read-only
  --json --output-schema <schema.json> --output-last-message <result.json>
  --cd <temporary-directory>
  -c web_search="disabled" -c shell_environment_policy.inherit="none"
  -c model_provider="testfix_https"
  -c model_providers.testfix_https.name="OpenAI ChatGPT HTTPS"
  -c model_providers.testfix_https.base_url="https://chatgpt.com/backend-api/codex"
  -c model_providers.testfix_https.wire_api="responses"
  -c model_providers.testfix_https.requires_openai_auth=true
  -c model_providers.testfix_https.supports_websockets=false
  --disable shell_tool ... -
```

这里使用临时命名的 provider 连接官方同源 HTTPS，复用 ChatGPT OAuth，避开本机出现过的 WebSocket 重连超时。内置 `openai` provider 不可直接覆写，因此使用 `testfix_https`；这些参数不写入用户配置。除 shell 外，还关闭 apps、plugins、hooks、浏览器、电脑操作、图像生成、子代理、工作区依赖、shell snapshot 与 code mode；保留 CLI 必需的宿主组件。程序只继承启动所需的环境白名单，不继承 API Key、token 或代理变量。

只读沙箱主要限制写入。临时目录、关闭命令工具和公开快照缩小模型可用范围，并不构成操作系统级读取隔离的证明；元数据明确记录 `os_read_isolation_claimed=false`。若事件中出现工具调用，当前模型阶段会失败。

参数依据见官方[非交互模式](https://learn.chatgpt.com/docs/non-interactive-mode)、[配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)、[认证说明](https://learn.chatgpt.com/docs/auth)及[ChatGPT 同源后端说明](https://learn.chatgpt.com/docs/enterprise/sign-in-with-chatgpt-through-a-gateway)。

## 配置其他传输

| provider | 配置来源 | 请求协议 |
| --- | --- | --- |
| `codex_cli` | 本机 Codex 可执行程序及已有 ChatGPT 登录；调用显式指定模型 | 非交互 CLI，stdin 快照与 JSON Schema |
| `openai` | `TESTFIX_LLM_BASE_URL`、`TESTFIX_LLM_API_KEY`；调用方显式传入模型 | 兼容 `/chat/completions`，严格 JSON Schema |
| `ollama` | `TESTFIX_OLLAMA_URL`；模型由调用方显式传入，`TESTFIX_OLLAMA_MODEL` 用于配置状态 | `/api/chat`，schema 格式输出 |
| `agentarts` | `TESTFIX_AGENTARTS_URL`、`TESTFIX_AGENTARTS_API_KEY` | 本工程的 `testfix.project-skill.v1` 业务契约 |

`Settings` 保留已有 `.env` 和 legacy 环境变量映射；凭据不要写入文档、请求日志或提交到版本库。API 请求头只在内存中构造。完整工程的 provider 和模型由运行请求选择，不会因为旧的纯函数流程默认 `TESTFIX_PROVIDER=replay` 而切换为回放。

AgentArts 项目入口为 `handle_project_invocation`，通用 `handle_invocation` 会按契约版本分派。服务端只接受本地 `project_workflow.SCHEMAS` 中注册的八个 LLM Skill 及对应固定 schema，并要求 `flaskr_project`。客户端传来的 `instructions` 不成为服务端提示词；服务端重建 `ProjectModelProvider`，重新加载本地 Skill 正文、契约与快照。禁止将上游再次设为 `agentarts`，也禁止 replay 或 fixture。旧 `testfix.role.v1` 仍使用原来的五角色入口。

AgentArts 契约测试使用明确标注的 fake provider，不访问外部模型；它们不证明华为云 Runtime 已部署或真实调用。部署说明另见 `docs/agentarts.md`。

## 时间、token 与模型身份

CLI 和 HTTP 都有独立进程的绝对调用截止时间。超时后由父进程终止该次进程树并回收管道，不留下继续请求的 socket 线程。HTTP worker 以 `python -I -B` 启动，URL、header 与 payload 只通过 stdin 传入，响应上限为 2 MiB；不跟随重定向、不使用环境代理，不把远端异常正文或 worker stderr 写入调用错误。

CLI 的 `token_cap` 是提示词中的输出软预算，CLI 没有在此实现可验证的输出 token 硬上限。HTTP 将上限传给服务端参数，但实际执行仍取决于服务端。整体调度会在后续步骤前核对预算，无法把超预算运行判为成功。

CLI 从 `turn.completed.usage` 提取 `input_tokens`、`output_tokens` 及缓存相关计数；有输入和输出计数时可派生总数。没有上报就保留 `usage=null`。HTTP 同样只记录服务端提供的合法整数计数。`usage_source` 标识来源；这些是服务端上报的 token 账目，不是费用审计，也不根据字符数伪造实测 token。

`requested_model` 表示请求指定的模型。只有响应或 CLI 事件实际带回模型名时才填写 `actual_model`。CLI 常只给出 `--model` 参数证据，此时 `actual_model=null`、`model_evidence=requested_cli_flag_only`。AgentArts 返回的 `model` 也必须保留这个 null，不能用请求名补成远端身份认证。若服务端明确报告另一个模型，传输会拒绝该结果。

## 公开输入与保存的证据

每次调用先递归拒绝隐藏验收、独立验收、参考答案等受保护键，再检查 Skill 快照。模型读取 `flaskr_project` 下的可信 Skill 正文、机器契约、公开 context 和输出 schema；源码、注释、需求、日志都属于待分析数据。最终响应必须是一个严格 JSON 对象，并通过阶段 schema 检查。

每次调用保存 `request.json`、`result.json` 和 `trace.json`。工作流可指定当前 run 下的 `model_calls` 目录；独立使用时，默认目录为 `artifacts/project_model_calls/<session>/`。trace 包含耗时、状态、请求模型、实际模型证据、usage 及来源、原始响应和清理超时标记。已知凭据和 token 形式会脱敏；Authorization header 不写入请求 artifact。CLI 状态查询只运行安全的 `login status`，结果缓存 20 秒，不触发模型推理。

HTTP、CLI、schema、模型名或预算失败均明确失败。没有自动切换模型、离线补丁、fake LLM 或 replay 的兜底。保存的旧产物可以用于重新验证，但不能描述为本次新模型生成。
