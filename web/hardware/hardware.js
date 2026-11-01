const $ = (id) => document.getElementById(id);
const clone = (value) => JSON.parse(JSON.stringify(value));
const format = (value) => typeof value === "string" ? value : JSON.stringify(value, null, 2);
let capabilities = null;
let serviceReady = false;
let busy = false;
let planningId = null;
let modelDraft = false;
let lastReport = null;
let planReady = true;
let singleRequirementId = "feature-1";

const EXAMPLES = {
  http: {
    description: "读取设备固件版本，确认设备返回版本 1.0.0。",
    expected: "version = 1.0.0",
    action: { id: "read-version", kind: "http", function_id: "feature-1", description: "读取固件版本接口", method: "GET", path: "/api/version", timeout_s: 3, assertions: [{ kind: "status_code", expected: 200 }, { kind: "json_equals", path: ["version"], expected: "1.0.0" }] },
  },
  serial: {
    description: "发送固件协议中的 VERSION 命令，确认 MCU 返回固件版本 1.0.0。",
    expected: "回包包含 VERSION:1.0.0",
    action: { id: "read-version", kind: "serial", function_id: "feature-1", description: "读取 MCU 固件版本", write_text: "VERSION\n", read_until: "\n", max_bytes: 4096, timeout_s: 3, assertions: [{ kind: "text_contains", expected: "VERSION:1.0.0" }] },
  },
  ssh: {
    description: "读取设备内核类型，确认返回 Linux。",
    expected: "输出包含 Linux",
    action: { id: "read-system", kind: "ssh", function_id: "feature-1", description: "读取设备内核类型", command: "uname -s", timeout_s: 3, max_bytes: 4096, assertions: [{ kind: "exit_code", expected: 0 }, { kind: "text_contains", expected: "Linux" }] },
  },
};

function element(tag, className, text) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== undefined && text !== null) item.textContent = String(text);
  return item;
}

function errorMessage(error) {
  return error?.name === "AbortError" ? "请求等待超时。本页没有自动重试设备动作，请检查运行记录后再操作。" : error?.message || "本地服务暂时不可用。";
}

function showError(id, error) {
  $(id).textContent = errorMessage(error);
  $(id).hidden = false;
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
      const detail = result.detail || result.error || `服务返回 ${response.status}`;
      const message = Array.isArray(detail) ? detail.map((item) => `${(item.loc || []).filter((part) => part !== "body").join(".")}: ${item.msg || format(item)}`).join("；") : format(detail);
      throw new Error(message);
    }
    return result;
  } finally { clearTimeout(timer); }
}

function jsonField(id, label, fallback) {
  const text = $(id).value.trim();
  if (!text && fallback !== undefined) return clone(fallback);
  try { return JSON.parse(text); } catch { throw new Error(`${label}不是有效 JSON，请检查引号、逗号和括号。`); }
}

function setBusy(value) {
  busy = value;
  for (const input of document.querySelectorAll("main input, main select, main textarea, main button")) input.disabled = value;
  if (!value) {
    $("generate-plan").disabled = !serviceReady;
    $("validate-plan").disabled = !serviceReady || !planReady;
    $("run-plan").disabled = !serviceReady || !planReady;
  }
}

function markConfigurationChanged() {
  const hadPlanning = Boolean(planningId);
  planningId = null;
  if (modelDraft || hadPlanning) {
    $("plan-origin").textContent = "配置已修改";
    $("plan-origin").className = "origin-tag";
    $("plan-state").textContent = "设备、需求或协议已修改，请重新生成步骤，或编辑并校验计划。";
  }
  invalidateCurrentResult();
}

function invalidateCurrentResult() {
  if (lastReport) {
    $("results-state").textContent = "以下为上次执行记录；当前配置已改变，尚未重新测试。";
    $("connection-status").textContent = "未检查";
    $("connection-status").className = "";
    $("connection-detail").textContent = "当前配置尚未重新连接。";
    $("function-status").textContent = "待重新执行";
    $("function-status").className = "";
    $("function-detail").textContent = "下方保留上次运行的回执。";
  }
}

