# 依赖约束与测试反馈：实验入口

已有完整工程工作流支持需求拆分、任务依赖、CP-SAT 分配、WCTP 执行和根据测试反馈重新派工。`study` 为已登记工程批量运行该真实工作流，固定输入及预算，保存每次结果；不复用其他运行的模型输出。

先导入并登记可执行的 Python 工程，准备其公开需求、原有回归和独立验收。当前登记适配器仍限 Flaskr、TaskBoard 和已适配的 Python CLI 包。任意 GitHub 仓库、FEA-Bench 任务和硬件工程尚不能直接通过本入口执行；下载数据集不代表完成环境和评测适配。团队之前要求清空的演示项目不会自动恢复。

## 准备实验

在本地创建 UTF-8 JSON 文件，填入实际登记工程 ID 和对应需求，例子中 ID 只是格式示意：

```json
{
  "name": "依赖调度并发试验",
  "provider": "codex_cli",
  "model": "gpt-5.6-luna",
  "wall_seconds": 7200,
  "token_limit": 800000,
  "cpu_slots": 2,
  "max_rounds": 2,
  "repetitions": 2,
  "cases": [{"id": "case-01", "project_id": "Project/实际登记ID", "request": "填写此工程的功能需求及预期"}],
  "variants": [{"name": "单模型槽", "model_slots": 1}, {"name": "双模型槽", "model_slots": 2}]
}
```

先生成研究计划，不调用模型：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m testfix.cli study .\study.json --prepare-only
```

核对打印的 study ID 和 `artifacts/studies/<study-id>/study.json`。其中固定源码与验收文件哈希、需求、每项运行 ID、模型、预算、槽位、执行顺序；偶数重复反转配置顺序。哈希记录输入内容，不表示依赖环境已经被完整封装。

准备正确后实际启动：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m testfix.cli study --execute-prepared 这里替换为实际studyID
```

这会执行刚才冻结的计划并发起新的模型调用；每项调用现有 `execute_project_run`，产生独立的 `artifacts/<run-id>/report.json`、补丁和工具记录。启动前会核对冻结源码和验收哈希。`study.json` 逐项记录状态、是否通过、回归结果、耗时、模型调用和服务报告的预算使用，并按配置汇总计划数、通过数、失败数和调用数。失败记录保留，队列执行完不表示全部任务成功。若进程被中断，已有结果保留，当前版本不支持从中断点自动续跑。

研究运行在公开测试和评审通过后最多执行一次独立验收；验收失败会结束当前任务，不把验收结果交回 Agent 继续修改。原有非研究运行保持原来的交互式修复行为。某任务若未通过公开测试或评审，独立验收可能不会执行，不计为成功。

## 结果解释

本入口当前只比较**同一多 Agent 工作流的不同模型并发槽位**。单槽不是单 Agent，也不是关闭 ILP；双槽与单槽的并发资源不同。因此不能据此宣称 ILP 的因果收益或公平的总资源对比。每个模型调用不一定有服务端可核实的实际型号或 Token 数，未报告时保留空值。

论文正式实验还需分别实现和测试单 Agent、无 ILP 与无反馈等对照，接入 FEA-Bench 官方环境与独立评测，对同一任务集统计通过率、回归、成本和失败原因。当前未完成这些对照，不能宣称已达到论文发表标准。

当前工作流仍把公开源码快照加载到模型上下文，并设有 180,000 字节上限；大型仓库需要先完成索引和按需读取改造。不要用放宽上限的方式把超过模型上下文的完整仓库直接传入。
