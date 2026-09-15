"""把 Core 查询结果投影为受控的 VLA 输出。"""

from .context import context_text, context_view
from .architecture import architecture_text, architecture_view
from .flow import flow_text, flow_view
from .impact import impact_text, impact_to_view
from .history import history_text, history_view
from .repo_overview import repo_overview_text, repo_overview_view
from .repo_tour import repo_tour_text, repo_tour_view

__all__ = [
    "context_text",
    "context_view",
    "architecture_text",
    "architecture_view",
    "flow_text",
    "flow_view",
    "impact_text",
    "impact_to_view",
    "history_text",
    "history_view",
    "repo_overview_text",
    "repo_overview_view",
    "repo_tour_text",
    "repo_tour_view",
]
