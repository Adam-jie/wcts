import {
  acceptanceState,
  asArray,
  compactNodeLabel,
  date,
  datasetState,
  display,
  externalHref,
  graphPositions,
  isOffline,
  isTerminal,
  makeRunRequest,
  normalizeGraph,
  number,
  parseRunId,
  safeHref,
  statusClass,
  statusLabel,
  suiteFraction,
} from "./lab-core.mjs";

const $ = (id) => document.getElementById(id);
const state = {
  bootstrap: null,
  projection: null,
  selectedNode: null,
  tab: "results",
  runId: null,
  startedHere: null,
  pollTimer: null,
  pollEpoch: 0,
  scene: null,
  flat: false,
  submitting: false,
  probing: false,
};
const transientStatuses = new Set(["queued", "running"]);
const probeSamples = {
  csv: "name,value\n[bold],[/not-opened]\nplain,[red]literal[/red]",
  csv_head_tail: "name,value\nfirstrow,1\nsecondrow,2",
  print: "[bold green]Rich CLI markup is preserved[/bold green]",
};
function el(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = display(text);
  return element;
}
function setText(id, value) {
  $(id).textContent = display(value);
}
function showError(id, message) {
  $(id).hidden = !message;
  $(id).textContent = message || "";
}
function connection(ok, label) {
  $("connection-dot").className = `status-dot ${ok ? "online" : "offline"}`;
  setText("connection-text", label);
}
function badge(status, label) {
  return el(
    "span",
    `status-badge ${statusClass(status)}`,
    label || statusLabel(status),
  );
}
function link(label, url, external = false) {
  const href = external ? externalHref(url) : safeHref(url, location.origin);
  if (!href) return el("span", "muted", label);
  const a = el("a", "", label);
  a.href = href;
  if (external) {
    a.target = "_blank";
    a.rel = "noreferrer noopener";
  }
  return a;
}
function code(value) {
  return el("pre", "", typeof value === "string" ? value : display(value));
}
function empty(target, message) {
  target.replaceChildren(el("p", "empty-copy", message));
}
async function api(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: {
      Accept: "application/json",
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...options.headers,
    },
    cache: "no-store",
  });
  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error(`服务返回非 JSON 响应（HTTP ${response.status}）。`);
  }
  if (!response.ok) {
    const detail = data.detail || data.error || data.message;
    throw new Error(
      typeof detail === "string"
        ? detail
        : detail
          ? JSON.stringify(detail)
          : `请求失败：HTTP ${response.status}`,
    );
  }
  return data;
}

