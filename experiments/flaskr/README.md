# 完整 Flaskr 工程补全实验

本实验直接复用 Pallets 官方 Flaskr 微型博客工程，而非单个函数或人为注入故障。
上游提交固定为 `2c1b30d0503cfb064f1cb252e6614a06915a362a`，子目录为 `examples/tutorial`。
23 个上游文件原样存放于 `baseline/`；`source.json` 保留出处及逐文件 SHA-256，BSD 许可证随源码保留。

需求来自官方 [Keep Developing](https://flask.palletsprojects.com/en/stable/tutorial/next/) 页面：文章详情、标题搜索、每页五篇分页。
这是实现上游明确提出的后续开发能力，不是一个已报告的上游 bug。
路由、搜索字面语义、排序及异常状态码的明确验收决定见 `requirements.json`。

GPT-5.6 Sol 在当前 Codex 会话分别承担测试设计、工程补全和独立代码评审。
`tester-input.json`/`tester-output.json` 和 `developer-input.json`/`developer-output.json` 保留公开输入与生成输出。
`generated_tests/` 为模型生成的真实 Flask/SQLite pytest 测试。
`acceptance_tests/` 为监督者提前编写的独立验收；不向补全 Agent 提供。
原有 `candidate/tests/` 必须与 `baseline/tests/` 逐文件一致。

最终运行 `b69513645ddf490f8392d33823262585` 已通过：原有24/24、生成25/25、独立验收20/20、wheel构建、真实HTTP40/40；原工程生成用例1/25、独立验收0/20。
实际模块路径分别绑定 baseline/candidate，防止测试导入错误项目造成误判。
候选合并测试69/69，Python应用语句/分支覆盖100%；框架自身107/107，独立评审确认发现已处理。
完整过程与边界见 `docs/flaskr-engineering-experiment.md`。

本机 Python 3.13.5、独立 `.venv-flaskr` 环境，依赖精确版本列于 `requirements.lock`。
可在工程根目录重建环境：

```powershell
uv venv .venv-flaskr --python .venv/Scripts/python.exe
uv pip install --python .venv-flaskr/Scripts/python.exe -r experiments/flaskr/requirements.lock
.venv/Scripts/python.exe -m testfix.cli evaluate-project experiments/flaskr/manifest.json
```

评测器检查文件与评审哈希，分别运行原工程/候选工程的原有、生成、独立验收测试。
只有正常收集后的断言失败才能证明基线缺少功能；收集错误、超时、零用例均不能用于得分。
最终交付还构建 wheel，直接从构建产物启动真实 HTTP 应用，测试账号、文章 CRUD、新功能和重启后的 SQLite 持久性。
模型输出使用当前会话保存结果，重跑命令不会产生新模型输出；没有直接模型 API 或 AgentArts 调用声明。

完整工程执行器当前限定在已检查的本地 Flaskr 案例。本机子进程执行不等于任意不可信仓库的通用安全沙箱。