function updateKind() {
  const kind = $("device-kind").value;
  for (const group of document.querySelectorAll("fieldset[data-kind]")) {
    group.hidden = group.dataset.kind !== kind;
    group.disabled = group.dataset.kind !== kind;
  }
  const name = $("device-name").value.trim();
  if (!name || name === "MPU" || name === "MCU") $("device-name").value = kind === "serial" ? "MCU" : "MPU";
  const supported = capabilities?.transports?.[kind];
  $("transport-note").textContent = supported?.available === false ? "本机暂缺此接口的执行依赖，仍可设计计划；执行前需补齐依赖。" : "";
  $("serial-loop-note").hidden = kind !== "serial" || $("serial-port").value.trim() !== "loop://";
}

function updateRequirementMode() {
  const text = $("requirements-json").value.trim();
  $("single-requirement-fields").hidden = Boolean(text);
  $("requirement-mode-note").hidden = !text;
  if (!text) return;
  $("multiple-requirements").open = true;
  try {
    const requirements = JSON.parse(text);
    if (!Array.isArray(requirements)) throw new Error("array required");
    $("requirement-mode-note").textContent = `当前使用 ${requirements.length} 项需求，请在下方 JSON 中编辑。清空 JSON 可切回单项输入。`;
    if (requirements[0]) {
      const first = requirements[0];
      singleRequirementId = first.id || "feature-1";
      $("requirement-description").value = first.description || "";
      $("requirement-expected").value = first.expected || "";
      $("requirement-category").value = first.category || "functional";
    }
  } catch {
    $("requirement-mode-note").textContent = "当前使用多项需求 JSON，内容尚未完整；请修正，或清空后切回单项输入。";
  }
}

function deviceFromForm(requireTarget = true) {
  const kind = $("device-kind").value;
  const name = $("device-name").value.trim() || (kind === "serial" ? "MCU" : "MPU");
  if (kind === "http") {
    const base_url = $("http-url").value.trim();
    if (requireTarget && !base_url) throw new Error("请先填写设备 URL，并依据设备协议填写动作路径和预期回包。" );
    if (base_url) {
      let url;
      try { url = new URL(base_url); } catch { throw new Error("设备 URL 格式不正确，例如 http://192.168.1.50。" ); }
      if (!["http:", "https:"].includes(url.protocol)) throw new Error("HTTP 设备仅支持 http 或 https 地址。" );
    }
    return { kind, name, base_url, headers: jsonField("http-headers", "HTTP 请求头", {}), verify_tls: $("http-verify-tls").checked };
  }
  if (kind === "serial") {
    const port = $("serial-port").value.trim();
    if (requireTarget && !port) throw new Error("请填写 MCU 串口名称，例如 COM3，并依据固件协议填写命令和预期回包。" );
    const baudrate = Number($("serial-baud").value);
    if (!Number.isInteger(baudrate) || baudrate < 50 || baudrate > 4000000) throw new Error("波特率应为 50 到 4000000 之间的整数。" );
    return { kind, name, port, baudrate, dtr: $("serial-dtr").checked, rts: $("serial-rts").checked };
  }
  const device = { kind, name, host: $("ssh-host").value.trim(), port: Number($("ssh-port").value), username: $("ssh-username").value.trim(), private_key_path: $("ssh-key").value.trim(), known_hosts_path: $("ssh-known-hosts").value.trim() };
  if (requireTarget && (!device.host || !device.username || !device.private_key_path || !device.known_hosts_path)) throw new Error("请填写 SSH 主机、用户名和本机已有认证文件路径。" );
  return device;
}

function readProtocol(requireTarget = true) {
  const device = deviceFromForm(requireTarget);
  let requirements;
  if ($("requirements-json").value.trim()) requirements = jsonField("requirements-json", "多项需求");
  else {
    const description = $("requirement-description").value.trim();
    const expected = $("requirement-expected").value.trim();
    if (!description || !expected) throw new Error("请填写功能要求和预期结果。只有 IP 或型号无法确定设备协议。" );
    requirements = [{ id: singleRequirementId, description, expected, category: $("requirement-category").value }];
  }
  if (!Array.isArray(requirements) || !requirements.length) throw new Error("需求必须是非空 JSON 数组。" );
  const allowed_actions = jsonField("allowed-actions", "允许动作");
  if (!Array.isArray(allowed_actions) || !allowed_actions.length) throw new Error("请至少声明一个协议动作及其回包断言。" );
  if (allowed_actions.some((action) => action.kind !== device.kind)) throw new Error("协议动作类型与当前设备接口不一致。请编辑动作，或载入该接口示例后按实际协议修改。" );
  return { device, requirements, allowed_actions };
}

