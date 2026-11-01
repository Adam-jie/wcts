const $ = (id) => document.getElementById(id);
const SKILLS = [
  ["plan-workflow", "CodeAgent 总控", "规划、约束与结果汇总", "理解需求、指定能力顺序与验收边界；本次沿用已产生的规划记录。"],
  ["explore-repository", "仓库探索", "目录与项目入口", "识别真实仓库的目录、运行入口和已有测试。"],
  ["ground-code", "代码定位", "需求关联到函数", "找到 Select 构建器、辅助函数与解析调用链。"],
  ["analyze-requirements", "需求分析", "行为与验收条件", "将需求拆成字符串、表达式、复制和兼容等可验证行为。"],
  ["generate-tests", "生成测试", "9 用例与执行步骤", "LLM 生成 pytest 测试和执行建议；框架冻结后再运行。"],
  ["diagnose-results", "失败诊断", "用失败证据定位", "分析公开生成测试的真实失败；独立裁判源码不进入模型上下文。"],
  ["repair-code", "代码补全", "受限修改与返工", "模型输出补丁，执行器检查修改范围和原文匹配，再执行冻结测试。"],
  ["review-patch", "独立审查", "检查补丁与行为", "另一轮模型检查最终候选，发现高优先级问题就要求返工。"],
  ["verify-delivery", "验收与交付", "回归、构建与安装", "可信宿主执行独立验收、140 项回归，并实际构建、安装和运行。"],
];
const STEP_SUMMARIES = [
  ["隔离放置测试", "将生成文件放入专用目录，不修改候选代码、原有测试或独立验收。"],
  ["核对版本与用例", "对原版和候选绑定运行同一组测试，核对收集 ID 及真实导入路径。"],
  ["交给 pytest 执行", "由框架启动受控 Python 进程，分别记录通过、断言失败、依赖和收集错误。"],
  ["验证缺失假设", "LLM 预测原版缺少方法可能报 AttributeError；是否真失败，以执行记录为准。"],
  ["修复后再回归", "重跑冻结测试，再由独立裁判执行功能验收和原项目回归。"],
  ["构建、安装后再测试", "检查 wheel，安装到新环境，再对实际安装的库运行测试和 API 检查。"],
];
let demo = null;
let activeSkill = "plan-workflow";
let busy = false;
let scene = null;
let evidencePage = "tests";
const EVIDENCE_TITLES = { overview: "任务、模型与实验范围", tests: "测试如何设计，又如何执行", patch: "从失败到真实代码补丁", runtime: "亲手运行补全后的程序", retest: "现场重新执行冻结测试" };

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined && text !== null) element.textContent = String(text);
  return element;
}

function display(value) {
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

function metric(id, value) {
  const target = $(id);
  target.replaceChildren();
  if (value && typeof value === "object") {
    target.append(document.createTextNode(String(value.passed ?? "—")), node("small", "", `/ ${value.total ?? "—"}`));
  } else {
    target.textContent = value ? String(value) : "—";
  }
}

function asError(error) {
  if (error?.name === "AbortError") return "请求等待超时。请稍后重试，已有实验记录不会改变。";
  return error?.message || "本地服务暂时不可用，请检查服务后重新尝试。";
}

async function request(path, payload, timeoutMs = 30000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const options = { signal: controller.signal, headers: { Accept: "application/json" } };
    if (payload !== undefined) {
      options.method = "POST";
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(payload);
    }
    const response = await fetch(path, options);
    const result = await response.json();
    if (!response.ok) {
      const detail = result.detail || result.error || `本地服务返回 ${response.status}`;
      const message = Array.isArray(detail)
        ? detail.map((item) => `${(item.loc || []).filter((part) => part !== "body").join(".")}: ${item.msg || display(item)}`).join("；")
        : display(detail);
      throw new Error(message);
    }
    return result;
  } finally {
    clearTimeout(timer);
  }
}

function showError(id, error) {
  const target = $(id);
  target.textContent = asError(error);
  target.hidden = false;
}

function setBusy(value) {
  busy = value;
  $("probe-submit").disabled = value || !demo;
  $("retest-submit").disabled = value || !demo;
  for (const field of $("probe-form").querySelectorAll("input, select")) field.disabled = value;
}

