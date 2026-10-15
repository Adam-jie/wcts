# TestFix 整数约束调度模型

`testfix.scheduling.solve_plan` 使用真实的 **OR-Tools CP-SAT** 求解器，选择策略并生成整数毫秒排程。模型使用布尔选择变量、整数起止时间、整数线性预算约束，以及 optional interval、Cumulative、NoOverlap 调度约束。没有把选择变量放松为连续数，也没有使用启发式结果冒充整数最优解。CP-SAT 是整数约束求解器；本实现不是 LP relaxation，也不把它称为单纯形 LP 求解器。

求解不需要 API 密钥，不调用 LLM，不修改候选代码，不执行测试或交付验证。策略数值全部由调用方提供；库中尚未测量的估计值仍保持 `null`。`OPTIMAL` 仅表示给定数值、资源和约束下的目标最优，**不代表真实端到端运行时间最优**。

## API 与输入

```python
from testfix.scheduling import solve_plan

result = solve_plan(options, budget, profile="flaskr_project", catalog=None)
```

`catalog` 可传已加载的 `SkillCatalog`，默认加载本地版本化目录。默认项目 profile 的九个 stage 固定为：

1. plan-workflow
2. explore-repository
3. ground-code
4. analyze-requirements
5. generate-tests
6. diagnose-results
7. repair-code
8. review-patch
9. verify-delivery

`pure_python` 使用其目录声明的六个核心 stage。每个必需 Skill 必须有至少一个外部策略选项，同一 Skill 可提供多个选项。每个选项必须恰好包含这些字段：

```json
{
  "skill_id": "repair-code",
  "id": "bounded-edit",
  "duration_ms": 120000,
  "token_cap": 4000,
  "resources": {
    "model_slots": 1,
    "cpu_slots": 0,
    "candidate_write": 1
  },
  "evidence": {
    "basis": "调用方声明的本轮时长上界和token上限；尚无运行时测量"
  }
}
```

`id` 在同一个 Skill 内唯一。`duration_ms` 是大于零的整数，表示调用方声明的时长上界；`token_cap` 是非负整数，表示该次 Skill 调用的 token 上限。`evidence.basis` 是非空文本，用于保留数值来源，求解器不会验证其真实性，也不会为其生成置信度。

所有资源字段必须齐全且为非负整数。LLM Skill 至少占用一个 `model_slots`；确定性 Skill 至少占用一个 `cpu_slots`。`candidate_write` 只能为 0 或 1，并必须与合同中 `candidate_sources` 写权限一致：当前只有 `repair-code` 为 1。策略不能通过声明零资源绕过这些要求。

预算的三个必需字段及三个可选字段如下：

```json
{
  "max_makespan_ms": 900000,
  "max_tokens": 24000,
  "resources": {"model_slots": 1, "cpu_slots": 1, "candidate_write": 1},
  "objective_weights": {"duration": 1, "tokens": 1},
  "solver_time_limit_seconds": 10,
  "random_seed": 0
}
```

`max_makespan_ms` 为正整数，`max_tokens` 和资源容量为非负整数。资源容量为零是合法配置，可能导致无可行解。目标权重为非负整数且至少一项大于零，默认均为 1；其单位分别对应毫秒和 token，调用方应按用途选择比例。求解时间限制必须为有限正数，默认 10 秒；它限制规划求解时间，不是业务执行超时。`random_seed` 默认为 0，使用一个搜索 worker。输入布尔值不能冒充整数；可能溢出求解器整数表达式的系数和总量会被拒绝。

不接受 `enabled`、可删除 gate、可跳过 Skill 等开关。选项顺序不会改变目录规定的执行顺序。

## 变量、约束与目标

设必需 Skill 集合为 \(S\)，Skill \(i\) 的策略集合为 \(O_i\)。调用方声明时长 \(d_{ij}\)、token 上限 \(t_{ij}\) 和资源需求 \(a_{ijr}\)，容量为 \(K_r\)。