function readRunPayload() {
  const payload = { ...readProtocol(), plan: jsonField("plan-json", "测试计划"), max_duration_s: 60 };
  if (planningId) payload.planning_id = planningId;
  if (!Array.isArray(payload.plan?.steps) || !payload.plan.steps.length) throw new Error("计划中还没有测试步骤，请生成或编辑计划。" );
  return payload;
}

function assertionDescription(assertion) {
  const expected = typeof assertion.expected === "string" ? JSON.stringify(assertion.expected) : format(assertion.expected);
  const path = (assertion.path || []).join(".");
  return ({ status_code: `HTTP 状态码 = ${expected}`, exit_code: `退出码 = ${expected}`, text_contains: `回包包含 ${expected}`, text_equals: `回包完全等于 ${expected}`, hex_equals: `回包十六进制 = ${expected}`, json_equals: `JSON${path ? ` ${path}` : ""} = ${expected}` })[assertion.kind] || `${assertion.kind}: ${expected}`;
}

function actionDescription(action) {
  if (!action) return "动作引用不存在，请补齐协议。";
  if (action.kind === "http") return `${action.method || "GET"} ${action.path || ""}`;
  if (action.kind === "ssh") return `SSH：${action.command || ""}`;
  return action.write_text !== undefined ? `串口发送：${JSON.stringify(action.write_text)}` : `串口发送 HEX：${action.write_hex || ""}`;
}

function renderPlan() {
  const target = $("plan-preview");
  target.replaceChildren();
  try {
    const plan = jsonField("plan-json", "测试计划");
    const actions = jsonField("allowed-actions", "允许动作", []);
    if (!Array.isArray(plan?.steps) || !plan.steps.length) throw new Error("尚无可执行步骤。" );
    for (const step of plan.steps) {
      const action = actions.find((item) => item.id === step.action_id);
      const item = element("li");
      item.append(element("h3", "", action?.description || step.action_id || "未指定动作"), element("p", action ? "" : "invalid", actionDescription(action)));
      for (const assertion of step.assertions || []) item.append(element("p", "assertions", assertionDescription(assertion)));
      target.append(item);
    }
    planReady = true;
  } catch (error) {
    target.append(element("li", "", errorMessage(error)));
    planReady = false;
  }
  if (!busy) setBusy(false);
}

function loadExample() {
  const example = clone(EXAMPLES[$("device-kind").value]);
  $("requirement-description").value = example.description;
  $("requirement-expected").value = example.expected;
  $("requirement-category").value = "functional";
  $("requirements-json").value = "";
  singleRequirementId = "feature-1";
  updateRequirementMode();
  $("allowed-actions").value = format([example.action]);
  $("plan-json").value = format({ steps: [{ id: "step-1", function_id: example.action.function_id, action_id: example.action.id, assertions: clone(example.action.assertions) }] });
  planningId = null;
  modelDraft = false;
  $("plan-origin").textContent = "手工示例";
  $("plan-origin").className = "origin-tag";
  $("planning-details").textContent = "";
  $("planning-record").hidden = true;
  $("plan-state").textContent = "示例没有连接设备，请按实际设备协议修改命令与预期。";
  $("plan-error").hidden = true;
  renderPlan();
  markConfigurationChanged();
}

