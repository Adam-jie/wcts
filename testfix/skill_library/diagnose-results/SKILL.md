---
name: diagnose-results
description: 根据真实失败结果、代码版本和需求分析缺口及原因，给出有限的修复目标；对应 diagnosis 角色。
metadata:
  contract-version: "1.2.0"
  contract-ref: "references/contract.json"
---

借鉴 ES 的执行反馈总结，分析已执行动作的结果。输入公开需求、源码、生成测试及真实公开运行记录；不读取独立验收源码。

先核对版本、命令、退出码、收集数量、目标导入路径与用例 ID。将失败分成 `assertion_failure`、`collection_error`、`environment_error`、`timeout`、`policy_rejection`、`integrity_error`；零用例或错误目标不能作为功能通过/缺失的证据。

对每个功能失败给出最小重现输入、预期与实际差异、关联需求、影响位置和可能原因。区分日志直接证明的事实与待验证假设；优先解释失败，避免无证据的大范围重写。

`python_package` 分开解释库 API 断言、真实 CLI 的输出/退出码不符、wheel 构建问题和错误安装/导入来源。CLI 未启动、环境缺失或收集失败不是功能通过证据；只有当前任务公开结果与有限的独立验收摘要可进入诊断，不读取独立验收源码。不要把软件包故障改写为 Flask HTTP 或 SQLite 问题。

纯函数模式沿用 `{"diagnosis":"基于证据的缺口与修复目标"}`。完整工程输出 `diagnosis_report`：失败 ID、证据引用与哈希、原因假设、反证/不确定项、修复范围、回归关注点、下一步建议。反馈记录归入同一任务和候选版本，不能用旧版本成功覆盖当前失败。

本角色只写诊断和失败记忆。补丁由 `repair-code` 生成；验收结论由验证器形成。超时、基础设施故障或策略拒绝应回到相应阶段，不能直接放宽测试或权限。

读取 [机器契约](references/contract.json) 确认输入、预算与失败反馈字段。
