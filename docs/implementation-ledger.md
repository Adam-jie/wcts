# 实现与验证记录

核查日期：2026-10-14（Asia/Shanghai）。当前工作目录最初为空 Git 仓库，没有旧提交或其他用户修改。

## 目标与验收条件

实现本地可演示的多 Agent 软件功能补全流程，依据此前 WCTP / DynaCap 文档与开源编排机制设计，使用 GitHub 开源测试数据，交付候选补丁与可复现验证报告。首版接受 Python 单模块任务；输入公开需求、源文件及评测端保管的独立验收。模型端不获得验收源码或参考答案。

完成条件：本地控制台/API/CLI可用；能复现缺失行为并生成补丁；固定独立验收及每条原有回归实际执行；失败/超时/政策拒绝有证据；真实模型与回放不可混报；AgentArts采用实际官方契约而非猜测接口。

## 已确认材料

- 用户提供 `wctp.zip`，只读扫描与本地旧代码对照，未执行压缩包代码，未修改原项目。源码和聊天定位见 `prior-context.md`。
- WCTP 的 LLM 模块尚为空，DynaCap 模型地址与名称也为空，没有发现可沿用的完整真实模型配置。已请求用户提供配置文件路径，尚待补齐。
- GitHub HumanEval 固定 commit `6d43fb980f9fee3c892a914eda09951f772ad10d`，164题，下载 SHA-256 `b796127e635a67f93fb35c04f4cb03cf06f38c8072ee7cee8833d7bee06979ef`，MIT许可随数据保存。
- 本机无 Docker daemon，未执行容器测试。没有创建云资源，AgentArts和真实模型调用没有冒充已验证。

## 实现范围

五种独立角色（需求、测试、诊断、补全、评审），确定性监督状态机，1–5轮修复预算；三类测试（生成/独立/回归）；源码和验收快照完整性检查；修改权限/签名/辅助函数 AST 锁定；兼容API/Ollama/AgentArts transport；WCTP callback适配器；临时解释器和Docker runner；中文前端、历史和JSON/Markdown报告；CLI、锁定依赖和Windows启动/停止脚本。

缺失行为注入在 HumanEval/0–4 的公开规格上独立设计，保留简单可用行为；不是声称上游原题有真实Bug。订单样例包含原有 subtotal 和待完善 discounted_total，验证原有辅助函数不变与目标行为回归。

## 验证证据

| 验证 | 命令/观察 | 实际结果 |
| --- | --- | --- |
| 工程测试 | `.venv/Scripts/python.exe -m pytest -q --basetemp artifacts/pytest-verified-final` | 49 passed，9.96秒；1条Starlette httpx TestClient弃用提示，无运行失败 |
| 完整回放 | `python -m testfix.cli run --provider replay --tasks HumanEval/0 HumanEval/1 HumanEval/2 HumanEval/3 HumanEval/4 Repo/order-discount` | run `30031a70117c4ecd92f0d4ef48605cb7`；6/6独立验收、0回归破坏、11.041秒 |
| 浏览器端启动 | 全选6任务，启动，等待自动轮询，查看补丁/事件/分析/历史/报告链接 | run `2e41723c4d534369abe6914d6cf6625b`；6/6、0回归破坏、10.9秒；console无错误；390px布局检查通过 |
| 静态检查 | `node --check web/app.js`、`compileall -q testfix agentarts`、`git diff --check` | 通过 |
| Windows资源控制 | 独立子进程建立并读回Job Object限值、1MiB分配、worker退出后后代回收 | 通过；512MiB进程及Job内存、5秒用户CPU、KILL_ON_JOB_CLOSE；未做真实OOM |
| POSIX资源控制 | 模拟resource模块严格上限/设置失败 | 通过模拟；当前Windows无法实际验证POSIX分支 |
| 模型/AgentArts契约 | mock HTTP检查URL/认证头/session/body/schema/失败不回放 | 通过；这是接口测试，没有真实远程服务调用 |
| WCTP callback | Normal失败、Conditional控制、未知分类、循环进度 | 通过；完整设备执行环境未运行 |
| 重启与健康 | stop/start managed background，GET health/tasks/static | 127.0.0.1:8765健康、6任务、JS MIME正确，历史可读取 |

## 审查发现与修复

协作审查继承了聊天上下文，不把它称为完全独立审查。该审查给出可复现测试并确认修复以下实质问题：

1. 候选通过 `math.isclose = ...` 污染受信比较器：禁止属性赋值/删除，并保留拒绝与错误答案测试。
2. `from math import floor as helper` 覆盖锁定辅助函数绑定：禁止导入影射模块函数、禁止重复定义。
3. 只比较回归总通过数会漏掉通过/失败互换：执行器返回passed_indices，按原通过用例逐条检查。
4. 伪造或矛盾worker counts：校验计数类型/范围/通过index/失败index一致性。
5. Windows把JS发为text/plain导致nosniff阻断：明确注册JS/CSS MIME，完成真实浏览器验证。
6. 本地进程原先只有时间限制：加入Windows Job内存/CPU/后代清理，POSIX使用rlimit，建立失败即停止执行。
7. 新审查者复现空验收文件导致独立 `0/0` 被接受：任务导入和执行器拒绝空/无check/无断言或抛失败的裁判，监督门禁另要求独立套件total>0；新增失败复现测试。
8. 新审查者复现修改导入或新增模块函数遮蔽builtin间接改变原helper：锁定原有import绑定，禁止新增模块绑定覆盖原有builtin依赖；新增三种导入绑定与三种builtin遮蔽测试。

