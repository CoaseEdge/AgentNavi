import type { AgentNaviView, ContextConcept, ContextFile, ContextView } from "./protocol.js";
import { renderRegisteredView } from "./views/index.js";
import { clearRepositoryOverview } from "./views/repo-overview.js";
import { clearRepositoryTour } from "./views/repo-tour.js";

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

  private setViewIdentity(view: AgentNaviView["view"]): void {
    if (view === "repo-overview") {
      replaceText(element("view-title"), "Repository Overview");
      replaceText(element("view-eyebrow"), "REPOSITORY UNDERSTANDING");
      replaceText(element("view-description"), "用证据建立项目目的、主流程、模块与首读路径。");
    } else if (view === "repo-tour") {
      replaceText(element("view-title"), "Repository Tour");
      replaceText(element("view-eyebrow"), "GUIDED REPOSITORY TOUR");
      replaceText(element("view-description"), "在讲人话、技术解释与源码证据之间逐层深入。");
    } else {
      replaceText(element("view-title"), "ContextMap");
      replaceText(element("view-eyebrow"), "READING CONTEXT");
      replaceText(element("view-description"), "从当前任务，沿概念定位到必要文件。");
    }
  }

  beginRequest(query: string | undefined, view: AgentNaviView["view"] = "context"): void {
    this.setQuery(query);
    this.clearResult();
    this.setViewIdentity(view);
    replaceText(element("task-query"), this.query);
    replaceText(
      element("empty-title"),
      view === "repo-overview"
        ? "正在读取项目概览"
        : view === "repo-tour"
          ? "正在生成仓库导览"
          : "正在读取 Context",
    );
    replaceText(element("empty-message"), "新请求已收到，旧视图结果已清除。");
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
    clearRepositoryOverview();
    clearRepositoryTour();
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

  private renderContext(view: ContextView): void {
    replaceText(element("task-query"), this.query);
    element("concept-list").replaceChildren(
      ...view.data.concepts.map((concept, index) => conceptNode(concept, index)),
    );
    element("file-list").replaceChildren(
      ...view.data.files.map((file, index) => fileNode(file, index)),
    );
    element("context-map").hidden = false;
  }

  render(view: AgentNaviView): void {
    this.clearResult();
    replaceText(element("project-name"), view.project.name);
    replaceText(element("source-state"), view.sourceState.status.toUpperCase());
    replaceText(element("file-count"), String(view.data.stats.files));
    this.setViewIdentity(view.view);

    renderRegisteredView(view, (context) => this.renderContext(context));

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
  }
}
