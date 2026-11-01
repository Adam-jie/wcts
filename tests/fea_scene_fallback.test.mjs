import assert from "node:assert/strict";
import { test } from "node:test";

class Element {
  constructor(tag = "div") {
    this.tag = tag; this.children = []; this.style = { position: "", overflow: "" };
    this.dataset = {}; this.events = new Map(); this.parent = null;
  }
  append(...nodes) { for (const node of nodes) { node.parent = this; this.children.push(node); } }
  replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter((node) => node !== this); }
  addEventListener(name, handler) { this.events.set(name, handler); }
  removeEventListener(name) { this.events.delete(name); }
}
globalThis.HTMLElement = Element;
globalThis.document = {
  createElement: (tag) => new Element(tag),
  createElementNS() { throw new Error("No WebGL context in this headless unit test"); },
};
globalThis.getComputedStyle = () => ({ position: "static" });
const { mountFEAScene, workstationLayout } = await import("../web/fea/fea-scene.js");

test("WebGL failure is labelled as fallback, remains selectable, and uses external CSP-safe CSS", () => {
  const container = new Element(), modes = [], selections = [];
  const scene = mountFEAScene(container, { onSelect: (id) => selections.push(id), onMode: (mode) => modes.push(mode) });
  assert.equal(modes[0].mode, "fallback");
  assert.equal(container.dataset.renderMode, "fallback");
  const stylesheet = container.children[0];
  assert.equal(stylesheet.tag, "link");
  assert.equal(stylesheet.rel, "stylesheet");
  assert.match(stylesheet.href, /\/web\/fea\/fea-scene\.css$/);
  assert.equal(stylesheet.textContent, undefined);
  const ids = ["plan-workflow", "explore-repository", "ground-code", "analyze-requirements", "generate-tests", "diagnose-results", "repair-code", "review-patch", "verify-delivery"];
  scene.setNodes(ids.map((id) => ({ id, label: id })));
  const list = container.children[1].children[2];
  assert.equal(list.children.length, 9);
  assert.equal(list.children[0].textContent, "CodeAgent 总控");
  scene.select("repair-code");
  assert.equal(selections.length, 0, "programmatic selection must not recurse into UI callback");
  list.children[6].events.get("click")();
  assert.deepEqual(selections, ["repair-code"]);
  scene.setNodes([{ id: "invented-employee", label: "Should not appear" }]);
  assert.equal(list.children.length, 0, "fallback must not invent agent roles outside the catalog");
  scene.destroy();
  assert.equal(container.children.length, 0);
  assert.equal(container.style.position, "");
  assert.equal(container.style.overflow, "");
});

test("nine real roles form one larger controller and organized responsive desk rows", () => {
  for (const width of [375, 500, 716, 760, 1100, 1600]) {
    const layout = workstationLayout(width);
    assert.equal(layout.length, 9);
    assert.equal(new Set(layout.map((node) => node.id)).size, 9);
    const controllers = layout.filter((node) => node.controller);
    assert.equal(controllers.length, 1);
    assert.equal(controllers[0].id, "plan-workflow");
    assert.ok(layout.slice(1).every((node) => node.scale < controllers[0].scale));
    const rows = new Set(layout.slice(1).map((node) => node.position[2]));
    const columns = new Set(layout.slice(1).map((node) => node.position[0]));
    assert.equal(rows.size, width < 760 ? 4 : 2);
    assert.equal(columns.size, width < 760 ? 2 : 4);
    for (let i = 0; i < layout.length; i++) for (let j = i + 1; j < layout.length; j++) {
      const a = layout[i].position, b = layout[j].position;
      assert.ok(Math.hypot(a[0] - b[0], a[2] - b[2]) >= 4, "desk footprints must not collide");
    }
  }
});