async function generatePlan() {
  if (busy || !serviceReady) return;
  $("plan-error").hidden = true;
  let payload;
  try { payload = { ...readProtocol(), provider: "codex_cli", model: "gpt-5.6-sol", timeout_s: 120, max_duration_s: 60 }; }
  catch (error) { showError("plan-error", error); return; }
  setBusy(true);
  $("generate-plan").textContent = "模型正在生成步骤…";
  $("plan-state").textContent = "正在调用现有模型，仅编排已声明动作；没有连接设备。";
  try {
    const result = await request("/api/hardware/plan", payload, 180000);
    if (result.status !== "ready" || !result.plan || result.model_used !== true) {
      planningId = null;
      modelDraft = false;
      $("plan-json").value = "";
      $("plan-origin").textContent = "需补充协议";
      $("plan-origin").className = "origin-tag";
      $("plan-state").textContent = result.summary || "模型未形成完整计划，请补充缺失协议与预期。";
      $("plan-preview").replaceChildren(element("li", "", format(result.uncovered || "尚无可执行计划。")));
      planReady = false;
    } else {
      $("plan-json").value = format(result.plan);
      planningId = result.planning_id || null;
      modelDraft = true;
      $("plan-origin").textContent = "模型已生成";
      $("plan-origin").className = "origin-tag generated";
      $("plan-state").textContent = result.summary || "步骤已生成，请查看后执行。";
      renderPlan();
      invalidateCurrentResult();
    }
    $("planning-details").textContent = `规划记录 ${result.planning_id || "未返回"} · 请求模型 ${result.model_requested || "gpt-5.6-sol"}${result.model_actual ? ` · 实际模型 ${result.model_actual}` : " · 服务未返回可核实的实际模型名"}`;
    $("planning-record").hidden = false;
  } catch (error) {
    showError("plan-error", error);
    $("plan-state").textContent = "本次生成未完成；已有计划保留，没有执行设备动作。";
  } finally {
    setBusy(false);
    $("generate-plan").textContent = "用模型生成步骤";
  }
}

async function validatePlan() {
  if (busy || !serviceReady) return;
  $("plan-error").hidden = true;
  let payload;
  try { payload = readRunPayload(); } catch (error) { showError("plan-error", error); return; }
  setBusy(true);
  $("validate-plan").textContent = "正在校验…";
  try {
    const result = await request("/api/hardware/validate-plan", payload);
    $("plan-state").textContent = result.valid ? `计划格式和协议约束通过：${result.steps} 个步骤，${result.requirements} 项需求。尚未执行设备动作。${result.is_simulation ? " 当前目标为软件模拟，不能验证真实固件。" : ""}` : "计划未通过校验，请补充协议。";
  } catch (error) { showError("plan-error", error); }
  finally { setBusy(false); $("validate-plan").textContent = "校验计划"; }
}

function resultTotal(label, value, detail) {
  const block = element("div", "result-total");
  block.append(element("span", "", label), element("strong", "", value), element("small", "", detail));
  return block;
}

