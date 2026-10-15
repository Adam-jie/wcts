import assert from "node:assert/strict";
import {
  acceptanceState,
  date,
  datasetState,
  graphPositions,
  isOffline,
  isTerminal,
  makeRunRequest,
  normalizeGraph,
  number,
  nodePresentation,
  parseRunId,
  safeHref,
  statusClass,
  suiteFraction,
} from "./lab-core.mjs";
import { capabilityRecords, nodeStory, presentationProjection, workflowStory } from "./lab-human.mjs";

const base = {
  project: "Project/rich-cli",
  provider: "codex_cli",
  model: "gpt-5.6-sol",
  requirement: "  preserve literal CSV  ",
  slots: 2,
  rounds: 2,
  minutes: 120,
  tokens: 1000000,
};
assert.deepEqual(makeRunRequest(base), {
  project_id: "Project/rich-cli",
  provider: "codex_cli",
  model: "gpt-5.6-sol",
  max_rounds: 2,
  budget: {
    wall_seconds: 7200,
    token_limit: 1000000,
    model_slots: 2,
    cpu_slots: 2,
  },
  strategy: "balanced",
  collaboration_request: "preserve literal CSV",
});
assert.throws(
  () => makeRunRequest({ ...base, provider: "replay" }),
  /真实模型/,
);
assert.throws(() => makeRunRequest({ ...base, requirement: " " }), /需求/);
assert.throws(() => makeRunRequest({ ...base, slots: 2.5 }), /预算/);
assert.throws(() => makeRunRequest({ ...base, minutes: 121 }), /预算/);
assert.equal(safeHref("javascript:alert(1)", "http://127.0.0.1:8765"), null);
assert.equal(
  safeHref("https://evil.test/report", "http://127.0.0.1:8765"),
  null,
);
assert.equal(safeHref("//evil.test/report", "http://127.0.0.1:8765"), null);
assert.equal(
  safeHref("/api/report/x", "http://127.0.0.1:8765"),
  "http://127.0.0.1:8765/api/report/x",
);
assert.equal(number(null), "—");
assert.equal(number(undefined), "—");
assert.equal(suiteFraction({ passed: 0, total: 0 }), "0 / 0");
assert.equal(suiteFraction({ passed: 1 }), "未记录");
assert.equal(isTerminal("failed"), true);
assert.equal(isTerminal("running"), false);
assert.equal(isOffline({ provider: "replay" }), true);
assert.equal(
  isOffline({ provider: "codex_cli", kind: "autonomous_project" }),
  false,
);
assert.equal(statusClass("failed"), "failed");
assert.equal(statusClass("completed"), "success");
assert.equal(statusClass("incomplete"), "failed");
assert.equal(
  datasetState({ status: "downloaded_unadapted" }).label,
  "已收集 · 待适配",
);
const graph = {
  nodes: [
    { id: "a", kind: "employee", depends_on: [] },
    { id: "b", kind: "employee", depends_on: [] },
    { id: "c", kind: "employee", depends_on: ["a", "b"] },
    { id: "a", label: "duplicate" },
  ],
  edges: [
    { source: "a", target: "c" },
    { source: "fake", target: "c" },
  ],
};
assert.equal(normalizeGraph(graph).nodes.length, 3);
assert.equal(normalizeGraph(graph).edges.length, 1);
const positions = graphPositions(graph).positions;
assert.equal(positions.get("a").y, positions.get("b").y);
assert.ok(positions.get("c").y < positions.get("a").y);
assert.doesNotThrow(() =>
  graphPositions({
    nodes: [
      { id: "x", kind: "employee", depends_on: ["y"] },
      { id: "y", kind: "employee", depends_on: ["x"] },
    ],
  }),
);
assert.equal(
  acceptanceState({ status: "completed", summary: { accepted: 2, total: 3 } }),
  "failed",
);
assert.equal(
  acceptanceState(
    { status: "completed", summary: { accepted: 1, total: 1 } },
    { regression_preserved: false },
  ),
  "failed",
);
assert.equal(
  acceptanceState({ status: "running", summary: { accepted: 1, total: 1 } }),
  "neutral",
);
assert.equal(
  acceptanceState(
    { status: "completed", summary: { accepted: 1, total: 1 } },
    { regression_preserved: true },
  ),
  "success",
);
assert.equal(
  acceptanceState(
    { status: "completed", summary: { accepted: 1, total: 1 } },
    {},
    { status: "failed" },
  ),
  "failed",
);
assert.equal(
  acceptanceState(
    { status: "completed", summary: { accepted: 1, total: 1 } },
    {},
    { status: "passed", checks: [{ status: "failed" }] },
  ),
  "failed",
);
assert.equal(
  acceptanceState(
    { status: "completed", summary: { accepted: 1, total: 1 } },
    {},
    { status: "passed", checks: [{ status: "passed" }] },
  ),
  "success",
);
assert.equal(
  acceptanceState({
    status: "interrupted",
    summary: { accepted: 1, total: 1 },
  }),
  "failed",
);
assert.ok(date("2026-10-15T07:44:13+00:00").includes("15:44:13"));
assert.equal(
  parseRunId("66c779505ef340cb83a758ca8be8c340"),
  "66c779505ef340cb83a758ca8be8c340",
);
assert.equal(parseRunId("../../secret"), null);
assert.equal(
  parseRunId("https://evil.test/66c779505ef340cb83a758ca8be8c340"),
  null,
);
assert.equal(parseRunId("66c779505ef340cb83a758ca8be8c340/"), null);
const knownSkills = ["plan-workflow", "explore-repository", "ground-code", "analyze-requirements", "generate-tests", "diagnose-results", "repair-code", "review-patch", "verify-delivery"];
const completed = {
  run: { id: "a".repeat(32), kind: "autonomous_project", status: "completed", can_probe: true, summary: { accepted: 1, total: 1 } },
  capabilities: knownSkills.map(skill_id => ({ skill_id, status: "completed" })),
  metrics: { suites: ["generated", "independent", "regression", "build", "runtime"].map(id => ({ id, before: { successful: false }, after: { successful: true } })) },
};
assert.equal(workflowStory(completed).tone, "success");
assert.equal(workflowStory(completed).nextTab, "probe");
assert.equal(workflowStory(completed).completed, 9);
const extraFailure = workflowStory({ ...completed, postchecks: { status: "passed", checks: [{ status: "failed", description: "head/tail 兼容性" }] } });
assert.equal(extraFailure.tone, "failed");
assert.match(extraFailure.copy, /head\/tail/);
assert.equal(extraFailure.stages[4].status, "failed");
assert.equal(extraFailure.nextTarget, "postchecks-panel");
assert.equal(workflowStory({ ...completed, postchecks: { status: "unverified" } }).tone, "pending");
assert.equal(workflowStory({ ...completed, verdict: { protected_files_unchanged: false } }).tone, "failed");
assert.equal(workflowStory({ ...completed, metrics: { suites: [] } }).tone, "pending");
assert.equal(workflowStory({ run: { status: "completed", id: "a".repeat(32) }, events: [] }).completed, 0);
assert.equal(workflowStory({ run: { status: "completed", id: "a".repeat(32) }, events: [] }).tone, "pending");
const contradictory = { graph: { nodes: [{ id: "repair-code", status: "completed" }] }, capabilities: [{ skill_id: "repair-code", status: "running" }] };
assert.equal(capabilityRecords(contradictory).get("repair-code").status, "running");
assert.equal(workflowStory(contradictory).completed, 0);
const employee = { id: "employee:1:csv", task_id: "inspect_csv", kind: "employee", role: "explore-repository", status: "running", detail: { steps: [{ action: "apply_patch", ok: false, receipt_verified: true, error: "候选版本已变化，补丁未应用" }, { action: "read_file", ok: true, receipt_verified: true, path: "src/main.py" }] } };
assert.match(nodePresentation(employee).label, /表格/);
assert.equal(nodeStory(employee).errorRecovered, true);
assert.match(nodeStory(employee).error, /补丁未应用/);
assert.match(nodeStory(employee).receipt, /读取文件.*操作成功.*回执已校验/);
const activeStory = workflowStory({ ...completed, run: { ...completed.run, status: "running" }, capabilities: [], graph: { nodes: [employee] } });
assert.equal(activeStory.tone, "running");
assert.equal(activeStory.focusId, employee.id);
assert.equal(activeStory.nextFocus, employee.id);
const boundedFailure = workflowStory({ run: { id: "a".repeat(32), status: "failed", error: "ValueError: Employee exhausted bounded action loop: repair_literal_tables" }, capabilities: [{ skill_id: "verify-delivery", status: "pending" }], graph: { nodes: [{ ...employee, role: "repair-code", status: "incomplete" }] } });
assert.match(boundedFailure.copy, /修复成员未在动作上限内提交有效补丁，后续验收未执行/);
assert.equal(boundedFailure.nextTab, "events");
assert.equal(boundedFailure.focusId, employee.id);
assert.match(nodeStory({ ...employee, detail: { steps: [{ action: "apply_patch", ok: false, receipt_verified: true, error: "Repair must propose bounded changed files" }] } }).error, /改动文件列表/);
const legacy = { run: { id: "a".repeat(32), kind: null, status: "completed", summary: { accepted: 3, total: 3 } }, metrics: { suites: [{ id: "generated", after: { successful: false, exit_code: null, runnable: true, total: 12, passed: 12 } }] } };
const legacyView = presentationProjection(legacy);
assert.equal(legacyView.metrics.suites[0].after.successful, null);
assert.equal(legacy.metrics.suites[0].after.successful, false);
assert.equal(workflowStory(legacyView).tone, "pending");
assert.match(workflowStory(legacyView).copy, /没有进程退出码/);
const legacyFailure = { ...legacy, metrics: { suites: [{ id: "generated", after: { ...legacy.metrics.suites[0].after, passed: 11 } }] } };
assert.equal(presentationProjection(legacyFailure).metrics.suites[0].after.successful, false);
assert.equal(workflowStory(presentationProjection(legacyFailure)).tone, "failed");
assert.equal(presentationProjection({ ...legacy, run: { ...legacy.run, kind: "autonomous_project" } }).metrics.suites[0].after.successful, false);
assert.equal(workflowStory({ ...completed, run: { ...completed.run, status: "running" }, graph: { nodes: [] } }).tone, "running");
console.log("Lab presentation helpers and evidence-aware workflow: all assertions passed.");