function groupStages(skill) {
  return (demo?.stages || []).filter((stage) => (stage.skill_id || stage.role || stage.id) === skill || stage.id === skill);
}

function artifactText(stages) {
  return display(stages.map((stage) => ({
    phase: stage.phase,
    attempt: stage.attempt,
    role: stage.role,
    artifact: stage.artifact ?? stage.details ?? {},
  })));
}

function renderManager(readable, stages) {
  const plan = stages.at(-1)?.artifact || {};
  const goals = Array.isArray(plan.goals) ? plan.goals : [];
  const skills = Array.isArray(plan.skill_ids) ? plan.skill_ids : [];
  const planSection = node("section", "manager-plan");
  planSection.append(node("h4", "", "本次计划"), node("p", "", "为 SQLGlot 补齐 SORT / CLUSTER 的链式构建 API，保持复制、追加及原有行为。"));
  planSection.append(node("p", "plan-count", `${goals.length} 条原始需求 · ${skills.length} 项能力顺序`));
  const original = node("details", "manager-original");
  original.append(node("summary", "", "原始目标与规划理由"), node("pre", "code-block", display({ goals, skill_ids: skills, rationale: plan.rationale || "未返回原始理由" })));
  planSection.append(original);
  readable.append(planSection);
  const feedback = node("section", "manager-feedback");
  feedback.append(node("h4", "", "失败反馈 → 修复 → 评审"));
  const oldReview = groupStages("review-patch").find((stage) => stage.phase === "parent");
  const latestReview = groupStages("review-patch").at(-1);
  const resumed = (demo.stages || []).filter((stage) => stage.phase === "continuation");
  const newPlanner = resumed.some((stage) => stage.skill_id === "plan-workflow");
  const rows = [
    ["原计划继承", `${stages.length} 份规划产物；续跑${newPlanner ? "包含新的规划记录" : "未新增规划模型调用"}。`],
    ["公开失败反馈", `旧候选 ${demo.metrics?.generated_failed?.passed ?? "—"}/${demo.metrics?.generated_failed?.total ?? "—"}；首次评审${oldReview?.artifact?.approved === false ? "拒绝" : "状态见原始产物"}。诊断指出 Sort / Cluster 解析器未注册。`],
    ["宿主依据证据续跑", `新调用 ${demo.provenance?.new_real_calls ?? "—"} 次：${resumed.map((stage) => SKILLS.find((item) => item[0] === stage.skill_id)?.[1] || stage.skill_id).join("、")}；保持原需求与冻结测试。`],
    ["最终审查与验证", `评审${latestReview?.artifact?.approved === true ? "批准" : "结果见产物"}；生成测试 ${demo.metrics?.generated_after?.passed ?? "—"}/${demo.metrics?.generated_after?.total ?? "—"}。独立验收和回归由宿主另行执行。`],
  ];
  const chain = node("ol", "handoff-chain");
  for (const [label, detail] of rows) {
    const item = node("li");
    item.append(node("strong", "", label), node("p", "", detail));
    chain.append(item);
  }
  feedback.append(chain);
  readable.append(feedback);
}