- \(x_{ij}\in\{0,1\}\)：是否选择策略 \(j\)。
- \(s_i,e_i,C\in\mathbb Z_{\ge0}\)：起始、结束和 makespan，单位为毫秒。
- 每 stage 恰选一个策略：\(\sum_{j\in O_i}x_{ij}=1\)。
- 时长绑定：\(e_i=s_i+\sum_j d_{ij}x_{ij}\)。每个选项另建由 \(x_{ij}\) 控制的 optional interval。
- 合同依赖：每条 \(p\rightarrow i\) 都有 \(s_i\ge e_p\)。
- 当前 profile 是固定顺序的单轮串行执行，首 stage 从 0 开始，相邻 stage 满足 \(s_{i+1}=e_i\)，不安排人为等待。
- 硬 token 预算：\(\sum_{i,j}t_{ij}x_{ij}\le T\)。
- 硬总时长预算：\(C=\max_i e_i\)，\(C\le D\)。
- 每种资源设置 `Cumulative`，任一时刻全部活动 interval 的需求之和不得超过 \(K_r\)。候选写入 interval 还受 `NoOverlap` 约束。

最小化：

\[
\sum_{i\in S}\sum_{j\in O_i}
\left(w_d d_{ij}+w_t t_{ij}\right)x_{ij}.
\]

目标没有学习得到的成功率、虚构成本或成本置信度。所有 stage 必选，因此低预算不会删除审核或验收。当前严格串行顺序下，跨 stage 的 NoOverlap 是冗余的安全约束；Cumulative 仍会拒绝单个策略超过资源容量的选择。当前版本不做跨任务、跨轮次并行调度，也不通过并行化宣称提速。无等待串行排程使 makespan 等于所选时长上界之和。

在输入边界和有解的输出边界都调用 `catalog.validate_plan`，检查核心顺序、必需 Skill 和依赖。输出保留全部 `required_gates`，其 `gate_status` 始终是 `execution_evidence_required`。这些检查不构成 gate 已通过的证据。

## 状态与结果

| 状态或异常 | 含义 | 可用排程 |
| --- | --- | --- |
| `OPTIMAL` | 已证明对声明目标最优 | `stages` 和 `totals` |
| `FEASIBLE` | 已找到满足约束的解，尚未证明最优 | `stages` 和 `totals` |
| `INFEASIBLE` | 求解器已证明预算或资源约束下无解 | 空 `stages`，`totals=null` |
| `UNKNOWN` | 在限制内未得到可报告的解或不可行性证明 | 空 `stages`，`totals=null` |
| `InvalidConfiguration` | 格式、数值、资源声明或目录约束非法；`code="invalid_configuration"` | 抛出异常，不伪装为不可行 |
| `SolverModelError` | 模型验证失败或求解器返回 `MODEL_INVALID` | 抛出异常，没有替代解 |

有解时，每个 stage 包含 `id`、`strategy`、整数 `start_ms`/`end_ms`、`duration_ms`、`token_cap`、资源和来源文本。`totals` 汇总时长、token、makespan 和精确整数目标值。`solver` 记录实际求解状态、OR-Tools 版本、求解耗时、分支/冲突数、目标值、目标界和模型数量。求解器的目标和目标界使用其原生浮点统计值，精确选择成本见 `totals.objective_value`。`assumptions` 保留声明成本与串行范围，`budget` 回显标准化配置。

`solver.wall_time_seconds` 是规划求解器实际耗时，不能用作执行九能力的实测时长。所有结果均标记 `execution_verified=false`，未执行时不会生成“交付成功”结论。

## 可复现验证

```powershell
.venv/Scripts/python.exe -m pytest tests/test_scheduling.py -q
```

测试使用真实 CP-SAT，包括完整九 stage、逐项不可删除、预算不可行、资源超容量的策略排除、合法策略切换、512 个组合穷举比对整数最优目标，以及真实搜索提前停止产生的 `FEASIBLE` 和极短时间限制产生的 `UNKNOWN`。测试也检查输入输出两次目录验证和输入不被修改。

参考官方说明：[CP-SAT 整数变量及状态](https://developers.google.com/optimization/cp/cp_solver)、[Job shop 的 interval 与 NoOverlap 排程](https://developers.google.com/optimization/scheduling/job_shop)、[OR-Tools Python CP-SAT API 源码](https://github.com/google/or-tools/blob/stable/ortools/sat/python/cp_model.py)。
