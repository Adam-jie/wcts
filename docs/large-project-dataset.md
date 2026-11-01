# 大型工程数据集：受控缺陷与需求实现

本版按用户确定的两条任务路线、四类运行对象组织。清单见 [large-project-suite.json](../examples/datasets/large-project-suite.json)，公开源版本锁见 [source-lock-20261016.json](../examples/datasets/source-lock-20261016.json)。**候选项目、已下载源码、已制备任务、通过环境预检查、Agent 修复成功是不同状态。**32 项是首批设计规模，不是已经完成的实验数。

## 两条任务路线

| 路线 | 输入与构造 | 最终判断 |
| --- | --- | --- |
| 开源完整工程受控缺陷修复 | 固定完整 GitHub 工程，验证健康原版后，在独立副本删除部分实现、注入行为错误或编译/导入错误。Agent 获得完整故障工程及公开需求、症状。 | 修复后能构建、启动，目标功能恢复，独立验收通过，原有回归保持，与健康原版的可观察行为一致。 |
| 用户描述功能后的实现 | 输入自然语言需求及现有完整工程。先明确需求编号、输入、预期、边界、设备协议、允许修改范围及独立验收合同，再分配给 Agent。 | 新功能满足合同，已有功能不退化，构建和运行有真实证据。没有新功能的参考实现时，按合同评判，不伪造“完整答案代码”。 |

从零描述一个应用也归第二条，但单列 `greenfield` 成绩：它没有原有回归基线，不与现有工程修复成功率混算。当前离线准备器只支持对已有本地工程扩展；从零工程生成仍需相应执行适配器。

“大型”按完整产品和多模块结构判定，保留源码、资源、依赖声明、构建入口及原有测试。实际采集后记录生产代码文件数、物理行数及统计方法、模块、依赖服务、可执行业务流程和测试收集数。仓库星数、断言数或一个很长的文件不能替代这些证据；从大型仓库摘出一个函数也不算完整工程实验。

## 首批工程

每类两个工程，每个设计三项缺陷任务及一项需求实现任务：**8 个项目、24 项受控缺陷、8 项需求实现，共 32 项拟制任务**。逐项通过健康原版、故障复现和验收质量门禁后才计入正式测试集；预先选中的环境失败与排除原因也保留，不根据修复成功与否换题。

