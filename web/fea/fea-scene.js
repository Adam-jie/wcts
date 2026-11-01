import * as THREE from "../lab/vendor/three.module.min.js";

// A spatial view of completed public records, not a simulation of employees.
const IDS = ["plan-workflow", "explore-repository", "ground-code", "analyze-requirements", "generate-tests", "diagnose-results", "repair-code", "review-patch", "verify-delivery"];
const LABELS = ["总控规划", "仓库探索", "代码定位", "需求分析", "生成测试", "失败诊断", "代码补全", "独立审查", "验收交付"];
const ACCENTS = [0xe7c181, 0x72b9e1, 0x78c7ce, 0x77aaa7, 0x79e3bf, 0xd8aa7d, 0x94c7f0, 0xc1b2e3, 0x9bdbb6];
const TAU = Math.PI * 2;
const clamp = (value, lo, hi) => Math.max(lo, Math.min(hi, value));

export function workstationLayout(width) {
  const compact = width < 760, columns = compact ? 2 : 4;
  return IDS.map((id, index) => {
    const controller = index === 0, n = index - 1;
    return { id, controller, scale: controller ? 1.45 : 1,
      position: controller ? [0, -.12, compact ? -8.1 : -4.6]
        : [((n % columns) - (columns - 1) / 2) * (compact ? 5.4 : 4.15), -.15,
          Math.floor(n / columns) * (compact ? 4.15 : 4.1) + (compact ? -3.8 : .6)] };
  });
}

function makeElement(tag, className, text) {
  const element = document.createElement(tag);
  element.className = className;
  if (text !== undefined) element.textContent = String(text);
  return element;
}