function project() {
  return asArray(state.bootstrap?.projects).find(
    (item) => item.id === $("project").value,
  );
}
function provider() {
  return asArray(state.bootstrap?.providers).find(
    (item) => item.id === $("provider").value,
  );
}
function fillSelect(target, options, current) {
  target.replaceChildren(
    ...options.map((option) => {
      const item = el("option", "", option.label);
      item.value = option.value;
      item.disabled = !!option.disabled;
      return item;
    }),
  );
  if (options.some((item) => item.value === current && !item.disabled))
    target.value = current;
  target.disabled = !options.some((item) => !item.disabled);
}
function updateProject(overwrite = true) {
  const item = project();
  $("project-note").replaceChildren();
  if (item) {
    const title =
      typeof item.source === "object"
        ? item.source.repository || item.source.url || item.source.name
        : item.source;
    $("project-note").append(
      document.createTextNode(`适配器：${item.adapter || "已注册工程"} · `),
      link(
        title || "来源见执行报告",
        typeof item.source === "object"
          ? item.source.repository || item.source.url
          : item.source,
        true,
      ),
    );
    if (overwrite && typeof item.default_request === "string")
      $("requirement").value = item.default_request;
  }
  updateStart();
}
function updateProvider() {
  const item = provider();
  const note = item?.configured
    ? "提交后将实际调用此服务。请求模型名与提供方返回的模型名分开记录。"
    : "所选模型服务未配置，不能启动真实运行。";
  setText("provider-note", note);
  updateStart();
}
function updateStart() {
  const active =
    asArray(state.bootstrap?.runs).some((run) =>
      transientStatuses.has(run.status),
    ) || transientStatuses.has(state.projection?.run?.status);
  $("start-run").disabled =
    state.submitting || active || !project() || !provider()?.configured;
  $("start-run").firstElementChild.textContent = state.submitting
    ? "正在提交…"
    : active
      ? "已有实验执行中"
      : "启动真实测试";
}
function runOption(run) {
  const offline = isOffline(run);
  return {
    value: run.id,
    label: `${offline ? "离线回放" : statusLabel(run.status)} · ${run.project_id || run.title || run.kind || "工程"} · ${date(run.created_at)} · ${run.id.slice(0, 8)}`,
  };
}
function renderHistory() {
  const runs = asArray(state.bootstrap?.runs);
  fillSelect(
    $("history"),
    runs.length
      ? runs.map(runOption)
      : [{ value: "", label: "尚无实验记录", disabled: true }],
    state.runId,
  );
  $("load-history").disabled = !runs.length;
}
async function loadDatasetTasks(id, details) {
  const content = el("div", "dataset-task-list");
  content.append(el("p", "budget-note", "读取已采集的公开问题…"));
  details.append(content);
  try {
    const result = await api(
      `/api/lab/datasets/${encodeURIComponent(id)}/tasks?limit=24`,
    );
    content.replaceChildren(
      el(
        "p",
        "budget-note",
        `已采集 ${number(result.total)} 项；以下为前 ${asArray(result.tasks).length} 项公开问题。采集不代表已经测试，暂不提供执行按钮。`,
      ),
    );
    for (const task of asArray(result.tasks)) {
      const item = el("details", "dataset-task");
      item.append(
        el(
          "summary",
          "",
          task.instance_id ||
            task.id ||
            task.bug_id ||
            task.project ||
            "公开任务",
        ),
      );
      const text = el("div");
      if (task.repo || task.project)
        text.append(el("p", "", `仓库：${task.repo || task.project}`));
      if (task.base_commit || task.version)
        text.append(
          el("p", "mono", `固定版本：${task.base_commit || task.version}`),
        );
      text.append(
        el(
          "p",
          "",
          task.problem_statement ||
            task.description ||
            task.summary ||
            task.title ||
            display(task),
        ),
      );
      item.append(text);
      content.append(item);
    }
    for (const note of asArray(result.notes))
      content.append(el("p", "budget-note", note));
    if (result.has_more)
      content.append(
        el(
          "p",
          "budget-note",
          "此处只展示前 24 项；完整索引保留在本地数据目录。",
        ),
      );
  } catch (error) {
    content.replaceChildren(
      el("p", "dataset-task-error", `任务读取失败：${error.message}`),
    );
    details.dataset.loaded = "";
  }
}
function renderDatasets() {
  const target = $("dataset-list"),
    datasets = asArray(state.bootstrap?.datasets);
  setText(
    "dataset-count",
    `${datasets.length} 个来源 · 状态按本地实际适配显示`,
  );
  if (!datasets.length) {
    empty(
      target,
      "尚未登记数据集。服务返回后会显示收集状态、许可证与执行条件。",
    );
    return;
  }
  target.replaceChildren(
    ...datasets.map((dataset) => {
      const card = el("article", "dataset-card"),
        top = el("div", "dataset-top");
      top.append(el("span", "dataset-icon", "⌘"));
      const status = datasetState(dataset);
      top.append(badge(status.css, status.label));
      card.append(
        top,
        el("h3", "", dataset.name || dataset.id),
        el(
          "p",
          "",
          dataset.description ||
            asArray(dataset.notes)[0] ||
            dataset.scope ||
            "公开任务来源，实际运行条件见适配状态。",
        ),
      );
      const meta = el("div", "dataset-meta"),
        count = el("span");
      count.append(
        el(
          "strong",
          "",
          typeof dataset.task_count === "number"
            ? number(dataset.task_count)
            : "—",
        ),
        document.createTextNode("项公开任务"),
      );
      meta.append(count, el("span", "", `许可 ${display(dataset.license)}`));
      card.append(meta);
      const foot = el("div", "dataset-links");
      foot.append(
        link("查看原始来源 ↗", dataset.repository || dataset.url, true),
      );
      const projectId =
        dataset.ready_project_id ||
        dataset.project_id ||
        asArray(dataset.project_ids)[0];
      if (
        projectId &&
        asArray(state.bootstrap?.projects).some((p) => p.id === projectId)
      ) {
        const button = el("button", "", "选择此工程 →");
        button.type = "button";
        button.addEventListener("click", () => {
          $("project").value = projectId;
          updateProject();
          $("setup-title").scrollIntoView({
            behavior: "smooth",
            block: "center",
          });
        });
        foot.append(button);
      } else
        foot.append(
          el("span", "", dataset.adapter_status || "数据集状态与成绩分别记录"),
        );
      card.append(foot);
      if (["swe-bench-lite", "bugsinpy"].includes(dataset.id)) {
        const details = document.createElement("details");
        details.className = "dataset-tasks";
        const summary = el("summary", "", "查看已采集任务");
        details.append(summary);
        details.addEventListener("toggle", () => {
          if (details.open && !details.dataset.loaded) {
            details.dataset.loaded = "true";
            loadDatasetTasks(dataset.id, details);
          }
        });
        card.append(details);
      }
      if (dataset.local_note || dataset.environment) {
        card.append(el("p", "", dataset.local_note || dataset.environment));
      }
      return card;
    }),
  );
}
async function loadBootstrap({ initial = false } = {}) {
  try {
    const data = await api("/api/lab/bootstrap");
    const oldProject = $("project").value,
      oldProvider = $("provider").value;
    state.bootstrap = data;
    const projects = asArray(data.projects);
    fillSelect(
      $("project"),
      projects.length
        ? projects.map((item) => ({
            value: item.id,
            label: item.title || item.id,
          }))
        : [{ value: "", label: "尚无已适配工程", disabled: true }],
      oldProject || data.defaults?.project_id || "Project/rich-cli",
    );
    const providers = asArray(data.providers).filter(
      (item) => item.id !== "replay",
    );
    fillSelect(
      $("provider"),
      providers.length
        ? providers.map((item) => ({
            value: item.id,
            label: `${item.title || item.label || item.id}${item.configured ? "" : "（未配置）"}`,
          }))
        : [{ value: "", label: "无真实模型配置", disabled: true }],
      oldProvider || data.defaults?.provider || "codex_cli",
    );
    if (initial) {
      $("model").value = data.defaults?.model || "gpt-5.6-sol";
      $("slots").value =
        data.defaults?.budget?.model_slots || data.defaults?.model_slots || 2;
      $("wall-minutes").value = Math.min(
        120,
        Math.max(
          1,
          Math.ceil(
            (data.defaults?.budget?.wall_seconds ||
              data.defaults?.wall_seconds ||
              7200) / 60,
          ),
        ),
      );
      $("tokens").value =
        data.defaults?.budget?.token_limit ||
        data.defaults?.token_limit ||
        1000000;
      updateProject(true);
    } else updateProject(false);
    updateProvider();
    renderHistory();
    renderDatasets();
    connection(true, "本地服务已连接");
    showError("global-error", null);
    if (initial && !state.runId) {
      const runs = asArray(data.runs);
      const requestedRaw = new URLSearchParams(location.search).get("run");
      const requestedId = parseRunId(requestedRaw);
      const requestedRun = requestedId
        ? runs.find((run) => run.id === requestedId)
        : null;
      if (requestedRaw && !requestedRun) {
        $("link-notice").hidden = false;
        setText(
          "link-notice",
          requestedId
            ? "链接中的运行记录未找到，已载入默认历史记录。"
            : "运行链接格式无效；只接受 32 位十六进制运行编号，已载入默认记录。",
        );
      }
      const preferred =
        requestedRun ||
        runs.find((run) => run.id === data.defaults?.run_id) ||
        runs.find(
          (run) =>
            run.project_id === "Project/rich-cli" &&
            ["completed", "accepted"].includes(run.status),
        ) ||
        runs.find((run) => ["completed", "accepted"].includes(run.status)) ||
        runs[0];
      if (preferred) await loadRun(preferred.id);
    }
    return data;
  } catch (error) {
    connection(false, "服务连接失败");
    showError("global-error", `无法读取实验室配置：${error.message}`);
  }
}