function renderReport(report) {
  lastReport = report;
  const steps = Array.isArray(report.steps) ? report.steps : [];
  const connected = steps.filter((step) => step.communication_passed === true || step.actual?.communication_passed === true).length;
  const functional = (report.coverage || []).filter((item) => item.category === "functional");
  const functionalPassed = functional.filter((item) => item.covered && item.passed).length;
  const simulation = report.is_simulation === true;
  $("connection-status").textContent = simulation ? (connected ? "模拟链路已通" : "模拟链路未通") : connected === steps.length && connected > 0 ? "连接成功" : connected > 0 ? "部分连通" : "未确认连通";
  $("connection-status").className = connected > 0 ? "passed" : "failed";
  $("connection-detail").textContent = `${connected}/${steps.length} 个步骤完成通信${simulation ? "，此为软件模拟。" : "。"}`;
  $("function-status").textContent = simulation ? "仅模拟结果" : !functional.length ? "未覆盖功能" : report.functional_verified ? "通过" : "未通过";
  $("function-status").className = report.functional_verified ? "passed" : simulation || !functional.length ? "" : "failed";
  $("function-detail").textContent = simulation ? "不能作为真实 MPU / MCU 固件功能验收。" : !functional.length ? "本计划只检查通信，没有功能行为断言。" : `${functionalPassed}/${functional.length} 项功能满足已声明断言。`;
  $("results-state").textContent = simulation ? "软件模拟执行记录，非真实设备功能验收。" : report.success ? "本次计划执行完成。" : "本次执行有失败，请查看回包与断言。";
  const totals = element("div", "result-totals");
  totals.append(resultTotal("测试步骤", `${report.validation?.passed ?? steps.filter((step) => step.status === "passed").length} / ${report.validation?.total ?? steps.length}`, simulation ? "模拟步骤" : "已满足动作与断言"), resultTotal("功能需求", `${functionalPassed} / ${functional.length}`, simulation ? "模拟结果，不验证真实固件" : "不以连接成功替代功能判断"));
  $("result-summary").replaceChildren(totals);
  $("result-summary").className = "";
  if (report.error) $("result-summary").append(element("p", "notice error", report.error));
  const sourceLabel = report.model_used === true ? "已绑定模型规划" : report.planning_source?.includes("edit") ? "手工或已编辑计划" : "手工计划";
  $("plan-origin").textContent = report.model_used === true ? "模型规划已核实" : sourceLabel;
  $("plan-origin").className = report.model_used === true ? "origin-tag generated" : "origin-tag";
  modelDraft = report.model_used === true;
  $("result-summary").append(element("p", "record-meta", `计划来源：${sourceLabel}`));
  const target = $("result-steps");
  target.replaceChildren();
  for (const [index, step] of steps.entries()) {
    const block = element("article", "result-step");
    const heading = element("div", "result-step-heading");
    const passed = step.status === "passed";
    heading.append(element("span", "", `${index + 1}. ${step.request?.description || step.action_id}`), element("span", passed ? "step-pass" : "step-fail", passed ? "断言通过" : "未通过"));
    block.append(heading, element("p", "", actionDescription(step.request)));
    const actual = step.actual || {};
    if (actual.error || step.error) block.append(element("p", "step-fail", actual.error || step.error));
    if (actual.text !== undefined || actual.hex !== undefined || actual.json !== undefined) {
      const value = actual.text || (actual.json !== undefined ? format(actual.json) : actual.hex || "（空回包）");
      block.append(element("pre", "", value));
    }
    for (const assertion of step.assertions || []) {
      const scope = assertion.scope === "communication" || ["status_code", "exit_code"].includes(assertion.kind) ? "通信" : "功能";
      block.append(element("div", `assertion-result ${assertion.passed ? "step-pass" : "step-fail"}`, `${assertion.passed ? "通过" : "失败"} · ${scope}断言：${assertionDescription(assertion)}；实际 ${format(assertion.actual)}`));
    }
    target.append(block);
  }
  $("receipt-details").hidden = false;
  $("receipt-source").textContent = format(report);
  $("receipt-meta").textContent = `记录 ${report.run_id || "未返回"} · 耗时 ${Number(report.duration_s || 0).toFixed(2)} 秒${report.report_path ? `\n${report.report_path}` : ""}`;
}

