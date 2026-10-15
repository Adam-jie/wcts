# 工程修复、需求实现与多 Agent 协作参考

核查日期：2026-10-15。以下仅使用论文原文、作者仓库和项目官方资料；借鉴方法不等于已经复现其算法或达到其论文成绩。

用户所说的代码 Agent，在本工程中指能够调用大模型、检索仓库、受 Skill 与执行权限约束、根据工具反馈修改代码的执行角色，不绑定某个同名开源库。

## 四篇直接相关的论文

| 工作 | 研究的问题 | 本工程可以借鉴的机制 | 使用边界 |
| --- | --- | --- | --- |
| **MAGIS**，NeurIPS 2024 | 多 Agent 协同解决 GitHub 仓库 issue | Manager 拆任务并组建开发团队；Repository Custodian 定位文件；Developer 实现；QA 审查并反馈返工。可用于需求拆解、按任务分配角色、显式反馈。 | 论文主要通过 SWE-bench 的 issue 解决任务验证，不代表任意需求都能实现，也不能把角色命名当作完整复现。 |
| **SWE-agent**，NeurIPS 2024 | Agent 如何浏览、编辑完整仓库并执行测试以解决问题 | 面向模型设计搜索、文件阅读、编辑、执行工具；限制输出范围；编辑后检查语法；把执行结果返回 Agent 形成行动循环。 | 同时涉及仓库级 SWE-bench 与函数级 HumanEvalFix；两种任务的结果不能混用。采用工具循环本身不构成对论文系统的复现。 |
| **Agentless**，2024 | 是否必须依赖复杂自主 Agent 才能解决工程问题 | 将定位、修复、补丁验证分开；为复杂多 Agent 系统提供流程简单、易解释的对照基线。 | 采用固定三阶段流程，不能据此证明动态多 Agent 调度更有效；本工程应进行同任务、同模型、同预算对照。 |
| **MetaGPT**，ICLR 2024 | 如何把自然语言需求转为有角色分工的软件开发流程 | 产品、架构、项目管理、工程角色按 SOP 传递需求、设计、任务与代码等结构化产物。特别适合参考“描述需求后自动拆任务并协作”。 | 新工程生成和已有仓库修复不同；生成了设计、文档或代码，不等于真实构建成功、需求验收通过。 |

论文与作者实现：

- MAGIS：[论文](https://arxiv.org/abs/2403.17927)、[作者仓库](https://github.com/co-evolve-lab/magis)。仓库包含 `manager.py`、`custodian.py`、`member.py`、`quality_assurance.py`，可直接核查角色交互实现。
- SWE-agent：[论文](https://arxiv.org/abs/2405.15793)、[作者仓库](https://github.com/SWE-agent/SWE-agent)、[官方工具接口说明](https://github.com/SWE-agent/SWE-agent/blob/main/docs/background/aci.md)。
- Agentless：[论文](https://arxiv.org/abs/2407.01489)、[作者仓库](https://github.com/OpenAutoCoder/Agentless)。
- MetaGPT：[论文](https://arxiv.org/abs/2308.00352)、[作者仓库](https://github.com/FoundationAgents/MetaGPT)。可进一步阅读 [`team.py`](https://github.com/FoundationAgents/MetaGPT/blob/main/metagpt/team.py) 与 [`base_env.py`](https://github.com/FoundationAgents/MetaGPT/blob/main/metagpt/environment/base_env.py) 的团队、环境与消息传递。

## 执行环境与评测参考

- **OpenHands**：[ICLR 2025 论文](https://arxiv.org/abs/2407.16741)描述通用软件 Agent、隔离执行环境、多 Agent 协调与评测集成；[官方 SDK](https://github.com/OpenHands/software-agent-sdk)提供可检查的执行框架。适合参考工具事件、环境隔离与执行记录；本工程接入华为云时仍需独立适配 AgentArts 的运行协议与资源限制。
- **SWE-bench**：[ICLR 2024 论文](https://arxiv.org/abs/2310.06770)、[官方评测仓库](https://github.com/SWE-bench/SWE-bench)。任务输入是历史代码仓库与 issue，输出是接受独立测试验证的补丁。它是评测基准，不是多 Agent 编排算法，也不覆盖用户描述的所有新增功能。

## 对本工程的落实与证据要求

拟参考的组合是：**自然语言需求 → 可验收需求项 → 有依赖关系的任务 → 能力与预算约束下的角色分配 → 工具观察与修改 → 审查反馈 → 构建、回归与独立验收**。这是本工程的设计选择，不应归为某一篇论文的原样算法。

Skill 负责角色可调用工具、输出格式、可修改范围和完成条件；调度器负责依赖、资源、并发、预算及冲突。角色的自然语言建议不能绕过这些执行约束。补全角色不能修改独立验收测试来获得通过。

动态任务 DAG、并行协作、工具循环和失败后重新规划是否真实生效，应查看对应运行记录中的任务依赖、分配结果、开始结束时间、工具请求与返回、补丁、审查及测试结果。存在相关代码、流程图或配置字段不算完成端到端验证。

已有 rich-cli 实验只能证明该固定仓库、该需求和该环境下的实验结果；在新的动态协作路径完成真实运行前，不能把旧实验记为新路径的成功。不能声称本工程已经超过上述论文或能修复任意完整工程。比较效果时应统一仓库提交、需求、模型、预算、验收测试与执行环境，并保留失败任务。