function renderMode() {
  const run = state.projection.run,
    offline = isOffline(run),
    live = run.id === state.startedHere;
  const banner = $("mode-banner");
  banner.className = `mode-banner ${offline ? "offline" : live ? "live" : ""}`;
  setText(
    "mode-title",
    offline
      ? "离线回放 · 不记为模型补全成绩"
      : live
        ? "本次新启动 · 真实模型运行"
        : transientStatuses.has(run.status)
          ? "正在观测已有真实运行"
          : "历史实测记录 · 当前未调用模型",
  );
  setText(
    "mode-description",
    offline
      ? "使用已存在的补丁/轨迹验证流程，不代表大模型新生成结果。"
      : live
        ? "下面的状态来自本次服务执行；页面每 2 秒读取一次实际记录。"
        : transientStatuses.has(run.status)
          ? "该任务已在服务中执行，页面仅观测其真实状态。"
          : "重新查看已保存的执行证据。点击“启动真实测试”才会创建新的模型调用。",
  );
  const status = $("run-status");
  status.className = `status-badge ${statusClass(run.status)}`;
  status.textContent = statusLabel(run.status);
  if (state.projection.postchecks?.status === "failed") {
    banner.classList.add("postcheck-failed");
    setText(
      "mode-description",
      "原注册验收已完成；追加检查发现行为回归，请查看下方失败证据。还需修复后重新验证。",
    );
    status.className = "status-badge failed";
    status.textContent = "追加检查未通过";
  }
  setText(
    "run-caption",
    `${run.project_id || "完整工程"} · ${run.id} · ${date(run.created_at)}`,
  );
}
function renderMetrics() {
  const metrics = state.projection.metrics || {};
  setText("metric-calls", number(metrics.model_calls));
  setText("metric-concurrency", number(metrics.peak_concurrency));
  setText("metric-tokens", number(metrics.reported_tokens));
  setText(
    "metric-duration",
    typeof metrics.elapsed_seconds === "number"
      ? `${number(metrics.elapsed_seconds, 1)} s`
      : "—",
  );
  setText(
    "metric-token-note",
    metrics.reported_tokens === null || metrics.reported_tokens === undefined
      ? "提供方未报告，不推算"
      : "来自提供方调用记录",
  );
  setText(
    "dispatch-note",
    typeof metrics.overlap_seconds === "number"
      ? `实际模型调用重叠 ${number(metrics.overlap_seconds, 3)} 秒 · 依赖就绪后按出度优先派工`
      : "依赖与资源约束满足后，按出度优先选择就绪任务。",
  );
}
function renderDownloads() {
  const downloads = state.projection.downloads || {};
  for (const [key, id] of [
    ["json", "download-json"],
    ["md", "download-md"],
    ["zip", "download-zip"],
  ]) {
    const href = safeHref(downloads[key], location.origin);
    $(id).hidden = !href;
    if (href) $(id).href = href;
    else $(id).removeAttribute("href");
  }
}
function renderWarnings() {
  const warnings = asArray(state.projection.warnings);
  $("warnings").replaceChildren(
    ...warnings.map((w) =>
      el("p", "", typeof w === "string" ? w : w.message || display(w)),
    ),
  );
}
function renderVerdict() {
  const { run, verdict = {}, postchecks = {} } = state.projection;
  const conclusion = acceptanceState(run, verdict, postchecks),
    accepted = conclusion === "success",
    failed = conclusion === "failed";
  const target = $("verdict");
  target.className = `verdict ${accepted ? "success" : failed ? "failed" : "neutral"}`;
  if (
    postchecks?.status === "failed" ||
    asArray(postchecks?.checks).some((check) => check.status === "failed")
  ) {
    target.textContent =
      acceptanceState(run, verdict) === "success"
        ? "原验收通过；追加检查未通过。追加测试发现行为回归，本次候选还需修复。原五类测试分数保持，失败详情如下。"
        : "追加检查未通过：本次候选存在待修复问题，请结合原套件与下方新增证据判断。";
    return;
  }
  target.textContent = accepted
    ? "验收通过：请结合下方独立测试、回归、构建与工程交付包检查本次结论。"
    : failed
      ? `本次未形成通过的交付结论：${verdict.reason || run.error || statusLabel(run.status)}。`
      : "当前尚未形成通过的交付结论；规划成功、模型返回或单项测试通过均不代表最终验收。";
}
function renderPostchecks() {
  const checks = state.projection.postchecks,
    target = $("postchecks-panel");
  target.hidden = !checks;
  target.replaceChildren();
  if (!checks) return;
  target.className = `postcheck-panel ${checks.status === "failed" ? "failed" : "passed"}`;
  const heading = el("h3", "", "原验收后的追加检查");
  heading.append(badge(checks.status));
  target.append(
    heading,
    el(
      "p",
      "budget-note",
      "追加检查独立保留，不回写原验收套件的通过数量。候选 SHA-256 绑定用于避免错用其他版本的结果。",
    ),
  );
  if (checks.candidate_sha256)
    target.append(
      el("p", "mono small", `候选 SHA-256 ${checks.candidate_sha256}`),
    );
  for (const check of asArray(checks.checks)) {
    const item = el("article", "postcheck-case"),
      title = el(
        "h4",
        "",
        `${check.id || "追加用例"}${check.requirement_id ? " · " + check.requirement_id : ""}`,
      );
    title.append(badge(check.status));
    item.append(title, el("p", "", check.description || check.id));
    const values = el("dl", "key-values");
    for (const [label, value] of [
      ["原版退出码", check.baseline_exit],
      ["候选退出码", check.candidate_exit],
      ["预期行为", check.expected],
      ["实际行为", check.actual],
    ]) {
      if (value !== undefined) {
        values.append(el("dt", "", label), el("dd", "", value));
      }
    }
    item.append(values);
    target.append(item);
  }
  if (parseRunId(checks.followup_run_id)) {
    const followup = link(
      "查看后续修复运行 →",
      `/lab?run=${checks.followup_run_id}`,
    );
    followup.className = "secondary-button";
    target.append(followup);
  }
}
function selectNode(id) {
  state.selectedNode = id;
  state.scene?.setSelected(id);
  renderInspector();
  if (state.flat) renderFallback();
}
function section(title, contents) {
  const item = el("section");
  item.append(el("h3", "", title));
  if (contents instanceof Node) item.append(contents);
  else if (Array.isArray(contents)) {
    const list = el("ul");
    contents.forEach((value) => list.append(el("li", "", value)));
    item.append(list);
  } else item.append(el("p", "", contents));
  return item;
}
function renderInspector() {
  const node = normalizeGraph(state.projection?.graph).nodes.find(
    (item) => item.id === state.selectedNode,
  );
  const target = $("node-body");
  if (!node) {
    setText("node-title", "选择一个节点");
    setText("node-kind", "节点详情");
    empty(target, "点击协作节点，查看真实状态与对应约束。");
    return;
  }
  setText(
    "node-title",
    node.kind === "employee" ? node.task_id || node.label : node.label,
  );
  setText(
    "node-kind",
    node.kind === "employee"
      ? "员工 Agent"
      : node.kind === "controller"
        ? "总控 Agent"
        : "能力模块",
  );
  target.replaceChildren(badge(node.status));
  const stats = el("div", "node-detail-grid");
  for (const [label, value] of [
    ["入度", node.in_degree],
    ["出度", node.out_degree],
  ]) {
    const cell = el("div");
    cell.append(el("span", "", label), el("strong", "", value));
    stats.append(cell);
  }
  target.append(stats);
  const detail = node.detail || {};
  const contract = detail.current_skill_contract;
  if (node.kind === "employee" && detail.title)
    target.append(section("本次任务", detail.title));
  if (node.role || detail.role)
    target.append(section("职责", contract?.role || node.role || detail.role));
  if (contract) {
    target.append(
      section(
        "当前 Skill 合同",
        `${contract.id || node.role} · v${contract.version || "未记录"}`,
      ),
    );
    if (contract.reads?.length)
      target.append(section("合同允许读取", contract.reads));
    if (contract.writes?.length)
      target.append(section("合同允许写入", contract.writes));
    if (contract.budgets)
      target.append(section("合同执行预算", code(contract.budgets)));
    target.append(
      section(
        "合同版本说明",
        "这里展示当前安装的合同；历史约束应以该次调用与步骤回执为准。",
      ),
    );
  }
  if (node.depends_on?.length)
    target.append(section("等待前置任务", node.depends_on));
  const skills =
    detail.skills || detail.skill_ids || detail.skill_id || detail.skill;
  if (skills)
    target.append(
      section(
        "Skill 约束",
        Array.isArray(skills)
          ? skills.map((value) =>
              typeof value === "string" ? value : display(value),
            )
          : skills,
      ),
    );
  const tools = detail.tools || detail.allowed_tools;
  if (tools)
    target.append(
      section(
        "可调用工具",
        Array.isArray(tools)
          ? tools.map((value) =>
              typeof value === "string" ? value : display(value),
            )
          : tools,
      ),
    );
  if (detail.constraints)
    target.append(
      section(
        "执行边界",
        Array.isArray(detail.constraints)
          ? detail.constraints
          : display(detail.constraints),
      ),
    );
  if (detail.summary || detail.result || detail.output)
    target.append(
      section(
        "实际结果",
        typeof (detail.summary || detail.result || detail.output) === "string"
          ? detail.summary || detail.result || detail.output
          : code(detail.summary || detail.result || detail.output),
      ),
    );
  const remaining = Object.fromEntries(
    Object.entries(detail).filter(
      ([key]) =>
        ![
          "role",
          "skills",
          "skill_ids",
          "skill_id",
          "skill",
          "tools",
          "allowed_tools",
          "constraints",
          "summary",
          "result",
          "output",
          "current_skill_contract",
          "title",
        ].includes(key),
    ),
  );
  if (Object.keys(remaining).length)
    target.append(section("执行详情", code(remaining)));
  if (!Object.keys(detail).length)
    target.append(
      section("记录状态", "本次报告尚未提供更多节点详情；不补写推测结果。"),
    );
}

