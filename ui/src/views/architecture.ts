import type { ArchitectureComponent, ArchitectureView, Evidence } from "../protocol.js";
import { byId, replaceText } from "./dom.js";

function evidenceText(evidence: Evidence): string {
  return evidence.path
    ? `${evidence.path}${evidence.lineStart ? `:${evidence.lineStart}` : ""}`
    : `${evidence.layer} · ${evidence.source}`;
}

function componentCard(component: ArchitectureComponent): HTMLLIElement {
  const item = document.createElement("li");
  item.className = "architecture-component";
  const title = document.createElement("h3");
  replaceText(title, component.name);
  const responsibility = document.createElement("p");
  replaceText(responsibility, component.responsibility);
  const paths = document.createElement("ul");
  paths.className = "architecture-paths";
  paths.replaceChildren(...component.paths.map((path) => {
    const entry = document.createElement("li");
    const code = document.createElement("code");
    replaceText(code, path);
    entry.append(code);
    return entry;
  }));
  item.append(title, responsibility, paths);
  return item;
}

export function clearArchitecture(): void {
  byId("architecture-view").hidden = true;
  replaceText(byId("architecture-summary"), "");
  byId("architecture-entry").replaceChildren();
  byId("architecture-core").replaceChildren();
  byId("architecture-support").replaceChildren();
  byId("architecture-connections").replaceChildren();
}

export function renderArchitecture(view: ArchitectureView): void {
  replaceText(byId("architecture-summary"), view.data.summary.text);
  for (const group of ["entry", "core", "support"] as const) {
    byId(`architecture-${group}`).replaceChildren(
      ...view.data.components.filter((component) => component.group === group).map(componentCard),
    );
  }
  byId("architecture-connections").replaceChildren(...view.data.connections.map((connection) => {
    const item = document.createElement("li");
    const direction = document.createElement("strong");
    replaceText(direction, `${connection.sourceId} → ${connection.targetId}`);
    const relation = document.createElement("span");
    replaceText(relation, connection.relation);
    const evidence = document.createElement("small");
    replaceText(evidence, connection.evidence.map(evidenceText).join(" · "));
    item.append(direction, relation, evidence);
    return item;
  }));
  byId("architecture-view").hidden = false;
}
