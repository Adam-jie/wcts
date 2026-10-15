import * as THREE from "./vendor/three.module.min.js";
import {
  compactNodeLabel,
  graphPositions,
  statusClass,
  statusLabel,
} from "./lab-core.mjs";

const colors = {
  success: 0xbddc8c,
  running: 0xdfbd73,
  failed: 0xe69987,
  pending: 0x527680,
};
export function createScene(container, onSelect, onMode) {
  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({
      antialias: true,
      alpha: true,
      powerPreference: "low-power",
    });
  } catch (error) {
    return null;
  }
  container.style.touchAction = "none";
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 120);
  const world = new THREE.Group();
  scene.add(world);
  const ambient = new THREE.AmbientLight(0xddeee2, 2.4);
  scene.add(ambient);
  const key = new THREE.DirectionalLight(0xc9f2bb, 3);
  key.position.set(4, 8, 10);
  scene.add(key);
  const rim = new THREE.DirectionalLight(0x70b9cb, 1.8);
  rim.position.set(-7, -1, -5);
  scene.add(rim);
  const grid = new THREE.GridHelper(30, 30, 0x3b645a, 0x23403c);
  grid.position.set(0, -8.2, -1);
  grid.material.transparent = true;
  grid.material.opacity = 0.3;
  scene.add(grid);
  const labels = document.createElement("div");
  labels.className = "scene-labels";
  container.append(renderer.domElement, labels);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.6));
  renderer.setClearColor(0, 0);
  renderer.domElement.tabIndex = 0;
  renderer.domElement.setAttribute(
    "aria-label",
    "3D 依赖图；也可使用节点按钮或切换平面视图",
  );
  let graph = { nodes: [], edges: [], positions: new Map() },
    objects = [],
    labelObjects = [],
    selected = null,
    theta = 0.06,
    phi = 0.24,
    distance = 26,
    target = new THREE.Vector3(0, -0.8, 0),
    pointerStart = null,
    drag = false,
    disposed = false,
    frame = 0,
    dirty = true;
  const reduceMotion = window.matchMedia(
    "(prefers-reduced-motion: reduce)",
  ).matches;
  function disposeWorld() {
    for (const object of objects) {
      world.remove(object);
      object.geometry?.dispose();
      if (Array.isArray(object.material))
        object.material.forEach((m) => m.dispose());
      else object.material?.dispose();
    }
    objects = [];
    labelObjects = [];
    labels.replaceChildren();
  }
  function updateCamera() {
    camera.position.set(
      target.x + distance * Math.sin(theta) * Math.cos(phi),
      target.y + distance * Math.sin(phi),
      target.z + distance * Math.cos(theta) * Math.cos(phi),
    );
    camera.lookAt(target);
  }
  function resize() {
    const { width, height } = container.getBoundingClientRect();
    if (!width || !height) return;
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    dirty = true;
  }
  const observer = new ResizeObserver(resize);
  observer.observe(container);
  function makeEdge(edge) {
    const a = graph.positions.get(edge.source),
      b = graph.positions.get(edge.target);
    if (!a || !b) return;
    const points = [
      new THREE.Vector3(a.x, a.y, a.z),
      new THREE.Vector3((a.x + b.x) / 2, (a.y + b.y) / 2, 0.1),
      new THREE.Vector3(b.x, b.y, b.z),
    ];
    const curve = new THREE.CatmullRomCurve3(points);
    const geometry = new THREE.BufferGeometry().setFromPoints(
      curve.getPoints(24),
    );
    const material = new THREE.LineBasicMaterial({
      color: edge.kind === "handoff" ? 0x77c6c4 : 0x547269,
      transparent: true,
      opacity: edge.kind === "handoff" ? 0.65 : 0.33,
    });
    const line = new THREE.Line(geometry, material);
    world.add(line);
    objects.push(line);
  }
  function makeNode(node) {
    const p = graph.positions.get(node.id);
    if (!p) return;
    const color = colors[statusClass(node.status)] || colors.pending;
    const geometry =
      node.kind === "controller"
        ? new THREE.OctahedronGeometry(0.42)
        : node.kind === "employee"
          ? new THREE.CylinderGeometry(0.31, 0.4, 0.32, 6)
          : new THREE.BoxGeometry(0.38, 0.38, 0.38);
    const material = new THREE.MeshStandardMaterial({
      color,
      metalness: 0.3,
      roughness: 0.37,
      emissive: color,
      emissiveIntensity: node.status === "running" ? 0.2 : 0.045,
    });
    const mesh = new THREE.Mesh(geometry, material);
    mesh.position.set(p.x, p.y + 0.28, p.z);
    mesh.rotation.y = Math.PI / 4;
    world.add(mesh);
    objects.push(mesh);
    const ring = new THREE.Mesh(
      new THREE.TorusGeometry(0.54, 0.012, 5, 40),
      new THREE.MeshBasicMaterial({
        color,
        transparent: true,
        opacity: node.status === "running" ? 0.9 : 0.3,
      }),
    );
    ring.rotation.x = Math.PI / 2;
    ring.position.set(p.x, p.y - 0.1, p.z);
    world.add(ring);
    objects.push(ring);
    const label = document.createElement("button");
    label.className = "scene-node-label";
    label.type = "button";
    label.setAttribute(
      "aria-label",
      `${node.label}，${statusLabel(node.status)}，入度 ${node.in_degree ?? "未记录"}，出度 ${node.out_degree ?? "未记录"}`,
    );
    const dot = document.createElement("span");
    dot.className = "node-color";
    dot.style.setProperty(
      "--node-color",
      `#${color.toString(16).padStart(6, "0")}`,
    );
    const text = document.createElement("span");
    text.textContent = compactNodeLabel(node);
    label.title = node.label;
    label.append(dot, text);
    if (node.kind === "employee") {
      const degree = document.createElement("span");
      degree.className = "degree";
      degree.textContent = `↓${node.out_degree ?? "—"}`;
      label.append(degree);
    }
    label.addEventListener("click", () => onSelect(node.id));
    labels.append(label);
    labelObjects.push({
      node,
      label,
      mesh,
      ring,
      position: new THREE.Vector3(p.x, p.y - 0.25, p.z),
    });
  }
  function setGraph(value) {
    const previousIds = graph.nodes.map((n) => n.id).join("|");
    graph = graphPositions(value);
    disposeWorld();
    graph.edges.forEach(makeEdge);
    graph.nodes.forEach(makeNode);
    if (previousIds !== graph.nodes.map((n) => n.id).join("|")) {
      const ys = [...graph.positions.values()].map((p) => p.y);
      const height = ys.length ? Math.max(...ys) - Math.min(...ys) : 8;
      target.y = ys.length ? (Math.max(...ys) + Math.min(...ys)) / 2 : -0.8;
      distance = Math.max(24, height * 2.1);
    }
    setSelected(selected);
    dirty = true;
  }
  function setSelected(id) {
    selected = id;
    for (const { node, label, mesh } of labelObjects) {
      label.classList.toggle("selected", node.id === id);
      mesh.scale.setScalar(node.id === id ? 1.35 : 1);
    }
    dirty = true;
  }
  function draw(time) {
    if (disposed) return;
    frame = requestAnimationFrame(draw);
    if (document.hidden) return;
    const running =
      !reduceMotion &&
      labelObjects.some((item) => item.node.status === "running");
    if (!dirty && !running) return;
    updateCamera();
    const width = container.clientWidth,
      height = container.clientHeight;
    for (const { node, label, mesh, ring, position } of labelObjects) {
      const v = position.clone().project(camera);
      label.style.left = `${(v.x * 0.5 + 0.5) * width}px`;
      label.style.top = `${(-v.y * 0.5 + 0.5) * height}px`;
      label.hidden = v.z > 1 || v.z < -1;
      label.style.zIndex = String(Math.round((1 - v.z) * 10000));
      if (node.status === "running" && !reduceMotion) {
        mesh.rotation.y = time * 0.00022;
        ring.material.opacity = 0.55 + Math.sin(time * 0.003) * 0.3;
      }
    }
    renderer.render(scene, camera);
    dirty = false;
  }
  const down = (e) => {
    if (e.target.closest(".scene-node-label")) return;
    pointerStart = { x: e.clientX, y: e.clientY, theta, phi };
    drag = true;
    renderer.domElement.setPointerCapture(e.pointerId);
  };
  const move = (e) => {
    if (!drag || !pointerStart) return;
    theta = pointerStart.theta + (e.clientX - pointerStart.x) * 0.005;
    phi = Math.max(
      -0.35,
      Math.min(0.8, pointerStart.phi + (e.clientY - pointerStart.y) * 0.003),
    );
    dirty = true;
  };
  const up = () => {
    drag = false;
    pointerStart = null;
  };
  const wheel = (e) => {
    e.preventDefault();
    distance = Math.max(14, Math.min(58, distance + e.deltaY * 0.018));
    dirty = true;
  };
  const keydown = (e) => {
    if (
      ![
        "ArrowLeft",
        "ArrowRight",
        "ArrowUp",
        "ArrowDown",
        "+",
        "-",
        "0",
      ].includes(e.key)
    )
      return;
    e.preventDefault();
    if (e.key === "ArrowLeft") theta -= 0.12;
    if (e.key === "ArrowRight") theta += 0.12;
    if (e.key === "ArrowUp") phi = Math.min(0.8, phi + 0.1);
    if (e.key === "ArrowDown") phi = Math.max(-0.35, phi - 0.1);
    if (e.key === "+") distance = Math.max(14, distance - 2);
    if (e.key === "-") distance = Math.min(58, distance + 2);
    if (e.key === "0") reset();
    dirty = true;
  };
  renderer.domElement.addEventListener("pointerdown", down);
  renderer.domElement.addEventListener("pointermove", move);
  renderer.domElement.addEventListener("pointerup", up);
  renderer.domElement.addEventListener("pointercancel", up);
  renderer.domElement.addEventListener("wheel", wheel, { passive: false });
  renderer.domElement.addEventListener("keydown", keydown);
  const lost = (e) => {
    e.preventDefault();
    onMode?.("lost");
  };
  renderer.domElement.addEventListener("webglcontextlost", lost);
  function reset() {
    theta = 0.06;
    phi = 0.24;
    distance = Math.max(24, graph.nodes.length > 13 ? 28 : 24);
    dirty = true;
  }
  resize();
  frame = requestAnimationFrame(draw);
  onMode?.("3d");
  return {
    setGraph,
    setSelected,
    reset,
    destroy() {
      disposed = true;
      cancelAnimationFrame(frame);
      observer.disconnect();
      disposeWorld();
      grid.geometry.dispose();
      grid.material.dispose();
      renderer.dispose();
      container.replaceChildren();
    },
  };
}
