import type { Evidence, FlowStep, FlowView } from "../protocol.js";
import { byId, replaceText } from "./dom.js";

function row(label: string, value: string): HTMLDivElement {
  const node = document.createElement("div");
  const term = document.createElement("dt");
  const detail = document.createElement("dd");
  replaceText(term, label);
  replaceText(detail, value);
  node.append(term, detail);
  return node;
}

function evidenceText(evidence: Evidence): string {
  return evidence.path
    ? `${evidence.path}${evidence.lineStart ? `:${evidence.lineStart}` : ""}`
    : `${evidence.layer} · ${evidence.source}`;
}

function flowStep(step: FlowStep): HTMLLIElement {
  const item = document.createElement("li");
  item.className = "flow-step";
  const details = document.createElement("details");
  const summary = document.createElement("summary");
  const number = document.createElement("span");
  const title = document.createElement("strong");
  replaceText(number, String(step.step).padStart(2, "0"));
  replaceText(title, step.title);
  summary.append(number, title);
  const purpose = document.createElement("p");
  purpose.className = "flow-purpose";
  replaceText(purpose, step.purpose);
  const facts = document.createElement("dl");
  const files = step.keyFiles.map((file) => file.path).join("、") || "未命中关键源码";
  facts.append(
    row("输入", step.input),
    row("输出", step.output),
    row("关键源码", files),
    row("为什么需要", step.why),
    row("下一步", step.nextStep ?? "交给 Agent"),
    row("证据", step.evidence.map(evidenceText).join(" · ")),
  );
  details.append(summary, purpose, facts);
  item.append(details);
  return item;
}

export function clearFlow(): void {
  byId("flow-view").hidden = true;
  replaceText(byId("flow-task"), "");
  byId("flow-steps").replaceChildren();
  byId("flow-empty").hidden = true;
}

export function renderFlow(view: FlowView): void {
  replaceText(byId("flow-task"), view.data.exampleTask?.title ?? "暂无可展示任务示例");
  byId("flow-steps").replaceChildren(...view.data.steps.map(flowStep));
  byId("flow-empty").hidden = view.data.steps.length > 0;
  byId("flow-view").hidden = false;
}
