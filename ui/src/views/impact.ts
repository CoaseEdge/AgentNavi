import type { Evidence, ImpactLane, ImpactView } from "../protocol.js";
import { byId, replaceText } from "./dom.js";

function evidenceText(evidence: Evidence): string {
  return evidence.path ? `${evidence.path}${evidence.lineStart ? `:${evidence.lineStart}` : ""}` : `${evidence.layer} · ${evidence.source}`;
}

function laneCard(item: ImpactLane, incoming: boolean): HTMLLIElement {
  const node = document.createElement("li");
  const path = document.createElement("code");
  const relation = document.createElement("span");
  const evidence = document.createElement("small");
  replaceText(path, item.peer.path ?? item.peer.label);
  replaceText(relation, incoming ? `${item.relation.relation} → Focus` : `Focus → ${item.relation.relation}`);
  replaceText(evidence, item.evidence.map(evidenceText).join(" · "));
  node.append(path, relation, evidence);
  return node;
}

function appendEvidence(node: HTMLElement, evidence: Evidence[]): void {
  if (evidence.length === 0) return;
  const list = document.createElement("ul"); list.className = "impact-evidence";
  list.replaceChildren(...evidence.map((item) => {
    const entry = document.createElement("li"); replaceText(entry, evidenceText(item)); return entry;
  }));
  node.append(list);
}

export function clearImpact(): void {
  byId("impact-view").hidden = true;
  for (const id of ["impact-semantic", "impact-incoming", "impact-outgoing", "impact-history", "impact-tests", "impact-risks", "impact-actions", "impact-anchors"]) byId(id).replaceChildren();
  replaceText(byId("impact-focus-label"), "");
  replaceText(byId("impact-focus-path"), "");
}

export function renderImpact(view: ImpactView): void {
  const focus = view.data.focus.entity;
  replaceText(byId("impact-focus-label"), focus.label);
  replaceText(byId("impact-focus-path"), focus.path ?? "概念焦点");
  byId("impact-anchors").replaceChildren(...view.data.anchorFiles.map((item) => {
    const node = document.createElement("li"); const path = document.createElement("code");
    replaceText(path, item.entity.path ?? item.entity.label); node.append(path); appendEvidence(node, item.evidence); return node;
  }));
  byId("impact-incoming").replaceChildren(...view.data.incoming.map((item) => laneCard(item, true)));
  byId("impact-outgoing").replaceChildren(...view.data.outgoing.map((item) => laneCard(item, false)));
  byId("impact-semantic").replaceChildren(...view.data.semantic.map((item) => {
    const node = document.createElement("li");
    const concept = view.data.focusConcepts.find((entry) => entry.entity.id === item.focusConceptId)?.entity;
    replaceText(node, item.direction === "outgoing"
      ? `${concept?.label ?? item.focusConceptId} —${item.relation.relation}→ ${item.peer.label}`
      : `${item.peer.label} —${item.relation.relation}→ ${concept?.label ?? item.focusConceptId}`);
    appendEvidence(node, item.evidence);
    return node;
  }));
  byId("impact-history").replaceChildren(...view.data.history.map((item) => {
    const node = document.createElement("li");
    replaceText(node, `${item.entity.label} · ${item.status} · #${item.recordedOrder}`);
    appendEvidence(node, item.evidence);
    return node;
  }));
  byId("impact-tests").replaceChildren(...view.data.testRecommendations.map((item) => {
    const node = document.createElement("li"); const code = document.createElement("code"); const reason = document.createElement("span");
    replaceText(code, item.path); replaceText(reason, item.reason); node.append(code, reason); appendEvidence(node, item.evidence); return node;
  }));
  byId("impact-risks").replaceChildren(...view.data.risks.map((item) => {
    const node = document.createElement("li"); node.dataset.severity = item.severity; replaceText(node, `[${item.severity}] ${item.summary}`); appendEvidence(node, item.evidence); return node;
  }));
  byId("impact-actions").replaceChildren(...view.data.actions.map((action) => {
    const details = document.createElement("details"); const summary = document.createElement("summary"); const text = document.createElement("p");
    replaceText(summary, action.label); replaceText(text, action.summary); details.append(summary, text); appendEvidence(details, action.evidence); return details;
  }));
  byId("impact-view").hidden = false;
}
