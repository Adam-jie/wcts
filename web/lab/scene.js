import * as THREE from "./vendor/three.module.min.js";
import { graphPositions, statusClass, statusLabel } from "./lab-core.mjs";

const COLORS = {
  success: 0xbddc8c, running: 0xf0c780, failed: 0xef9d8c, pending: 0x7a9ca4,
};
const ROLES = {
  "plan-workflow": "总控规划", "explore-repository": "仓库探索",
  "ground-code": "代码定位", "analyze-requirements": "需求分析",
  "generate-tests": "测试设计", "diagnose-results": "故障诊断",
  "repair-code": "代码修复", "review-patch": "独立评审",
  "verify-delivery": "交付验收",
};
const UP = new THREE.Vector3(0, 1, 0);

// The diorama represents report nodes, not simulated employee activity.
function spatialGraph(value) {
  const graph = graphPositions(value);
  const controllers = graph.nodes.filter((node) => node.kind === "controller");
  const stations = graph.nodes.filter((node) => node.kind === "capability");
  const employees = graph.nodes.filter((node) => !["controller", "capability"].includes(node.kind));
  const positions = new Map();
  controllers.forEach((node, index) => positions.set(node.id, {
    x: (index - (controllers.length - 1) / 2) * 3.5, y: 0.72, z: -7.0,
  }));
  const horseshoe = [
    [-9, -3.6], [-3, -3.6], [3, -3.6], [9, -3.6],
    [9, 0.6], [3, 0.6], [-3, 0.6], [-9, 0.6],
  ];
  stations.forEach((node, index) => {
    const [x, z] = horseshoe[index % horseshoe.length];
    const outer = Math.floor(index / horseshoe.length) * 3.2;
    positions.set(node.id, { x: x + Math.sign(x || 1) * outer, y: -0.08, z: z - outer });
  });
  const levels = new Map();
  const originalYs = [...new Set(employees.map((node) => graph.positions.get(node.id)?.y || 0))].sort((a, b) => b - a);
  for (const node of employees) {
    const level = originalYs.indexOf(graph.positions.get(node.id)?.y || 0);
    if (!levels.has(level)) levels.set(level, []);
    levels.get(level).push(node);
  }
  let rowOffset = 0;
  for (const [, group] of [...levels].sort(([a], [b]) => a - b)) {
    group.forEach((node, index) => {
      const row = Math.floor(index / 3), inRow = Math.min(3, group.length - row * 3);
      positions.set(node.id, {
        x: ((index % 3) - (inRow - 1) / 2) * (inRow === 2 ? 9 : 6),
        y: -0.08, z: 4.5 + (rowOffset + row) * 3.3,
      });
    });
    rowOffset += Math.ceil(group.length / 3);
  }
  return { ...graph, positions };
}

function nodeTitle(node) {
  if (node.kind === "controller") return "总控 · 任务调度";
  if (node.kind !== "employee") return ROLES[node.id] || node.label || node.id;
  const role = String(node.role || "");
  if (/explor/.test(role)) {
    const subject = `${node.task_id || ""} ${node.label || ""}`.toLowerCase();
    if (/export|print|compat|导出|兼容/.test(subject)) return "探索员 · 导出兼容";
    if (/csv|tsv|input|literal|delimiter|输入|字面/.test(subject)) return "探索员 · 数据输入";
    return "探索员 · 仓库调查";
  }
  if (/repair|coding|implement/.test(role)) return "工程师 · 代码修复";
  if (/review/.test(role)) return "评审员 · 补丁审查";
  if (/test|verif/.test(role)) return "测试员 · 执行验证";
  if (/diagnos|analy/.test(role)) return "分析员 · 问题定位";
  return "协作员工";
}