function selectStage(skill) {
  const index = SKILLS.findIndex((item) => item[0] === skill);
  if (index < 0) return;
  activeSkill = skill;
  const descriptor = SKILLS[index];
  const stages = groupStages(skill);
  const latest = stages.at(-1);
  for (const button of $("flow-grid").querySelectorAll("button")) {
    button.setAttribute("aria-pressed", String(button.dataset.skill === skill));
  }
  $("stage-number").textContent = "";
  $("stage-title").textContent = descriptor[1];
  $("stage-type").textContent = skill === "plan-workflow" && latest?.inherited ? "继承规划" : latest?.llm === false ? "执行记录" : "模型产物";
  $("stage-summary").textContent = skill === "plan-workflow" ? descriptor[3] : latest?.summary || latest?.detail || descriptor[3];
  const readable = $("stage-readable");
  readable.replaceChildren();
  const list = node("ul");
  const items = {
    "plan-workflow": ["总控安排技能阶段、预算和依赖顺序。", "计划输出是建议；是否执行、是否通过由框架记录。"],
    "explore-repository": ["读取公开仓库结构，识别 Python 库与已有测试。", "不把独立验收源码交给模型。"],
    "ground-code": ["定位 Select、_apply_child_list_builder 和 Parser。", "模型阅读相关源码片段，不直接运行或改动目标仓库。"],
    "analyze-requirements": ["把需求变成行为约束：字符串、表达式、追加/替换、复制/原地更新、方言与兼容。", "每项测试关联同一条原始功能需求。"],
    "generate-tests": ["模型输出 1 份 pytest 文件、9 个用例、6 条执行建议。", "先冻结文件 SHA256，再运行原版和候选；补全 Agent 不可修改它。", "基线 1/9 → 旧候选 2/9 → 最终候选 9/9，是实际执行结果。"],
    "diagnose-results": ["第一版方法存在，但字符串输入失败：解析器未注册 Sort / Cluster。", "诊断只使用公开生成测试的失败和源码，独立验收反馈不参与补丁生成。"],
    "repair-code": ["第一轮新增 sort_by() 和 cluster_by() 方法。", "返工轮由模型补齐两个 Parser 注册；测试内容保持原样。", "完整补丁可在下方逐行查看。"],
    "review-patch": ["旧候选曾被审查拒绝；不能把审查失败当作成功。", "本次续跑产生一次修复调用、一次审查调用。", "最终审查的候选与验收、构建安装使用同一版本。"],
    "verify-delivery": ["模型阶段结束后才进行独立验收 1/1 和原项目回归 140/140。", "实际构建 wheel、创建新环境、安装并运行 API 3/3。", "安装后再执行生成测试 9/9 与原项目回归 140/140。"],
  };
  if (skill === "plan-workflow") renderManager(readable, stages);
  else {
    for (const text of items[skill] || []) list.append(node("li", "", text));
    readable.append(list);
  }
  const related = skill === "plan-workflow" ? (demo.stages || []).filter((stage) => ["plan-workflow", "diagnose-results", "repair-code", "review-patch"].includes(stage.skill_id)) : stages;
  $("stage-artifact").textContent = related.length ? artifactText(related) : "暂无该阶段的可展示产物。";
  $("stage-artifact-path").textContent = latest?.artifact_path || (skill === "verify-delivery" ? "证据来自构建、安装和复测的宿主执行记录。" : "");
  const evidence = ["repair-code", "review-patch"].includes(skill) ? "patch" : skill === "verify-delivery" ? "retest" : ["generate-tests", "diagnose-results"].includes(skill) ? "tests" : "overview";
  $("stage-evidence").dataset.evidence = evidence;
  scene?.select(skill);
}

function userSelectStage(skill) {
  selectStage(skill);
}

function openEvidence(page) {
  if (!Object.hasOwn(EVIDENCE_TITLES, page)) return;
  const changed = page !== evidencePage;
  evidencePage = page;
  $("dialog-title").textContent = EVIDENCE_TITLES[page];
  for (const target of document.querySelectorAll(".evidence-page")) target.hidden = target.dataset.page !== page;
  const tabs = [...document.querySelectorAll(".evidence-tabs button")];
  for (const tab of tabs) {
    const selected = tab.dataset.evidence === page;
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
  }
  if (!$("evidence-dialog").open) $("evidence-dialog").showModal();
  if (changed) $("dialog-content").scrollTop = 0;
}

async function initializeScene() {
  try {
    const { mountFEAScene } = await import("./fea-scene.js");
    scene = mountFEAScene($("fea-scene"), {
      onSelect: userSelectStage,
      onMode({ mode, reason }) {
        $("scene-mode").textContent = mode === "webgl" ? "" : `三维场景暂不可用：${reason || "当前设备无法启动 WebGL"}`;
        $("scene-mode").hidden = mode === "webgl";
        $("scene-mode").classList.toggle("fallback-mode", mode !== "webgl");
        for (const id of ["scene-reset", "scene-focus", "scene-zoom-in", "scene-zoom-out"]) $(id).disabled = mode !== "webgl";
      },
    });
    scene.setNodes(SKILLS.map(([id, label]) => {
      const latest = groupStages(id).at(-1);
      return { id, label, shortLabel: label, status: latest?.status || "unknown", attempt: latest?.attempt || 1, llm: latest?.llm !== false, summary: latest?.summary || "" };
    }));
    scene.select(activeSkill);
  } catch (error) {
    $("scene-mode").hidden = false;
    $("scene-mode").textContent = `三维场景未载入：${asError(error)}。仍可使用下方能力按钮与证据面板。`;
    $("scene-mode").classList.add("fallback-mode");
    for (const id of ["scene-reset", "scene-focus", "scene-zoom-in", "scene-zoom-out"]) $(id).disabled = true;
  }
}

