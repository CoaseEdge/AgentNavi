import type { AgentNaviView, ContextView } from "../protocol.js";
import { renderRepositoryOverview } from "./repo-overview.js";

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
  };
  renderers[view.view](view);
}
