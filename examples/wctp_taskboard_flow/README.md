# WCTP 逐步测试样例

这个独立样例用用户提供的 WCTP 原版步骤库和图执行器，测试已登记的 TaskBoard 副本。它将准备数据库、新增中英文任务、搜索、分页、完成状态保存和删除拆成 11 个有名称的实际动作，按图串行执行两轮。每个动作输出其真实 WSGI 请求与响应；这是进程内接口测试，不是浏览器操作或网络 HTTP 测试。

`setup_demo.py` 根据步骤定义生成 WCTP 环境、步骤库、用例和测试集，并复制所选的 TaskBoard 源码。生成目录必须是独立 WCTP 副本中的 `repository/taskboard_demo`；首次执行会拒绝覆盖已有目录。确实需要重新生成此专用演示目录时传 `--update`。源 WCTP 和 TaskBoard 工程不会被修改。

```powershell
.venv/Scripts/python.exe examples/wctp_taskboard_flow/setup_demo.py --wctp-root <WCTP副本> --project <TaskBoard工程>
```

进入该 WCTP 副本后，可在原编辑器打开 `taskboard_demo` 环境和 `case_TaskboardFlow` 用例。`show_demo.py` 是在该副本工作目录中启动原编辑器并打开该用例的快捷入口，需要运行环境已安装 PyQt5 及 WCTP 所需依赖。

该样例的步骤由开发者编写，尚未由 Agent 根据任意需求自动生成，也没有接入无遁桌面软件的正式任务列表。要作为通用能力，仍需将步骤协议、模型生成、可视化编辑、执行与结果报告接入正式工作流，并针对更多工程和设备验证。
