---
name: ground-code
description: 将自然语言功能和搜索线索绑定到真实文件、符号、路由及代码版本；用于避免基于猜测生成修改。
metadata:
  contract-version: "1.2.0"
  contract-ref: "references/contract.json"
---

借鉴 OG 的目标定位接口，把需求中的“对象”落到代码实体。工程输出是位置与代码证据，不生成视觉坐标。

输入公开需求、搜索候选、源码快照和范围清单。实际打开候选文件，核对函数签名、路由、模板引用、数据表和已有测试；只把读到的实体标为 `located`。

输出 `code_locations`：`requirement_id`、`status`（`located`、`partial`、`not_found`）、`path`、`symbol_or_route`、行号、源码哈希及支持判断的代码片段。拟新增文件须明确标为尚不存在（`not_found`），不能冒充现有文件。

明确实现入口和相关依赖分别在哪；同名函数、不同测试副本及安装包副本不能混用。路径和行号依赖当前快照，修改版本后重新定位。最终测试目标还须由执行器的真实导入路径证明。

`python_package` 核实公开库函数、CLI 入口和构建配置的实际关联，并绑定当前候选文件哈希。源树、安装后的 wheel 与命令入口的来源须分别有证据；不能借用全局安装或另一候选副本制造功能通过，不把 CLI 执行解释为 HTTP 启动。

只交付定位产物，不生成或执行补丁。路径存在性和描述是否支持需求是本角色的核对责任；实际 Flaskr 测试导入绑定由 `project_pytest_worker` 与评测器检查。

读取 [机器契约](references/contract.json) 查看资源与失败返回要求。