| 类别 | 完整工程 | 工程范围与运行验收 |
| --- | --- | --- |
| 网页 | [Uptime Kuma](https://github.com/louislam/uptime-kuma) | Vue/Express、监控器、数据库、告警和状态页。添加本地目标，验证正常、异常、恢复、持久化与真实页面。 |
| 网页 | [Paperless-ngx](https://github.com/paperless-ngx/paperless-ngx) | Django/Angular、文档导入、OCR、后台任务、标签与全文检索。导入固定文档，检查检索集合、权限、任务完成和下载。 |
| 桌面软件 | [Spyder](https://github.com/spyder-ide/spyder) | Qt IDE 的编辑器、控制台、搜索、调试和插件。打开多文件工程，搜索、跳转、编辑保存并运行脚本。 |
| 工具软件 | [SQLFluff](https://github.com/sqlfluff/sqlfluff) | 完整解析、规则、修复、CLI、多方言和插件工程。真实处理 SQL 工程目录，对比诊断、退出码、文件变化和幂等性。 |
| MCU | [Zephyr](https://github.com/zephyrproject-rtos/zephyr) | 内核、驱动、板级支持和子系统。Twister/ztest 运行队列、超时、唤醒及相关回归；固件启动与外设行为另有证据。 |
| MCU | [FreeRTOS 完整发行版](https://github.com/FreeRTOS/FreeRTOS) | 保留内核子模块、库及完整 QEMU 应用，检查任务、队列、事件组和计时器协作。 |
| MPU | [U-Boot](https://github.com/u-boot/u-boot) | 启动程序、命令、环境变量、存储和板级模块。在 sandbox/QEMU 中验证命令、导入导出、持久化与启动。 |
| MPU | [Mosquitto](https://github.com/eclipse-mosquitto/mosquitto) | Linux 网关上的完整 MQTT broker、客户端、QoS、保留消息、桥接和持久化。用真实客户端验证发布、订阅、重连与重启。 |

这里只列候选范围与验收方案，不代表八个工程已经能由现有通用入口执行。许可证按固定版本中的原始声明保留；元数据锁不代替源材料、子模块和第三方文件许可核对。

## 三态对照与独立验收

受控缺陷任务采用同一环境的三份工程：

```text
原始工程 G --构建/启动/冻结验收通过--> 健康参考
    |
    +--独立复制、显式注入--> 故障工程 B --复现目标失败--> Agent 输入
                                  |
                                  +--复制并由 Agent 修改--> 候选 R
R --构建+启动+目标验收+原有回归+完整性核验--> 与 G 对照
```

`pristine` 在准备器中只指“未改动的输入快照”，通过完整预检查后才能称健康原版。B 可以有编译、导入或启动故障，不强求它还能通过原有回归；应以 G 与 R 的同一套冻结回归对照。只有 G 已健康、注入前置哈希匹配、B 在相同环境确实出现预期目标失败，才把它归为合法代码缺陷。缺编译器、依赖未安装、外部服务未启动不能算一次成功构造的源码错误。

Agent 看见完整 B 与公开需求，可按文件检索与调用图定位；健康 G、原始替换片段、逆向补丁、独立验收源码只供评测者。模型生成的公开测试应在修复前冻结，修复者不能删测试、降断言或修改依赖锁来制造通过。独立验收与原回归在修复后由宿主执行，不能只采信模型的总结。

源码 diff 用于审查，不要求修复代码逐字等于 G。行为对照使用同样的输入、数据库初态、设备协议与工具链；时间戳、随机 ID 等只按事先声明且封存的规则归一化，不能失败后临时丢掉差异。

三态均使用同一冻结数据、oracle、环境与执行策略，按 case/data 保留每轮回执。固定版本循环、失败后无改动重试、修改后的新候选复测分别统计；同输入首次失败不能被后轮通过覆盖。功能 scope 通过不等于产品合格，稳定性、并发、性能、恢复、安全、兼容与交付缺口仍阻止产品验收，具体见 [产品级测试与循环准则](product-testing.md)。

对自然语言需求，例子是“在文档管理系统增加组合筛选，并保留权限和分页”。应先固定：谁可访问、哪些筛选可组合、空结果及非法参数行为、页面和 API 输出、原有检索如何回归。CodeAgent 再按依赖派工、汇总实际实现和失败证据、二次修改。需求合同尚不明确时不能让实现者自己决定什么算成功。

## 四类环境分别证明什么

网页须有完整服务启动、真实浏览器业务路径及后端持久化证据；桌面须有真实 GUI 操作与插件/进程回归；CLI 工具须实际调用安装后的命令。只导入模块或打印版本不算完整应用验收。

MCU 与 MPU 分别记录 `host_simulation`、`qemu_firmware`、`physical_board`。固件编译成功不等于功能通过，QEMU 通过不等于真实板卡通过，SSH/HTTP/串口连通及软件回环也不等于源码已经补全。真实板卡任务须绑定板型、编译配置、固件哈希、烧录与启动记录、串口/网络回执及实际外设状态。

FreeRTOS 应锁定顶层及子模块 SHA，使用 `FreeRTOS/Demo/CORTEX_MPS2_QEMU_IAR_GCC`。三份工程共享固定的 `mainCREATE_SIMPLE_BLINKY_DEMO_ONLY=0` 配置后才运行 `main_full` 协作自检；不得只见一次 `PASS` 就验收。[官方完整自检源码](https://github.com/FreeRTOS/FreeRTOS/blob/main/FreeRTOS/Demo/CORTEX_MPS2_QEMU_IAR_GCC/main_full.c)。Zephyr 的 [Twister](https://docs.zephyrproject.org/latest/develop/twister/index.html)、U-Boot 的 [pytest/sandbox](https://docs.u-boot-project.org/en/latest/develop/pytest/usage.html)、Mosquitto 的 [官方测试入口](https://github.com/eclipse-mosquitto/mosquitto/blob/master/README-tests.md) 作为适配依据。

## 已落地的制备与实际状态

1. 版本化的八项目清单与 32 项任务设计。
2. 八个公开 Git ref 已实际解析为完整 commit，保存原始 ref 回执摘要。`git ls-remote` 只查引用，不下载、安装或执行工程；GitHub REST 曾返回限流，采集改用公开 Git 引用，没有申请或暴露凭据。
3. 离线三态制备器 `testfix/dataset_cases.py`：在新目录准备原始、故障及候选快照，支持唯一文本替换和生产文件删除；支持已有工程的无注入需求扩展。原始源码保持不变，不覆盖历史目录。
4. Uptime Kuma 2.0.0 完整源码已浅克隆并验证 HEAD=`124e79bce978ce29f0de681267b62b7d0d3d4429`。实际 tracked 文件 **618** 个、**5,961,846** 字节；`server/` 和 `src/` 的 js/ts/vue/css/scss 共 **332** 文件、**47,537 物理行**，包括空行和注释，不称可执行 LOC。
5. 首个语法错误配方来自上述完整工程。原始监控模块 `node --check` 退出 0，故障副本退出 1 并报 SyntaxError。这只证明受控语法故障可复现；尚未安装该工程依赖、启动完整应用或运行 Agent 修复，状态必须为 `preflight_pending`，不能计成功率，也不是跨模块复杂修复已经完成。
6. 已实际制备两种输入：`artifacts/dataset-suite-20261016/cases/uptime-kuma-syntax-001/` 与 `uptime-kuma-requirement-001/`。后者是 [自然语言需求示例合同](../examples/datasets/uptime-kuma-requirement.json)，涉及监控逻辑、页面参数和数据库迁移；尚须核查功能缺口、制定独立验收，若已经满足则记录 `already_satisfied`，不计补全成功。需求模式三份初始快照相同；故障模式原版语法通过、Agent 输入及初始候选均语法失败，原克隆保持干净。

制备工具的 **32 项单元与边界检查通过**，并经独立复核；这与“32 项拟制工程任务”是两个不同数量。测试与源文件摘要见 `artifacts/dataset-suite-20261016/verification.json`。两个样本均未通过完整应用预检查，因此正式可计分任务数仍为 **0**。

源码快照与实验事实见 `artifacts/dataset-suite-20261016/uptime-source.json`。八源引用锁保存的是采集当时的元数据状态，之后实际下载源码的状态由各样本证据单独记录，不回写旧锁制造历史变化。

准备首个完整工程输入：

```powershell
.venv/Scripts/python.exe -X utf8 scripts/prepare_dataset_case.py `
  --spec examples/datasets/uptime-kuma-syntax-defect.json `
  --source data/large-project-suite-v1/sources/uptime-kuma-124e79b `
  --output artifacts/dataset-suite-20261016/cases/uptime-kuma-syntax-001
```

输出目录已存在时会拒绝覆盖。配方是评测者输入，包含原始片段，不得把它和 `evaluator-only/` 挂载到 Agent 环境。

```text
case/
  evaluator-only/pristine/       未改原始快照，仅评测者可读
  evaluator-only/preparation.json 注入、哈希与排除项
  agent-input/project/           完整故障输入或待扩展工程
  agent-input/task.json          白名单公开需求与修改范围
  candidate/                    初始同输入，之后接收受控补丁
  manifest.json                 制备状态、快照摘要与限制
```

目录划分不是操作系统安全沙箱。实际执行须通过受限工具或独立只读挂载隔离评测材料。制备器拒绝路径逃逸、链接/junction、硬链接和危险覆盖，并限制体积及文件数；它也会隔离潜在凭据、缓存等路径。必须审查排除清单：公开 `.pem/.key` 可能是 TLS 测试夹具，真实 `.env` 也可能是上游默认配置，不能在缺少它们时称完整测试通过。FreeRTOS 的链接及子模块、可执行位、工具链和板级配置须完成相应适配后才能入正式集。

当前 Uptime 原始克隆有 618 个 tracked 文件；制备快照各 617 个文件，因为保守排除了 `.npmrc`（以及不属于 tracked 源码的 `.git/`）。上游 `.npmrc` 是否包含必要的公开构建设置需要在完整环境预检查前审核和处理，不能把过滤快照描述成已经验证可构建的原样副本。原始完整克隆仍保留该文件。

通用准备器只封存本地实际字节；40 位 commit 字段保持 `declared_not_checked`，不会仅凭字符串宣称该快照等于上游。Uptime 的 Git HEAD 校验属于独立采集证据。现有 Flaskr/rich-cli 入口及 SQLGlot 历史实验保留；它们不能冒充已支持四类任意大型工程。现有整包上下文 180 KB 门限、只接收行为断言失败的旧入口，也没有因为新增清单就自动升级为大型多语言修复器。

## 正式成绩

分别报告每条路线、每个领域及每种硬件验证级别：预选数、源码已收集数、环境可运行数、有效故障数、完整成功任务数、首次成功率、固定预算多轮成功率、原有回归破坏数、构建/启动失败数、墙钟时间、模型调用与实际报告 token。所有关键门禁通过才算一个任务成功，不能用大量简单断言稀释核心功能失败。

按仓库及注入家族划分开发、验证和留出集；同一缺陷的轻微改写不跨组。公开仓库及模型训练可能已有重合，不宣称无污染盲测。调度算法对比使用相同模型、相同任务、相同预算与冻结环境；串行、无优先级及完整策略分开，实际重复后再报告差异，不事先承诺提升数字。

旧集合的真实采集状态与指标定义仍见 [测试数据与评测协议](benchmark-datasets.md)。HumanEval 与小工程仅作工具调试，不进入本版大型完整工程主成绩。新准备器的微型测试夹具同样只是框架单元测试，不是正式工程样本。
