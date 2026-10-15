// Human-readable views of host records. No elapsed-time estimates or invented progress.
import { acceptanceState, asArray, date, isTerminal, nodePresentation, ROLE_PRESENTATION, statusLabel } from "./lab-core.mjs";

const DONE = new Set(["completed", "accepted", "passed", "successful", "succeeded", "success", "handed_off"]);
const FAILED = new Set(["failed", "rejected", "error", "interrupted", "incomplete", "budget_exhausted", "cancelled"]);
const STAGES = [
  { id: "plan", label: "安排分工", ids: ["plan-workflow"] },
  { id: "understand", label: "理解工程", ids: ["explore-repository", "ground-code", "analyze-requirements"] },
  { id: "test", label: "测试找问题", ids: ["generate-tests", "diagnose-results"] },
  { id: "repair", label: "修改与评审", ids: ["repair-code", "review-patch"] },
  { id: "verify", label: "验收与交付", ids: ["verify-delivery"] },
];
const trim = (value, limit = 360) => typeof value === "string" ? value.slice(0, limit) : "";
function humanToolError(error) {
  const message = trim(error);
  if (/invalid syntax/i.test(message)) return "补丁中的 Python 代码存在语法错误，宿主拒绝应用。";
  if (/Only an informed explorer can finish without a patch/.test(message)) return "修复员工还未提交有效补丁，不能直接交接结束。";
  if (/Repair must propose bounded changed files/.test(message)) return "补丁未提供符合约束的改动文件列表，宿主拒绝应用。";
  return message || "步骤未通过或回执校验失败，请展开技术详情复核。";
}
export function postcheckFailed(postchecks) {
  return postchecks?.status === "failed" || asArray(postchecks?.checks).some(check => check.status === "failed");
}
export function presentationProjection(projection = {}) {
  if (projection.run?.kind === "autonomous_project") return projection;
  let pending = false;
  const suites = asArray(projection.metrics?.suites).map(suite => {
    const after = suite.after;
    // Older function records contain assertion counts but no process exit.
    // A recorded failed assertion, timeout, or collection error remains failed.
    if (after?.successful !== false || after.exit_code != null || after.runnable !== true || after.timeout === true || after.collection_errors || !(after.total > 0) || after.passed !== after.total) return suite;
    pending = true;
    return { ...suite, after: { ...after, successful: null, verification_pending: true } };
  });
  if (!pending) return projection;
  return { ...projection, metrics: { ...projection.metrics, suites }, legacy_evidence_pending: true, warnings: [...asArray(projection.warnings), "这是旧格式的非工程记录：部分用例计数完整，但进程退出码未记录，相关套件显示待核验。原始报告保留。"] };
}
export function capabilityRecords(projection = {}) {
  const records = new Map();
  for (const node of asArray(projection.graph?.nodes)) {
    if (ROLE_PRESENTATION[node.id] && node.kind !== "employee") records.set(node.id, node);
  }
  for (const item of asArray(projection.capabilities)) {
    if (ROLE_PRESENTATION[item.skill_id]) records.set(item.skill_id, { ...records.get(item.skill_id), ...item, id: item.skill_id });
  }
  return records;
}
export function nodeStory(node = {}, projection = {}) {
  const info = nodePresentation(node), steps = asArray(node.detail?.steps);
  const recent = steps.at(-1), errors = steps.filter(step => step.ok === false || step.receipt_verified === false);
  const names = new Map(asArray(projection.graph?.nodes).map(item => [item.id, nodePresentation(item).label]));
  const dependencies = asArray(node.depends_on).map(id => names.get(id) || id);
  const toolNames = { read_file: "读取文件", search: "搜索源码", apply_patch: "提交补丁", write_file: "写入文件", run_tests: "执行测试", finish: "提交交接", inspect: "检查工程" };
  const action = recent ? toolNames[recent.action] || recent.action || "工具操作" : null;
  let current = node.status === "running" ? `正在${info.action}。` : DONE.has(node.status) ? "本环节已记录完成。" : FAILED.has(node.status) ? "本环节未能完成，请查看失败原因。" : node.status === "observed" ? "报告记录了此角色；没有记录足够信息来判断是否完成。" : dependencies.length ? `尚未执行，前置环节：${dependencies.join("、")}。` : "尚未执行，等待总控安排。";
  let receipt = "尚无可展示的工具回执；有新记录后会自动更新。";
  if (recent) {
    const verification = recent.receipt_verified === true ? "回执已校验" : "回执尚未核验";
    receipt = `${action} · ${recent.ok === true ? "操作成功" : recent.ok === false ? "操作未通过" : "结果未记录"} · ${verification}`;
    if (recent.path) receipt += ` · ${trim(recent.path, 120)}`;
    if (recent.action === "finish" && recent.message) receipt += `\n交接：${trim(recent.message, 500)}`;
  } else if (node.detail?.finished_at) receipt = `完成于 ${date(node.detail.finished_at)}。该环节没有单独的工具操作记录。`;
  const error = errors.at(-1);
  return { ...info, current, receipt, error: error ? humanToolError(error.error) : null, errorCount: errors.length, errorRecovered: !!error && recent !== error && recent?.ok === true, dependencies };
}
export function workflowStory(projection = {}) {
  const run = projection.run || {}, records = capabilityRecords(projection);
  const nodes = asArray(projection.graph?.nodes);
  const completed = [...records.values()].filter(item => DONE.has(item.status)).length;
  const total = Object.keys(ROLE_PRESENTATION).length;
  const active = [...nodes].reverse().find(node => node.kind === "employee" && node.status === "running") || [...nodes].reverse().find(node => node.status === "running");
  const failed = [...nodes].reverse().find(node => FAILED.has(node.status));
  const stages = STAGES.map(stage => {
    const items = stage.ids.map(id => records.get(id));
    const done = items.filter(item => DONE.has(item?.status)).length;
    let status = items.some(item => FAILED.has(item?.status)) ? "failed" : items.some(item => item?.status === "running") ? "running" : done === items.length ? "success" : "pending";
    let label = status === "success" ? "已完成" : status === "running" ? "正在进行" : status === "failed" ? "需要处理" : done ? `已完成 ${done}/${items.length}` : "等待记录";
    if (stage.id === "verify" && postcheckFailed(projection.postchecks)) { status = "failed"; label = "追加检查未通过"; }
    if (stage.id === "verify" && projection.postchecks?.status === "unverified") { status = "pending"; label = "追加证据待核验"; }
    return { ...stage, status, stateLabel: label, nodeId: items.find(item => item?.status === "running")?.id || items.find(item => FAILED.has(item?.status))?.id || stage.ids.find(id => records.has(id)) || null };
  });
  const result = { completed, total, stages, focusId: active?.id || failed?.id || null, tone: "pending", title: "先看看一次真实实验", copy: "选择一条运行记录，查看总控如何分工、员工如何修改，以及测试有没有通过。", status: "等待选择", next: "查看测试结果", nextTab: "results" };
  if (!run.id) return result;
  const suites = asArray(projection.metrics?.suites);
  const brokenSuite = suites.find(suite => suite.after?.successful === false);
  const originalAcceptance = acceptanceState(run, projection.verdict || {});
  if (postcheckFailed(projection.postchecks)) {
    const check = asArray(projection.postchecks?.checks).find(item => item.status === "failed");
    return { ...result, tone: "failed", title: "追加检查发现了兼容性问题", status: "还需修复", copy: `${check?.description || check?.id || "追加用例未通过"}。原注册验收分数保留，但这份候选还不能视为完整正确。`, next: "查看追加失败证据", nextTab: "results", nextTarget: "postchecks-panel" };
  }
  if (FAILED.has(run.status)) {
    let failureCopy = trim(run.error || projection.verdict?.reason) || "当前未形成通过的工程交付。查看执行记录，确认阻塞原因和已保留的结果。";
    if (/Employee exhausted bounded action loop/.test(String(run.error || ""))) {
      const member = nodes.some(node => node.kind === "employee" && node.role === "repair-code" && FAILED.has(node.status)) ? "修复成员" : "协作成员";
      const verification = records.get("verify-delivery");
      failureCopy = `${member}未在动作上限内提交有效补丁${!verification || ["pending", "not_run"].includes(verification.status) ? "，后续验收未执行" : "，需要核对已有验收是否覆盖本次候选"}。拒绝原因与工具回执已保留，可展开成员详情查看。`;
    }
    return { ...result, tone: "failed", title: run.status === "budget_exhausted" ? "实验已达到预算上限" : run.status === "interrupted" || run.status === "cancelled" ? "这次实验已停止" : "这次实验还没有通过", status: statusLabel(run.status), copy: failureCopy, next: "查看原因与执行记录", nextTab: "events" };
  }
  if (isTerminal(run.status)) {
    if (projection.postchecks?.status === "unverified") return { ...result, title: "流程结束，追加证据待核验", status: "待核验", copy: "追加检查暂时无法与本次候选版本绑定。请先核对证据，再判断最终结果。", next: "查看待核验的检查", nextTarget: "postchecks-panel" };
    if (brokenSuite || Object.values(projection.verdict || {}).some(value => value === false) || (typeof run.summary?.accepted === "number" && run.summary.accepted < run.summary.total)) return { ...result, tone: "failed", title: "测试发现了待修复问题", status: "验收未通过", copy: `${brokenSuite?.label ? brokenSuite.label + "未通过。" : "尚未满足本次验收要求。"}${trim(projection.verdict?.reason) || "请从测试结果与失败日志查看实际差异。"}`, next: "查看未通过的测试" };
    if (projection.legacy_evidence_pending) return { ...result, title: "旧格式记录，执行证据待核验", status: "待核验", copy: "这条记录未采用当前完整工程流程。部分用例计数已保存，但没有进程退出码，不能据此确认最终运行成功。原报告未改写。", next: "查看已有测试与原报告" };
    const project = run.kind === "autonomous_project";
    const ready = !project || ["generated", "independent", "regression", "build", "runtime"].every(id => suites.some(suite => suite.id === id && suite.after?.successful === true));
    if (originalAcceptance === "success" && ready) return { ...result, tone: "success", title: "修复已通过本次注册验收", status: "可以亲手验证", copy: project ? "已记录的测试、回归和交付检查通过。你可以查看补丁，或输入自己的样例，比较原版与修复版。有限测试不代表所有输入均正确。" : "这条记录报告了验收通过。请查看其实际测试证据；它未采用当前九能力完整工程流程。", next: run.can_probe ? "输入样例，亲手对比" : "查看验收与交付证据", nextTab: run.can_probe ? "probe" : "results" };
    return { ...result, title: "流程已结束，验收证据尚待核对", status: "未形成通过结论", copy: "完成流程不等于功能正确；当前记录不足以确认所有交付检查通过。请查看已产生的测试结果。", next: "核对测试与验收结果" };
  }
  if (run.status === "queued") return { ...result, title: "实验已排队，等待开始", status: "排队中", copy: "请求已交给服务；尚未收到执行记录，不预估完成时间。", next: "查看当前执行记录", nextTab: "events" };
  if (active) return { ...result, tone: "running", title: `${nodePresentation(active).person}正在${nodePresentation(active).action}`, status: "真实运行中", copy: brokenSuite ? `${brokenSuite.label}记录了失败，团队正在处理。点击当前员工可查看最近的工具回执；页面只按实际记录更新。` : "点击亮起的员工，查看正在处理什么、读了哪些文件、最近的工具操作是否成功。状态随实际记录更新。", next: "聚焦当前工作", nextFocus: active.id };
  return { ...result, tone: "running", title: completed === total ? "能力执行完毕，等待服务收尾" : "服务执行中，等待下一条回执", status: "等待更新", copy: "暂时没有新的执行回执。进度只统计已记录完成的能力，不随等待时间自动增加。", next: "查看最近执行记录", nextTab: "events" };
}