/** Mount the local Three.js scene. Programmatic selection never emits onSelect. */
export function mountFEAScene(container, { onSelect = () => {}, onMode = () => {} } = {}) {
  if (!(container instanceof HTMLElement)) throw new TypeError("A scene container is required");
  const style = makeElement("link", "fea-space-style");
  style.rel = "stylesheet";
  style.href = new URL("./fea-scene.css", import.meta.url).href;
  container.append(style);
  const oldPosition = container.style.position, oldOverflow = container.style.overflow;
  if (getComputedStyle(container).position === "static") container.style.position = "relative";
  container.style.overflow = "hidden";
  let disposed = false, renderer;
  const state = { nodes: [], selected: null };
  const mode = (value) => { container.dataset.renderMode = value.mode; onMode(value); };
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "low-power" });
  } catch (error) {
    const fallback = makeElement("div", "fea-space-fallback");
    fallback.append(makeElement("strong", "", "当前设备未能启动 WebGL"),
      makeElement("p", "", "可以继续点击能力节点查看真实实验记录；这不是 3D 渲染。"));
    const list = makeElement("div", "fea-space-fallback-list");
    fallback.append(list); container.append(fallback);
    mode({ mode: "fallback", reason: "WebGL 初始化不可用" });
    return {
      setNodes(nodes) {
        if (!Array.isArray(nodes)) return;
        list.replaceChildren();
        for (const id of IDS) {
          const node = nodes.find((item) => item.id === id);
          if (!node) continue;
          const button = makeElement("button", "", id === "plan-workflow" ? "CodeAgent 总控" : node.label || LABELS[IDS.indexOf(id)]);
          button.type = "button";
          button.addEventListener("click", () => onSelect(id));
          list.append(button);
        }
      }, select() {}, focus() {}, reset() {}, zoom() {},
      destroy() { fallback.remove(); style.remove(); container.style.position = oldPosition; container.style.overflow = oldOverflow; }
    };
  }

  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.7));
  renderer.setClearColor(0x0b111c, 0);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;
  const canvas = renderer.domElement;
  canvas.className = "fea-space-canvas";
  canvas.tabIndex = 0;
  canvas.dataset.engine = "three-webgl";
  canvas.setAttribute("aria-label", "真实三维能力工作台：拖动旋转，普通滚轮滚动页面，Ctrl 或 Meta 加滚轮缩放，点击选择，双击聚焦。方向键旋转，加减缩放，Home 恢复全景，Enter 聚焦所选节点。");
  const labelLayer = makeElement("div", "fea-space-labels");
  const coreLabel = makeElement("div", "fea-space-core-label");
  labelLayer.append(coreLabel);
  container.append(canvas, labelLayer);

  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0x0b111c, 0.007);
  const world = new THREE.Group();
  scene.add(world);
  const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 120);
  const ambient = new THREE.HemisphereLight(0xc3deed, 0x14243a, 1.5);
  const key = new THREE.DirectionalLight(0xd9edf6, 1.8); key.position.set(-7, 14, 9);
  const rim = new THREE.DirectionalLight(0xb0d6ff, 1.8); rim.position.set(9, 5, -8);
  const warm = new THREE.PointLight(0xeec18a, 20, 18, 2); warm.position.set(0, 5, 1);
  scene.add(ambient, key, rim, warm);
  const geometries = new Set(), materials = new Set(), textures = new Set(), listeners = [], labelListeners = [];
  let graphResources = { geometries: new Set(), materials: new Set() };
  const records = new Map(), clickable = [], edges = [], persistent = new THREE.Group(), graph = new THREE.Group();
  world.add(persistent, graph);
  const mat = (color, extra = {}) => {
    const material = new THREE.MeshStandardMaterial({ color, metalness: 0.62, roughness: 0.28, ...extra });
    materials.add(material); return material;
  };
  const basic = (color, extra = {}) => {
    const material = new THREE.MeshBasicMaterial({ color, ...extra }); materials.add(material); return material;
  };
  const mesh = (parent, geometry, material, position = [0, 0, 0]) => {
    geometries.add(geometry);
    const object = new THREE.Mesh(geometry, material);
    object.position.set(...position); parent.add(object); return object;
  };
  const box = (parent, size, material, position) => mesh(parent, new THREE.BoxGeometry(...size), material, position);
  const ring = (parent, radius, thickness, material, y = 0) => {
    const object = mesh(parent, new THREE.TorusGeometry(radius, thickness, 8, 100), material, [0, y, 0]);
    object.rotation.x = -Math.PI / 2; return object;
  };
  const cylinder = (parent, radius, height, material, y = 0, sides = 64) =>
    mesh(parent, new THREE.CylinderGeometry(radius, radius, height, sides), material, [0, y, 0]);
  const line = (parent, points, color, opacity = 1) => {
    const geometry = new THREE.BufferGeometry().setFromPoints(points); geometries.add(geometry);
    const material = new THREE.LineBasicMaterial({ color, transparent: opacity < 1, opacity, depthWrite: false });
    materials.add(material); const object = new THREE.Line(geometry, material); parent.add(object); return object;
  };
  const glowCanvas = document.createElement("canvas"); glowCanvas.width = glowCanvas.height = 128;
  const glowContext = glowCanvas.getContext("2d");
  const gradient = glowContext.createRadialGradient(64, 64, 3, 64, 64, 64);
  gradient.addColorStop(0, "rgba(140,235,218,.45)"); gradient.addColorStop(.25, "rgba(75,194,190,.17)"); gradient.addColorStop(1, "rgba(30,120,154,0)");
  glowContext.fillStyle = gradient; glowContext.fillRect(0, 0, 128, 128);
  const glowTexture = new THREE.CanvasTexture(glowCanvas); textures.add(glowTexture);
  function aura(parent, color, scale, y = 0) {
    const material = new THREE.SpriteMaterial({ map: glowTexture, color, transparent: true, opacity: .55,
      depthWrite: false, blending: THREE.AdditiveBlending }); materials.add(material);
    const sprite = new THREE.Sprite(material); sprite.scale.set(scale, scale, 1); sprite.position.y = y; parent.add(sprite); return sprite;
  }

  // A rectangular engineering desk, with one controller and eight workstations.
  // The organization is a role illustration, not a measured concurrency/DAG result.
  const metal = mat(0x254158, { roughness: .5, metalness: .3 }), dark = mat(0x142b40, { roughness: .5 });
  const platform = new THREE.Group(); persistent.add(platform);
  box(platform, [18.7, .22, 13.7], dark, [0, -1.7, 0]);
  box(platform, [18.5, .08, 13.5], metal, [0, -1.54, 0]);
  const border = basic(0x85aabc, { transparent: true, opacity: .65 });
  box(platform, [18.5, .012, .018], border, [0, -1.49, -6.72]);
  box(platform, [18.5, .012, .018], border, [0, -1.49, 6.72]);
  box(platform, [.018, .012, 13.5], border, [-9.22, -1.49, 0]);
  box(platform, [.018, .012, 13.5], border, [9.22, -1.49, 0]);
  for (let x = -8; x <= 8; x += 2)
    line(platform, [new THREE.Vector3(x, -1.49, -6.4), new THREE.Vector3(x, -1.49, 6.4)], 0x89a8bb, .23);
  for (let z = -6; z <= 6; z += 2)
    line(platform, [new THREE.Vector3(-8.9, -1.49, z), new THREE.Vector3(8.9, -1.49, z)], 0x89a8bb, .23);

  let theta = .12, phi = .83, distance = 27;
  const target = new THREE.Vector3(0, -.3, .4), desiredTarget = target.clone();
  let desiredDistance = distance, easing = false, frame = 0, hidden = document.hidden, width = 1, height = 1;
  let compact = true;
  const reducedQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  let reduceMotion = reducedQuery.matches;
  const raycaster = new THREE.Raycaster(), pointer = new THREE.Vector2(), projection = new THREE.Vector3();
  let pointerStart = null, dragging = false, hovered = null;
  function listen(element, event, handler, options) {
    element.addEventListener(event, handler, options); listeners.push(() => element.removeEventListener(event, handler, options));
  }
  listen(style, "load", () => requestRender());
  function cameraUpdate() {
    const x = Math.sin(theta) * Math.sin(phi) * distance;
    const y = Math.cos(phi) * distance;
    const z = Math.cos(theta) * Math.sin(phi) * distance;
    camera.position.copy(target).add(new THREE.Vector3(x, y, z)); camera.lookAt(target); camera.updateMatrixWorld();
  }
  function projectLabel(point) {
    projection.copy(point).project(camera);
    return { x: (projection.x * .5 + .5) * width, y: (-projection.y * .5 + .5) * height,
      visible: projection.z > -1 && projection.z < 1 };
  }
  function labelsUpdate() {
    const used = [];
    const prioritized = [...records.values()].sort((a, b) => Number(b.id === state.selected) - Number(a.id === state.selected));
    for (const record of prioritized) {
      const anchor = record.position.clone(); anchor.y += record.id === "plan-workflow" ? 2.35 : 1.7;
      const p = projectLabel(anchor);
      const w = record.label.offsetWidth || 130, h = record.label.offsetHeight || 33;
      const x = clamp(p.x - w / 2, 10, Math.max(10, width - w - 10));
      const y = p.y - h / 2;
      const important = record.id === "plan-workflow" || record.id === state.selected || record.id === hovered;
      const collides = used.some((rect) => x < rect.x + rect.w + 6 && x + w + 6 > rect.x && y < rect.y + rect.h + 6 && y + h + 6 > rect.y);
      // At narrow widths fixed UI role buttons carry the full inventory. Do not
      // shrink or scatter all nine labels to force them into the same viewport.
      const visible = p.visible && (width >= 1050 || important) && !collides
        && p.x > 0 && p.x < width && y > 12 && y < height - h - 12;
      record.label.style.visibility = visible ? "visible" : "hidden";
      record.label.style.transform = `translate(${Math.round(x)}px,${Math.round(y)}px)`;
      record.label.style.zIndex = record.id === state.selected ? "6" : "2";
      if (visible) used.push({ x, y, w, h });
    }
    coreLabel.style.visibility = "hidden";
  }
  function render() {
    frame = 0;
    if (disposed || hidden) return;
    if (easing) {
      const factor = reduceMotion ? 1 : .16;
      target.lerp(desiredTarget, factor); distance += (desiredDistance - distance) * factor;
      if (target.distanceToSquared(desiredTarget) < .00015 && Math.abs(desiredDistance - distance) < .015) {
        target.copy(desiredTarget); distance = desiredDistance; easing = false;
      }
    }
    cameraUpdate(); renderer.render(scene, camera); labelsUpdate();
    if (easing) requestRender();
  }
  function requestRender() { if (!disposed && !hidden && !frame) frame = requestAnimationFrame(render); }
  function resize() {
    const bounds = container.getBoundingClientRect();
    width = Math.max(1, bounds.width); height = Math.max(1, bounds.height);
    renderer.setSize(width, height, false); camera.aspect = width / height;
    camera.updateProjectionMatrix();
    const nextCompact = width < 760;
    platform.scale.set(nextCompact ? .61 : 1, 1, nextCompact ? 1.75 : 1);
    platform.position.z = nextCompact ? 1.35 : 0;
    if (compact !== nextCompact) {
      compact = nextCompact;
      if (state.nodes.length) setNodes(state.nodes);
      reset();
    }
    requestRender();
  }
  function select(id) {
    state.selected = IDS.includes(id) ? id : null;
    container.dataset.selectedSkill = state.selected || "";
    for (const record of records.values()) {
      const selected = record.id === state.selected;
      record.label.dataset.selected = String(selected); record.label.setAttribute("aria-pressed", String(selected));
      record.selection.visible = selected;
      record.shell.emissiveIntensity = selected ? .1 : .015;
      record.beacon.material.opacity = selected ? .9 : .3;
    }
    for (const edge of edges) {
      const selected = state.selected !== "plan-workflow" && edge.to === state.selected;
      edge.object.material.opacity = selected ? .56 : 0;
      edge.arrow.material.opacity = selected ? .65 : 0;
    }
    requestRender();
  }
  function focus(id = state.selected) {
    const record = records.get(id);
    if (!record) return;
    select(id); desiredTarget.copy(record.position); desiredTarget.y += .2;
    desiredDistance = compact ? 13 : 11.8; easing = true; requestRender();
  }
  function reset() {
    desiredTarget.set(0, -.3, compact ? 1.2 : .4); desiredDistance = compact ? 30 : 27;
    theta = .12; phi = compact ? .72 : .83; easing = true; requestRender();
  }
  function zoom(delta) {
    if (!Number.isFinite(Number(delta))) return;
    desiredDistance = clamp(desiredDistance * Math.pow(.86, clamp(Number(delta), -4, 4)), 8.5, 48);
    easing = true; requestRender();
  }

  function makeSymbol(parent, index, accent) {
    const bright = mat(accent, { emissive: accent, emissiveIntensity: .21, metalness: .38, roughness: .22 });
    const group = new THREE.Group(); group.position.y = .34; parent.add(group);
    if (index === 4) {
      for (let row = 0; row < 3; row++) for (let col = 0; col < 3; col++) {
        box(group, [.18, .18, .18], bright, [(col - 1) * .25, (row - 1) * .25, 0]);
      }
    } else if (index === 6) {
      box(group, [.2, .78, .2], bright); box(group, [.78, .2, .2], bright);
      group.rotation.z = Math.PI / 4;
    } else if (index === 7 || index === 8) {
      const arc = mesh(group, new THREE.TorusGeometry(.35, .065, 8, 32, Math.PI * 1.6), bright);
      arc.rotation.z = .25;
      const stick = box(group, [.14, .4, .14], bright, [.18, -.08, .08]); stick.rotation.z = -.75;
    } else if (index === 2) {
      for (const sign of [-1, 1]) {
        const a = box(group, [.12, .5, .12], bright, [sign * .31, .15, 0]); a.rotation.z = sign * -.65;
        const b = box(group, [.12, .5, .12], bright, [sign * .31, -.15, 0]); b.rotation.z = sign * .65;
      }
    } else {
      const geometry = index === 1 ? new THREE.OctahedronGeometry(.47, 0) : new THREE.IcosahedronGeometry(.45, 0);
      const icon = mesh(group, geometry, bright); icon.rotation.set(.2, .4 + index, .15); icon.scale.y = index === 5 ? .72 : 1;
    }
    return group;
  }
  function buildNode(node, index) {
    const layout = workstationLayout(width)[index];
    const controller = layout.controller;
    const position = new THREE.Vector3(...layout.position);
    const group = new THREE.Group(); group.position.copy(position); graph.add(group);
    const accent = controller ? 0xdac18c : ACCENTS[index];
    const scale = layout.scale;
    const desk = new THREE.Group(); desk.scale.set(scale, controller ? 1.2 : 1, scale); group.add(desk);
    const shell = mat(controller ? 0x34556a : 0x243d54,
      { emissive: accent, emissiveIntensity: .015, metalness: .3, roughness: .42 });
    // Actual desk, monitor, keyboard and two support pedestals; no floating pods.
    box(desk, [2.8, .14, 1.6], shell, [0, -.08, 0]);
    for (const sign of [-1, 1]) {
      box(desk, [.28, .95, 1.15], dark, [sign * 1.04, -.61, 0]);
      box(desk, [.68, .08, 1.32], shell, [sign * 1.04, -1.12, 0]);
    }
    box(desk, [.85, .025, .3], mat(0x6c8b94, { roughness: .7 }), [0, .01, .47]);
    box(desk, [.13, .3, .13], shell, [0, .13, -.29]);
    const screen = new THREE.Group(); screen.position.set(0, .74, -.35); desk.add(screen);
    screen.rotation.x = -.08;
    box(screen, [1.76, 1.02, .13], dark);
    const display = mat(controller ? 0x287c88 : 0x226886,
      { emissive: controller ? 0x376e71 : 0x225565, emissiveIntensity: .3, metalness: .18, roughness: .3 });
    box(screen, [1.59, .84, .014], display, [0, 0, .075]);
    for (let n = 0; n < 4; n++)
      box(screen, [.45 + (n % 2) * .34, .025, .012], basic(n === 0 ? accent : 0x648b97),
        [n % 2 ? .05 : -.12, .24 - n * .15, .089]);
    const symbol = makeSymbol(screen, index, accent); symbol.scale.setScalar(.22); symbol.position.set(.56, -.14, .11);
    if (controller) {
      // Broad secondary monitor makes the one real plan-workflow controller
      // visually distinct; it remains the same selectable catalog record.
      const wing = screen.clone(); wing.scale.set(.64, .74, .8); wing.position.set(1.33, .66, -.15); wing.rotation.y = -.27; desk.add(wing);
      const wing2 = screen.clone(); wing2.scale.set(.64, .74, .8); wing2.position.set(-1.33, .66, -.15); wing2.rotation.y = .27; desk.add(wing2);
    }
    const known = ["produced", "executed", "completed", "passed"].includes(node.status);
    mesh(desk, new THREE.SphereGeometry(.055, 10, 8), basic(known ? 0x9ed8ba : 0x728b98), [.67, .82, -.27]);
    const selection = box(group, [3.08 * scale, .025, 1.83 * scale],
      basic(0x77c6ad, { transparent: true, opacity: .28 }), [0, -1.38, 0]); selection.visible = false;
    const beacon = aura(group, accent, controller ? 4 : 2.2, .1); beacon.material.opacity = .3;
    const hit = box(group, [3 * scale, 2.6 * scale, 1.9 * scale],
      basic(0xffffff, { visible: false }), [0, .3, 0]);
    hit.userData.skillId = node.id; clickable.push(hit);
    const label = makeElement("button", "fea-space-tag"); label.type = "button";
    label.style.setProperty("--node-color", `#${new THREE.Color(accent).getHexString()}`);
    label.dataset.skillId = node.id; label.setAttribute("aria-pressed", "false");
    label.setAttribute("aria-label", `${index + 1} ${node.label}，${known ? "已有真实产物" : "状态待核实"}，第${node.attempt || 1}次尝试。点击选择，双击聚焦。`);
    label.append(makeElement("span", "", controller ? "CodeAgent 总控" : node.shortLabel || node.label));
    const onClick = (event) => { event.stopPropagation(); select(node.id); onSelect(node.id); };
    const onDouble = (event) => { event.stopPropagation(); focus(node.id); };
    label.addEventListener("click", onClick); label.addEventListener("dblclick", onDouble);
    labelListeners.push(() => { label.removeEventListener("click", onClick); label.removeEventListener("dblclick", onDouble); });
    labelLayer.append(label);
    records.set(node.id, { id: node.id, node, group, position, label, selection, shell, beacon });
  }
  function addRoute(from, to) {
    const a = records.get(from).position.clone(), b = records.get(to).position.clone();
    a.y = b.y = -1.1;
    const middle = a.clone().add(b).multiplyScalar(.5); middle.y = -.88;
    const curve = new THREE.QuadraticBezierCurve3(a, middle, b);
    const points = curve.getPoints(36);
    const object = line(graph, points, 0x72acae, .46);
    const arrowMat = basic(0xa4d5cc, { transparent: true, opacity: .5, depthWrite: false });
    const arrow = mesh(graph, new THREE.ConeGeometry(.075, .24, 6), arrowMat);
    arrow.position.copy(curve.getPoint(.7)); arrow.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), curve.getTangent(.7).normalize());
    edges.push({ from, to, object, arrow });
  }
  function setNodes(nodes) {
    if (!Array.isArray(nodes) || disposed) return;
    // Exactly the nine catalog capabilities; no fabricated employees or DAG edges.
    const items = IDS.map((id, index) => nodes.find((node) => node.id === id) || { id, label: LABELS[index], status: "unknown", llm: id !== "verify-delivery" });
    for (const record of records.values()) record.label.remove();
    labelListeners.splice(0).forEach((remove) => remove());
    graphResources.geometries.forEach((geometry) => { geometry.dispose(); geometries.delete(geometry); });
    graphResources.materials.forEach((material) => { material.dispose(); materials.delete(material); });
    records.clear(); edges.length = 0; graph.clear(); clickable.length = 0;
    const oldGeometries = new Set(geometries), oldMaterials = new Set(materials);
    state.nodes = items;
    items.forEach((node, index) => buildNode({ ...node, label: node.label || LABELS[index] }, index));
    // Show only the selected role-to-controller responsibility connector. It
    // is not an assertion that these Skills ran concurrently or form an ILP DAG.
    for (let i = 1; i < IDS.length; i++) addRoute(IDS[0], IDS[i]);
    graphResources = { geometries: new Set([...geometries].filter((item) => !oldGeometries.has(item))),
      materials: new Set([...materials].filter((item) => !oldMaterials.has(item))) };
    select(state.selected || "plan-workflow"); requestRender();
  }

  function pick(event) {
    const rect = canvas.getBoundingClientRect();
    pointer.set((event.clientX - rect.left) / rect.width * 2 - 1, -(event.clientY - rect.top) / rect.height * 2 + 1);
    raycaster.setFromCamera(pointer, camera);
    return raycaster.intersectObjects(clickable, false)[0]?.object.userData.skillId || null;
  }
  listen(canvas, "pointerdown", (event) => {
    if (event.button !== 0) return;
    canvas.focus({ preventScroll: true }); canvas.setPointerCapture(event.pointerId);
    pointerStart = { x: event.clientX, y: event.clientY, lastX: event.clientX, lastY: event.clientY, id: event.pointerId };
    dragging = false; easing = false; desiredTarget.copy(target); desiredDistance = distance;
  });
  listen(canvas, "pointermove", (event) => {
    if (pointerStart?.id === event.pointerId) {
      const dx = event.clientX - pointerStart.lastX, dy = event.clientY - pointerStart.lastY;
      if (Math.hypot(event.clientX - pointerStart.x, event.clientY - pointerStart.y) > 5) dragging = true;
      if (dragging) { theta -= dx * .006; phi = clamp(phi - dy * .004, .3, 1.44); requestRender(); }
      pointerStart.lastX = event.clientX; pointerStart.lastY = event.clientY;
    } else {
      const id = pick(event);
      if (hovered !== id) { hovered = id; canvas.style.cursor = id ? "pointer" : "grab"; requestRender(); }
    }
  });
  listen(canvas, "pointerup", (event) => {
    if (pointerStart?.id !== event.pointerId) return;
    if (!dragging) { const id = pick(event); if (id) { select(id); onSelect(id); } }
    if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
    pointerStart = null; dragging = false;
  });
  listen(canvas, "pointercancel", () => { pointerStart = null; dragging = false; });
  listen(canvas, "dblclick", (event) => { const id = pick(event); if (id) { select(id); onSelect(id); focus(id); } });
  listen(canvas, "wheel", (event) => {
    if (!event.ctrlKey && !event.metaKey) return;
    event.preventDefault();
    zoom(clamp(-event.deltaY / 180, -1, 1));
  }, { passive: false });
  listen(canvas, "keydown", (event) => {
    let handled = true;
    if (event.key === "ArrowLeft") theta += .12;
    else if (event.key === "ArrowRight") theta -= .12;
    else if (event.key === "ArrowUp") phi = clamp(phi - .08, .3, 1.44);
    else if (event.key === "ArrowDown") phi = clamp(phi + .08, .3, 1.44);
    else if (["+", "="].includes(event.key)) zoom(1);
    else if (event.key === "-") zoom(-1);
    else if (["Home", "0"].includes(event.key)) reset();
    else if (event.key === "Enter") focus();
    else if (event.key === "Escape") reset();
    else handled = false;
    if (handled) { event.preventDefault(); requestRender(); }
  });
  listen(document, "visibilitychange", () => { hidden = document.hidden; if (hidden && frame) { cancelAnimationFrame(frame); frame = 0; } else requestRender(); });
  const motionChange = (event) => { reduceMotion = event.matches; requestRender(); };
  if (reducedQuery.addEventListener) listen(reducedQuery, "change", motionChange);
  listen(canvas, "webglcontextlost", (event) => { event.preventDefault(); mode({ mode: "fallback", reason: "WebGL 上下文已丢失，请刷新页面恢复三维视图" }); });
  listen(canvas, "webglcontextrestored", () => { mode({ mode: "webgl" }); requestRender(); });
  const observer = new ResizeObserver(resize); observer.observe(container);
  resize(); reset(); mode({ mode: "webgl" }); requestRender();
  return { setNodes, select, focus, reset, zoom,
    destroy() {
      if (disposed) return; disposed = true; if (frame) cancelAnimationFrame(frame);
      observer.disconnect(); listeners.forEach((remove) => remove()); labelListeners.forEach((remove) => remove());
      geometries.forEach((geometry) => geometry.dispose()); materials.forEach((material) => material.dispose());
      textures.forEach((texture) => texture.dispose()); renderer.dispose();
      canvas.remove(); labelLayer.remove(); style.remove();
      container.style.position = oldPosition; container.style.overflow = oldOverflow;
      delete container.dataset.renderMode; delete container.dataset.selectedSkill;
    }
  };
}
