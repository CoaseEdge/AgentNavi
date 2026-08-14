from __future__ import annotations

import sqlite3
from dataclasses import replace

from .database import Database
from .scanning import ScanReport, scan_physical_layer
from .semantics import build_semantic_layer


def scan_project(database: Database, project: sqlite3.Row, *, full: bool = False) -> ScanReport:
    physical = scan_physical_layer(database, project, full=full)
    semantic_nodes, semantic_edges = build_semantic_layer(database, project)
    return replace(physical, semantic_nodes=semantic_nodes, semantic_edges=semantic_edges)