async function runPlan() {
  if (busy || !serviceReady) return;
  $("plan-error").hidden = true;
  let payload;
  try { payload = readRunPayload(); } catch (error) { showError("plan-error", error); return; }
  setBusy(true);
  $("run-plan").textContent = "正在执行设备动作…";
  $("results-state").textContent = "按声明的动作执行中，没有重新调用模型。";
  $("connection-status").textContent = "检查中";
  $("connection-status").className = "";
  $("function-status").textContent = "执行中";
  $("function-status").className = "";
  try {
    const report = await request("/api/hardware/run", payload, 160000);
    renderReport(report);
    $("results").scrollIntoView({ block: "start", behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
  } catch (error) {
    showError("plan-error", error);
    $("results-state").textContent = "本次未取得完整回执，请检查错误；页面没有自动重试设备动作。";
    $("connection-status").textContent = "未确认";
    $("function-status").textContent = "未完成";
    $("connection-detail").textContent = "未取得本次完整连接结果。";
    $("function-detail").textContent = "未取得本次完整功能结果。";
  } finally { setBusy(false); $("run-plan").textContent = "执行测试"; }
}

function importProfile() {
  if (busy) return;
  try {
    const profile = jsonField("profile-json", "设备档案");
    const importedPlanningId = profile.planning_id ?? null;
    if (importedPlanningId !== null && (typeof importedPlanningId !== "string" || !/^[a-f0-9]{32}$/.test(importedPlanningId))) throw new Error("planning_id 必须是有效的 32 位小写十六进制记录 ID。" );
    const device = profile.device;
    if (!device || !["http", "ssh", "serial"].includes(device.kind) || !Array.isArray(profile.requirements) || !Array.isArray(profile.allowed_actions)) throw new Error("档案需要 device、requirements 和 allowed_actions，接口为 http、ssh 或 serial。" );
    $("device-kind").value = device.kind;
    $("device-name").value = device.name || (device.kind === "serial" ? "MCU" : "MPU");
    if (device.kind === "http") {
      $("http-url").value = device.base_url || "";
      $("http-headers").value = format(device.headers || {});
      $("http-verify-tls").checked = device.verify_tls !== false;
    } else if (device.kind === "serial") {
      $("serial-port").value = device.port || "";
      $("serial-baud").value = device.baudrate || 115200;
      $("serial-dtr").checked = device.dtr === true;
      $("serial-rts").checked = device.rts === true;
    } else {
      $("ssh-host").value = device.host || "";
      $("ssh-port").value = device.port || 22;
      $("ssh-username").value = device.username || "";
      $("ssh-key").value = device.private_key_path || "";
      $("ssh-known-hosts").value = device.known_hosts_path || "";
    }
    $("requirements-json").value = format(profile.requirements);
    updateRequirementMode();
    $("allowed-actions").value = format(profile.allowed_actions);
    $("plan-json").value = profile.plan ? format(profile.plan) : "";
    modelDraft = false;
    planningId = importedPlanningId;
    $("plan-origin").textContent = importedPlanningId ? "规划来源待核实" : "导入的手工档案";
    $("plan-origin").className = "origin-tag";
    $("planning-details").textContent = importedPlanningId ? `导入规划记录 ${importedPlanningId}，执行时由服务端核对设备、需求、动作和完整计划。` : "";
    $("planning-record").hidden = !importedPlanningId;
    $("profile-state").textContent = "档案已载入；没有连接设备或调用模型。";
    updateKind();
    renderPlan();
    invalidateCurrentResult();
  } catch (error) { $("profile-state").textContent = errorMessage(error); }
}

function exportProfile() {
  if (busy) return;
  try {
    const profile = readProtocol(false);
    try { profile.plan = jsonField("plan-json", "测试计划"); } catch { /* A protocol profile may precede a plan. */ }
    if (planningId && /^[a-f0-9]{32}$/.test(planningId)) profile.planning_id = planningId;
    $("profile-json").value = format(profile);
    $("profile-state").textContent = "已生成当前档案 JSON，可保存后在其他设备测试中导入。";
  } catch (error) { $("profile-state").textContent = errorMessage(error); }
}

async function loadCapabilities() {
  try {
    capabilities = await request("/api/hardware/capabilities");
    serviceReady = true;
    $("service-state").textContent = "本地服务已连接";
    $("capabilities-source").textContent = format(capabilities);
    const ports = $("serial-ports");
    for (const item of capabilities.serial_ports || []) {
      const option = element("option");
      option.value = item.port;
      option.textContent = item.description || item.port;
      ports.append(option);
    }
    updateKind();
  } catch (error) {
    $("service-state").textContent = "本地服务不可用";
    showError("global-error", error);
  } finally { setBusy(false); }
}

$("device-kind").addEventListener("change", () => { updateKind(); markConfigurationChanged(); });
$("serial-port").addEventListener("input", updateKind);
$("requirements-json").addEventListener("input", updateRequirementMode);
$("reset-example").addEventListener("click", loadExample);
$("generate-plan").addEventListener("click", generatePlan);
$("validate-plan").addEventListener("click", validatePlan);
$("run-plan").addEventListener("click", runPlan);
$("import-profile").addEventListener("click", importProfile);
$("export-profile").addEventListener("click", exportProfile);
for (const field of document.querySelectorAll(".configuration input, .configuration select, .configuration textarea")) {
  if (field.id === "profile-json") continue;
  field.addEventListener("input", () => { markConfigurationChanged(); if (field.id === "allowed-actions") renderPlan(); });
}
$("plan-json").addEventListener("input", () => {
  $("plan-origin").textContent = modelDraft ? "已编辑的计划" : "手工计划";
  $("plan-origin").className = "origin-tag";
  renderPlan();
  invalidateCurrentResult();
});
updateKind();
loadExample();
loadCapabilities();
