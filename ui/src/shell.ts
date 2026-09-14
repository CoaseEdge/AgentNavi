import type { ContextConcept, ContextFile, ContextView } from "./protocol.js";

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
  if (concept.files.length > 0) {
    const links = document.createElement("ul");
    links.className = "concept-file-links";
    links.setAttribute("aria-label", `${concept.label} 的文件关系`);
    for (const file of concept.files) {
      const link = document.createElement("li");
      const relation = document.createElement("span");
      relation.className = "relation-label";
      replaceText(relation, file.relation);
      const path = document.createElement("code");
      replaceText(path, file.path);
      link.append(relation, path);
      links.append(link);
    }
    item.append(links);
  }
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
  item.append(number, path, meta(`候选 · ${file.relation}`, file.language));
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

  beginRequest(query: string | undefined): void {
    this.setQuery(query);
    this.clearResult();
    replaceText(element("task-query"), this.query);
    replaceText(element("empty-title"), "正在读取 Context");
    replaceText(element("empty-message"), "新请求已收到，旧导航结果已清除。");
    element("empty-state").hidden = false;
    this.setConnection("正在查询", true);
  }

  showError(message: string): void {
    this.clearResult();
    replaceText(element("error-message"), message);
    element("error-panel").hidden = false;
    this.setConnection("查询失败", false);
  }

  private clearResult(): void {
    element("context-map").hidden = true;
    element("concept-list").replaceChildren();
    element("file-list").replaceChildren();
    element("warning-list").replaceChildren();
    element("warning-panel").hidden = true;
    element("error-panel").hidden = true;
    element("empty-state").hidden = true;
    replaceText(element("project-name"), "等待数据");
    replaceText(element("source-state"), "—");
    replaceText(element("file-count"), "0");
  }

  render(view: ContextView): void {
    element("error-panel").hidden = true;
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
