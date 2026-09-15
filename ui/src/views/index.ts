import type { AgentNaviView, ContextView } from "../protocol.js";
import { renderRepositoryOverview } from "./repo-overview.js";
import { renderRepositoryTour } from "./repo-tour.js";
import { renderArchitecture } from "./architecture.js";
import { renderFlow } from "./flow.js";
import { renderImpact } from "./impact.js";

export function renderRegisteredView(
  view: AgentNaviView,
  renderContext: (context: ContextView) => void,
): void {
  const renderers: Record<AgentNaviView["view"], (value: AgentNaviView) => void> = {
    context: (value) => {
      if (value.view === "context") renderContext(value);
    },
    "repo-overview": (value) => {
      if (value.view === "repo-overview") renderRepositoryOverview(value);
    },
    "repo-tour": (value) => {
      if (value.view === "repo-tour") renderRepositoryTour(value);
    },
    architecture: (value) => {
      if (value.view === "architecture") renderArchitecture(value);
    },
    flow: (value) => {
      if (value.view === "flow") renderFlow(value);
    },
    impact: (value) => {
      if (value.view === "impact") renderImpact(value);
    },
  };
  renderers[view.view](view);
}