最终候选另交无聊天历史的新审查者只读审查。首轮快照 `5fe2463ecfa896049890c67173188edcaa155dc288fd0e47036336442d17f784` 发现以上最后两项；修后快照 `8b174aa2ef2cd9b3a9381594842d932ca04ce1643926d8d5378a5537b98786a0` 保存在 `artifacts/review-snapshot-final.json`，最终处置见 `docs/review.md`。

## 待补齐的外部条件

真实模型配置文件路径或服务地址/模型名称/本地凭据；华为AgentArts真实运行时和入站凭据；Docker环境。三者不影响当前回放演示，但分别限制真实模型实验、平台采用证明及容器运行验证。跨文件工程、真实软件缺陷数据集与完整WCTP设备环境作为下一阶段，未包装为本版已完成能力。

## 后续更新：用户指定GPT-5.6

用户随后要求现在使用ChatGPT 5.6，取GitHub数据补全并执行模型生成测试。已用Codex可调度的 `gpt-5.6-sol` 子Agent完成三个新任务（HumanEval/10、/13、/20）的真实测试/补丁/评审生成，绕过本地尚缺API配置的外部条件，采用保存会话输出导入本地执行器的明确模式。

36条模型开发测试、三个官方验收套件、六条回归和2432个额外输入全通过；工程全套86测试通过。网页已显示来源模式、测试步骤、覆盖与功能判定。来源/指纹/评审/性质套件完整性有独立只读审查，最终快照 `a2c56f73477426b5958c32027569e6ee83e25918bff34dda46cf1975c6f408ee`。

这完成了本轮真实模型生成与本地测试实验；本地自主API调用、华为云和Docker依然尚未实际接通。详细方法、结果和审查见 `gpt56-experiment.md` 与 `review-gpt56.md`。

## 2026-10-15：v0.2 自主完整工程

上文保留各历史阶段事实。本次已接通本地真实 Codex CLI 调用，复用用户已选择的 ChatGPT OAuth 登录态，按 `gpt-5.6-sol` 请求。没有读取、复制或写出认证文件；CLI 没有回传实际模型 ID 时记录为 null，不能把请求参数当作服务端型号证明。

已实现真实 OR-Tools CP-SAT 求解及九项能力的自动执行：规划、探索、定位、需求、测试生成、诊断、补丁、独立模型评审、确定性验收。每项 Skill 的输入、写入范围、前置依赖、超时、调用额度和最终验证门禁均由宿主执行。默认两轮计划包含公开测试工具限额，共4410秒，受4500秒总预算约束；Token预测和实际provider报告分别记录。CLI输出额度为软约束，超预算后停止后续调用。

完整工程采用官方 Pallets/flask 的教程工程（固定 commit `2c1b30d0503cfb064f1cb252e6614a06915a362a`）。从原始快照生成新测试和补丁，只允许四个业务文件变更，保留原有24项回归及20项独立验收；交付另须通过构建产物的40项真实HTTP、SQLite持久化及重启检查。旧候选和历史会话输出不参与新自主运行的答案生成。

已修复并保留失败证据：HTTP流持续活动绕过inactivity timeout（改独立进程总时限）、交付ZIP发布后被替换（下载校验SHA-256）、重试复用数据库（各轮独立目录）、外部CLI终态未刷新、Windows服务只停venv launcher、模型提出不存在的探索文件、生成文件路径不合约。测试及补丁采用整组无写入预检；拒绝原因反馈模型，纠正消耗同一计划和预算额度，不放宽保护规则。

框架测试、独立审查和本次真实运行最终成绩见 `autonomous-engineering.md`、`artifacts/probes/autonomous-independent-review.json` 及最终交付验证收据。注册适配器目前为已检查的Flaskr；不是任意语言或恶意仓库的操作系统沙箱。AgentArts完整调用适配器和契约测试已接上，但尚无真实云端部署凭据/实例，不以mock结果作为赛题平台采用证明。Docker亦未在本机验证。

最终框架验证：`.venv/Scripts/python.exe -m pytest -q --tb=short --junitxml artifacts/release-v0.2.0/framework-tests-final.xml --basetemp artifacts/full-autonomous-final-green-20261015`，439 passed、3 skipped，170.85秒。三项跳过均为Windows无创建文件符号链接权限；真实Windows junction检查通过。存在一条既有Starlette/httpx弃用提示。源码语法、JS语法、锁定依赖同步及核心wheel构建均已验证；未用修改业务断言解决失败。

新自主运行 `1a500f9521c141168613504e1c57d352`：441.452秒，九能力全部执行，一轮补丁、8次真实CLI请求；生成测试0/10→10/10，原有回归24/24，独立验收0/20→20/20，构建1/1，真实HTTP40/40，所有门禁通过。provider报告198161 tokens，不等同独立核验的账单。候选与评审绑定哈希 `3e21525c240266d1f52184f1c14a4c9ca3bdc51665087b4ed940bd762443e492`。预览部署另用示例数据，未改动本次验收数据库或冻结测试。

审查后还收口了ZIP阶段墙钟预算：打包到unpublished文件，完成哈希与完整性检查后再次核验剩余预算，合法后才发布completed归档；超时归零accepted并取消download。真实用例低于总预算；另以合成时钟验证4501秒时不能在4500秒预算下交付。Windows报告发布改为唯一临时文件和至多0.62秒的PermissionError退避，永久失败仍抛出并保留旧目标；JSON/MD各自原子，不宣称跨文件事务。

