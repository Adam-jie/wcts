# TestFix 功能 Skill 契约与执行 v1.1

九项项目 Skill 已接入完整工程的自动运行端。它们采用 `SKILL.md` 与 `references/contract.json`，保存在 `testfix/skill_library`，随 Python 包交付；当前目录与合同版本为 `1.1.0`，JSON schema 家族仍为 v1。完整工程使用八个模型能力及一个确定性验证能力，支持失败反馈、有限修复和重新评审。它们没有安装到用户的全局 Codex Skills，也不表示九个独立大模型。

运行方式、预算与证据目录见 [九能力自主工程补全](autonomous-engineering.md)，整数模型见 [ilp-model.md](ilp-model.md)。历史会话结果与新的自主运行分别记录，不把旧实验追认为新版本 Skill 的效果验证。

## 参考来源与工程适配

参考论文是 [RoboAgent: Chaining Basic Capabilities for Embodied Task Planning，v1](https://arxiv.org/html/2604.07774v1)，重点是 §3.2 与 §6 的按能力划分接口、局部上下文及执行反馈。论文由一个 VLM 实现调度器和五类能力。以下软件工程映射是 TestFix 的设计；没有复现论文训练、具身实验或视觉检测。

| 论文接口 | 本工程 Skill | 输入→交付 | 关键约束 |
| --- | --- | --- | --- |
| Scheduler | [plan-workflow](../testfix/skill_library/plan-workflow/SKILL.md) | 目标、环境、历史→能力调用计划 | 合法依赖顺序，必需关卡不删除 |
| EG | [explore-repository](../testfix/skill_library/explore-repository/SKILL.md) | 功能、目录、查询记忆→候选搜索位置 | 不编造路径，记忆绑定查询与仓库版本 |
| OG | [ground-code](../testfix/skill_library/ground-code/SKILL.md) | 搜索线索、源码→文件/符号/路由位置 | 位置有真实代码证据及哈希 |
| SD | [analyze-requirements](../testfix/skill_library/analyze-requirements/SKILL.md) | 需求、当前实现→目标/现状/缺口 | 需求来源与工程假设分开，不能推断隐藏答案 |
| AD 的工程适配 | [generate-tests](../testfix/skill_library/generate-tests/SKILL.md) | 行为要求→测试和执行步骤 | 测真实入口，基线/候选使用相同用例 |
| AD 的工程适配 | [repair-code](../testfix/skill_library/repair-code/SKILL.md) | 诊断、允许范围→补丁 | 只有此能力能声明业务源码写权限 |
| ES | [diagnose-results](../testfix/skill_library/diagnose-results/SKILL.md) | 执行结果、版本→失败原因与修复目标 | 环境错误、收集错误与功能断言失败分开 |
| TestFix 新增 | [review-patch](../testfix/skill_library/review-patch/SKILL.md) | 候选、公开证据→评审 | 评审绑定候选，不替代实际验收 |
| TestFix 新增 | [verify-delivery](../testfix/skill_library/verify-delivery/SKILL.md) | 冻结候选、测试与关卡→验证/交付报告 | 确定性执行与判分，无大模型最终裁决 |

[Agent Skills 官方规范](https://agentskills.io/specification) 提供目录、frontmatter 和逐步加载组织方式；本工程把额外契约放在引用文件，不虚构规范的必填字段。[mini-SWE-agent](https://github.com/SWE-agent/mini-swe-agent/tree/04d809ceab9df28f9adaed044884180159172930) 提供动作反馈、有限执行和轨迹记录的参考。原项目的工具形式或成功退出标记不等于业务验收。

两个开源参考固定 commit、许可证和一手证据，以及论文的适用边界，记录在 [skill-sources.json](skill-sources.json)。全部 Skill 正文和工程契约为本项目重新编写，没有复制第三方长提示词。

## 机器契约与当前强制范围

`catalog.json` 映射五个现有模型角色，1.2.0 声明三种 profile：`pure_python`、`flaskr_project` 与 `python_package`；每个契约声明版本、输入/输出名称、逻辑读写资源、依赖、预算、失败反馈及优化元数据。正文解释业务前后置条件与实际输出格式。

profile 的 `required_skills` 是本版本单轮必需顺序；目录加载时检查 v1 核心能力和相对顺序，不能通过改写配置或将 `mandatory` 设为 false 删除核心步骤。计划同时检查该顺序与具体 Skill 的依赖，完整工程不能先验收再探索定位。`validate_invocation` 只检查该能力声明的直接依赖，不负责整个计划历史；纯函数已收到目标模块，因此需求分析没有额外探索/定位依赖。

| 层次 | 当前实现 | 不能据此推断的结论 |
| --- | --- | --- |
| 模型能力 | 函数任务保留五个模型角色；完整工程的八个模型能力加载各自 Skill 正文、合同与输出 schema | 模型必然遵守正文或一次调用必然成功 |
| 目录校验 | `SkillCatalog` 检查 JSON schema、路径边界、角色与依赖、受保护资源及版本哈希；核心能力与关卡有固定下限 | 已执行 Skill 或业务条件通过 |
| 整数计划 | 真实 OR-Tools CP-SAT 选择合法策略，约束整数时间、依赖、资源、总声明时长和 token 预算 | 真实运行时间最优或工程通过 |
| 请求与产物 | `validate_plan`/`validate_invocation` 加上控制器的实际路径、需求 ID、候选/产物哈希检查；支持的语义错误反馈后有限纠正，非法结构明确失败 | 通用操作系统权限隔离或任意第三方仓库安全 |
| 执行关卡 | 纯函数 AST 与逐项回归保留；Flaskr 的成对测试身份、导入绑定、冻结文件、独立验收、当前评审、wheel/40 项 HTTP | 全部语言、工程或输入均已验证 |

资源是逻辑名称，不是操作系统权限。独立调用目录校验时，读写与 `completed` 仍是调用者声明，不构成业务执行证据。完整工程控制器另外实施工作区快照、允许文件补丁、测试冻结、真实工具执行与产物哈希检查；每个能力写出带来源版本、输入产物哈希和候选版本的 JSON。计划中的 `required_gates` 始终是待执行要求，最终通过由执行证据决定。当前没有复现论文训练或跨任务学习，也不将单次探索上下文表述为持久化查询记忆。

测试与补丁先经过整组 dry-run 校验，再执行写入；预检错误可反馈后有限纠正，仍受相同范围和累计次数约束。生成测试只接受裸 `test_*.py`/`conftest.py` 文件名，目标目录由宿主选择，不接受模型添加 `generated_tests/` 前缀。

`ModelProvider` 与完整工程的 `ProjectModelProvider` 在调用前核对 Skill 快照，保存 profile、正文应用情况和模型轨迹。完整工程默认复用已登录 Codex CLI 的 ChatGPT OAuth，明确请求 `gpt-5.6-sol`；宿主提供公开 JSON，模型命令工具禁用。CLI 只读模式不构成文件读取的操作系统隔离。CLI output cap 是提示词软约束，用量按 provider 报告及执行账本记录，不能冒充服务端硬上限或账单费用。AgentArts 的客户端与业务协议已有适配和 mock 测试，云端尚未部署。回放模式不调用 LLM，不能充当新模型结果。

此前 GPT-5.6 Flaskr 补全过程发生在这套 Skill 发布前，保留为原实验，不能追认成新 Skill 的模型效果实验。现有独立验收不变，新契约检验也不替代业务测试。

## 两种执行环境

`pure_python` 使用现有五个模型角色与验证关卡。输入已带目标模块，因此不强制额外仓库探索。保持函数签名、既有辅助函数与原导入绑定，只允许原纯计算 AST 子集；新测试是 JSON 数据。

`flaskr_project` 由 `complete-project` CLI 和网页 `/api/project-runs` 实际编排九项能力。正常 Flask 路由、装饰器、模板、SQLite 允许存在；宿主依据注册任务清单控制文件范围。八项模型能力之后，由确定性工具执行独立验收。公开失败、拒审或独立失败进入有限修复循环；新补丁必须重新绑定源码哈希、重跑公开测试并重新评审。

`python_package` 同样执行九项能力，注册 `src` 布局的 Python CLI 工程。门禁采用上游测试、自建冻结行为回归、生成测试、独立验收、wheel 构建及真实 CLI；不要求 Flask 的 HTTP 或数据库关卡。全仓文件参与版本哈希，源码导入分别绑定原始树、候选树和构建后的 wheel。生成测试与独立测试均在子进程导入包之前固定 UTF-8 和 200×60 终端视口，避免输出宽度差异造成假失败。rich-cli 的完整实测记录见 `experiments/rich-cli/README.md`。此前已交付的 1.1.0 Skill 压缩包与旧实验保持原样。

当前 Flaskr 合同覆盖原有 24 项回归、20 项独立验收和 40 项 HTTP 检查，生成用例数由本次实际收集结果决定。冻结测试、导入路径绑定、从 wheel 启动和 SQLite 跨进程重启均须有执行证据。只有这些关卡全部满足，才生成已完成工程下载包。该 adapter 面向已检查的 Flaskr，不是任意语言或恶意仓库的通用操作系统沙箱。

默认总预算为 4500 秒、800000 token；单次超时依次为规划/探索/定位/需求各 120 秒，生成测试/诊断/修复/评审各 180 秒，确定性验证 240 秒。所有 Skill 合同累计上限为五次，所选计划可进一步收紧；纠正调用也消耗该额度。默认业务修复两轮，可配置 1–5 轮。实际调用还受剩余总时间和调用前预留、调用后 provider 用量记账约束。详见自主工程文档中的估计与实际账本区别。

## 本地检查

在工程根目录执行：

```powershell
.\.venv\Scripts\python.exe -m testfix.cli skills --profile flaskr_project
.\.venv\Scripts\python.exe -m testfix.cli skills --profile pure_python
```

输出目录哈希、校验后的计划和待执行关卡，明确 `execution_performed=false`。可用 `--plan` 后接 Skill ID 检查其它顺序；缺少评审/验证或违反依赖会非零退出。Python 调用方式：

```python
from testfix.skills import load_catalog
catalog = load_catalog()
catalog.validate_invocation(
    'repair-code', 'flaskr_project',
    reads=['candidate_sources', 'diagnosis_report'],
    writes=['candidate_sources', 'patch_artifact'],
    completed=['diagnose-results'], attempt=1, timeout_seconds=120,
)
```

上面两个 `skills` 命令和 `validate_invocation` 示例只做合法声明检查，不执行模型或工程。发起九能力实际执行使用 `complete-project`；不能把目录校验输出当作验收结果。

## 已接入的整数调度

`testfix.scheduling.solve_plan(options, budget, profile='flaskr_project', catalog=None)` 使用已安装的 OR-Tools CP-SAT，输入与有解输出都经过 `catalog.validate_plan`。每个必需 Skill 恰好选择一个外部策略选项，选择变量为布尔值，起止时间为整数毫秒；依赖、总时长、token、Cumulative 资源容量和候选写入 NoOverlap 均进入真实模型。没有 LP relaxation 或无解时的假计划回退。

运行端提供 focused/expanded 两种输出额度策略，并把有限纠正与修复允许次数计入累计声明时长和 token 预测。声明时长另外包含生成测试后的 150 秒公开基线、每个修复轮次的 150 秒候选公开测试；最终验证有自身工具额度。默认两轮计划为 4410 秒，处于 4500 秒总预算内。通用求解接口也接受外部的多个合法选项及数值来源说明。当前目标是配置权重下的声明时长与 token 之和；策略输入是配置的上界/预测，合同中未测量的 `estimated_seconds`/`estimated_tokens` 仍为 `null`，没有生成成功率、费用或成本置信度。

`OPTIMAL` 只表示给定声明目标已证明最优，`FEASIBLE` 是未证明最优的合法解。`INFEASIBLE`/`UNKNOWN` 没有可执行计划，配置非法另行报告。当前固定单轮串行顺序不等于跨任务并行调度。求解后的所有策略必须接受完全相同的独立关卡；详细状态、字段与数学模型见 [ilp-model.md](ilp-model.md)。

完整运行端将求解时间设为 `min(10, remaining_seconds)`，给繁忙 CPU 下的搜索保留余量；真实返回 `UNKNOWN` 时仍停止，不通过增加等待或假结果宣布成功。环境由 bootstrap 调用 `setup-project.ps1` 准备，默认 Python 3.13 与 17 项固定版本依赖；测试导入绑定本次独立工程，不 editable 安装历史候选。

Skill 文件随 v0.2.0 核心 wheel 交付，但完整 `web/`、工程 fixture 和运行脚本随源码包提供。源码打包入口、固定输出与逐文件哈希见 [自主工程分发说明](autonomous-engineering.md#可复现的完整源码分发)；核心 wheel 不构成独立可运行完整控制台的发行包。

## 本次版本的真实执行证据

运行 `1a500f9521c141168613504e1c57d352` 的最终 `report.json` 已核对：九项能力均完成，一轮补丁后通过全部关卡，耗时 441.452 秒。本次生成测试在原工程为 0/10、候选为 10/10；原有回归保持 24/24，独立验收为 0/20→20/20，wheel 构建 1/1，包含 SQLite 重启的 HTTP 检查 40/40，全部 verdict 为真。候选与独立评审引用同一个实际 SHA-256。

八次真实传输来自 Codex CLI，均明确请求 `gpt-5.6-sol`；`actual_model` 全部为 `null`，模型证据仅标记 `requested_cli_flag_only`，没有补写服务端确认。provider 报告 token 总量为 198161，不等于独立核验的账单费用。报告位于 `artifacts/1a500f9521c141168613504e1c57d352/report.json`，交付包及哈希见 [本次实测记录](autonomous-engineering.md#本次真实运行记录)。这提供了当前 Skill 契约的一次实际运行证据，不把边界 fixture 测试或历史候选复验混入本次成绩。
