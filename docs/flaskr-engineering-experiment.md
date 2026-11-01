# Flaskr 完整工程补全与验证记录

本次对象为 [Pallets 官方 Flaskr](https://github.com/pallets/flask/tree/2c1b30d0503cfb064f1cb252e6614a06915a362a/examples/tutorial)，固定提交 `2c1b30d0503cfb064f1cb252e6614a06915a362a`。
它是完整的小型博客应用，包含应用工厂、注册/登录/退出、SQLite、文章增删改、HTML/Jinja、静态 CSS 和原有 pytest 测试。
原始副本未人为注入故障。需求来自 [官方后续开发说明](https://flask.palletsprojects.com/en/stable/tutorial/next/)：文章详情页、标题搜索、每页五篇分页。
路由、字面搜索、排序、页码异常码等明确验收选择列于 `experiments/flaskr/requirements.json`，并不冒称这些细节全是官方原文。

## 修改与模型参与

GPT-5.6 Sol 在当前 Codex 会话的独立角色实际生成测试、修改完整工程并审查。没有直接调用未配置的模型 API 或 AgentArts。

1. 测试 Agent 根据公开需求、完整上游源码设计临时 SQLite fixture、HTTP 测试、HTML 解析与覆盖映射，最终保留 25 个功能用例。
2. 补全 Agent 根据基线失败及需求修改 `flaskr/blog.py`、`templates/blog/index.html`、新增 `templates/blog/detail.html` 和 `static/style.css` 四处。
3. 首版通过原有、生成及独立用例后，额外探针和独立评审发现非 ASCII 字面搜索不一致与超长页码返回 500。测试 Agent 追加两条断言，补全 Agent修复，独立评审绑定最终 13 个运行资源的 SHA-256 后通过。
4. 最终执行器还发现生成 fixture 固定导入 candidate，使原工程一组测试测到新代码。该运行 `29d8460d533744b9bd386eaadac6ad38` 被严格拒绝。测试 Agent 只修导入配置，25 个功能断言文件保持不变；执行器增加受信 pytest 启动器及逐模块来源核验。
5. 原有测试与独立验收源码自修改前保持不变；角色输出、完整代码、输入和产物哈希通过 manifest 绑定。多轮原始结果保留，不把重新读取输出说成新推理。

## 最终实测

运行 `b69513645ddf490f8392d33823262585`，模型输出评测阶段耗时 84.704 秒；此数不包括模型生成和人工编排时间。

| 验证 | 原工程 | 补全后 |
| --- | --- | --- |
| 原有应用测试 | 24/24 | 24/24 |
| GPT-5.6 生成的功能用例 | 1/25 | 25/25 |
| 独立 HTTP/HTML/SQLite 验收 | 0/20 | 20/20 |
| 六组 pytest 实际模块来源 | 各自绑定 baseline | 各自绑定 candidate |
| wheel 构建 | 不作为补全评分 | 1/1 |
| 构建产物的真实 HTTP 检查 | 不作为补全评分 | 40/40 |

候选的三组 pytest 总计 69 个。真实 HTTP 检查从 wheel 产物启动独立应用，验证注册、登录、文章创建/修改/删除、读者权限、详情、搜索、分页、输入转义、超长页码、重启后的账号和文章持久性，结束后清理验证进程。
另一次候选合并测试收集 Python coverage：应用四模块 206 条语句、50 个分支，语句和分支均覆盖 100%。这是声明需求及用例范围的执行证据，不是对所有输入的正确性证明。
TestFix 框架自身最终 107 个测试通过；这些不是上表的业务用例数。框架独立终审快照 SHA-256 为 `bc130418f774dd7c5840a221a3215e548390fb6fc3107b9f70a78b165df63308`，18 个独立聚焦检查通过，确认发现均已处理。
最终应用候选 SHA-256 为 `bdd2f5959331b6ec36c9d702f68fb4daea813a3913cc7f67a36060862a36b9a2`。

## 查看与复现

- 控制台：http://127.0.0.1:8765/ （最新完整工程记录）
- 补全后：http://127.0.0.1:8766/
- 原始对照：http://127.0.0.1:8767/
- 工程副本：`experiments/flaskr/candidate/`
- 报告与完整日志：`artifacts/b69513645ddf490f8392d33823262585/`
- 可交付源码及测试压缩包：`artifacts/flaskr-completed-project.zip`

```powershell
.venv/Scripts/python.exe -m testfix.cli evaluate-project experiments/flaskr/manifest.json
.\scripts\start-flaskr.ps1
```

一次重跑只验证保存的模型产物，生成新补丁仍需新的模型角色输出。完整工程当前支持这个已检查的 Flaskr 案例，WCTP 的真实设备场景及 AgentArts 云部署没有计入本次测试。
