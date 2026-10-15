---
name: analyze-requirements
description: 对照公开功能描述和当前实现，形成可观察的验收条件与缺口；对应 requirements 角色。
metadata:
  contract-version: "1.2.0"
  contract-ref: "references/contract.json"
---

输入公开功能描述、固定源码及允许修改范围；完整工程还参考已核实的代码位置。借鉴 SD 的目标状态描述，把当前实现、目标行为和依赖说清楚。

区分资料明确要求、用户补充要求和本次工程假设。每项需求应有可观察输入与输出、边界、需要保持的已有行为，以及 `missing`、`partial`、`implemented` 或 `unknown` 的证据判断。没有运行证据时不能把“看起来实现了”写成通过。

完整工程输出 `requirement_matrix` 与 `state_description`，每项包含 ID、来源、行为、现有证据、缺口、验收方式与未决假设。不能扩大用户目标，不能把现有实现的偶然行为当成需求。

纯函数模式沿用现有 JSON：`{"requirements":["可观察行为","边界行为"]}`；不增加与调用端不兼容的必需字段。保留入口签名及已有辅助函数。完整 Flaskr 模式允许路由、类、装饰器、模板、SQLite 等正常工程结构，按文件范围约束。

`python_package` 按公开任务分析库 API 与命令行行为：输入参数、标准输入输出、错误输出、退出码、Unicode/边界与需保持的兼容性。轮子构建和真实 CLI 是软件包交付关卡；不能自行套入 Flaskr 的 HTTP、路由或 SQLite 需求，也不根据隐藏验收猜测功能。

只写需求产物。不得读取独立验收源码或参考答案，也不得通过改写需求使失败变成通过。公开描述不足以决定行为时明确假设和影响，不生成隐藏测试的猜测。

读取 [机器契约](references/contract.json) 确认前置输入、资源和反馈格式。