export function createScene(container, onSelect, onMode) {
  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "low-power" });
  } catch (error) {
    console.warn("TestFix 3D renderer unavailable:", error instanceof Error ? error.message : String(error));
    return null;
  }
  const originalTouchAction = container.style.touchAction;
  container.style.touchAction = "none";
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.7));
  renderer.setClearColor(0, 0);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.22;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.shadowMap.autoUpdate = false;
  const canvas = renderer.domElement;
  canvas.tabIndex = 0;
  canvas.setAttribute("aria-label", "立体协作空间。拖动旋转，滚轮缩放，点击工作位查看真实记录，双击聚焦。方向键旋转，加减缩放，0 恢复全景。");
  const labels = document.createElement("div");
  labels.className = "scene-labels";
  container.append(canvas, labels);

  const scene = new THREE.Scene(), world = new THREE.Group();
  const camera = new THREE.PerspectiveCamera(39, 1, 0.1, 160);
  scene.add(world);
  scene.add(new THREE.HemisphereLight(0xe7f2da, 0x172f3a, 2.1));
  const key = new THREE.DirectionalLight(0xfff1d1, 3.1);
  key.position.set(-6, 16, 9);
  key.castShadow = true;
  key.shadow.mapSize.set(1024, 1024);
  Object.assign(key.shadow.camera, { left: -17, right: 17, top: 18, bottom: -18, near: 1, far: 50 });
  key.shadow.camera.updateProjectionMatrix();
  key.shadow.normalBias = 0.035;
  key.shadow.bias = -0.0003;
  key.shadow.radius = 3;
  scene.add(key);
  const rim = new THREE.DirectionalLight(0x87d4d4, 2.3);
  rim.position.set(8, 8, -12);
  scene.add(rim);

  let graph = { nodes: [], edges: [], positions: new Map() };
  let nodeObjects = [], edgeObjects = [], selected = null;
  let graphKey = "", structureKey = "";
  let theta = 0.08, phi = 0.84, distance = 32;
  let overview = true, fitPoints = [];
  const target = new THREE.Vector3(0, 0.1, 0.8), overviewTarget = target.clone();
  let focusGoal = null, pointerStart = null, drag = false, disposed = false, frame = 0;
  const motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  let reduceMotion = motionQuery.matches;
  const raycaster = new THREE.Raycaster(), pointer = new THREE.Vector2(), projected = new THREE.Vector3();
  const geometries = new Set(), materials = new Set(), labelListeners = [];

  function material(color, options = {}) {
    const result = new THREE.MeshStandardMaterial({ color, roughness: 0.6, metalness: 0.18, ...options });
    materials.add(result);
    return result;
  }
  function mesh(parent, geometry, mat, x = 0, y = 0, z = 0) {
    geometries.add(geometry);
    const result = new THREE.Mesh(geometry, mat);
    result.position.set(x, y, z);
    result.castShadow = true;
    result.receiveShadow = true;
    parent.add(result);
    return result;
  }
  function box(parent, w, h, d, mat, x = 0, y = 0, z = 0) {
    return mesh(parent, new THREE.BoxGeometry(w, h, d), mat, x, y, z);
  }
  function cylinder(parent, radius, height, mat, x = 0, y = 0, z = 0, segments = 32) {
    return mesh(parent, new THREE.CylinderGeometry(radius, radius, height, segments), mat, x, y, z);
  }
  function roundedBlock(parent, width, depth, height, mat, y, radius = 0.28) {
    const shape = new THREE.Shape(), x = -width / 2, z = -depth / 2;
    const r = Math.min(radius, width / 2, depth / 2);
    shape.moveTo(x + r, z);
    shape.lineTo(x + width - r, z);
    shape.quadraticCurveTo(x + width, z, x + width, z + r);
    shape.lineTo(x + width, z + depth - r);
    shape.quadraticCurveTo(x + width, z + depth, x + width - r, z + depth);
    shape.lineTo(x + r, z + depth);
    shape.quadraticCurveTo(x, z + depth, x, z + depth - r);
    shape.lineTo(x, z + r);
    shape.quadraticCurveTo(x, z, x + r, z);
    const geometry = new THREE.ExtrudeGeometry(shape, {
      depth: height, bevelEnabled: true, bevelSize: 0.035, bevelThickness: 0.035,
      bevelSegments: 2, curveSegments: 5,
    });
    geometry.rotateX(-Math.PI / 2);
    return mesh(parent, geometry, mat, 0, y, 0);
  }
  function ring(parent, radius, thickness, mat, y) {
    const result = mesh(parent, new THREE.TorusGeometry(radius, thickness, 6, 56), mat, 0, y, 0);
    result.rotation.x = -Math.PI / 2;
    result.castShadow = false;
    return result;
  }
  function removeGraph() {
    for (const [element, handler] of labelListeners) element.removeEventListener("click", handler);
    labelListeners.length = 0;
    world.clear();
    geometries.forEach((geometry) => geometry.dispose());
    materials.forEach((mat) => mat.dispose());
    geometries.clear();
    materials.clear();
    nodeObjects = [];
    edgeObjects = [];
    labels.replaceChildren();
  }
  function buildFloor() {
    const positions = [...graph.positions.values()];
    const maxX = Math.max(12.0, ...positions.map((p) => Math.abs(p.x) + 1.7));
    const minZ = Math.min(-8.5, ...positions.map((p) => p.z - 1.5));
    const maxZ = Math.max(8.0, ...positions.map((p) => p.z + 1.7));
    const floor = new THREE.Group();
    floor.position.z = (minZ + maxZ) / 2;
    world.add(floor);
    const width = maxX * 2, depth = maxZ - minZ;
    roundedBlock(floor, width + 0.4, depth + 0.4, 0.28,
      material(0x10232c, { metalness: 0.4, roughness: 0.48 }), -0.7, 1.2);
    roundedBlock(floor, width, depth, 0.18,
      material(0x29494c, { roughness: 0.86 }), -0.4, 1.1);
    roundedBlock(floor, width + 0.13, depth + 0.13, 0.045,
      material(0x4e7471, { roughness: 0.55 }), -0.44, 1.1);
    const lane = material(0x446564, { transparent: true, opacity: 0.4, depthWrite: false });
    for (let x = -Math.floor(maxX / 2) * 2; x < maxX; x += 2)
      box(floor, 0.014, 0.006, depth - 1.1, lane, x, -0.168, 0).castShadow = false;
    for (let z = -depth / 2 + 1; z < depth / 2 - 0.5; z += 2)
      box(floor, width - 1.0, 0.006, 0.014, lane, 0, -0.165, z).castShadow = false;
    overviewTarget.set(0, 0.1, (minZ + maxZ) / 2 + Math.min(1.7, depth * 0.1));
    fitPoints = [];
    for (const x of [-maxX - 0.2, maxX + 0.2])
      for (const z of [minZ - 0.2, maxZ + 0.2]) fitPoints.push(new THREE.Vector3(x, -0.7, z));
  }
  function statusMaterial(node) {
    const color = COLORS[statusClass(node.status)] || COLORS.pending;
    return material(color, {
      emissive: color, emissiveIntensity: node.status === "running" ? 0.46 : 0.16,
      roughness: 0.4, metalness: 0.3,
    });
  }
  function terminal(parent, node, elevated = false) {
    const metal = material(0x263e49, { metalness: 0.5, roughness: 0.42 });
    const frameMat = material(0x829b9b, { metalness: 0.62, roughness: 0.33 });
    const displayMat = material(0x234c59, { emissive: 0x2a6470, emissiveIntensity: 0.45, roughness: 0.3 });
    const indicatorMat = statusMaterial(node);
    box(parent, elevated ? 1.9 : 1.35, 0.14, elevated ? 0.92 : 0.78, metal, 0, 0.71, 0.02);
    box(parent, 0.12, 0.62, 0.48, metal, -0.49, 0.36, 0.02);
    box(parent, 0.12, 0.62, 0.48, metal, 0.49, 0.36, 0.02);
    box(parent, 0.11, 0.28, 0.12, frameMat, 0, 0.94, -0.17);
    const monitor = new THREE.Group();
    monitor.position.set(0, 1.16, -0.2);
    monitor.rotation.x = -0.14;
    parent.add(monitor);
    box(monitor, elevated ? 1.25 : 0.94, 0.65, 0.11, metal);
    box(monitor, elevated ? 1.12 : 0.82, 0.51, 0.012, displayMat, 0, 0, 0.062);
    // Static terminal glyphs are decoration, never invented progress metrics.
    box(monitor, 0.08, 0.16, 0.016, indicatorMat, -0.22, 0.04, 0.074).castShadow = false;
    box(monitor, 0.28, 0.026, 0.016, frameMat, 0.02, 0.08, 0.074).castShadow = false;
    box(monitor, 0.35, 0.026, 0.016, frameMat, 0.05, -0.035, 0.074).castShadow = false;
    box(parent, 0.6, 0.035, 0.23, frameMat, 0, 0.807, 0.22);
    box(parent, 0.22, 0.045, 0.075, indicatorMat, 0.45, 0.81, 0.26);
    return indicatorMat;
  }
  function employee(parent, node) {
    // Only report nodes explicitly typed employee get a human figure.
    const figure = new THREE.Group();
    figure.position.set(0, 0, 0.72);
    parent.add(figure);
    const uniform = material(/repair/.test(node.role || "") ? 0x7898bc : 0x77aaa2);
    const skin = material(0xe9d6b9, { roughness: 0.88, metalness: 0 });
    const dark = material(0x253b47, { roughness: 0.84 });
    const chair = material(0x536c78, { roughness: 0.72 });
    box(figure, 0.5, 0.12, 0.46, chair, 0, 0.45, 0.12);
    box(figure, 0.5, 0.48, 0.1, chair, 0, 0.71, 0.34);
    cylinder(figure, 0.06, 0.38, dark, 0, 0.2, 0.14);
    cylinder(figure, 0.34, 0.045, dark, 0, 0.03, 0.14, 20);
    const torso = mesh(figure, new THREE.CapsuleGeometry(0.18, 0.26, 4, 10), uniform, 0, 0.82, 0);
    torso.scale.set(1.2, 1, 0.8);
    mesh(figure, new THREE.SphereGeometry(0.185, 14, 10), skin, 0, 1.23, -0.015);
    const hair = mesh(figure, new THREE.SphereGeometry(0.188, 14, 8, 0, Math.PI * 2, 0, Math.PI * 0.52), dark, 0, 1.27, -0.015);
    hair.rotation.x = 0.2;
    for (const side of [-1, 1]) {
      const arm = mesh(figure, new THREE.CapsuleGeometry(0.065, 0.26, 3, 8), uniform, side * 0.22, 0.82, -0.2);
      arm.rotation.x = -0.95;
      mesh(figure, new THREE.SphereGeometry(0.065, 8, 6), skin, side * 0.22, 0.76, -0.38);
      const leg = box(figure, 0.13, 0.38, 0.15, dark, side * 0.12, 0.3, -0.09);
      leg.rotation.x = -0.1;
    }
    return figure;
  }
  function makeNode(node) {
    const p = graph.positions.get(node.id);
    if (!p) return;
    const group = new THREE.Group();
    group.position.set(p.x, p.y, p.z);
    group.userData.nodeId = node.id;
    world.add(group);
    const color = COLORS[statusClass(node.status)] || COLORS.pending;
    const baseMat = material(0x375757, { roughness: 0.58 });
    const edgeMat = material(color, { transparent: true, opacity: 0.52, depthWrite: false, emissive: color, emissiveIntensity: 0.18 });
    const isController = node.kind === "controller", isEmployee = node.kind === "employee";
    let indicator;
    if (isController) {
      cylinder(group, 1.65, 0.84, material(0x284b4b, { roughness: 0.65 }), 0, -0.48, 0, 64);
      cylinder(group, 1.63, 0.25, material(0x203e43, { roughness: 0.48 }), 0, -0.1, 0, 64);
      cylinder(group, 1.44, 0.19, material(0x51736c, { metalness: 0.35 }), 0, 0.06, 0, 64);
      const consoleGroup = new THREE.Group();
      consoleGroup.position.y = 0.16;
      group.add(consoleGroup);
      indicator = terminal(consoleGroup, node, true);
      ring(group, 1.48, 0.035, edgeMat, 0.19);
      const core = mesh(group, new THREE.OctahedronGeometry(0.19), indicator, 0, 1.97, -0.15);
      core.rotation.y = Math.PI / 4;
    } else {
      roundedBlock(group, isEmployee ? 2.15 : 1.9, isEmployee ? 2.35 : 1.7, 0.16, baseMat, -0.1, 0.27);
      box(group, isEmployee ? 1.85 : 1.6, 0.055, 0.07, edgeMat, 0, 0.086, isEmployee ? 1.05 : 0.74);
      const consoleGroup = new THREE.Group();
      consoleGroup.position.z = isEmployee ? -0.28 : 0;
      group.add(consoleGroup);
      indicator = terminal(consoleGroup, node);
      if (isEmployee) employee(group, node);
    }
    const selectionMat = material(0xd1edb0, { transparent: true, opacity: 0, depthWrite: false, emissive: 0xc4eb9b, emissiveIntensity: 0.5 });
    const halo = ring(group, isController ? 1.78 : 1.48, 0.035, selectionMat, -0.08);
    const label = document.createElement("button");
    label.className = `scene-node-label scene-node-${node.kind || "capability"}`;
    label.type = "button";
    const title = nodeTitle(node);
    label.setAttribute("aria-label", `${title}，${statusLabel(node.status)}，${node.task_id || node.label}。入度 ${node.in_degree ?? "未记录"}，出度 ${node.out_degree ?? "未记录"}`);
    label.title = `${node.task_id || node.label || node.id}\n${statusLabel(node.status)} · 单击查看记录，双击工作位聚焦`;
    const dot = document.createElement("span");
    dot.className = "node-color";
    dot.style.setProperty("--node-color", `#${color.toString(16).padStart(6, "0")}`);
    const copy = document.createElement("span");
    copy.className = "scene-node-copy";
    const name = document.createElement("span");
    name.className = "scene-node-title";
    name.textContent = title;
    const state = document.createElement("span");
    state.className = "scene-node-status";
    state.textContent = `${statusLabel(node.status)}${isEmployee ? ` · 入${node.in_degree ?? "—"} 出${node.out_degree ?? "—"}` : ""}`;
    copy.append(name, state);
    label.append(dot, copy);
    const click = () => onSelect(node.id);
    label.addEventListener("click", click);
    labelListeners.push([label, click]);
    labels.append(label);
    nodeObjects.push({
      node, group, halo, selectionMat, indicator, edgeMat, label,
      position: new THREE.Vector3(p.x, p.y + (isController ? 2.75 : -0.13), p.z + (isController ? -0.2 : isEmployee ? 1.52 : 1.11)),
    });
  }
  function makeEdge(edge) {
    const source = graph.positions.get(edge.source), destination = graph.positions.get(edge.target);
    if (!source || !destination) return;
    const a = new THREE.Vector3(source.x, source.y + 0.22, source.z);
    const b = new THREE.Vector3(destination.x, destination.y + 0.22, destination.z);
    const delta = b.clone().sub(a), length = delta.length();
    if (length < 0.2) return;
    const sourceController = graph.nodes.find((n) => n.id === edge.source)?.kind === "controller";
    const direction = delta.normalize();
    a.addScaledVector(direction, Math.min(sourceController ? 1.72 : 1.13, length * 0.24));
    b.addScaledVector(direction, -Math.min(1.32, length * 0.24));
    const isEmployee = /dependency/.test(edge.kind || "") || String(edge.source).startsWith("employee:");
    const isDelegation = /delegat|handoff/.test(edge.kind || "");
    const middle = a.clone().lerp(b, 0.5);
    middle.y += isDelegation ? 1.35 : isEmployee ? 0.32 : 0.12;
    const curve = new THREE.QuadraticBezierCurve3(a, middle, b);
    const color = isDelegation ? 0xe5c78b : isEmployee ? 0x8dcac7 : 0x6b928c;
    const lineMat = new THREE.LineBasicMaterial({ color, transparent: true, opacity: isDelegation ? 0.69 : 0.6 });
    materials.add(lineMat);
    const geometry = new THREE.BufferGeometry().setFromPoints(curve.getPoints(40));
    geometries.add(geometry);
    world.add(new THREE.Line(geometry, lineMat));
    const endDirection = curve.getTangent(0.98).normalize();
    const arrowMat = material(color, { emissive: color, emissiveIntensity: 0.2, transparent: true, opacity: 0.88 });
    const arrow = mesh(world, new THREE.ConeGeometry(0.115, 0.34, 8), arrowMat);
    arrow.position.copy(b).addScaledVector(endDirection, -0.14);
    arrow.quaternion.setFromUnitVectors(UP, endDirection);
    arrow.castShadow = false;
    edgeObjects.push({ edge, lineMat, arrowMat });
  }
  function requestDraw() {
    if (!disposed && !frame) frame = requestAnimationFrame(draw);
  }
  function updateCamera() {
    camera.position.set(
      target.x + distance * Math.sin(theta) * Math.cos(phi),
      target.y + distance * Math.sin(phi),
      target.z + distance * Math.cos(theta) * Math.cos(phi),
    );
    camera.lookAt(target);
    camera.updateMatrixWorld();
  }
  function fitOverview() {
    target.copy(overviewTarget);
    const labelX = Math.max(0.42, 1 - 160 / Math.max(container.clientWidth, 220));
    const labelY = Math.max(0.5, 1 - 46 / Math.max(container.clientHeight, 160));
    // Fit the actual projected stage and labels, including near-side perspective.
    // A fixed camera distance made wide panels look empty and labels overlap.
    for (distance = 16; distance < 120; distance += 0.4) {
      updateCamera();
      const stageFits = fitPoints.every((point) => {
        projected.copy(point).project(camera);
        return Math.abs(projected.x) < 0.95 && Math.abs(projected.y) < 0.95 && projected.z < 1;
      });
      const labelsFit = nodeObjects.every((item) => {
        projected.copy(item.position).project(camera);
        return Math.abs(projected.x) < labelX && Math.abs(projected.y) < labelY && projected.z < 1;
      });
      if (stageFits && labelsFit) break;
    }
  }
  function draw(time) {
    frame = 0;
    if (disposed || document.hidden) return;
    let animate = false;
    if (focusGoal) {
      if (reduceMotion) {
        target.copy(focusGoal.target);
        distance = focusGoal.distance;
        focusGoal = null;
      } else {
        target.lerp(focusGoal.target, 0.16);
        distance += (focusGoal.distance - distance) * 0.16;
        if (target.distanceTo(focusGoal.target) < 0.008 && Math.abs(distance - focusGoal.distance) < 0.008) {
          target.copy(focusGoal.target);
          distance = focusGoal.distance;
          focusGoal = null;
        } else animate = true;
      }
    }
    updateCamera();
    const width = container.clientWidth, height = container.clientHeight;
    for (const { node, label, position, indicator, selectionMat } of nodeObjects) {
      projected.copy(position).project(camera);
      label.style.left = `${(projected.x * 0.5 + 0.5) * width}px`;
      label.style.top = `${(-projected.y * 0.5 + 0.5) * height}px`;
      label.hidden = projected.z > 1 || projected.z < -1;
      label.style.zIndex = String(Math.round((1 - projected.z) * 10000));
      // Historical records remain still. Only a reported running node pulses.
      const running = node.status === "running" && !reduceMotion;
      indicator.emissiveIntensity = running ? 0.37 + Math.sin(time * 0.003) * 0.15 : 0.16;
      if (running) animate = true;
      if (node.id === selected) selectionMat.opacity = running ? 0.74 + Math.sin(time * 0.003) * 0.16 : 0.85;
    }
    renderer.render(scene, camera);
    if (animate) requestDraw();
  }
  function resize() {
    if (disposed) return;
    const { width, height } = container.getBoundingClientRect();
    if (!width || !height) return;
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    if (overview && graph.nodes.length) fitOverview();
    requestDraw();
  }
  const observer = new ResizeObserver(resize);
  observer.observe(container);
  function setGraph(value) {
    if (disposed) return;
    const next = spatialGraph(value);
    const nextKey = JSON.stringify({
      nodes: next.nodes.map(({ id, kind, role, label, task_id, status, in_degree, out_degree }) =>
        [id, kind, role, label, task_id, status, in_degree, out_degree]),
      edges: next.edges, positions: [...next.positions],
    });
    if (nextKey === graphKey) return;
    const nextStructure = next.nodes.map((n) => n.id).join("|");
    const changedStructure = structureKey !== nextStructure;
    graphKey = nextKey;
    structureKey = nextStructure;
    graph = next;
    removeGraph();
    buildFloor();
    graph.edges.forEach(makeEdge);
    graph.nodes.forEach(makeNode);
    renderer.shadowMap.needsUpdate = true;
    if (changedStructure) reset();
    setSelected(selected);
    requestDraw();
  }
  function setSelected(id) {
    if (disposed) return;
    selected = id;
    for (const item of nodeObjects) {
      const active = item.node.id === id;
      item.label.classList.toggle("selected", active);
      item.label.setAttribute("aria-pressed", String(active));
      item.selectionMat.opacity = active ? 0.85 : 0;
      item.edgeMat.opacity = active ? 0.9 : 0.52;
    }
    for (const item of edgeObjects) {
      const related = item.edge.source === id || item.edge.target === id;
      item.lineMat.opacity = id ? related ? 0.98 : 0.3 : 0.6;
      item.arrowMat.opacity = id ? related ? 1 : 0.38 : 0.88;
    }
    requestDraw();
  }
  function focus(id) {
    const p = graph.positions.get(id);
    if (!p || disposed) return false;
    focusGoal = { target: new THREE.Vector3(p.x, p.y + 0.5, p.z), distance: 15 };
    overview = false;
    setSelected(id);
    requestDraw();
    return true;
  }
  function reset() {
    if (disposed) return;
    theta = 0.08;
    phi = 0.84;
    overview = true;
    fitOverview();
    focusGoal = null;
    requestDraw();
  }
  function hitNode(event) {
    const rect = canvas.getBoundingClientRect();
    pointer.set(((event.clientX - rect.left) / rect.width) * 2 - 1,
      -((event.clientY - rect.top) / rect.height) * 2 + 1);
    raycaster.setFromCamera(pointer, camera);
    for (const hit of raycaster.intersectObjects(nodeObjects.map((item) => item.group), true)) {
      let object = hit.object;
      while (object && !object.userData.nodeId) object = object.parent;
      if (object?.userData.nodeId) return object.userData.nodeId;
    }
    return null;
  }
  const down = (event) => {
    if (event.button !== 0) return;
    focusGoal = null;
    pointerStart = { x: event.clientX, y: event.clientY, theta, phi, moved: false };
    drag = true;
    canvas.setPointerCapture(event.pointerId);
    canvas.style.cursor = "grabbing";
  };
  const move = (event) => {
    if (!drag || !pointerStart) return;
    const dx = event.clientX - pointerStart.x, dy = event.clientY - pointerStart.y;
    if (Math.abs(dx) + Math.abs(dy) > 4) pointerStart.moved = true;
    if (!pointerStart.moved) return;
    overview = false;
    theta = pointerStart.theta - dx * 0.005;
    phi = Math.max(0.35, Math.min(1.22, pointerStart.phi + dy * 0.004));
    requestDraw();
  };
  const up = (event) => {
    const clicked = drag && pointerStart && !pointerStart.moved && event.type === "pointerup";
    drag = false;
    pointerStart = null;
    canvas.style.cursor = "grab";
    if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
    if (clicked) {
      const id = hitNode(event);
      if (id) onSelect(id);
    }
  };
  const doubleClick = (event) => {
    const id = hitNode(event);
    if (id) { onSelect(id); focus(id); }
  };
  const wheel = (event) => {
    event.preventDefault();
    focusGoal = null;
    overview = false;
    distance = Math.max(9, Math.min(90, distance * Math.exp(Math.max(-500, Math.min(500, event.deltaY)) * 0.0012)));
    requestDraw();
  };
  const keydown = (event) => {
    if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "+", "=", "-", "0", "f", "F", "Escape"].includes(event.key)) return;
    event.preventDefault();
    focusGoal = null;
    overview = false;
    if (event.key === "ArrowLeft") theta -= 0.12;
    if (event.key === "ArrowRight") theta += 0.12;
    if (event.key === "ArrowUp") phi = Math.min(1.22, phi + 0.08);
    if (event.key === "ArrowDown") phi = Math.max(0.35, phi - 0.08);
    if (["+", "="].includes(event.key)) distance = Math.max(9, distance - 2);
    if (event.key === "-") distance = Math.min(90, distance + 2);
    if (["0", "Escape"].includes(event.key)) reset();
    if (["f", "F"].includes(event.key) && selected) focus(selected);
    requestDraw();
  };
  const lost = (event) => { event.preventDefault(); onMode?.("lost"); };
  const visibility = () => { if (!document.hidden) requestDraw(); };
  const motionChanged = (event) => { reduceMotion = event.matches; requestDraw(); };
  const listeners = [
    ["pointerdown", down], ["pointermove", move], ["pointerup", up],
    ["pointercancel", up], ["lostpointercapture", up], ["dblclick", doubleClick],
    ["wheel", wheel, { passive: false }], ["keydown", keydown], ["webglcontextlost", lost],
  ];
  for (const [event, handler, options] of listeners) canvas.addEventListener(event, handler, options);
  document.addEventListener("visibilitychange", visibility);
  motionQuery.addEventListener?.("change", motionChanged);
  canvas.style.cursor = "grab";
  resize();
  onMode?.("3d");
  return {
    setGraph, setSelected, focus, reset,
    destroy() {
      if (disposed) return;
      disposed = true;
      cancelAnimationFrame(frame);
      frame = 0;
      observer.disconnect();
      for (const [event, handler, options] of listeners) canvas.removeEventListener(event, handler, options);
      document.removeEventListener("visibilitychange", visibility);
      motionQuery.removeEventListener?.("change", motionChanged);
      removeGraph();
      key.shadow.map?.dispose();
      renderer.dispose();
      renderer.forceContextLoss();
      canvas.remove();
      labels.remove();
      container.style.touchAction = originalTouchAction;
    },
  };
}
