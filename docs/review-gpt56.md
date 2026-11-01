# GPT-5.6 追加实验最终审查

日期：2026-10-14。独立工程审查者为另一个 `gpt-5.6-sol` 子Agent，未继承构建聊天；只读审查，没有修改候选代码、读取凭据或请求模型API。

最终快照：**`a2c56f73477426b5958c32027569e6ee83e25918bff34dda46cf1975c6f408ee`**，`artifacts/review-snapshot-gpt56-oracle-final.json` 记录44个源码、测试、前端、脚本、依赖与实验输入输出/性质文件的哈希。审查前后44/44一致。

## 确认并关闭的契约问题

| 级别 | 问题 | 最终处置 |
| --- | --- | --- |
| P2 | 保存的评审可没有候选代码hash，审批失去版本绑定 | SessionProvider强制64hex并匹配developer.code；监督门禁再次要求captured评审绑定候选 |
| P2 | public_tasks可为空或缺部分字段，保存输出可能套用于漂移的任务 | task ID完整对应，规格/初始代码/入口/指纹四字段强制齐全并逐字段一致 |
| P2 | 读取保存输出时事件仍写实时“生成”，与来源信息冲突 | 捕获模式明确“读取已保存”，包括需求、测试、诊断、补丁和评审；API实际模式保留实际生成描述 |
| P2 | prompt_file只声明字符串，没有路径/存在/hash验证 | 相对路径与存在检查、SHA-256校验、JSON解析与递归oracle字段检查；保存安全相对名及hash |
| P2 | 额外性质套件未绑定bundle hash，重评可能静默用不同测试 | 每task强制独立套件path/hash，构造时校验，CLI从同一读取字节再次比hash后冻结源码 |
| P2 | oracle键保护漏了HumanEval原生字段test | 增加test/hidden_test/hidden_tests，6项负向测试覆盖合法hash但夹带oracle及运行上下文嵌套夹带 |

生成输出与bundle角色内容的逐字段对应也已加固：不能引用一个未变的输出文件hash，却在bundle中替换为不同候选。tester的steps/coverage/assumptions来自原始模型输出，合并规则确定且验证一致。

## 最终证据与局限

构建方全套 **86 passed，17.23秒**；审查方独立focused **37 passed，5.95秒**。既有TestClient弃用提示不影响执行。最终未发现剩余P1/P2，不等于证明所有实现没有缺陷。

实际最终run `f09ae3e5bce848c4b35d70368c8ffdf8`：3/3任务、36/36开发测试、3个官方套件、6/6原有回归、255/1088/1089额外输入、三个runnable和评审代码hash核对一致。最终oracle门禁重新对三份实际输入完成preflight；它们原本没有oracle键，未修改模型原始输出、候选或验收内容。

来源是本次Codex指定GPT-5.6子Agent生成与可信本地保存记录；文件hash固定输入输出及裁判一致性，不能作为远程云模型身份的密码学证明。本轮没有接通本地模型API、AgentArts或Docker，不把确定性重评称为新模型采样。官方benchmark完整指标、跨文件工程与无界输入仍不在本轮证据范围。
