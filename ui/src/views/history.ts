import type { Evidence, HistoryView } from "../protocol.js";
import { byId, replaceText } from "./dom.js";

function evidenceText(item: Evidence): string {
  return item.path ? `${item.path}${item.lineStart ? `:${item.lineStart}` : ""}` : `${item.layer} · ${item.source}`;
}

function evidenceDetails(evidence: Evidence[]): HTMLDetailsElement {
  const details = document.createElement("details");
  const summary = document.createElement("summary");
  replaceText(summary, "Evidence");
  const list = document.createElement("ul");
  list.replaceChildren(...evidence.map((entry) => {
    const item = document.createElement("li"); replaceText(item, evidenceText(entry)); return item;
  }));
  details.append(summary, list);
  return details;
}

export function clearHistory(): void {
  byId("history-view").hidden = true;
  byId("history-timeline").replaceChildren();
  byId("history-story").replaceChildren();
  replaceText(byId("history-disclaimer"), "");
}

export function renderHistory(view: HistoryView): void {
  const timelinePanel = byId("history-timeline-panel");
  const storyPanel = byId("history-story-panel");
  const timelineButton = byId("history-mode-timeline");
  const storyButton = byId("history-mode-story");
  const select = (mode: "timeline" | "story") => {
    timelineButton.setAttribute("aria-pressed", String(mode === "timeline"));
    storyButton.setAttribute("aria-pressed", String(mode === "story"));
    timelinePanel.hidden = mode !== "timeline";
    storyPanel.hidden = mode !== "story";
  };
  timelineButton.onclick = () => select("timeline");
  storyButton.onclick = () => select("story");
  byId("history-timeline").replaceChildren(...view.data.timeline.map((entry) => {
    const item = document.createElement("li");
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    const title = document.createElement("strong"); const time = document.createElement("time");
    replaceText(title, `${entry.entity.label} · ${entry.status}`); replaceText(time, entry.sortTime);
    summary.append(title, time);
    const taskSummary = document.createElement("p"); replaceText(taskSummary, entry.summary);
    const relations = document.createElement("ul");
    relations.replaceChildren(...entry.relations.map((relation) => {
      const row = document.createElement("li");
      replaceText(row, `${relation.relation.relation} → ${relation.entity.path ?? relation.entity.label}`);
      row.append(evidenceDetails(relation.evidence)); return row;
    }));
    details.append(summary, taskSummary, relations, evidenceDetails(entry.evidence)); item.append(details); return item;
  }));
  byId("history-story").replaceChildren(...view.data.story.map((entry) => {
    const item = document.createElement("li"); const heading = document.createElement("strong");
    const summary = document.createElement("p"); const groups = document.createElement("ul");
    replaceText(heading, `${entry.sortTime} · ${entry.title}`); replaceText(summary, entry.summary);
    groups.replaceChildren(...entry.groups.map((group) => {
      const row = document.createElement("li");
      replaceText(row, `${group.relation}：${[...group.paths, ...group.concepts].join("、") || "无可展示目标"}`);
      row.append(evidenceDetails(group.evidence)); return row;
    }));
    item.append(heading, summary, groups, evidenceDetails(entry.evidence)); return item;
  }));
  replaceText(byId("history-disclaimer"), view.data.disclaimer);
  select(view.data.selectedMode);
  byId("history-view").hidden = false;
}
