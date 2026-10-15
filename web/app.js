"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const state = {health:null, tasks:[], projects:[], projectProviders:[], projectDefaults:{}, projectSubmitting:false, projectReady:false, runs:[], run:null, taskId:null, tab:"tests", selection:new Set(), submitting:false, loading:false, polling:null, detailVersion:0, failures:0, toastTimer:null};
  const ACTIVE = new Set(["pending", "queued", "running", "in_progress"]);
  const STATUS = {pending:["等待执行","neutral"],queued:["排队中","neutral"],running:["执行中","running"],awaiting_delivery:["等待构建与HTTP验收","neutral"],in_progress:["执行中","running"],completed:["已完成","success"],complete:["已完成","success"],accepted:["验收通过","success"],passed:["通过","success"],success:["成功","success"],rejected:["未通过验收","warning"],failed:["执行失败","danger"],error:["执行失败","danger"],interrupted:["运行中断","warning"],cancelled:["已取消","neutral"],skipped:["已跳过","neutral"]};
  const PROVIDERS = {replay:"离线回放", codex_session:"GPT-5.6 Sol · Codex 会话生成", codex_cli:"Codex CLI · 真实模型", openai:"兼容 OpenAI 的模型", ollama:"Ollama 本地模型", agentarts:"华为 AgentArts"};
  const CAPABILITIES = [["plan-workflow","规划与编排"],["explore-repository","探索工程"],["ground-code","代码定位"],["analyze-requirements","需求分析"],["generate-tests","生成测试"],["diagnose-results","诊断结果"],["repair-code","补全代码"],["review-patch","独立评审"],["verify-delivery","验证交付"]];
  const AGENTS = {requirements:"需求分析 Agent",planner:"需求分析 Agent",analyst:"需求分析 Agent",analysis:"需求分析 Agent",diagnosis:"缺口诊断 Agent",test:"测试 Agent",tester:"测试生成 Agent",test_designer:"测试生成 Agent",testgen:"测试生成 Agent",developer:"补全 Agent",repair:"补全 Agent",coder:"补全 Agent",repairer:"补全 Agent",review:"评审 Agent",reviewer:"评审 Agent",verifier:"独立验证 Agent",validator:"独立验证 Agent",supervisor:"监督编排器",orchestrator:"编排器",system:"系统"};

  function node(tag, className, text) {
    const result = document.createElement(tag);
    if (className) result.className = className;
    if (text !== undefined && text !== null) result.textContent = String(text);
    return result;
  }
  function clear(element) { element.replaceChildren(); }
  function stringify(value) { return typeof value === "string" ? value : value === undefined || value === null ? "" : JSON.stringify(value, null, 2); }
  function status(element, value) {
    const info = STATUS[value] || [value || "等待执行", "neutral"];
    element.className = "status-badge " + info[1];
    element.textContent = info[0];
  }
  function labelProvider(id) { return state.projectProviders.find((item) => item.id === id)?.label || state.health?.providers?.find((item) => item.id === id)?.label || PROVIDERS[id] || id || "未知模式"; }
  function isProjectRun(run) { return run?.kind === "autonomous_project"; }
  function labelRunner(run) { return isProjectRun(run) ? "自主完整工程" : run?.runner === "project_process" ? "完整工程进程" : run?.runner === "docker" ? "Docker 容器" : "受限 Python 进程"; }
  function activeRun() { return (state.run && ACTIVE.has(state.run.status) && state.run) || state.runs.find((run) => ACTIVE.has(run.status)); }
  function formatDate(value, timeOnly = false) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return new Intl.DateTimeFormat("zh-CN", {timeZone:"Asia/Shanghai", ...(timeOnly ? {} : {month:"2-digit",day:"2-digit"}),hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false}).format(date);
  }
  function duration(value) {
    const seconds = Number(value);
    if (!Number.isFinite(seconds) || value === null || value === undefined) return "—";
    return seconds < 60 ? seconds.toFixed(1) + " 秒" : Math.floor(seconds / 60) + " 分 " + Math.round(seconds % 60) + " 秒";
  }
  function getError(value) {
    if (!value) return "";
    if (typeof value === "string") return value;
    if (Array.isArray(value)) return value.map((item) => item.msg || stringify(item)).join("；");
    return value.message || value.detail || stringify(value);
  }
  function showError(message) { $("global-error").textContent = message; $("global-error").hidden = !message; }
  function toast(message) {
    clearTimeout(state.toastTimer);
    $("toast").textContent = message;
    $("toast").hidden = false;
    state.toastTimer = setTimeout(() => { $("toast").hidden = true; }, 3500);
  }
  async function api(url, options) {
    const response = await fetch(url, {...options, headers:{"Content-Type":"application/json",...options?.headers},cache:"no-store"});
    let data;
    try { data = await response.json(); } catch { throw new Error("服务返回了无法读取的响应（HTTP " + response.status + "）"); }
    if (!response.ok) throw new Error(getError(data.detail || data.error) || "请求失败（HTTP " + response.status + "）");
    return data;
  }

  function updateProjectConfiguration() {
    const project = state.projects.find((item) => item.id === $("project-select").value);
    const provider = state.projectProviders.find((item) => item.id === $("project-provider-select").value);
    const source = project?.source;
    $("project-source-note").textContent = project ? (project.description ? project.description + " · " : "") + "来源：" + (typeof source === "string" ? source : source?.name || source?.repository || source?.url || project.id) : "暂无可运行的完整工程，请检查本地工程配置。";
    $("project-provider-note").textContent = provider?.configured ? "真实模型调用 · " + (provider.model || provider.label || PROVIDERS[provider.id] || provider.id) + "。执行失败会保留错误与日志，不会自动回退为回放。" : "未找到已配置的真实模型服务；配置后刷新即可启动。";
    updateProjectStartButton();
  }
  function updateProjectStartButton() {
    const active = activeRun();
    const configured = state.projectProviders.find((item) => item.id === $("project-provider-select").value)?.configured;
    const validBudget = ["project-wall-minutes","project-token-limit","project-model-slots","project-cpu-slots"].every((id) => $(id).checkValidity());
    $("project-start-button").disabled = !state.projectReady || !$("project-select").value || !configured || !validBudget || state.projectSubmitting || state.submitting || state.loading || Boolean(active);
    $("project-start-button").firstElementChild.textContent = state.projectSubmitting ? "正在创建运行…" : active ? "工程验证执行中…" : "启动完整工程";
    $("project-start-note").textContent = active ? "已有运行执行中 · 完成后可启动下一次" : !validBudget ? "请填写有效预算：时间、Token 与并发槽位均为正整数" : !configured ? "需要已配置的真实模型服务 · 刷新可重新读取" : "均衡策略 · 独立工程快照 · 点击后开始真实模型调用";
  }
  function loadProjectConfiguration(data, initial) {
    const previousProject = $("project-select").value;
    const previousProvider = $("project-provider-select").value;
    state.projects = data.projects || [];
    state.projectProviders = (data.providers || []).filter((item) => !["replay","codex_session"].includes(item.id));
    state.projectDefaults = data.defaults || {};
    clear($("project-select"));
    for (const project of state.projects) {
      const option = node("option", "", project.title || project.id);
      option.value = project.id;
      option.disabled = project.available === false;
      $("project-select").append(option);
    }
    if (!state.projects.length) $("project-select").append(node("option", "", "暂无可用工程"));
    const preferredProject = initial ? state.projectDefaults.project_id : previousProject;
    const project = state.projects.find((item) => item.id === preferredProject && item.available !== false) || state.projects.find((item) => item.available !== false);
    $("project-select").value = project?.id || "";
    $("project-select").disabled = !project;
    clear($("project-provider-select"));
    for (const provider of state.projectProviders) {
      const option = node("option", "", (provider.label || PROVIDERS[provider.id] || provider.id) + (provider.configured ? "" : " · 未配置"));
      option.value = provider.id;
      option.disabled = !provider.configured;
      $("project-provider-select").append(option);
    }
    if (!state.projectProviders.length) $("project-provider-select").append(node("option", "", "暂无真实模型服务"));
    const preferredProvider = initial ? state.projectDefaults.provider : previousProvider;
    const provider = state.projectProviders.find((item) => item.id === preferredProvider && item.configured) || state.projectProviders.find((item) => item.configured);
    $("project-provider-select").value = provider?.id || "";
    $("project-provider-select").disabled = !provider;
    if (initial) {
      const defaults = state.projectDefaults.budget || {};
      if (defaults.wall_seconds) $("project-wall-minutes").value = Math.max(1, Math.round(defaults.wall_seconds / 60));
      for (const [key,id] of [["token_limit","project-token-limit"],["model_slots","project-model-slots"],["cpu_slots","project-cpu-slots"]]) if (defaults[key]) $(id).value = defaults[key];
      if (state.projectDefaults.max_rounds) $("project-rounds-select").value = state.projectDefaults.max_rounds;
    }
    state.projectReady = true;
    $("project-config-error").hidden = true;
    updateProjectConfiguration();
  }

  function updateConfiguration() {
    const provider = $("provider-select").value;
    const replay = provider === "replay";
    $("provider-note").className = "mode-note" + (replay ? "" : " real");
    $("provider-note").textContent = replay ? "离线回放使用预写补丁，只验证流程，不计入真实大模型补全成绩。" : "真实模型调用 · 测试与需求作为证据，独立验收测试对补全 Agent 隐藏。";
    $("runner-note").textContent = $("runner-select").value === "docker" ? "Docker 容器 · 独立环境执行" : "受限 Python 子集 · 开发验证（非通用沙箱），本地独立进程执行。";
    updateStartButton();
  }
  function updateStartButton() {
    const active = activeRun();
    const configured = state.health?.providers?.find((item) => item.id === $("provider-select").value)?.configured;
    $("start-button").disabled = state.submitting || state.projectSubmitting || state.loading || Boolean(active) || state.selection.size === 0 || !state.health || !configured;
    $("start-button").firstElementChild.textContent = state.submitting ? "正在启动…" : active ? "验证执行中…" : "启动验证";
    $("selection-note").textContent = active ? "当前运行完成后，可启动下一次验证" : state.selection.size ? "已选择 " + state.selection.size + " 项任务 · 独立验收与回归自动执行" : "选择任务后开始验证";
    $("select-all-button").disabled = !state.tasks.length || state.submitting;
    $("select-all-button").textContent = state.tasks.length && state.selection.size === state.tasks.length ? "取消全选" : "全选任务";
    updateProjectStartButton();
  }
  function renderTaskSelector() {
    const container = $("task-selector");
    clear(container);
    $("task-count").textContent = state.tasks.length ? "/ " + state.tasks.length + " 项" : "/ 待导入";
    if (!state.tasks.length) {
      const empty = node("div", "selector-empty");
      empty.append(node("strong", "", "公开任务尚未导入"), node("span", "", "请先按项目文档导入 GitHub 基准数据，再点击右上角刷新。"));
      container.append(empty);
    }
    for (const task of state.tasks) {
      const label = node("label", "task-option");
      const checkbox = node("input");
      checkbox.type = "checkbox";
      checkbox.checked = state.selection.has(task.id);
      checkbox.value = task.id;
      checkbox.addEventListener("change", () => { checkbox.checked ? state.selection.add(task.id) : state.selection.delete(task.id); updateStartButton(); });
      const content = node("div");
      content.append(node("strong", "", task.title || task.id), node("small", "", task.id));
      if (task.kind) content.append(node("small", "task-kind", {humaneval:"HumanEval · 函数补全",repository:"仓库 · 功能补全"}[task.kind] || task.kind));
      label.append(checkbox, content);
      container.append(label);
    }
    const sources = [...new Set(state.tasks.map((task) => typeof task.source === "string" ? task.source : task.source?.name || task.source?.repository || task.source?.url).filter(Boolean))];
    $("source-description").textContent = sources.length ? "当前任务来源：" + sources.join("；") + "。报告保留各任务的来源信息。" : "公开任务尚未导入。数据导入后显示来源，运行报告保留出处。";
    updateStartButton();
  }
  async function loadConfiguration(initial = false) {
    const results = await Promise.allSettled([api("/api/health"), api("/api/tasks"), api("/api/runs"), api("/api/projects")]);
    const errors = [];
    if (results[0].status === "fulfilled") {
      state.health = results[0].value;
      const previousProvider = $("provider-select").value;
      const previousRunner = $("runner-select").value;
      clear($("provider-select"));
      for (const provider of state.health.providers || []) {
        const providerLabel = provider.label || PROVIDERS[provider.id] || provider.id;
        const suffix = provider.id === "codex_session" ? " · 从已保存会话导入" : provider.configured || providerLabel.includes("未配置") ? "" : " · 未配置";
        const option = node("option", "", providerLabel + suffix);
        option.value = provider.id;
        option.disabled = !provider.configured;
        $("provider-select").append(option);
      }
      const preferred = initial ? state.health.default_provider : previousProvider;
      const usable = state.health.providers?.find((item) => item.id === preferred && item.configured) || state.health.providers?.find((item) => item.configured);
      if (usable) $("provider-select").value = usable.id;
      $("provider-select").disabled = !usable;
      const dockerOption = [...$("runner-select").options].find((option) => option.value === "docker");
      dockerOption.disabled = !state.health.docker_available;
      dockerOption.textContent = state.health.docker_available ? "Docker 容器" : "Docker 容器 · 不可用";
      const desiredRunner = initial ? state.health.default_runner : previousRunner;
      $("runner-select").value = desiredRunner === "docker" && state.health.docker_available ? "docker" : "process";
      $("runner-select").disabled = false;
      $("connection-dot").className = "connection-dot online";
      $("connection-label").textContent = "本地服务已连接";
      $("version-label").textContent = "TestFix-Agent " + (state.health.version || "");
      $("footer-version").textContent = state.health.version ? "v" + state.health.version : "LOCAL WORKSPACE";
      const agentarts = state.health.providers?.find((item) => item.id === "agentarts");
      $("agentarts-description").textContent = agentarts?.configured ? "AgentArts 接口已配置。选择华为 AgentArts 模式后，以真实运行日志和测试结果验证接入。" : "AgentArts 接口尚未配置。当前本地流程可验证工程，不能据此宣称已完成赛题平台接入。";
      updateConfiguration();
    } else {
      state.health = null;
      $("connection-dot").className = "connection-dot offline";
      $("connection-label").textContent = "本地服务连接失败";
      errors.push("无法读取服务配置：" + results[0].reason.message);
    }
    if (results[1].status === "fulfilled") {
      state.tasks = results[1].value.tasks || [];
      const available = new Set(state.tasks.map((task) => task.id));
      state.selection = new Set([...state.selection].filter((id) => available.has(id)));
      if (initial && state.tasks.length) state.selection.add(state.tasks[0].id);
      renderTaskSelector();
    } else {
      clear($("task-selector"));
      $("task-selector").append(node("div", "selector-empty", "任务加载失败，请确认本地服务已启动，再刷新页面。"));
      errors.push("无法读取任务：" + results[1].reason.message);
    }
    if (results[2].status === "fulfilled") {
      state.runs = results[2].value.runs || [];
      renderHistory();
    } else errors.push("无法读取运行记录：" + results[2].reason.message);
    if (results[3].status === "fulfilled") loadProjectConfiguration(results[3].value, initial);
    else {
      state.projectReady = false;
      $("project-config-error").textContent = "完整工程配置读取失败：" + results[3].reason.message + "。可点击右上角刷新重试。";
      $("project-config-error").hidden = false;
      $("project-source-note").textContent = "工程配置暂不可用；已有运行记录仍可查看。";
    }
    showError(errors.join("\n"));
    updateStartButton();
    if (initial && state.runs.length) {
      const latest = state.runs.find((run) => ACTIVE.has(run.status)) || state.runs[0];
      await selectRun(latest.id, false);
    }
  }

  function renderHistory() {
    const body = $("history-body");
    clear(body);
    $("history-count").textContent = state.runs.length + " 次运行";
    if (!state.runs.length) {
      const row = node("tr");
      const cell = node("td", "table-empty", "还没有运行记录，启动第一项任务后会自动保存。 ");
      cell.colSpan = 7;
      row.append(cell); body.append(row); return;
    }
    for (const run of state.runs) {
      const row = node("tr", state.run?.id === run.id ? "history-selected" : "");
      const identity = node("td");
      identity.append(node("span", "run-id", run.id), node("span", "run-time", formatDate(run.created_at)));
      const mode = node("td");
      mode.append(node("span", "history-mode", labelProvider(run.provider)), node("span", "mode-small", (isProjectRun(run) ? "自主运行 · 真实模型" : run.provider === "replay" ? "回放 · 非模型评分" : run.provider === "codex_session" ? "会话生成 · 本地评测" : "真实模型 API") + " / " + labelRunner(run)));
      const summary = run.summary || {};
      const accepted = node("td", "", summary.total === undefined ? "—" : (summary.accepted ?? 0) + " / " + summary.total);
      const regressions = node("td", "", summary.regressions ?? "—");
      const time = node("td", "", duration(summary.duration_s));
      const statusCell = node("td");
      const badge = node("span"); status(badge, run.status); statusCell.append(badge);
      const action = node("td");
      const button = node("button", "table-detail-button", "查看 ↗");
      button.addEventListener("click", () => selectRun(run.id, true));
      action.append(button);
      row.append(identity, mode, accepted, regressions, time, statusCell, action); body.append(row);
    }
  }
  function runStats() {
    const run = state.run;
    const summary = run?.summary || {};
    const tasks = run?.tasks || [];
    const total = summary.total ?? tasks.length;
    const accepted = summary.accepted ?? tasks.filter((task) => task.status === "accepted").length;
    $("metric-total").textContent = total;
    $("metric-accepted").textContent = accepted;
    $("metric-regressions").textContent = summary.regressions ?? "—";
    $("metric-regressions").closest(".metric").classList.toggle("has-regression", Number(summary.regressions) > 0);
    $("metric-rate").textContent = total ? "任务验证通过率 " + Math.round(accepted / total * 100) + "%" : "等待独立验收结果";
    $("metric-mode").textContent = isProjectRun(run) ? "自主完整工程 · " + labelProvider(run.provider) : run?.runner === "project_process" ? "GPT-5.6 · 完整工程验证" : run?.provider === "replay" ? "离线回放 · 非模型评分" : run?.provider === "codex_session" ? "GPT-5.6 会话生成 · 本地评测" : "真实大模型 API · " + labelProvider(run?.provider);
    let seconds = summary.duration_s;
    if (ACTIVE.has(run?.status) && run?.created_at) {
      const elapsed = (Date.now() - new Date(run.created_at).getTime()) / 1000;
      if (Number.isFinite(elapsed)) seconds = Math.max(0, elapsed);
    }
    $("metric-duration").textContent = seconds === null || seconds === undefined ? "—" : Number(seconds).toFixed(1);
    $("metric-status").textContent = STATUS[run?.status]?.[0] || run?.status || "等待执行";
  }
  function renderProjectWorkflow(run) {
    const workflow = run.workflow || {};
    const plan = run.plan || workflow.plan;
    const capabilities = Array.isArray(workflow.capabilities) ? workflow.capabilities : [];
    const calls = Array.isArray(run.model_calls) ? run.model_calls : [];
    const measuredCalls = calls.filter((call) => call.status === "completed" || call.status === "success" || call.status === "succeeded");
    $("project-model-label").textContent = labelProvider(run.provider);
    $("project-execution-note").textContent = "能力状态来自实际执行记录；规划通过不代表工程验收通过。" + (calls.length ? " 已记录 " + calls.length + " 次模型调用。" : " 尚无模型调用记录。");
    clear($("project-capabilities"));
    for (const [id,label] of CAPABILITIES) {
      const capability = capabilities.find((item) => item.skill_id === id || item.id === id);
      const card = node("div", "capability-card");
      const heading = node("div", "capability-heading");
      heading.append(node("strong", "", label));
      const badge = node("span");
      const value = capability?.status;
      if (["completed","complete","success","succeeded"].includes(value)) {
        badge.className = "status-badge success";
        badge.textContent = "已执行";
      } else if (["planned","plan_valid","validated","ready"].includes(value)) {
        badge.className = "status-badge neutral";
        badge.textContent = "待执行";
      } else status(badge, value || "pending");
      heading.append(badge);
      card.append(heading, node("small", "capability-id", id));
      const attempt = capability?.attempt ?? capability?.attempts;
      const attempts = Array.isArray(attempt) ? attempt.length : attempt;
      const timing = capability?.duration_s;
      card.append(node("span", "capability-meta", capability ? [(attempts ? "第 " + attempts + " 次" : ""), (timing !== undefined && timing !== null ? "实测 " + duration(timing) : "")].filter(Boolean).join(" · ") || "等待执行证据" : "等待执行记录"));
      if (capability?.error) card.append(node("p", "capability-error", getError(capability.error)));
      if (capability?.artifact) {
        const artifact = node("span", "capability-artifact", "产物：" + stringify(capability.artifact));
        artifact.title = stringify(capability.artifact);
        card.append(artifact);
      }
      $("project-capabilities").append(card);
    }
    const solver = plan?.solver_status || plan?.status;
    $("project-solver-status").className = "status-badge neutral";
    const solverNames = {optimal:"已求得最优计划",feasible:"已求得可行计划",infeasible:"预算内无可行计划",unknown:"求解结果未知",model_invalid:"规划模型无效",not_solved:"尚未求解",failed:"规划失败"};
    $("project-solver-status").textContent = solver ? solverNames[String(solver).toLowerCase()] || String(solver) : "等待求解";
    const planDetailsOpen = $("project-plan").querySelector("details")?.open || false;
    clear($("project-plan"));
    const selected = Array.isArray(plan?.selected) ? plan.selected : Array.isArray(plan?.stages) ? plan.stages : [];
    if (selected.length) {
      const scroll = node("div", "table-scroll");
      const table = node("table", "plan-table");
      const head = node("thead"); const headings = node("tr");
      for (const label of ["能力 / 策略","预计开始","预计结束","累计 Token 预算估计"]) headings.append(node("th", "", label));
      head.append(headings);
      const body = node("tbody");
      for (const item of selected) {
        const row = node("tr"); const identity = node("td");
        const skillId = item.skill_id || item.id;
        identity.append(node("strong", "", CAPABILITIES.find(([id]) => id === skillId)?.[1] || skillId || "未命名能力"), node("small", "", item.strategy || "—"));
        const time = (value) => value !== undefined && value !== null ? duration(Number(value) / 1000) : "—";
        row.append(identity, node("td", "", time(item.start_ms)), node("td", "", time(item.end_ms)), node("td", "", item.token_cap !== undefined && item.token_cap !== null ? Number(item.token_cap).toLocaleString("zh-CN") : "—"));
        body.append(row);
      }
      table.append(head,body); scroll.append(table); $("project-plan").append(scroll);
    } else $("project-plan").append(node("div", "plan-empty", plan ? "尚无可执行计划。请查看求解状态及运行日志。" : "编排器正在根据时间、Token 和并发预算求解执行计划。"));
    if (plan) {
      const details = node("details", "plan-details");
      details.open = planDetailsOpen;
      details.append(node("summary", "", "查看求解器、约束与预算估计"), node("pre", "", stringify(plan)));
      $("project-plan").append(details);
    }
    const budget = workflow.budget || run.budget || {};
    const requested = budget.requested || budget.limits || run.budget || budget;
    const actual = budget.actual || budget.measured || budget;
    const tokenUsage = actual.tokens_used ?? actual.total_tokens;
    const elapsed = actual.elapsed_s ?? actual.duration_s ?? run.summary?.duration_s;
    const slots = [requested.model_slots !== undefined ? requested.model_slots + " 个模型槽位" : "",requested.cpu_slots !== undefined ? requested.cpu_slots + " 个 CPU 槽位" : ""].filter(Boolean).join(" / ") || "未记录资源上限";
    const wallLimit = requested.wall_seconds;
    const tokenLimit = requested.token_limit;
    clear($("project-budget"));
    for (const [label,value,note] of [
      ["实测运行耗时", elapsed !== undefined && elapsed !== null ? duration(elapsed) : "等待实测", wallLimit !== undefined ? "时间上限 " + duration(wallLimit) : "时间上限未记录"],
      ["实测模型 Token", tokenUsage !== undefined && tokenUsage !== null ? Number(tokenUsage).toLocaleString("zh-CN") : "未上报", tokenLimit !== undefined ? "预算上限 " + Number(tokenLimit).toLocaleString("zh-CN") + " Token" : "Token 上限未记录"],
      ["真实模型调用", calls.length + " 次", calls.length ? "已完成 " + measuredCalls.length + " 次 · 详情保留原始状态" : "调用发生后显示记录"],
      ["并发资源上限", requested.model_slots !== undefined ? requested.model_slots + " / " + (requested.cpu_slots ?? "—") : "—", slots]
    ]) {
      const card = node("div", "project-budget-item");
      card.append(node("span", "", label), node("strong", "", value), node("small", "", note));
      $("project-budget").append(card);
    }
    $("project-call-count").textContent = "计划估计与实测分开记录";
    const callEvidence = calls.map((call) => ({skill_id:call.skill_id,status:call.status,provider:call.provider,requested_model:call.requested_model,actual_model:call.actual_model ?? "服务未回报模型名",model_evidence:call.model_evidence,duration_s:call.duration_s,usage:call.usage ?? "未上报",error:call.error,artifact_dir:call.artifact_dir}));
    $("project-telemetry-content").textContent = stringify({provider:run.provider,model_calls:callEvidence,budget,source:run.source || run.tasks?.[0]?.source || null,note:"完整模型请求、响应与产物见 JSON 报告及工程证据 ZIP。"});
  }
  function renderRun() {
    const run = state.run;
    $("run-empty").hidden = Boolean(run);
    $("run-content").hidden = !run;
    $("project-workflow").hidden = !isProjectRun(run);
    $("download-project").hidden = !(isProjectRun(run) && run.status === "completed" && run.download);
    if (!run) return;
    const runSources = [...new Set((run.tasks || []).map((task) => {
      const source = task.source;
      return typeof source === "string" ? source : source?.repository || source?.url || source?.name;
    }).filter(Boolean))];
    if (runSources.length) $("source-description").textContent = "当前运行来源：" + runSources.join("；") + "。固定版本和功能需求见本次报告。";
    if (isProjectRun(run)) {
      $("download-project").href = "/api/project-runs/" + encodeURIComponent(run.id) + "/download";
      $("download-project").setAttribute("download", "testfix-project-" + run.id + ".zip");
      renderProjectWorkflow(run);
    }
    status($("run-status"), run.status);
    $("run-caption").textContent = run.id + " · " + labelProvider(run.provider) + " · " + labelRunner(run) + " · " + formatDate(run.created_at);
    $("replay-banner").hidden = !["replay", "codex_session"].includes(run.provider);
    $("replay-banner").textContent = run.runner === "project_process"
      ? "完整 Flaskr 工程：GPT-5.6 在本次会话中生成工程测试并修改应用；本地执行原有测试、独立验收、wheel 构建及真实 HTTP 验证。模型生成与评测分阶段执行。"
      : run.provider === "codex_session"
      ? "本次 GPT-5.6 Sol 会话真实生成测试和补丁；本地执行器读取已保存输出验证。尚未直接接通模型 API，显示耗时为本地评测阶段。"
      : "离线回放使用预写补丁，仅验证工程流程，不代表真实大模型补全成绩。";
    $("run-error").textContent = getError(run.error);
    $("run-error").hidden = !run.error;
    for (const format of ["md","json"]) {
      const link = $("download-" + format);
      link.href = "/api/runs/" + encodeURIComponent(run.id) + "/report?format=" + format;
      link.hidden = false;
      link.setAttribute("download", "testfix-" + run.id + "." + format);
    }
    runStats();
    const tasks = run.tasks || [];
    if (!tasks.some((task) => task.id === state.taskId)) state.taskId = tasks[0]?.id || null;
    clear($("run-task-list"));
    for (const task of tasks) {
      const button = node("button", "run-task-button" + (task.id === state.taskId ? " selected" : ""));
      button.append(node("strong", "", task.title || task.id));
      const small = node("small");
      const badge = node("span"); status(badge, task.status);
      small.append(badge, node("span", "", duration(task.duration_s)));
      button.append(small);
      button.setAttribute("aria-pressed", String(task.id === state.taskId));
      button.addEventListener("click", () => { state.taskId = task.id; renderRun(); });
      $("run-task-list").append(button);
    }
    renderDetail();
    updateStartButton();
  }
  function testContent(result) {
    if (!result) return "尚未执行。";
    const entries = Object.entries(result).filter(([key]) => !["passed","total"].includes(key));
    return entries.map(([key,value]) => key + ": " + stringify(value)).join("\n\n") || "没有附加日志。";
  }
  function renderTests(panel, task) {
    const grid = node("div", "test-grid");
    const evidence = [["baseline","补全前基线",false],["generated","Agent 生成测试",false],["acceptance","独立验收测试",true],["regression",task.delivery_kind === "cli" || task.upstream_regression ? "回归测试合计" : "原有回归测试",true]];
    if (task.upstream_regression) evidence.push(["upstream_regression","上游原有测试",true]);
    if (task.behavior_regression) evidence.push(["behavior_regression","冻结的既有行为回归",true]);
    if (task.build) evidence.push(["build","工程打包构建",true]);
    if (task.extended) evidence.push(["extended",task.delivery_kind === "cli" ? "构建产物的真实 CLI" : task.kind === "project" ? "真实 HTTP 与持久性" : "额外性质与有界穷举",true]);
    for (const [key,title,protectedTest] of evidence) {
      const isGeneratedBaseline = key === "generated" && !task.generated && task.generated_baseline;
      const result = task[key] || (key === "generated" ? task.generated_baseline : key === "acceptance" ? task.independent : null);
      const card = node("div", "test-card");
      const top = node("div", "test-card-top");
      top.append(node("strong", "", title + (isGeneratedBaseline ? " · 补全前" : "")));
      if (protectedTest) top.append(node("span", "test-protected", "受保护"));
      const stats = node("div", "test-card-result");
      const hasResult = result && result.total !== undefined && result.passed !== undefined;
      stats.append(node("strong", hasResult && result.total > 0 ? (result.passed === result.total ? "passed" : "failed") : "", hasResult && result.total > 0 ? result.passed : "—"), node("span", "", hasResult ? (result.total > 0 ? "/ " + result.total + " 通过" : "无测试用例") : "尚未执行"));
      card.append(top, stats);
      if (result?.metrics?.checks !== undefined) card.append(node("p", "evidence-note", "实际验证输入数：" + result.metrics.checks));
      if (result) {
        const details = node("details");
        const prior = key === "generated" && task.generated && task.generated_baseline ? "补全前生成测试：" + task.generated_baseline.passed + " / " + task.generated_baseline.total + " 通过\n\n" : key === "regression" && task.regression_baseline ? "补全前回归：" + task.regression_baseline.passed + " / " + task.regression_baseline.total + " 通过\n\n" : "";
        details.append(node("summary", "", "查看执行日志与结果"), node("pre", "", (prior + testContent(result)).slice(0, 100000)));
        card.append(details);
      }
      grid.append(card);
    }
    panel.append(grid, node("p", "evidence-note", "独立验收由受保护测试判定。生成测试用来提供失败证据；最终交付同时检查验收与回归。具体断言、退出码和执行日志保留在完整报告中。"));
  }
  function renderPatch(panel, task) {
    const patch = task.diff || task.patch;
    if (!patch) { panel.append(node("div", "panel-empty", "尚未生成补丁。代码补全 Agent 生成并应用补丁后，差异将在这里显示。")); return; }
    const text = stringify(patch);
    const toolbar = node("div", "patch-toolbar");
    toolbar.append(node("span", "", task.diff ? "UNIFIED DIFF / 补丁差异" : "PATCH / 补丁内容"));
    const copy = node("button", "copy-button", "复制补丁");
    copy.addEventListener("click", async () => { try { await navigator.clipboard.writeText(text); toast("补丁已复制"); } catch { toast("浏览器未开放剪贴板权限，可选中文本复制"); } });
    toolbar.append(copy);
    const pre = node("pre", "code-block");
    const code = node("code");
    for (const line of text.split("\n")) {
      let kind = "";
      if (line.startsWith("+++") || line.startsWith("---") || line.startsWith("diff ")) kind = "header";
      else if (line.startsWith("+")) kind = "added";
      else if (line.startsWith("-")) kind = "removed";
      else if (line.startsWith("@@")) kind = "context";
      code.append(node("span", "diff-line" + (kind ? " " + kind : ""), line || " "));
    }
    pre.append(code); panel.append(toolbar,pre);
  }
  function renderEvents(panel, task) {
    const events = (state.run?.events || []).filter((event) => !event.task_id || event.task_id === task.id);
    if (!events.length) { panel.append(node("div", "panel-empty", "等待 Agent 事件。任务启动后，协作日志将自动更新。")); return; }
    const timeline = node("div", "timeline");
    for (const event of events) {
      const entry = node("div", "event");
      const heading = node("div", "event-heading");
      const title = node("strong", "", AGENTS[event.agent] || event.agent || "系统");
      if (event.stage) title.append(node("span", "event-stage", event.stage));
      heading.append(title,node("time", "", formatDate(event.time, true)));
      entry.append(heading,node("p", "", stringify(event.message)));
      timeline.append(entry);
    }
    panel.append(timeline);
  }
  function renderAnalysis(panel, task) {
    if (task.kind === "project" && !isProjectRun(state.run)) {
      const section = node("section", "analysis-section");
      const link = node("a", "", "打开补全后的 Flaskr 博客工程 ↗");
      link.href = "http://127.0.0.1:8766/";
      link.target = "_blank"; link.rel = "noopener";
      section.append(node("h4", "", "完整应用本地演示"),link,node("p", "", "文章详情、标题搜索、每页五篇分页；原有注册、登录和文章管理保留。"));
      panel.append(section);
    }
    for (const [key,title] of [["requirements","需求与预期行为"],["test_steps","大模型生成的自动化测试步骤"],["requirement_coverage","功能与测试用例对应"],["test_assumptions","测试域与假设"],["diagnosis","缺口分析与失败证据"],["review","补丁评审"],["verdict","运行与功能满足判定"]]) {
      const section = node("section", "analysis-section");
      section.append(node("h4", "", title),node("pre", "", stringify(task[key]) || "暂无记录。"));
      panel.append(section);
    }
    if (task.source) {
      const section = node("section", "analysis-section");
      section.append(node("h4", "", "任务来源"),node("pre", "", stringify(task.source)));
      panel.append(section);
    }
    if (task.acceptance_sha256) {
      const section = node("section", "analysis-section");
      section.append(node("h4", "", "独立验收完整性 SHA-256"),node("pre", "", task.acceptance_sha256));
      panel.append(section);
    }
  }
  function renderDetail() {
    const task = state.run?.tasks?.find((item) => item.id === state.taskId);
    const panel = $("evidence-panel");
    const oldScroll = panel.querySelector(".code-block")?.scrollTop;
    const openDetails = [...panel.querySelectorAll("details")].map((details) => details.open);
    clear(panel);
    if (!task) {
      $("detail-task-id").textContent = "等待任务初始化";
      $("detail-task-title").textContent = "运行已创建";
      $("detail-attempts").textContent = "";
      $("detail-duration").textContent = "";
      status($("detail-task-status"), state.run?.status);
      panel.append(node("div", "panel-empty", "编排器正在初始化任务，请稍候…"));
      return;
    }
    $("detail-task-id").textContent = task.id;
    $("detail-task-title").textContent = task.title || task.id;
    status($("detail-task-status"), task.status);
    const attempts = Array.isArray(task.attempts) ? task.attempts.length : task.attempts ?? 0;
    $("detail-attempts").textContent = "补全轮次 " + attempts;
    $("detail-duration").textContent = "任务耗时 " + duration(task.duration_s);
    if (task.error) panel.append(node("div", "task-error", getError(task.error)));
    const renderer = {tests:renderTests,patch:renderPatch,events:renderEvents,analysis:renderAnalysis}[state.tab];
    renderer(panel,task);
    [...panel.querySelectorAll("details")].forEach((details,index) => { details.open = openDetails[index] || false; });
    const code = panel.querySelector(".code-block");
    if (code && oldScroll !== undefined) code.scrollTop = oldScroll;
  }
  async function selectRun(id, scroll = false) {
    clearTimeout(state.polling);
    const version = ++state.detailVersion;
    state.loading = true;
    updateStartButton();
    try {
      const run = await api("/api/runs/" + encodeURIComponent(id));
      if (version !== state.detailVersion) return;
      state.run = run;
      state.runs = state.runs.map((item) => item.id === run.id ? {...item,status:run.status,summary:run.summary} : item);
      state.taskId = run.tasks?.[0]?.id || null;
      state.failures = 0;
      showError("");
      renderRun(); renderHistory();
      if (scroll) $("evidence-title").scrollIntoView({behavior:"smooth",block:"start"});
      if (ACTIVE.has(run.status)) schedulePoll();
    } catch (error) { if (version === state.detailVersion) showError("无法读取运行详情：" + error.message); }
    finally { if (version === state.detailVersion) { state.loading = false; updateStartButton(); } }
  }
  function schedulePoll() { clearTimeout(state.polling); state.polling = setTimeout(pollRun, 1000); }
  async function pollRun() {
    if (!state.run || !ACTIVE.has(state.run.status)) return;
    const id = state.run.id;
    const version = state.detailVersion;
    try {
      const run = await api("/api/runs/" + encodeURIComponent(id));
      if (state.run?.id !== id || state.detailVersion !== version) return;
      state.run = run;
      state.runs = state.runs.map((item) => item.id === run.id ? {...item,status:run.status,summary:run.summary} : item);
      state.failures = 0;
      renderRun();
      if (ACTIVE.has(run.status)) schedulePoll();
      else {
        const history = await api("/api/runs");
        state.runs = history.runs || [];
        renderHistory();
        updateStartButton();
        toast(run.status === "failed" ? "运行结束，存在执行错误，请查看报告" : "验证已完成，报告已保存");
      }
    } catch (error) {
      if (state.run?.id !== id || state.detailVersion !== version) return;
      state.failures += 1;
      showError("运行状态更新失败：" + error.message + "。正在重试；可点击刷新重新连接。");
      state.polling = setTimeout(pollRun, Math.min(1000 * state.failures, 5000));
    }
  }
  async function startRun() {
    if ($("start-button").disabled || state.submitting) return;
    state.submitting = true;
    showError(""); updateStartButton();
    try {
      const run = await api("/api/runs", {method:"POST", body:JSON.stringify({task_ids:[...state.selection],provider:$("provider-select").value,runner:$("runner-select").value,max_rounds:Number($("rounds-select").value)})});
      await selectRun(run.id, true);
      const history = await api("/api/runs"); state.runs = history.runs || []; renderHistory();
    } catch (error) { showError("启动验证失败：" + error.message); }
    finally { state.submitting = false; updateStartButton(); }
  }
  async function startProjectRun() {
    if ($("project-start-button").disabled || state.projectSubmitting) return;
    state.projectSubmitting = true;
    showError(""); updateStartButton();
    try {
      const payload = {
        project_id:$("project-select").value,
        provider:$("project-provider-select").value,
        max_rounds:Number($("project-rounds-select").value),
        strategy:state.projectDefaults.strategy || "balanced",
        budget:{wall_seconds:Number($("project-wall-minutes").value) * 60,token_limit:Number($("project-token-limit").value),model_slots:Number($("project-model-slots").value),cpu_slots:Number($("project-cpu-slots").value)}
      };
      const run = await api("/api/project-runs", {method:"POST",body:JSON.stringify(payload)});
      await selectRun(run.id, true);
      const history = await api("/api/runs");
      state.runs = history.runs || []; renderHistory();
      toast("完整工程运行已创建，执行证据将自动更新");
    } catch (error) { showError("启动完整工程失败：" + error.message); }
    finally { state.projectSubmitting = false; updateStartButton(); }
  }

  $("project-select").addEventListener("change", () => {
    const project = state.projects.find((item) => item.id === $("project-select").value);
    if (project?.default_budget?.wall_seconds) $("project-wall-minutes").value = project.default_budget.wall_seconds / 60;
    updateProjectConfiguration();
  });
  $("project-provider-select").addEventListener("change", updateProjectConfiguration);
  $("project-start-button").addEventListener("click", startProjectRun);
  for (const id of ["project-wall-minutes","project-token-limit","project-model-slots","project-cpu-slots"]) $(id).addEventListener("input", updateProjectStartButton);
  $("provider-select").addEventListener("change", updateConfiguration);
  $("runner-select").addEventListener("change", updateConfiguration);
  $("start-button").addEventListener("click", startRun);
  $("select-all-button").addEventListener("click", () => { state.selection = state.selection.size === state.tasks.length ? new Set() : new Set(state.tasks.map((task) => task.id)); renderTaskSelector(); });
  $("refresh-button").addEventListener("click", async () => {
    document.body.classList.add("busy");
    try { await loadConfiguration(); if (state.run) await selectRun(state.run.id, false); }
    finally { document.body.classList.remove("busy"); }
  });
  document.querySelectorAll(".tab").forEach((tab,index,tabs) => {
    tab.addEventListener("click", () => {
      state.tab = tab.dataset.tab;
      for (const item of tabs) { item.classList.toggle("active", item === tab); item.setAttribute("aria-selected", String(item === tab)); }
      $("evidence-panel").setAttribute("aria-labelledby", tab.id);
      renderDetail();
    });
    tab.addEventListener("keydown", (event) => {
      if (!["ArrowLeft","ArrowRight","Home","End"].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
      tabs[next].focus(); tabs[next].click();
    });
  });
  loadConfiguration(true).catch((error) => showError("工作台初始化失败：" + error.message));
})();