function renderFallback() {
  const target = $("scene");
  target.style.touchAction = "pan-y";
  const graph = graphPositions(state.projection?.graph);
  const shell = el("div", "fallback-graph"),
    plane = el("div", "fallback-plane");
  const xs = [...graph.positions.values()].map((p) => p.x),
    ys = [...graph.positions.values()].map((p) => p.y);
  const minX = Math.min(-5, ...xs) - 1.5,
    maxX = Math.max(5, ...xs) + 1.5,
    maxY = Math.max(3, ...ys) + 1,
    minY = Math.min(-3, ...ys) - 1;
  const coords = new Map(
    [...graph.positions.entries()].map(([id, p]) => [
      id,
      {
        x: ((p.x - minX) / (maxX - minX)) * 100,
        y: ((maxY - p.y) / (maxY - minY)) * 100,
      },
    ]),
  );
  const planeHeight = Math.max(390, (maxY - minY) * 44);
  plane.style.height = `${planeHeight}px`;
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 1000 ${planeHeight}`);
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("aria-hidden", "true");
  for (const edge of graph.edges) {
    const a = coords.get(edge.source),
      b = coords.get(edge.target);
    if (!a || !b) continue;
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    const x1 = a.x * 10,
      x2 = b.x * 10,
      y1 = (a.y * planeHeight) / 100,
      y2 = (b.y * planeHeight) / 100;
    path.setAttribute(
      "d",
      `M${x1},${y1} C${x1},${(y1 + y2) / 2} ${x2},${(y1 + y2) / 2} ${x2},${y2}`,
    );
    svg.append(path);
  }
  plane.append(svg);
  for (const node of graph.nodes) {
    const p = coords.get(node.id),
      button = el(
        "button",
        `fallback-node status-${statusClass(node.status)}${node.id === state.selectedNode ? " selected" : ""}`,
      );
    button.type = "button";
    button.style.left = `${p.x}%`;
    button.style.top = `${p.y}%`;
    button.append(
      el("span", "", compactNodeLabel(node)),
      el(
        "small",
        "",
        `${statusLabel(node.status)} · 入 ${node.in_degree ?? "—"} / 出 ${node.out_degree ?? "—"}`,
      ),
    );
    button.addEventListener("click", () => selectNode(node.id));
    plane.append(button);
  }
  shell.append(plane);
  target.replaceChildren(shell);
  $("render-mode").textContent = state.flat
    ? "平面依赖图 · 真实记录"
    : "CSS 3D 降级视图 · 真实记录";
  setText("interaction-hint", "点击节点查看 Skill 与结果 · 可滚动");
}
async function ensureScene() {
  if (state.flat) {
    renderFallback();
    return;
  }
  if (state.scene) {
    state.scene.setGraph(state.projection?.graph || {});
    state.scene.setSelected(state.selectedNode);
    return;
  }
  try {
    const module = await import("./scene.js");
    state.scene = module.createScene($("scene"), selectNode, (mode) => {
      if (mode === "lost") {
        state.scene?.destroy();
        state.scene = null;
        renderFallback();
        setText("render-mode", "WebGL 已中断 · CSS 3D 降级");
      } else setText("render-mode", "WebGL 3D · 本地渲染");
    });
    if (state.scene) {
      state.scene.setGraph(state.projection?.graph || {});
      state.scene.setSelected(state.selectedNode);
      setText("interaction-hint", "拖动旋转 · 滚轮缩放 · 点击节点");
    } else renderFallback();
  } catch (error) {
    renderFallback();
    setText("render-mode", "3D 渲染不可用 · CSS 3D 降级");
  }
}
function renderSource(target) {
  const source = state.projection.source || {},
    row = el("div", "source-binding");
  const parts = [];
  if (source.repository || source.url)
    parts.push(
      link(
        source.title || source.repository || source.url,
        source.repository || source.url,
        true,
      ),
    );
  if (source.commit)
    parts.push(el("span", "mono", `固定提交 ${source.commit}`));
  if (source.issue || source.issue_url)
    parts.push(link("原始问题 ↗", source.issue || source.issue_url, true));
  if (source.candidate_sha256)
    parts.push(el("span", "mono", `候选 SHA-256 ${source.candidate_sha256}`));
  if (!parts.length) return;
  parts.forEach((part, index) => {
    if (index) row.append(document.createTextNode(" · "));
    row.append(part);
  });
  target.append(row);
}
function renderResults(target) {
  renderSource(target);
  const suites = asArray(state.projection.metrics?.suites);
  if (!suites.length) {
    target.append(
      el("p", "empty-copy", "本次尚无测试套件结果。运行结束前不推算通过数量。"),
    );
    return;
  }
  const scroll = el("div", "table-scroll"),
    table = el("table"),
    head = el("thead"),
    headRow = el("tr");
  ["测试套件", "补全前", "补全后", "结论"].forEach((text) =>
    headRow.append(el("th", "", text)),
  );
  head.append(headRow);
  table.append(head);
  const body = el("tbody");
  for (const suite of suites) {
    const row = el("tr");
    row.append(
      el("td", "", suite.label || suite.id),
      el("td", "", suiteFraction(suite.before)),
      el("td", "result-change", suiteFraction(suite.after)),
    );
    const result = el("td");
    result.append(
      suite.after?.successful === true
        ? badge("passed")
        : suite.after?.successful === false
          ? badge("failed")
          : el("span", "muted", "未完成判定"),
    );
    row.append(result);
    body.append(row);
  }
  table.append(body);
  scroll.append(table);
  target.append(scroll);
  for (const evidence of asArray(state.projection.evidence)) {
    const details = document.createElement("details");
    details.className = "step-card";
    details.append(
      el("summary", "", evidence.label || "真实执行日志"),
      code(evidence.text || evidence.output || "日志未记录"),
    );
    target.append(details);
  }
  if (
    state.projection.verdict &&
    Object.keys(state.projection.verdict).length
  ) {
    const detail = document.createElement("details");
    detail.className = "step-card";
    detail.append(
      el("summary", "", "查看宿主验收结论字段"),
      code(state.projection.verdict),
    );
    target.append(detail);
  }
}
function renderCoverage(target) {
  const rows = asArray(state.projection.coverage);
  if (!rows.length) {
    empty(target, "报告尚未提供结构化需求覆盖记录。");
    return;
  }
  for (const item of rows) {
    const card = el("article", "coverage-card");
    const title = el("h3", "", item.id || item.requirement_id || "需求");
    if (item.status) title.append(badge(item.status));
    card.append(
      title,
      el(
        "p",
        "",
        item.requirement ||
          asArray(state.projection.requirements).find(
            (requirement) =>
              typeof requirement === "string" &&
              requirement.startsWith(`${item.requirement_id || item.id}:`),
          ) ||
          item.description ||
          item.text ||
          item.label ||
          item.title ||
          display(item),
      ),
    );
    if (item.evidence || item.tests || item.test_ids)
      card.append(
        el(
          "small",
          "",
          `验证证据：${display(item.evidence || item.tests || item.test_ids)}`,
        ),
      );
    if (item.result) card.append(el("p", "", `结果：${display(item.result)}`));
    target.append(card);
  }
}
function renderSteps(target) {
  const rows = asArray(state.projection.test_steps);
  if (!rows.length) {
    empty(target, "尚未产生可展示的自动化测试步骤。");
    return;
  }
  rows.forEach((step, index) => {
    if (typeof step === "string") {
      const card = el("article", "step-card");
      card.append(
        el("h3", "", `${String(index + 1).padStart(2, "0")} · 自动化测试步骤`),
        el("p", "", step),
      );
      target.append(card);
      return;
    }
    const card = el("article", "step-card"),
      heading = el(
        "h3",
        "",
        `${String(index + 1).padStart(2, "0")} · ${step.label || step.title || step.id || step.stage || "测试步骤"}`,
      );
    if (step.status) heading.append(badge(step.status));
    card.append(heading);
    for (const [key, label] of [
      ["description", "步骤"],
      ["command", "执行命令"],
      ["expected", "预期"],
      ["actual", "实际"],
      ["summary", "结果摘要"],
    ])
      if (step[key])
        card.append(el("p", "", `${label}：${display(step[key])}`));
    const output =
      step.output || step.stdout || step.stderr || step.failure_output;
    if (output) {
      const details = document.createElement("details");
      details.append(
        el("summary", "", "查看实际输出 / 失败日志"),
        code(output),
      );
      card.append(details);
    }
    if (
      !step.description &&
      !step.command &&
      !step.expected &&
      !step.actual &&
      !step.summary &&
      !output
    )
      card.append(code(step));
    target.append(card);
  });
}
function renderDiff(target) {
  const diff = state.projection.diff;
  if (!diff) {
    empty(target, "本次运行尚无可交付的代码差异。");
    return;
  }
  const pre = el("pre", "diff");
  String(diff)
    .split("\n")
    .forEach((line) => {
      const type = /^(\+\+\+|---|@@|diff )/.test(line)
        ? "diff-header"
        : line.startsWith("+")
          ? "diff-add"
          : line.startsWith("-")
            ? "diff-delete"
            : "";
      pre.append(el("span", `diff-line ${type}`, line));
    });
  target.append(pre);
}
function renderEvents(target) {
  const rows = asArray(state.projection.events);
  if (!rows.length) {
    empty(target, "尚无已记录的执行事件。");
    return;
  }
  const list = el("ol", "timeline");
  for (const event of rows.slice(-300)) {
    const row = el("li");
    row.append(
      el("time", "", date(event.time || event.timestamp)),
      el("span", "event-stage", event.agent || event.stage || "宿主"),
      el("p", "", event.message || event.event || display(event)),
    );
    list.append(row);
  }
  target.append(list);
  if (rows.length > 300)
    target.prepend(
      el(
        "p",
        "budget-note",
        `展示最近 300 / ${rows.length} 条事件；完整记录请下载 JSON。`,
      ),
    );
}
function renderModels(target) {
  const budget = state.projection.budget || {};
  target.append(
    el(
      "p",
      "budget-note",
      "模型名以请求记录和提供方返回为准；时间与 Token 的计划值不作为实测结果。仅展示调用摘要，不展示原始模型输入或隐藏验收测试源码。",
    ),
  );
  if (Object.keys(budget).length) {
    const details = document.createElement("details");
    details.className = "step-card";
    details.append(el("summary", "", "预算分配与实际用量"), code(budget));
    target.append(details);
  }
  const calls = asArray(state.projection.model_calls);
  if (!calls.length) {
    target.append(el("p", "empty-copy", "尚无实际模型调用记录。"));
    return;
  }
  const table = el("table"),
    head = el("tr");
  [
    "序号 / Skill",
    "请求模型",
    "提供方返回模型",
    "耗时 / Token",
    "状态",
  ].forEach((label) => head.append(el("th", "", label)));
  const thead = el("thead");
  thead.append(head);
  table.append(thead);
  const body = el("tbody");
  calls.forEach((call, index) => {
    const row = el("tr");
    row.append(
      el(
        "td",
        "",
        `${index + 1} · ${call.skill_id || call.skill || call.stage || call.agent || "模型调用"}`,
      ),
      el("td", "", call.requested_model || call.model || "未记录"),
      el("td", "", call.actual_model || call.served_model || "未报告"),
      el(
        "td",
        "",
        `${number(call.elapsed_seconds ?? call.duration_s, 2)} s / ${number(call.reported_tokens ?? call.total_tokens ?? call.usage?.total_tokens)}`,
      ),
    );
    const cell = el("td");
    cell.append(
      call.status ? badge(call.status) : el("span", "muted", "见完整报告"),
    );
    row.append(cell);
    body.append(row);
  });
  table.append(body);
  const scroll = el("div", "table-scroll");
  scroll.append(table);
  target.append(scroll);
}
function renderEvidence() {
  const target = $("evidence-content");
  document.querySelectorAll(".evidence-tabs button").forEach((button) => {
    const active = button.dataset.tab === state.tab;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
  });
  target.setAttribute("aria-labelledby", `tab-${state.tab}`);
  target.hidden = state.tab === "probe";
  $("probe-panel").hidden = state.tab !== "probe";
  updateProbe();
  if (state.tab === "probe") return;
  if (!state.projection) {
    empty(target, "尚未选择运行。");
    return;
  }
  target.replaceChildren();
  (
    ({
      results: renderResults,
      coverage: renderCoverage,
      steps: renderSteps,
      diff: renderDiff,
      events: renderEvents,
      models: renderModels,
    })[state.tab] || renderResults
  )(target);
}
function renderRun() {
  // Keep the selected history label and admission gate aligned with the
  // projection, including a terminal run opened after it finished elsewhere.
  const index = asArray(state.bootstrap?.runs).findIndex(
    (item) => item.id === state.projection.run.id,
  );
  if (index >= 0) {
    state.bootstrap.runs[index] = {
      ...state.bootstrap.runs[index],
      ...state.projection.run,
    };
    const option = [...$("history").options].find(
      (item) => item.value === state.projection.run.id,
    );
    if (option)
      option.textContent = runOption(state.bootstrap.runs[index]).label;
  }
  renderMode();
  renderMetrics();
  renderDownloads();
  renderWarnings();
  renderVerdict();
  renderPostchecks();
  $("graph-empty").hidden =
    normalizeGraph(state.projection.graph).nodes.length > 0;
  if (
    !state.selectedNode ||
    !normalizeGraph(state.projection.graph).nodes.some(
      (node) => node.id === state.selectedNode,
    )
  )
    state.selectedNode =
      normalizeGraph(state.projection.graph).nodes.find(
        (node) => node.status === "running",
      )?.id || normalizeGraph(state.projection.graph).nodes[0]?.id;
  ensureScene();
  renderInspector();
  renderEvidence();
  updateStart();
}
async function loadRun(id, { poll = false } = {}) {
  if (!parseRunId(id)) {
    showError("global-error", "运行编号格式无效，未请求任何文件或路径。");
    return;
  }
  if (!poll) {
    state.pollEpoch++;
    clearTimeout(state.pollTimer);
    state.runId = id;
    const runUrl = new URL(location.href);
    runUrl.searchParams.set("run", id);
    history.replaceState(null, "", runUrl);
    $("history").value = id;
    state.selectedNode = null;
    resetProbe();
  }
  const epoch = state.pollEpoch;
  try {
    const projection = await api(`/api/lab/runs/${encodeURIComponent(id)}`);
    if (epoch !== state.pollEpoch || state.runId !== id) return;
    state.projection = projection;
    connection(true, "本地服务已连接");
    showError("global-error", null);
    renderRun();
    if (
      !isTerminal(projection.run.status) &&
      transientStatuses.has(projection.run.status)
    )
      state.pollTimer = setTimeout(() => loadRun(id, { poll: true }), 2000);
    else {
      clearTimeout(state.pollTimer);
      if (poll) loadBootstrap();
    }
  } catch (error) {
    if (epoch !== state.pollEpoch) return;
    connection(false, "读取运行失败");
    showError(
      "global-error",
      `运行 ${id.slice(0, 8)} 读取失败：${error.message}`,
    );
    if (transientStatuses.has(state.projection?.run?.status))
      state.pollTimer = setTimeout(() => loadRun(id, { poll: true }), 5000);
  }
}

function updateProbe() {
  const run = state.projection?.run,
    allowed = run?.can_probe === true;
  $("probe-run").disabled = !allowed || state.probing;
  $("probe-input").disabled = state.probing;
  $("probe-mode").disabled = state.probing;
  setText(
    "probe-note",
    state.probing
      ? "正在执行原版与修复版 CLI 子进程，请稍候…"
      : allowed
        ? state.projection?.postchecks?.status === "failed"
          ? "该候选追加检查未通过；本探针只验证当前输入，不代表整体功能正确。这次操作不会调用大模型。"
          : "输出绑定本次通过原注册验收的候选 wheel；这次操作不会调用大模型。"
        : "仅通过验收、且交付证据完整的 rich-cli 运行可用。",
  );
}
function resetProbe() {
  setText("probe-baseline-code", "尚未执行");
  setText("probe-candidate-code", "尚未执行");
  setText("probe-baseline", "等待你的测试输入。");
  setText("probe-candidate", "等待你的测试输入。");
  setText("probe-receipt", "");
  showError("probe-error", null);
}
function stripAnsi(value) {
  return String(value || "")
    .replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, "")
    .replace(/\x1b\][^\x07]*(?:\x07|\x1b\\)/g, "");
}
function renderProbeOutput(side, result = {}) {
  setText(
    `probe-${side}-code`,
    `退出码 ${result.exit_code ?? "未报告"}${result.timeout ? " · 超时" : ""}${result.output_limit_exceeded ? " · 输出截断" : ""}${result.binding_ok === false ? " · 绑定校验失败" : ""}`,
  );
  const output = [
    result.stdout
      ? `STDOUT\n${stripAnsi(result.stdout)}`
      : "STDOUT\n（无标准输出）",
    result.stderr ? `STDERR\n${stripAnsi(result.stderr)}` : "",
  ]
    .filter(Boolean)
    .join("\n\n");
  setText(`probe-${side}`, output);
}
async function runProbe() {
  if (state.probing || !state.projection?.run?.can_probe) return;
  const text = $("probe-input").value;
  if (!text.trim()) {
    showError("probe-error", "请输入要测试的内容。");
    return;
  }
  const runId = state.runId;
  state.probing = true;
  updateProbe();
  showError("probe-error", null);
  try {
    const result = await api(
      `/api/lab/runs/${encodeURIComponent(runId)}/probe`,
      {
        method: "POST",
        body: JSON.stringify({ text, mode: $("probe-mode").value }),
      },
    );
    if (state.runId !== runId) return;
    renderProbeOutput("baseline", result.baseline);
    renderProbeOutput("candidate", result.candidate);
    setText(
      "probe-receipt",
      `探针 ${result.receipt?.id || result.id || "未报告"} · SHA-256 ${result.receipt?.sha256 || "未报告"}\n${result.note || "真实子进程结果；人工探针不计入原始验收分数。"}`,
    );
  } catch (error) {
    if (state.runId === runId)
      showError("probe-error", `人工探针执行失败：${error.message}`);
  } finally {
    state.probing = false;
    updateProbe();
  }
}
async function submitRun(event) {
  event.preventDefault();
  if (state.submitting) return;
  showError("form-error", null);
  try {
    const payload = makeRunRequest({
      project: $("project").value,
      provider: $("provider").value,
      model: $("model").value,
      requirement: $("requirement").value,
      slots: Number($("slots").value),
      rounds: Number($("rounds").value),
      minutes: Number($("wall-minutes").value),
      tokens: Number($("tokens").value),
    });
    if (!provider()?.configured) throw new Error("所选真实模型服务尚未配置。");
    state.submitting = true;
    updateStart();
    const result = await api("/api/project-runs", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    state.startedHere = result.id;
    await loadBootstrap();
    await loadRun(result.id);
    $("scene-title").scrollIntoView({ behavior: "smooth", block: "center" });
  } catch (error) {
    showError("form-error", `无法启动真实实验：${error.message}`);
  } finally {
    state.submitting = false;
    updateStart();
  }
}

$("run-form").addEventListener("submit", submitRun);
$("project").addEventListener("change", () => updateProject(true));
$("provider").addEventListener("change", updateProvider);
$("load-history").addEventListener("click", () => loadRun($("history").value));
$("refresh").addEventListener("click", async () => {
  await loadBootstrap();
  if (state.runId) await loadRun(state.runId);
});
$("reset-view").addEventListener("click", () => {
  state.scene?.reset();
  if (state.flat) renderFallback();
});
$("toggle-view").addEventListener("click", () => {
  state.flat = !state.flat;
  $("graph-shell").classList.toggle("flat-view", state.flat);
  $("toggle-view").textContent = state.flat ? "切换 3D" : "切换平面";
  state.scene?.destroy();
  state.scene = null;
  ensureScene();
});
document.querySelectorAll(".evidence-tabs button").forEach((button) => {
  button.addEventListener("click", () => {
    state.tab = button.dataset.tab;
    renderEvidence();
  });
  button.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const tabs = [...document.querySelectorAll(".evidence-tabs button")];
    const index = tabs.indexOf(button),
      next =
        event.key === "Home"
          ? 0
          : event.key === "End"
            ? tabs.length - 1
            : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) %
              tabs.length;
    tabs[next].click();
    tabs[next].focus();
  });
});
$("probe-run").addEventListener("click", runProbe);
$("probe-mode").addEventListener("change", () => {
  $("probe-input").value = probeSamples[$("probe-mode").value];
  $("probe-mode-note").hidden=$("probe-mode").value!=="csv_head_tail";
  resetProbe();
});
$("probe-input").addEventListener("input", resetProbe);
window.addEventListener("beforeunload", () => {
  clearTimeout(state.pollTimer);
  state.scene?.destroy();
});
loadBootstrap({ initial: true });
