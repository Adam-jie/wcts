# 产品级测试：多数据、循环与独立验收

目标是验证交付产品在约定环境与使用范围内能运行、满足需求并保持原有行为。一次功能断言通过、测试数量很多、固定十个维度都有名称，都不能代替测试质量与产品验收合同的人工审查。

## 三种循环分别记录

| 循环 | 固定项与允许变化 | 结果口径 |
| --- | --- | --- |
| 固定版本重复测试 | 源码、数据集、独立预期和环境冻结；按保存的 seed 改变执行顺序，重复每个 case/data 组合 | 逐轮保存首次失败、全部回执及偶发失败，不用后轮成功覆盖前轮失败。 |
| 自动重试 | 不修改代码，失败后再次执行同一输入 | 重试通过归为 flaky，保留首失败；严格门禁仍拒绝该次活动。当前产品活动执行器没有自动“重试直到绿”。 |
| 修复迭代 | Agent 根据公开失败证据修改代码，候选哈希变化 | 生成新活动，保留历史版本和原失败数据，重新执行冻结验收与全部适用回归。现有 `max_rounds` 指修复次数，不是稳定性轮数。 |

[Playwright 官方重试机制](https://playwright.dev/docs/test-retries)明确区分首次通过、重试通过的 flaky 和持续失败。此处严格门禁是本工程的验收政策。有限轮数只证明这批数据、这个版本和本次环境的观察结果；不能把 25 轮或几分钟称为长期稳定性证明。

## 固定十个质量维度

`testfix/product_campaign.py` 的最低产品维度固定为下表。每项需要实际检查、明确预期和原始证据；没有声明用例、未执行、工具不可用、超时或轮数/数据量不足均阻止 `product_accepted`。当前版本不支持通过删掉维度或事后标为“不适用”跳过门禁。

| ID | 需要验证的内容 |
| --- | --- |
| `functional` | 构建后真实执行业务链路，核对输出、状态和副作用。 |
| `regression` | 原有行为逐项保持，比较同一组测试身份与结果，不只比较通过总数。 |
| `data_integrity` | 写入、读取、事务、文件和消息一致性；无丢失、串写或意外重复。 |
| `stability` | 固定版本反复操作、冷/热启动与持续运行，观察偶发错误及资源退化。 |
| `concurrency` | 按产品共享资源设计同时访问、冲突写、排队、竞态与幂等测试。 |
| `performance` | 在声明负载、硬件和时长下核验延迟、吞吐、错误率及资源预算。 |
| `recovery` | 异常中断、断连、重启、依赖恢复后的状态与数据恢复。 |
| `security` | 按攻击面检查权限、输入、会话、配置与敏感信息处理。 |
| `compatibility` | 逐项验证声明支持的 OS、浏览器、依赖、架构或板卡配置。 |
| `delivery` | 从实际交付包安装、配置、启动和停止，复现关键业务及部署说明。 |

阈值由项目的需求、部署配置与风险决定，不把某个演示的并发数或响应时间套给所有产品。[k6 测试类型](https://grafana.com/docs/k6/latest/testing-guides/test-types/)区分常态负载、压力、突增和长稳；[阈值机制](https://grafana.com/docs/k6/latest/using-k6/thresholds/)将指标转为明确通过/失败条件。[Soak 测试](https://grafana.com/docs/k6/latest/testing-guides/test-types/soak-testing/)还需实际持续负载、时间序列与资源监测；活动中若干请求耗时的总和不能替代这些证据。网页安全检查参考 [OWASP WSTG v4.2](https://wstg.owasp.org/v4.2/4-Web_Application_Security_Testing/)，不据此宣称覆盖所有嵌入式安全风险。

## 多数据与独立裁判

每个需求按正常等价类、空/缺失、上下界及越界、错误类型、Unicode/超长、重复/顺序、大数据规模分层；状态型功能还需操作序列，随机数据保存 seed 与实际输入。组合数较大时先声明覆盖策略及未覆盖范围，不能把大量重复正常值当作全面测试。

评测者冻结输入与独立 oracle（预期输出、数据副作用和允许误差），修复 Agent 只能看到公开需求与有限失败信息。模型生成的开发测试与独立验收分开，不让实现者修改验收、降低断言或改数据来通过。每条回执记录轮次、case ID、data ID、实际结果与耗时，报告绑定合同、数据、oracle、执行顺序和候选哈希。缺少预期、错误码只证明连通、检查永远为真等问题仍需人工审查；哈希不证明裁判正确。

受控缺陷使用 `pristine` / `defective` / `candidate` 三态，在同一工具链、依赖、初始数据、配置、设备与负载下比较。健康原版先通过预检查；故障输入复现目标失败；修复版满足原冻结预期并保持回归。同一输入的第一次失败不能被后续成功覆盖。自然语言新功能没有现成健康参考时，按事先冻结的需求合同判断，不伪造参考实现。详见 [大型工程数据集](large-project-dataset.md)。

## 四类产品的执行适配

| 产品 | 实际运行与重点 | 不能替代的证据 |
| --- | --- | --- |
| 网页 | 完整前后端、数据库与依赖；浏览器业务、API、鉴权、持久化、多用户与跨浏览器 | 健康接口 200 不代表完整业务可用。 |
| 软件 | 从交付包启动 GUI/CLI；工程/文件操作、编码、大文件、并行任务、退出与重启 | 模块导入、版本输出不能替代完整操作。 |
| MCU | 工具链、固件构建、板级配置；正常/错误帧、边界、时序、连续收发、复位与外设状态 | 编译成功或串口回环不证明固件功能；仿真与真板分开统计。 |
| MPU | 实际系统和服务；启动依赖、网络/消息、存储持久化、断连恢复与资源负载 | 本机模拟 HTTP/SSH 不证明目标架构、板卡或实际外设通过。 |

MCU/MPU 分别标明 `host_simulation`、`qemu_firmware`、`physical_board`，真板结果绑定板型、固件哈希与烧录/启动/外设回执。串口单通道需要资源仲裁，不能用同时乱写设备代替有定义的并发测试。四类产品尚未全部接入统一执行适配器。

## 现有入口与本次执行边界

- `testfix/product_campaign.py`：冻结合同和多数据，按 seed 打乱每轮顺序，每个计划组合执行一次；保存全部失败、未执行项与维度缺口。适配器须自行执行 I/O/进程硬超时，执行器不能强行中断任意 Python 回调。
- `testfix/product_http.py`：受限的本机 HTTP 探针，通过独立 worker 执行读请求并受进程时限约束；不调用模型，不修改真实板卡。
- `scripts/run_product_campaign.py`：命令行读取 `source_files` 与 `contract`，由宿主绑定源码摘要，输出独立活动目录。
- `examples/product-testing/testfix-http-smoke.json`：当前 TestFix 服务的只读多数据短循环 profile，25 轮 × 16 组输入。实际执行 400/400 通过；此前诊断运行的 200 次适配器错误单独保留，修复没有改变预期输出。
- `testfix/product_http_load.py` 与 `examples/product-testing/testfix-http-load-smoke.json`：GET 轻负载适配，测量客户端在途请求、逐请求错误、状态与延迟预算。2/4 个客户端、1 秒预先声明预算仅用于本机小负载检查；客户端在途并不证明服务器用几个线程处理请求，也不是完整性能或并发业务验收。

在项目根目录、服务已启动的 PowerShell 中执行：

```powershell
.\.venv\Scripts\python.exe -X utf8 .\scripts\run_product_campaign.py `
  --profile .\examples\product-testing\testfix-http-smoke.json `
  --base-url http://127.0.0.1:8765 `
  --output .\artifacts\product-campaigns
$LASTEXITCODE
```

**默认退出 `0` 必须通过完整产品门禁**；退出 `1` 表示未达到该门禁，哪怕已声明的短循环全部通过；退出 `2` 为无法启动活动。仅在命令显式增加 `--scope-only` 时，退出码才按 `tested_scope_passed` 判断，仍不改变 `product_accepted`。CI 产品验收应使用默认严格模式。早期 400 次运行使用的是已归档的旧 CLI，它按 scope 返回 `0`，不能把该退出码追认为产品合格。

结果目录包含 `contract.json`、`schedule.json`、逐次 `attempts.jsonl`、`report.json` 与 HTTP 适配上下文 `adapter-context.json`；新运行另保留所列公开源码的 `source-snapshot/` 并重算摘要。以 `product_accepted` 和 `acceptance_blockers` 查看产品门禁。缺失维度保留 `not_evaluated`，不能从统计分母中删除。`source_files` 只能列出规范相对路径的公开源码，不得包含凭据。

运行本机轻负载：

```powershell
.\.venv\Scripts\python.exe -X utf8 .\scripts\run_product_campaign.py `
  --adapter http-load `
  --profile .\examples\product-testing\testfix-http-load-smoke.json `
  --base-url http://127.0.0.1:8765 `
  --output .\artifacts\product-campaigns
```

此 profile 分别记录 `concurrency_smoke` 与 `performance_smoke`，不会自动替代固定十维中的完整 `concurrency` 和 `performance`。超时批次的已完成请求数未知，不能把计划数量计为实际完成。

## 2026-10-16 实测

| 范围 | 实际结果 |
| --- | --- |
| 新增框架检查 | 63 项通过，0 失败、错误或跳过 |
| 本机只读接口循环 | 25 轮 × 16 组输入，400/400 通过 |
| 本机客户端轻负载 | 3 轮、4 组负载、12 批次，264 次请求完成，0 请求错误 |
| 本次轻负载请求耗时 | 2/4 客户端；p95 0.136727 秒，最大 0.146599 秒；预先声明预算 1 秒 |
| 完整产品结论 | 两次最终活动均 `product_accepted=false`，尚缺完整业务、恢复等证据 |

逐请求证据、版本归档、首轮问题和未完成项见 [实测汇总](../artifacts/product-campaigns-20261016/verification.md) 与 [机器汇总](../artifacts/product-campaigns-20261016/verification.json)。首轮诊断的 200 次适配器错误没有被删除或合并为成功；新负载活动按默认严格门禁返回退出 `1`，符合完整验收缺证据时的预期。上述耗时仅描述本次本机轻负载，不作通用 SLA 或性能提升结论。

`duration_metrics_s.p95` 是包含 worker 启动、进程通信与检查开销的单次执行耗时分位值，**不是服务器纯处理延迟**；HTTP metadata 中请求时间也包含客户端网络与读取时间。当前源码探针只绑定列出的磁盘文件，尚不能证明正在运行的进程/镜像、完整配置及数据库与该摘要一致，不能把它当作完整部署身份认证。

当前 Uptime Kuma 大型工程只有完整源码采集与三态输入制备，npm 依赖、完整构建/应用启动和 LLM 修复实验尚未完成。本机 TestFix HTTP 短循环不能计作 Uptime 大工程修复成绩，也不能计作桌面、MCU 或 MPU 完整产品验收。现有 Flaskr、rich-cli 与 SQLGlot 历史结果同样按各自已验证范围报告。
