import type { SemanticReviewView } from "../protocol.js";
import { byId, replaceText } from "./dom.js";

export type ReviewDecisionHandler = (reviewId: string, decision: "accept" | "reject", projectId: string) => void;

export function clearSemanticReview(): void {
  byId("semantic-review-view").hidden = true;
  byId("semantic-review-items").replaceChildren();
}

export function renderSemanticReview(view: SemanticReviewView, onDecision: ReviewDecisionHandler): void {
  const list = byId<HTMLOListElement>("semantic-review-items");
  list.replaceChildren(...view.data.reviewItems.map((entry, index) => {
    const item = document.createElement("li"); item.className = "semantic-review-item";
    const heading = document.createElement("h3");
    replaceText(heading, `${String(index + 1).padStart(2, "0")} · ${entry.subject.label} ${entry.object ? `→ ${entry.object.label}` : "（概念候选）"}`);
    const meta = document.createElement("p");
    replaceText(meta, `${entry.relation} · confidence ${(entry.confidence * 100).toFixed(0)}% · ${entry.source}`);
    const why = document.createElement("p"); replaceText(why, `Why · ${entry.evidence.map((e) => e.summary).join("；")}`);
    const evidence = document.createElement("ul"); evidence.className = "semantic-review-evidence";
    evidence.replaceChildren(...entry.evidence.map((e) => { const row = document.createElement("li"); replaceText(row, e.path ? `${e.summary} · ${e.path}` : e.summary); return row; }));
    const actions = document.createElement("div"); actions.className = "semantic-review-actions";
    for (const decision of entry.allowedActions) {
      const button = document.createElement("button"); button.type = "button"; button.className = decision === "accept" ? "review-accept" : "review-reject";
      replaceText(button, decision === "accept" ? "Accept" : "Reject");
      button.addEventListener("click", () => onDecision(entry.reviewId, decision, view.project.id));
      actions.append(button);
    }
    if (entry.decision) { const status = document.createElement("span"); status.className = "review-decision"; replaceText(status, entry.decision === "accepted" ? "已接受" : "已拒绝"); actions.append(status); }
    item.append(heading, meta, why, evidence, actions); return item;
  }));
  byId("semantic-review-count").textContent = `${view.data.stats.pending} / ${view.data.stats.candidates}`;
  byId("semantic-review-view").hidden = false;
}
