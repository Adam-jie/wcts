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
  parseRunId,
  safeHref,
  statusClass,
  suiteFraction,
} from "./lab-core.mjs";

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
console.log("Lab presentation helpers: all assertions passed.");
