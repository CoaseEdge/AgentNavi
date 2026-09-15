import type {
  AgentNaviView, ContextConcept, ContextFile, ContextReadingItem, ContextView,
} from "./protocol.js";
import { renderRegisteredView } from "./views/index.js";
import { clearRepositoryOverview } from "./views/repo-overview.js";
import { clearRepositoryTour } from "./views/repo-tour.js";
import { clearArchitecture } from "./views/architecture.js";
import { clearFlow } from "./views/flow.js";
import { clearImpact } from "./views/impact.js";
import { clearHistory } from "./views/history.js";
import { clearSemanticReview, type ReviewDecisionHandler } from "./views/semantic-review.js";

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

function fileNode(
  file: ContextFile,
  index: number,
  reading?: ContextReadingItem,
): HTMLLIElement {
  const item = document.createElement("li");
  item.className = "node-card file-node";
  const number = document.createElement("span");
  number.className = "node-number";
  replaceText(number, String(index + 1).padStart(2, "0"));
  const path = document.createElement("code");
  replaceText(path, file.path);
  item.append(number, path, meta(`候选 · ${file.relation}`, file.language));
  if (!reading) return item;

  const trigger = document.createElement("button");
  trigger.type = "button";
  trigger.className = "file-explain-trigger";
  trigger.setAttribute("aria-expanded", "false");
  replaceText(trigger, "查看 Why / Evidence / Next Step");
  const panel = document.createElement("section");
  panel.className = "file-explanation";
  panel.hidden = true;
  const why = document.createElement("p");
  why.className = "file-why";
  replaceText(why, `Why · ${reading.why}`);
  const evidence = document.createElement("ul");
  evidence.className = "context-evidence";
  for (const entry of reading.evidence) {
    const line = document.createElement("li");
    replaceText(line, `Evidence · ${entry.summary}${entry.path ? ` · ${entry.path}` : ""}`);
    evidence.append(line);
  }
  const chainList = document.createElement("ol");
  chainList.className = "context-chains";
  chainList.setAttribute("aria-label", `${file.path} 的可追溯关系链`);
  for (const chain of reading.chains) {
    const chainNode = document.createElement("li");
    let traversal = chain.sourceConcept.label;
    if (chain.conceptRelation && chain.relatedConcept) {
      traversal = chain.conceptRelation.sourceId === chain.sourceConcept.id
        ? `${chain.sourceConcept.label} —${chain.conceptRelation.relation}→ ${chain.relatedConcept.label}`
        : `${chain.sourceConcept.label} ←${chain.conceptRelation.relation}— ${chain.relatedConcept.label}`;
    }
    replaceText(
      chainNode,
      `${traversal} —${chain.fileRelation.relation}→ ${chain.file.path}`,
    );
    chainList.append(chainNode);
  }
  const actionNav = document.createElement("div");
  actionNav.className = "context-actions";
  actionNav.setAttribute("role", "group");
  actionNav.setAttribute("aria-label", `${file.path} 的导航操作`);
  const actionSummary = document.createElement("p");
  actionSummary.className = "context-action-summary";
  const showAction = (selected: number) => {
    for (const [actionIndex, button] of Array.from(actionNav.children).entries()) {
      button.setAttribute("aria-pressed", String(actionIndex === selected));
    }
    replaceText(actionSummary, reading.actions[selected]?.summary ?? "");
  };
  reading.actions.forEach((action, actionIndex) => {
    const button = document.createElement("button");
    button.type = "button";
    button.setAttribute("aria-pressed", String(actionIndex === 0));
    replaceText(button, action.label);
    button.addEventListener("click", () => showAction(actionIndex));
    actionNav.append(button);
  });
  showAction(0);
  const next = document.createElement("p");
  next.className = "context-next-step";
  replaceText(
    next,
    reading.nextStep
      ? `Next Step · ${reading.nextStep.path} — ${reading.nextStep.reason}`
      : "Next Step · 完成当前阅读路径",
  );
  panel.append(why, evidence, chainList, actionNav, actionSummary, next);
  trigger.addEventListener("click", () => {
    panel.hidden = !panel.hidden;
    trigger.setAttribute("aria-expanded", String(!panel.hidden));
  });
  item.append(trigger, panel);
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
    } else if (view === "architecture") {
      replaceText(element("view-title"), "Architecture");
      replaceText(element("view-eyebrow"), "SYSTEM COMPONENTS");
      replaceText(element("view-description"), "按入口、核心与支撑理解系统组成及真实关系。");
    } else if (view === "flow") {
      replaceText(element("view-title"), "Task Flow");
      replaceText(element("view-eyebrow"), "EVIDENCE-BACKED FLOW");
      replaceText(element("view-description"), "沿 5–7 个可展开步骤理解任务如何流经源码。");
    } else if (view === "impact") {
      replaceText(element("view-title"), "Impact");
      replaceText(element("view-eyebrow"), "BOUNDED CHANGE IMPACT");
      replaceText(element("view-description"), "从真实关系判断调用方、依赖、测试与风险位置。");
    } else if (view === "history") {
      replaceText(element("view-title"), "History");
      replaceText(element("view-eyebrow"), "TASK TIMELINE / PROJECT STORY");
      replaceText(element("view-description"), "按权威任务时间与 L3 关系理解项目如何演进。");
    } else if (view === "semantic-review") {
      replaceText(element("view-title"), "Semantic Review");
      replaceText(element("view-eyebrow"), "SEMANTIC REVIEW · 3 / 12");
      replaceText(element("view-description"), "查看来源、置信度与证据，由人决定是否写入持久化语义 Overlay。");
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
          : view === "architecture"
            ? "正在读取系统架构"
            : view === "flow"
              ? "正在生成任务流"
            : view === "impact"
              ? "正在分析影响"
            : view === "history"
              ? "正在读取项目历史"
            : view === "semantic-review"
              ? "正在读取语义审查"
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
    clearArchitecture();
    clearFlow();
    clearImpact();
    clearHistory();
    clearSemanticReview();
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
    const filesByPath = new Map(view.data.files.map((file) => [file.path, file]));
    const renderedPaths = new Set(view.data.navigation.readingOrder.map((item) => item.path));
    const ordered: Array<{ file: ContextFile; reading?: ContextReadingItem }> =
      view.data.navigation.readingOrder.map((reading) => ({
      file: filesByPath.get(reading.path)!, reading,
      }));
    ordered.push(...view.data.files.filter((file) => !renderedPaths.has(file.path)).map(
      (file) => ({ file, reading: undefined }),
    ));
    element("file-list").replaceChildren(
      ...ordered.map(({ file, reading }, index) => fileNode(file, index, reading)),
    );
    element("context-map").hidden = false;
  }

  render(view: AgentNaviView): void {
    this.clearResult();
    replaceText(element("project-name"), view.project.name);
    replaceText(element("source-state"), view.sourceState.status.toUpperCase());
    replaceText(element("file-count"), String("files" in view.data.stats ? view.data.stats.files : 0));
    this.setViewIdentity(view.view);

    renderRegisteredView(view, (context) => this.renderContext(context), this.onDecision ?? (() => undefined));

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

  private onDecision: ReviewDecisionHandler | undefined;

  setSemanticReviewAction(handler: ReviewDecisionHandler): void {
    this.onDecision = handler;
  }
}
