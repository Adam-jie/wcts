// Pure presentation helpers. No model or untrusted report content is executed.
export const TERMINAL = new Set([
  "completed",
  "accepted",
  "passed",
  "failed",
  "rejected",
  "interrupted",
  "cancelled",
  "budget_exhausted",
  "error",
]);
export const STATUS_LABELS = {
  completed: "已完成",
  accepted: "验收通过",
  passed: "通过",
  successful: "通过",
  succeeded: "成功",
  handed_off: "已交接",
  running: "执行中",
  queued: "排队中",
  pending: "等待",
  waiting: "等待依赖",
  blocked: "受阻",
  failed: "失败",
  rejected: "未通过",
  interrupted: "已中断",
  incomplete: "未完成",
  cancelled: "已取消",
  budget_exhausted: "预算耗尽",
  observed: "已观测",
  not_collected: "尚未采集",
  skipped: "已跳过",
  not_run: "未执行",
  unknown: "未记录",
};
export function statusLabel(value) {
  return STATUS_LABELS[String(value)] || String(value || "未记录");
}
export function statusClass(value) {
  if (
    [
      "completed",
      "accepted",
      "passed",
      "successful",
      "succeeded",
      "success",
      "handed_off",
    ].includes(value)
  )
    return "success";
  if (["running", "queued"].includes(value)) return "running";
  if (
    ["failed", "rejected", "interrupted", "incomplete", "budget_exhausted", "error"].includes(
      value,
    )
  )
    return "failed";
  return "pending";
}
export const ROLE_PRESENTATION = {
  "plan-workflow": { label: "总控规划", person: "项目总控", duty: "把需求拆成任务，安排依赖、人员和预算。", action: "拆解需求与安排分工" },
  "explore-repository": { label: "了解工程", person: "源码调查员", duty: "阅读和搜索当前源码，向后续员工交接实际发现。", action: "阅读源码与查找线索" },
  "ground-code": { label: "定位代码", person: "代码定位员", duty: "把需求对应到具体文件、函数和调用关系。", action: "定位需要改动的代码" },
  "analyze-requirements": { label: "确认需求", person: "需求分析员", duty: "区分已有行为和功能缺口，明确需要保留的兼容行为。", action: "整理需求与验收边界" },
  "generate-tests": { label: "设计测试", person: "测试设计员", duty: "根据公开需求编写测试，先检查原版能否满足要求。", action: "编写可执行的自动化测试" },
  "diagnose-results": { label: "分析失败", person: "问题诊断员", duty: "根据真实失败日志确定问题原因，给修复提供证据。", action: "分析测试失败的原因" },
  "repair-code": { label: "补全修复", person: "编码修复员", duty: "结合需求、调查交接和失败证据，在允许范围内修改代码。", action: "根据证据修改代码" },
  "review-patch": { label: "审查修改", person: "独立评审员", duty: "检查补丁是否符合需求、修改范围和行为兼容约束。", action: "检查补丁与修改边界" },
  "verify-delivery": { label: "独立验收", person: "验收交付员", duty: "执行独立验收、回归、构建和安装后的真实运行检查。", action: "执行验收与真实运行检查" },
};
export function nodePresentation(node = {}) {
  const role = node.role || node.detail?.skill_id || node.id;
  const info = ROLE_PRESENTATION[role];
  if (!info) return { label: node.label || node.id || "协作节点", person: "协作角色", duty: "查看此角色已记录的任务与执行结果。", action: "执行已分配任务" };
  if (node.kind !== "employee") return info;
  const task = String(node.task_id || "").toLowerCase();
  const label = role === "explore-repository"
    ? /export|print/.test(task) ? "导出与样式调查员" : /csv|tsv|table/.test(task) ? "表格行为调查员" : info.person
    : info.person;
  return { ...info, label };
}
export function compactNodeLabel(node) {
  return nodePresentation(node).label;
}
export function isTerminal(value) {
  return TERMINAL.has(value);
}
export function parseRunId(value) {
  return typeof value === "string" && /^[a-f0-9]{32}$/i.test(value)
    ? value.toLowerCase()
    : null;
}
export function acceptanceState(run = {}, verdict = {}, postchecks = {}) {
  if (
    postchecks?.status === "failed" ||
    asArray(postchecks?.checks).some((check) => check.status === "failed")
  )
    return "failed";
  if (!isTerminal(run.status)) return "neutral";
  const allSummary =
    typeof run.summary?.total === "number" &&
    run.summary.total > 0 &&
    run.summary.accepted === run.summary.total;
  const passed =
    (verdict.accepted === true || verdict.successful === true || allSummary) &&
    !Object.values(verdict).some((value) => value === false) &&
    !["failed", "rejected", "interrupted", "budget_exhausted"].includes(
      run.status,
    );
  return passed ? "success" : "failed";
}
export function asArray(value) {
  return Array.isArray(value) ? value : [];
}
export function display(value) {
  if (value === undefined || value === null || value === "") return "—";
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}
export function number(value, digits = 0) {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString("zh-CN", { maximumFractionDigits: digits })
    : "—";
}
export function date(value) {
  if (!value) return "未记录时间";
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf())
    ? String(value)
    : parsed.toLocaleString("zh-CN", {
        hour12: false,
        timeZone: "Asia/Shanghai",
      });
}
export function safeHref(value, origin) {
  if (typeof value !== "string" || !value) return null;
  try {
    const resolved = new URL(value, origin);
    return resolved.origin === origin &&
      ["http:", "https:"].includes(resolved.protocol)
      ? resolved.href
      : null;
  } catch {
    return null;
  }
}
export function externalHref(value) {
  try {
    const url = new URL(value);
    return ["https:", "http:"].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}
export function normalizeGraph(graph = {}) {
  const seen = new Set();
  const nodes = asArray(graph.nodes)
    .filter(
      (node) =>
        node &&
        typeof node.id === "string" &&
        !seen.has(node.id) &&
        seen.add(node.id),
    )
    .map((node) => ({
      ...node,
      label: String(node.label || node.id),
      depends_on: asArray(node.depends_on),
    }));
  const ids = new Set(nodes.map((node) => node.id));
  const edges = asArray(graph.edges).filter(
    (edge) => edge && ids.has(edge.source) && ids.has(edge.target),
  );
  return { nodes, edges };
}
export function graphPositions(graph) {
  const { nodes, edges } = normalizeGraph(graph),
    positions = new Map();
  const controller = nodes.filter((n) => n.kind === "controller");
  const capabilities = nodes.filter((n) => n.kind === "capability");
  const employees = nodes.filter(
    (n) => !["controller", "capability"].includes(n.kind),
  );
  controller.forEach((node, index) =>
    positions.set(node.id, {
      x: (index - (controller.length - 1) / 2) * 4.2,
      y: 4.2,
      z: -1.0,
    }),
  );
  capabilities.forEach((node, index) => {
    const row = Math.floor(index / 3),
      col = index % 3;
    positions.set(node.id, {
      x: (col - 1) * 4.25,
      y: 2.0 - row * 1.85,
      z: col === 1 ? -0.8 : 0.35,
    });
  });
  // Employees follow their actual dependency depth, never a fictitious timeline.
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const depths = new Map();
  function depth(id, visiting = new Set()) {
    if (depths.has(id)) return depths.get(id);
    if (visiting.has(id)) return 0;
    const next = new Set(visiting);
    next.add(id);
    const deps = asArray(byId.get(id)?.depends_on).filter((dep) =>
      employees.some((n) => n.id === dep),
    );
    const value = deps.length
      ? Math.max(...deps.map((dep) => depth(dep, next))) + 1
      : 0;
    depths.set(id, value);
    return value;
  }
  const groups = new Map();
  employees.forEach((node) => {
    const level = depth(node.id);
    if (!groups.has(level)) groups.set(level, []);
    groups.get(level).push(node);
  });
  const base = capabilities.length ? -4.25 : 1.5;
  for (const [level, group] of groups)
    group.forEach((node, index) =>
      positions.set(node.id, {
        x: (index - (group.length - 1) / 2) * 4.5,
        y: base - level * 1.9,
        z: 1.25,
      }),
    );
  return { nodes, edges, positions };
}
export function makeRunRequest(values) {
  const requirement = String(values.requirement || "").trim();
  if (!values.project) throw new Error("请先选择可运行工程。");
  if (!values.provider || values.provider === "replay")
    throw new Error("新实验需要已配置的真实模型服务。");
  if (!requirement) throw new Error("请填写需求及验收条件。");
  if (requirement.length > 20000) throw new Error("需求不能超过 20,000 字符。");
  const model = String(values.model || "").trim();
  if (!model) throw new Error("请填写要请求的模型。");
  const limits = {
    slots: [1, 8],
    rounds: [1, 5],
    minutes: [1, 120],
    tokens: [5000, 2000000],
  };
  for (const [key, [min, max]] of Object.entries(limits)) {
    if (
      !Number.isInteger(values[key]) ||
      values[key] < min ||
      values[key] > max
    )
      throw new Error(`预算字段 ${key} 必须是 ${min} 至 ${max} 之间的整数。`);
  }
  return {
    project_id: values.project,
    provider: values.provider,
    model,
    max_rounds: values.rounds,
    budget: {
      wall_seconds: values.minutes * 60,
      token_limit: values.tokens,
      model_slots: values.slots,
      cpu_slots: 2,
    },
    strategy: "balanced",
    collaboration_request: requirement,
  };
}
export function suiteFraction(suite) {
  return suite &&
    typeof suite.passed === "number" &&
    typeof suite.total === "number"
    ? `${number(suite.passed)} / ${number(suite.total)}`
    : "未记录";
}
export function isOffline(run = {}) {
  return (
    run.provider === "replay" || /replay|offline/.test(String(run.kind || ""))
  );
}
export function datasetState(dataset = {}) {
  const value = String(dataset.status || "unknown");
  if (
    ["ready", "runnable", "registered", "adapted", "available"].includes(value)
  )
    return { label: "已适配 · 可运行", css: "success" };
  if (
    [
      "downloaded",
      "collected",
      "downloaded_unadapted",
      "needs_adapter",
      "not_adapted",
    ].includes(value)
  )
    return { label: "已收集 · 待适配", css: "pending" };
  if (
    [
      "environment_required",
      "environment_missing",
      "environment_unavailable",
      "missing_environment",
      "blocked_environment",
      "docker_required",
      "unavailable",
      "blocked",
    ].includes(value)
  )
    return { label: "执行环境不足", css: "failed" };
  if (value === "not_collected") return { label: "尚未采集", css: "pending" };
  if (value === "integrity_failed")
    return { label: "完整性校验失败", css: "failed" };
  return { label: dataset.status_label || value, css: "pending" };
}
