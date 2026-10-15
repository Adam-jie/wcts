---
name: plan-workflow
description: 为完整工程的功能补全选择能力调用顺序、参数和验证关卡；用于编排，不负责生成补丁或判定功能通过。
metadata:
  contract-version: "1.2.0"
  contract-ref: "references/contract.json"
---

接收公开目标、固定仓库版本、执行环境与当前证据，输出下一组能力调用及其参数。RoboAgent 的 Scheduler 提供了能力编排思路；工程关卡与权限是 TestFix 的设计。

开始前确认完整工程任务属于 `flaskr_project` 或 `python_package`，目标和允许修改的文件已进入 `scope_manifest`。从目录选择真实存在的 Skill；传递上一步产物，不凭空补造代码位置或执行结果。`pure_python` 沿用其六项核心计划。

输出 `run_plan`：`profile`、`source_revision`、按顺序排列的 `skill_ids`、每次调用的参数来源、必需关卡、预算与停止条件。同时维护完成、剩余与失败的任务条目，状态变化必须关联产物哈希或执行记录。给出简短决策依据即可。

探索未找到目标时转回探索或定位；断言失败时进入诊断；导入、环境或构建失败时先解决对应执行问题。修复后再评审和验证。有限预算内无法完成时交付失败证据与待办，不能删除关卡制造成功。

完整工程必需顺序为规划→探索→定位→需求分析→测试生成→诊断→补丁→评审→独立验证。目录校验单轮顺序，重试由控制器的有界补丁循环处理；真实 CP-SAT 选择合法策略与资源，不能把求解最优解释为功能通过。

`python_package` 面向固定版本的 Python 软件包和真实 CLI：保留库 API、参数/标准输入输出/退出码等公开行为要求，独立验收、回归、wheel 构建和 `real_cli` 不可删除。`flaskr_project` 继续要求 `real_http` 与 `sqlite_restart`；CLI 执行不能冒称 HTTP 或 SQLite 重启证据，也不能反向替代这些原关卡。

只写计划和调度记忆，不改业务文件、测试或评分依据。程序可校验已声明的顺序、资源请求和预算；自然语言决策的正确性仍要靠执行证据。

读取 [机器契约](references/contract.json) 确认允许资源、依赖与失败反馈。
