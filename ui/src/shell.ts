import type { ContextConcept, ContextFile, ContextView } from "./protocol";

function element<T extends HTMLElement>(id: string): T {
  const node = document.getElementById(id);
  if (!node) throw new Error(`缺少 UI 节点：${id}`);
  return node as T;
}

function replaceText(node: HTMLElement, value: string): void {
  node.replaceChildren(document.createTextNode(value));
}

function meta(label: string, value: string): HTMLSpanElement {
  const node = document.createElement("span");
  node.className = "node-meta";
  replaceText(node, `${label} · ${value}`);
  return node;
}

function conceptNode(concept: ContextConcept, index: number): HTMLLIElement {
  const item = document.createElement("li");
  item.className = "node-card concept-node";
  const number = document.createElement("span");
  number.className = "node-number";
  replaceText(number, String(index + 1).padStart(2, "0"));
  const label = document.createElement("strong");
  replaceText(label, concept.label);
  item.append(number, label, meta(concept.source, `${Math.round(concept.confidence * 100)}%`));
  return item;
}

function fileNode(file: ContextFile, index: number): HTMLLIElement {
  const item = document.createElement("li");
  item.className = "node-card file-node";
  const number = document.createElement("span");
  number.className = "node-number";
  replaceText(number, String(index + 1).padStart(2, "0"));
  const path = document.createElement("code");
  replaceText(path, file.path);
  item.append(number, path, meta(file.relation, file.language));
  return item;
}

export class AgentNaviShell {
  private query = "当前查询";

  setConnection(label: string, connected: boolean): void {
    replaceText(element("connection-label"), label);
    element("connection-state").classList.toggle("is-connected", connected);
  }

  setTheme(theme: unknown): void {
    document.documentElement.dataset.theme = theme === "dark" ? "dark" : "light";
  }

  setQuery(query: string | undefined): void {
    if (query) this.query = query;
  }

  render(view: ContextView): void {
    replaceText(element("project-name"), view.project.name);
    replaceText(element("source-state"), view.sourceState.status.toUpperCase());
    replaceText(element("file-count"), String(view.data.stats.files));
    replaceText(element("task-query"), this.query);

    element("concept-list").replaceChildren(
      ...view.data.concepts.map((concept, index) => conceptNode(concept, index)),
    );
    element("file-list").replaceChildren(
      ...view.data.files.map((file, index) => fileNode(file, index)),
    );

    const warningPanel = element("warning-panel");
    const warningList = element("warning-list");
    warningList.replaceChildren(
      ...view.warnings.map((warning) => {
        const item = document.createElement("li");
        replaceText(item, `${warning.code} · ${warning.message}`);
        return item;
      }),
    );
    warningPanel.hidden = view.warnings.length === 0;
    element("empty-state").hidden = true;
    element("context-map").hidden = false;
  }
}
