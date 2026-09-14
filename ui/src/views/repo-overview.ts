import type { Evidence, RepositoryOverviewView } from "../protocol.js";
import { byId, replaceText } from "./dom.js";

function evidenceLabel(evidence: Evidence[]): string {
  const item = evidence.find((entry) => entry.path);
  if (!item?.path) return "";
  return item.lineStart ? `证据 · ${item.path}:${item.lineStart}` : `证据 · ${item.path}`;
}

function detailItem(title: string, detail: string, evidence: Evidence[]): HTMLLIElement {
  const item = document.createElement("li");
  const heading = document.createElement("strong");
  replaceText(heading, title);
  const copy = document.createElement("p");
  replaceText(copy, detail);
  item.append(heading, copy);
  const label = evidenceLabel(evidence);
  if (label) {
    const source = document.createElement("span");
    source.className = "evidence-line";
    replaceText(source, label);
    item.append(source);
  }
  return item;
}

export function clearRepositoryOverview(): void {
  byId("repository-view").hidden = true;
  replaceText(byId("overview-purpose"), "");
  replaceText(byId("purpose-evidence"), "");
  replaceText(byId("overview-problem"), "");
  replaceText(byId("problem-evidence"), "");
  replaceText(byId("overview-solution"), "");
  replaceText(byId("solution-evidence"), "");
  byId("overview-workflow").replaceChildren();
  byId("overview-modules").replaceChildren();
  byId("overview-reading-order").replaceChildren();
}

export function renderRepositoryOverview(view: RepositoryOverviewView): void {
  replaceText(byId("overview-purpose"), view.data.purpose.summary || "暂无足够文档证据。");
  replaceText(byId("purpose-evidence"), evidenceLabel(view.data.purpose.evidence));
  replaceText(byId("overview-problem"), view.data.need.problem.summary || "暂无足够文档证据。");
  replaceText(byId("problem-evidence"), evidenceLabel(view.data.need.problem.evidence));
  replaceText(byId("overview-solution"), view.data.need.solution.summary || "暂无足够文档证据。");
  replaceText(byId("solution-evidence"), evidenceLabel(view.data.need.solution.evidence));
  byId("overview-workflow").replaceChildren(
    ...view.data.workflow.map((step) => detailItem(
      `${String(step.step).padStart(2, "0")} · ${step.title}`,
      step.detail,
      step.evidence,
    )),
  );
  byId("overview-modules").replaceChildren(
    ...view.data.modules.map((module) => {
      const paths = module.paths.length > 0 ? ` · ${module.paths.join(" · ")}` : "";
      return detailItem(module.name, `${module.summary}${paths}`, module.evidence);
    }),
  );
  byId("overview-reading-order").replaceChildren(
    ...view.data.readingOrder.map((entry) => detailItem(
      `${String(entry.position).padStart(2, "0")} · ${entry.path}`,
      entry.reason,
      entry.evidence,
    )),
  );
  byId("repository-view").hidden = false;
}