function renderFlow() {
  const flow = $("flow-grid");
  flow.replaceChildren();
  function roleButton(index) {
    const [id, title, subtitle] = SKILLS[index];
    const button = node("button", "flow-card");
    button.type = "button";
    button.dataset.skill = id;
    button.setAttribute("aria-pressed", String(id === activeSkill));
    button.setAttribute("aria-controls", "stage-title");
    const text = node("span", "role-text");
    text.append(node("strong", "", title), node("small", "", subtitle));
    button.append(text);
    if (index === 0) button.append(node("span", "role-badge", "总控"));
    if (index === 8) button.append(node("span", "role-badge", "宿主"));
    button.addEventListener("click", () => {
      userSelectStage(id);
      if (window.matchMedia("(max-width: 1000px)").matches) $("stage-panel").scrollIntoView({ block: "start", behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
    });
    return button;
  }
  const manager = roleButton(0);
  manager.classList.add("manager-role");
  flow.append(manager);
  const groups = node("div", "role-groups");
  for (const [label, indexes] of [["理解需求与测试设计", [1, 2, 3, 4]], ["修复与交付验证", [5, 6, 7, 8]]]) {
    const group = node("section", "role-group");
    group.append(node("h3", "", label));
    const buttons = node("div", "role-buttons");
    for (const index of indexes) buttons.append(roleButton(index));
    group.append(buttons);
    groups.append(group);
  }
  flow.append(groups);
  selectStage(activeSkill);
}

function caseStatus(stages, stageId, name) {
  const stage = stages.find((item) => item.id === stageId);
  const test = stage?.cases?.find((item) => (item.id || item.name || "").split("::").at(-1) === name);
  return test || { status: "unknown" };
}

function statusCell(test) {
  const status = test.status;
  const passed = ["passed", "pass", "success"].includes(status);
  const failed = ["failure", "failed", "error"].includes(status);
  const cell = node("td", passed ? "result-pass" : failed ? "result-fail" : "result-unknown");
  if (test.message && failed) {
    const details = node("details", "failure-details");
    details.append(node("summary", "result-fail", "× 失败 · 看原因"), node("code", "", test.message));
    cell.append(details);
  } else {
    cell.textContent = passed ? "✓ 通过" : failed ? "× 失败" : "— 未记录";
  }
  return cell;
}

function renderTests() {
  const generated = demo.generated_tests || {};
  const steps = $("test-steps");
  steps.replaceChildren();
  for (const [title, detail] of STEP_SUMMARIES) {
    const item = node("li");
    item.append(node("strong", "", title), node("p", "", detail));
    steps.append(item);
  }
  $("original-steps").textContent = (generated.steps || []).map((step, index) => `${index + 1}. ${step}`).join("\n\n");
  $("tests-sha").textContent = `冻结 SHA256 · ${generated.frozen_sha256 || generated.sha256 || "未返回"}`;
  $("test-source").textContent = generated.source || "未返回测试源码。";
  const body = $("test-cases");
  body.replaceChildren();
  for (const test of generated.cases || []) {
    const row = node("tr");
    const title = node("td");
    title.append(node("div", "", test.label || test.description || test.name));
    const details = node("details", "case-name");
    details.append(node("summary", "", "查看函数名与检查步骤"), node("code", "", test.name));
    if (test.description) details.append(node("p", "", test.description));
    title.append(details);
    row.append(title, ...["base", "failed", "final"].map((stage) => statusCell(caseStatus(generated.stages || [], stage, test.name))));
    body.append(row);
  }
}

function renderPatch() {
  const patch = demo.patch || {};
  $("patch-files").textContent = (patch.files || []).join(" · ");
  const target = $("patch-source");
  target.replaceChildren();
  for (const line of String(patch.diff || patch.source || "补丁暂不可用。").split("\n")) {
    let cls = "";
    if (line.startsWith("@@") || line.startsWith("diff ") || line.startsWith("---") || line.startsWith("+++")) cls = "diff-header";
    else if (line.startsWith("+")) cls = "diff-add";
    else if (line.startsWith("-")) cls = "diff-remove";
    target.append(node("span", cls, `${line}\n`));
  }
}

function provenanceRow(label, value) {
  const target = $("provenance");
  target.append(node("dt", "", label), node("dd", "", value));
}

function renderProvenance() {
  const provenance = demo.provenance || {};
  const integrity = demo.integrity || {};
  $("provenance").replaceChildren();
  provenanceRow("模型请求与实际身份", `${provenance.requested_model || demo.meta?.requested_model || "未知"}；提供方未返回可独立核实的实际模型身份。`);
  provenanceRow("真实调用与用量", `本次续跑 ${provenance.new_real_calls ?? "—"} 次；含先前失败累计 ${provenance.cumulative_real_calls ?? "—"} 次，提供方报告 ${Number(provenance.cumulative_reported_tokens || 0).toLocaleString("zh-CN")} tokens。`);
  provenanceRow("编排方式", provenance.orchestration || "串行 Skill 流程，续跑继承冻结的真实产物。" );
  provenanceRow("失败与返工历史", "第一轮 4 次调用止于需求 ID 校验；第二轮 9 次调用得到 2/9 的旧候选后预算停止；本次续跑 2 次调用完成修复与审查。旧失败记录保留，不能计为首次成功。" );
  provenanceRow("最终候选 SHA256", integrity.candidate_sha256 || "未返回");
  provenanceRow("wheel SHA256", integrity.wheel_sha256 || "未返回");
  provenanceRow("冻结测试 SHA256", integrity.frozen_tests_sha256 || "未返回");
  const limits = $("experiment-limits");
  limits.replaceChildren();
  for (const limit of demo.limits || []) limits.append(node("li", "", limit));
}

function updateProbeCode() {
  const fields = readProbeFields(false);
  if (!fields) return;
  $("probe-code").textContent = `import sqlglot\n\n(sqlglot.select(${fields.columns.map((part) => JSON.stringify(part)).join(", ")})\n    .from_(${JSON.stringify(fields.table)})\n    .${fields.operation}(${fields.clauses.map((part) => JSON.stringify(part)).join(", ")})\n    .sql())`;
}

function readProbeFields(validate = true) {
  const fields = {
    operation: $("probe-operation").value,
    table: $("probe-table").value.trim(),
    columns: $("probe-columns").value.split(",").map((part) => part.trim()).filter(Boolean),
    clauses: $("probe-clauses").value.split(",").map((part) => part.trim()).filter(Boolean),
  };
  if (!validate) return fields;
  const identifier = /^[A-Za-z_][A-Za-z0-9_]{0,63}(?:\.[A-Za-z_][A-Za-z0-9_]{0,63})?$/;
  const clause = /^[A-Za-z_][A-Za-z0-9_]{0,63}(?:\.[A-Za-z_][A-Za-z0-9_]{0,63})?(?: +(ASC|DESC))?$/i;
  if (!identifier.test(fields.table) || fields.columns.length < 1 || fields.columns.length > 5 || fields.columns.some((part) => !identifier.test(part))) {
    throw new Error("请使用普通表名和列名，多个列用英文逗号分隔，最多 5 个。示例：tbl、a、tbl.a。");
  }
  if (fields.clauses.length < 1 || fields.clauses.length > 5 || fields.clauses.some((part) => !clause.test(part))) {
    throw new Error("字段支持普通标识符和 ASC / DESC，最多 5 个。示例：a DESC, b ASC。");
  }
  return fields;
}

async function runProbe(event) {
  event.preventDefault();
  if (busy || !demo) return;
  $("probe-error").hidden = true;
  let fields;
  try { fields = readProbeFields(); } catch (error) { showError("probe-error", error); return; }
  setBusy(true);
  $("probe-submit").textContent = "三个版本正在真实运行…";
  $("probe-state").textContent = "执行中 · 未调用模型";
  $("probe-state").className = "loading-text";
  $("probe-results").replaceChildren(node("p", "empty-state", "正在启动本地 Python，分别导入三个固定版本并运行同一段调用…"));
  $("probe-time").textContent = "";
  try {
    const result = await request("/api/fea/probe", fields, 120000);
    if (result.code) $("probe-code").textContent = result.code;
    const target = $("probe-results");
    target.replaceChildren();
    if (result.expected_sql) {
      const expected = node("div", "probe-result");
      expected.append(node("div", "console-caption", "按输入构造的预期 SQL"), node("pre", "", result.expected_sql));
      target.append(expected);
    }
    for (const version of result.versions || []) {
      const block = node("div", "probe-result");
      const heading = node("div", "probe-result-heading");
      heading.append(node("span", "", version.label || version.id), node("span", version.ok ? "result-pass" : "result-fail", version.ok ? "✓ 正常返回" : "× 实际异常"));
      block.append(heading, node("pre", version.ok ? "result-pass" : "result-fail", version.ok ? version.sql : version.error || "执行失败"));
      block.append(node("small", "", version.binding_verified ? "已核对导入路径与源码指纹" : "导入绑定未核实"));
      if (version.ok && result.expected_sql) block.append(node("small", version.sql === result.expected_sql ? "result-pass" : "result-fail", version.sql === result.expected_sql ? "输出与上述预期一致" : "实际输出与上述预期有差异，请检查参数和 SQL 语义。"));
      target.append(block);
    }
    $("probe-state").textContent = "真实运行完成";
    $("probe-time").textContent = `本次执行 · ${result.executed_at || "服务器未返回时间"}${result.id ? ` · ${result.id}` : ""}`;
  } catch (error) {
    showError("probe-error", error);
    $("probe-state").textContent = "运行未完成";
    $("probe-results").replaceChildren(node("p", "empty-state", "本次请求没有取得完整运行结果。请查看错误后重试。"));
  } finally {
    setBusy(false);
    $("probe-state").className = "";
    $("probe-submit").textContent = "运行这个 Python 调用 →";
  }
}

async function runRetest() {
  if (busy || !demo) return;
  $("retest-error").hidden = true;
  setBusy(true);
  $("retest-submit").textContent = "测试执行中，请稍候…";
  $("retest-state").textContent = "执行中 · 真实 pytest · 0 次模型调用";
  $("retest-state").className = "tag neutral-tag";
  $("retest-results").replaceChildren(node("p", "empty-state loading-text", "正在核对冻结指纹，执行功能测试与原项目回归。不会改变测试或补丁。"));
  $("retest-time").textContent = "";
  try {
    const result = await request("/api/fea/retest", {}, 180000);
    const target = $("retest-results");
    target.replaceChildren();
    for (const suite of result.suites || []) {
      const block = node("div", "retest-suite");
      const count = node("strong", suite.ok ? "result-pass" : "result-fail");
      count.append(document.createTextNode(String(suite.passed ?? "—")), node("small", "", ` / ${suite.total ?? "—"}`));
      block.append(node("span", "", suite.label || suite.id), count);
      target.append(block);
      if (!suite.ok) target.append(node("p", "result-fail field-help", `失败 ${suite.failed ?? "—"} 项；错误 ${suite.errors ?? "—"} 项。结果以本次记录为准。`));
    }
    $("retest-state").textContent = result.ok ? "✓ 本次重测通过 · 0 次模型调用" : "本次重测有失败，请查看运行记录";
    $("retest-state").className = result.ok ? "tag" : "tag error-tag";
    const integrity = result.integrity || {};
    const verified = integrity.verified === true || (integrity.source_unchanged === true && integrity.case_ids_match_frozen_run === true && integrity.installed_runtime_verified === true);
    $("retest-time").textContent = `本次执行 · ${result.executed_at || "时间未返回"} · 耗时 ${Number(result.duration_s || 0).toFixed(2)} 秒${verified ? " · 指纹核验通过" : ""}${result.artifact_id || result.id ? `\n运行记录 · ${result.artifact_id || result.id}` : ""}`;
  } catch (error) {
    showError("retest-error", error);
    $("retest-state").textContent = "重测未完成";
    $("retest-state").className = "tag error-tag";
    $("retest-results").replaceChildren(node("p", "empty-state", "未获得本次完整测试结果。原有实验记录保留，请稍后重试。"));
  } finally {
    setBusy(false);
    $("retest-submit").textContent = "重新执行冻结测试 ↻";
  }
}

async function load() {
  try {
    demo = await request("/api/fea/demo");
    if (demo.schema !== "testfix.fea-visual-demo.v1") throw new Error("本地服务返回的数据版本不匹配，请刷新页面。" );
    $("requirement-original").textContent = demo.meta?.description || "未返回原始需求。";
    $("requirement-revision").textContent = `任务 ${demo.meta?.instance_id || "SQLGlot #415"}\n固定基线 ${demo.meta?.base_commit || "未返回"}`;
    const metrics = demo.metrics || {};
    metric("metric-generated", metrics.generated_after);
    metric("metric-independent", metrics.official_after);
    metric("metric-regression", metrics.regression_after);
    metric("metric-installed", metrics.installed_api);
    renderFlow();
    renderTests();
    renderPatch();
    renderProvenance();
    setBusy(false);
    initializeScene();
  } catch (error) {
    demo = null;
    showError("page-error", error);
    $("page-error").append(document.createTextNode(" 当前未载入完整真实证据，现场运行按钮保持禁用。"));
    setBusy(false);
  }
}

$("probe-form").addEventListener("submit", runProbe);
$("probe-form").addEventListener("input", updateProbeCode);
$("probe-operation").addEventListener("change", () => {
  if ($("probe-operation").value === "cluster_by" && $("probe-clauses").value === "a DESC") $("probe-clauses").value = "a";
  updateProbeCode();
});
$("retest-submit").addEventListener("click", runRetest);
document.addEventListener("click", (event) => {
  const trigger = event.target.closest("[data-evidence]");
  if (trigger) openEvidence(trigger.dataset.evidence);
  const anchor = event.target.closest('a[href^="#"]');
  const target = { "#test-design": "tests", "#patch": "patch", "#runtime": "runtime", "#retest": "retest" }[anchor?.getAttribute("href")];
  if (target) { event.preventDefault(); openEvidence(target); }
});
$("close-evidence").addEventListener("click", () => $("evidence-dialog").close());
$("evidence-dialog").addEventListener("click", (event) => {
  if (event.target !== $("evidence-dialog")) return;
  const rect = $("evidence-dialog").getBoundingClientRect();
  if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) $("evidence-dialog").close();
});
const tabs = [...document.querySelectorAll(".evidence-tabs button")];
document.querySelector(".evidence-tabs").setAttribute("role", "tablist");
for (const [index, tab] of tabs.entries()) {
  const page = document.querySelector(`.evidence-page[data-page="${tab.dataset.evidence}"]`);
  tab.id = `evidence-tab-${tab.dataset.evidence}`;
  tab.setAttribute("role", "tab");
  tab.setAttribute("aria-controls", `evidence-page-${tab.dataset.evidence}`);
  page.id = `evidence-page-${tab.dataset.evidence}`;
  page.setAttribute("role", "tabpanel");
  page.setAttribute("aria-labelledby", tab.id);
  tab.addEventListener("keydown", (event) => {
    let next = null;
    if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
    else if (event.key === "ArrowLeft") next = (index - 1 + tabs.length) % tabs.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = tabs.length - 1;
    if (next !== null) { event.preventDefault(); openEvidence(tabs[next].dataset.evidence); tabs[next].focus(); }
  });
}
$("show-inspector").addEventListener("click", () => {
  $("stage-panel").scrollIntoView({ block: "start", behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
});
$("codeagent-home").addEventListener("click", () => {
  userSelectStage("plan-workflow");
  scene?.focus("plan-workflow");
  $("stage-panel").scrollIntoView({ block: "start", behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
});
$("scene-reset").addEventListener("click", () => scene?.reset());
$("scene-focus").addEventListener("click", () => scene?.focus(activeSkill));
$("scene-zoom-in").addEventListener("click", () => scene?.zoom(1));
$("scene-zoom-out").addEventListener("click", () => scene?.zoom(-1));
window.addEventListener("pagehide", () => scene?.destroy(), { once: true });
updateProbeCode();
load();
