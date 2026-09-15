import type { Evidence, RepositoryTourView, TourDepth, TourStop } from "../protocol.js";
import { byId, replaceText } from "./dom.js";

let currentView: RepositoryTourView | undefined;
let activeDepth: TourDepth = "one-minute";
const attachedButtons = new WeakSet<HTMLElement>();

function evidenceItem(evidence: Evidence): HTMLLIElement {
  const item = document.createElement("li");
  const location = evidence.path
    ? `${evidence.path}${evidence.lineStart ? `:${evidence.lineStart}` : ""}`
    : `${evidence.layer} · ${evidence.source}`;
  replaceText(item, `${location} · ${evidence.summary}`);
  return item;
}

function stopItem(stop: TourStop, position: number): HTMLLIElement {
  const item = document.createElement("li");
  item.className = "tour-stop";
  const kind = document.createElement("span");
  kind.className = "stage-index";
  replaceText(kind, `${String(position).padStart(2, "0")} / ${stop.kind}`);
  const title = document.createElement("h3");
  replaceText(title, stop.title);
  const plain = document.createElement("p");
  plain.className = "tour-plain";
  replaceText(plain, stop.plainLanguage);
  const details = document.createElement("details");
  const summary = document.createElement("summary");
  replaceText(summary, "技术说明与源码证据");
  const technical = document.createElement("p");
  technical.className = "tour-technical";
  replaceText(technical, stop.technicalExplanation);
  const evidence = document.createElement("ul");
  evidence.className = "tour-evidence";
  evidence.replaceChildren(...stop.evidence.map(evidenceItem));
  details.append(summary, technical, evidence);
  item.append(kind, title, plain, details);
  return item;
}

function showDepth(depth: TourDepth): void {
  if (!currentView) return;
  activeDepth = depth;
  for (const candidate of ["one-minute", "five-minutes", "source-deep-dive"] as TourDepth[]) {
    const button = byId(`tour-depth-${candidate}`);
    button.setAttribute("aria-pressed", String(candidate === depth));
  }
  const tier = currentView.data.tiers.find((item) => item.depth === depth);
  replaceText(byId("tour-depth-label"), tier?.label ?? "");
  byId("tour-stops").replaceChildren(
    ...(tier?.stops ?? []).map((stop, index) => stopItem(stop, index + 1)),
  );
  byId("tour-empty").hidden = Boolean(tier?.stops.length);
}

function attachListeners(): void {
  for (const depth of ["one-minute", "five-minutes", "source-deep-dive"] as TourDepth[]) {
    const button = byId(`tour-depth-${depth}`);
    if (attachedButtons.has(button)) continue;
    button.addEventListener("click", () => showDepth(depth));
    attachedButtons.add(button);
  }
}

export function clearRepositoryTour(): void {
  currentView = undefined;
  activeDepth = "one-minute";
  byId("repository-tour").hidden = true;
  byId("tour-stops").replaceChildren();
  replaceText(byId("tour-depth-label"), "");
}

export function renderRepositoryTour(view: RepositoryTourView): void {
  currentView = view;
  attachListeners();
  showDepth(activeDepth);
  byId("repository-tour").hidden = false;
}
