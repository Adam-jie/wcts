---
name: generate-tests
description: 从公开需求生成能暴露缺失功能的自动化测试与执行步骤；对应 tester 角色，不生成实现补丁。
metadata:
  contract-version: "1.2.0"
  contract-ref: "references/contract.json"
---

在需求分析后调用，输入需求条目、公开源码与环境说明。将验收行为转成测试动作，是 TestFix 对 AD 的工程适配；测试与补丁分别由不同能力承担。

逐项写测试步骤：准备隔离输入/数据库→调用真实入口→检查可观察结果→记录证据→清理状态。正常路径、空输入、非法输入、边界和已有行为按需求选择，不能只测试新写的辅助函数或用 mock 替代待验证功能。

纯函数模式输出既有 `cases` 与 `rationale` JSON；用例是数据。每项有 `args`，可选 `kwargs`，且 `expected` 或允许的 `raises` 二选一；推荐 3–8 个有区分力的用例，调用端允许 1–30。误差界限必须来自行为需要，不放宽断言换取通过。

Flaskr 模式输出新增 pytest 文件、需求覆盖映射和可复现命令；使用真实 Flask 客户端、临时 SQLite 与页面/HTTP 断言。导入目标由执行器指定，不在 fixture 内硬编码 candidate 路径。基线与候选必须运行同一批用例，核对 collection IDs 和导入绑定。测试收集错误、依赖缺失和断言失败分开记录。

`python_package` 针对当前公开的库 API 与 CLI 合同设计用例、输入和预期输出。通过受控适配器验证真实命令行的参数、标准输入输出和退出码，不以 mock 代替目标功能，也不提供任意 shell 命令。基线与候选仍须使用同一非空用例集合并绑定真实包导入；从 wheel 执行 CLI 的交付证据不叫作 HTTP/SQLite 检查。新增 pytest 只写宿主指定的独立目录，路径使用裸 `test_*.py`/`conftest.py`，不得重定向到全局包。

新测试冻结后供补丁角色只读使用。原有回归及独立验收由评测器保管，本角色不得改写；独立验收源码和参考实现不可读。未运行的测试标为 `not_run`，没有有效失败不能伪造“已发现缺口”。发现测试自身错误时提交理由，经控制器审核生成新版本并重跑两侧，保留旧记录。

输出 `generated_tests` 与 `test_plan`，包括预期、准备步骤、命令、覆盖和假设。读取 [机器契约](references/contract.json) 查看数据与资源约束。
