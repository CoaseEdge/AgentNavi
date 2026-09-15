from __future__ import annotations

import json
import math
import sqlite3
import uuid
from collections import defaultdict
from pathlib import Path, PurePosixPath
from statistics import mean
from typing import Any, Iterable, Mapping, Sequence

from .database import Database
from .query import context_data, search_terms
from .utils import json_dumps, json_loads, utc_now

RETRIEVAL_MODES = {"full-scan", "filename-search", "agentnavi"}
OBSERVED_MODES = {"baseline", "agentnavi"}

VLA_BENCHMARK_VIEWS = (
    "repo-overview",
    "repo-tour",
    "context",
    "impact",
    "history",
    "semantic-review",
)


def evaluate_vla_surface(
    results: Mapping[str, Mapping[str, Any]],
    *,
    gates: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate measured VLA observations; missing measurements are never a pass."""

    gates = gates or {
        "necessary_file_recall": 1.0,
        "candidate_set_expansion": 0.0,
        "max_model_text_budget_increase": 0.05,
        "task_success_regression": 0.0,
    }
    recall_gate = float(gates.get("necessary_file_recall", 1.0))
    token_gate = float(gates.get("max_model_text_budget_increase", 0.05))
    missing_views = [view for view in VLA_BENCHMARK_VIEWS if view not in results]
    view_metrics: dict[str, dict[str, Any]] = {}
    for view in VLA_BENCHMARK_VIEWS:
        item = results.get(view, {})
        required = {str(path) for path in item.get("required_paths", []) if str(path)}
        returned = {str(path) for path in item.get("returned_paths", []) if str(path)}
        baseline_candidates = item.get("baseline_candidate_count")
        candidate_count = item.get("candidate_count")
        baseline_tokens = item.get("baseline_model_tokens")
        model_tokens = item.get("model_tokens")
        baseline_success = item.get("baseline_success")
        success = item.get("success")
        measured = bool(item.get("measured", False))
        valid_measurement = (
            measured
            and bool(required)
            and isinstance(baseline_candidates, int)
            and baseline_candidates > 0
            and isinstance(candidate_count, int)
            and candidate_count > 0
            and isinstance(baseline_tokens, int)
            and baseline_tokens > 0
            and isinstance(model_tokens, int)
            and model_tokens > 0
            and baseline_success is True
            and isinstance(success, bool)
        )
        recall = len(required & returned) / len(required) if required else 0.0
        token_delta = (
            (model_tokens - baseline_tokens) / baseline_tokens
            if isinstance(baseline_tokens, int) and baseline_tokens > 0 and isinstance(model_tokens, int)
            else None
        )
        view_metrics[view] = {
            "recall": recall,
            "candidate_count": candidate_count,
            "candidate_set_not_expanded": (
                valid_measurement and candidate_count <= baseline_candidates
            ),
            "model_token_delta": token_delta,
            "model_budget_ok": valid_measurement and token_delta is not None and token_delta <= token_gate,
            "task_success_not_lower": valid_measurement and bool(success),
            "measurement_valid": valid_measurement,
            "invalid_reason": None if valid_measurement else "缺少有效 baseline / VLA observation",
        }
    recalls = [metric["recall"] for metric in view_metrics.values()]
    invalid_views = [view for view, metric in view_metrics.items() if not metric["measurement_valid"]]
    return {
        "views": list(VLA_BENCHMARK_VIEWS),
        "missing_views": missing_views,
        "invalid_views": invalid_views,
        "metrics": view_metrics,
        "necessary_file_recall": min(recalls, default=0.0),
        "candidate_set_not_expanded": all(
            metric["candidate_set_not_expanded"] for metric in view_metrics.values()
        ),
        "model_budget_ok": all(metric["model_budget_ok"] for metric in view_metrics.values()),
        "task_success_not_lower": all(
            metric["task_success_not_lower"] for metric in view_metrics.values()
        ),
        "passed": (
            not missing_views
            and not invalid_views
            and min(recalls, default=0.0) >= recall_gate
            and all(metric["candidate_set_not_expanded"] for metric in view_metrics.values())
            and all(metric["model_budget_ok"] for metric in view_metrics.values())
            and all(metric["task_success_not_lower"] for metric in view_metrics.values())
        ),
    }


def load_vla_benchmark_fixture(path: str | Path) -> dict[str, Any]:
    """Load a VLA fixture containing cases and the single source of gate thresholds."""

    source = Path(path).expanduser().resolve()
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"无法读取 VLA 基准 fixture：{source}: {exc}") from exc
    if not isinstance(payload, Mapping) or not isinstance(payload.get("cases"), list):
        raise ValueError("VLA 基准 fixture 必须包含 cases 数组")
    gates = payload.get("gates")
    if not isinstance(gates, Mapping):
        raise ValueError("VLA 基准 fixture 必须包含 gates 对象")
    cases: dict[str, dict[str, Any]] = {}
    for raw in payload["cases"]:
        if not isinstance(raw, Mapping):
            raise ValueError("VLA 基准 case 必须是对象")
        view = str(raw.get("view", ""))
        if view not in VLA_BENCHMARK_VIEWS or view in cases:
            raise ValueError(f"VLA 基准 view 无效或重复：{view}")
        required = raw.get("required_paths")
        if not isinstance(required, list) or not required or not all(isinstance(item, str) and item for item in required):
            raise ValueError(f"VLA 基准 {view} 必须声明非空 required_paths")
        for key in ("baseline_candidate_count", "baseline_model_tokens"):
            if not isinstance(raw.get(key), int) or raw[key] <= 0:
                raise ValueError(f"VLA 基准 {view} 的 {key} 必须为正整数")
        if raw.get("baseline_success") is not True:
            raise ValueError(f"VLA 基准 {view} 的 baseline_success 必须为 true")
        cases[view] = dict(raw)
    if set(cases) != set(VLA_BENCHMARK_VIEWS):
        raise ValueError("VLA 基准必须覆盖全部固定 view")
    return {"gates": dict(gates), "cases": cases}


def _paths_in(value: Any) -> set[str]:
    paths: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key == "path" and isinstance(item, str) and item:
                paths.add(item)
            paths.update(_paths_in(item))
    elif isinstance(value, list):
        for item in value:
            paths.update(_paths_in(item))
    return paths


def _observation_present(data: Mapping[str, Any], dotted: str) -> bool:
    current: Any = data
    for part in dotted.split("."):
        if isinstance(current, Mapping):
            current = current.get(part)
        else:
            return False
    if current is None or current == "":
        return False
    return not isinstance(current, (list, dict)) or bool(current)


def run_vla_benchmark(
    database: Database,
    project: sqlite3.Row,
    *,
    fixture_path: str | Path,
) -> dict[str, Any]:
    """Execute real Core view builders and MCP adapters against a fixture."""

    fixture = load_vla_benchmark_fixture(fixture_path)
    from .history_view import history_view_data
    from .impact_view import impact_view_data
    from .mcp.adapters.context import context_text, context_view
    from .mcp.adapters.history import history_text, history_view
    from .mcp.adapters.impact import impact_text, impact_to_view
    from .mcp.adapters.repo_overview import repo_overview_text, repo_overview_view
    from .mcp.adapters.repo_tour import repo_tour_text, repo_tour_view
    from .mcp.adapters.semantic_review import semantic_review_text, semantic_review_view
    from .repository_views import (
        repository_architecture_data,
        repository_flow_data,
        repository_overview_data,
        repository_tour_data,
    )
    from .semantic_review_view import semantic_review_view_data

    results: dict[str, dict[str, Any]] = {}
    for view, case in fixture["cases"].items():
        try:
            if view == "repo-overview":
                core = repository_overview_data(database, project)
                projected = repo_overview_view(core).to_dict()
                text = repo_overview_text(core)
            elif view == "repo-tour":
                core = repository_tour_data(database, project)
                projected = repo_tour_view(core).to_dict()
                text = repo_tour_text(core)
            elif view == "context":
                core = context_data(database, project, str(case.get("query", "")))
                projected = context_view(core).to_dict()
                text = context_text(core)
            elif view == "impact":
                core = impact_view_data(database, project, str(case.get("selector", "")))
                projected = impact_to_view(core).to_dict()
                text = impact_text(core)
            elif view == "history":
                core = history_view_data(database, project, str(case.get("query", "")))
                projected = history_view(core).to_dict()
                text = history_text(core)
            else:
                core = semantic_review_view_data(database, project, limit=50)
                projected = semantic_review_view(core).to_dict()
                text = semantic_review_text(core)
            data = projected.get("data", {})
            observation_ok = all(
                _observation_present(projected, str(path))
                for path in case.get("required_observations", [])
            )
            returned_paths = sorted(_paths_in(data))
            results[view] = {
                **case,
                "returned_paths": returned_paths,
                "candidate_count": len(returned_paths),
                "model_tokens": max(1, math.ceil(len(text) / 4)),
                "success": observation_ok,
                "measured": True,
                "view_observation": {
                    "stable_review_ids": view == "semantic-review" and all(
                        bool(item.get("reviewId")) for item in data.get("reviewItems", [])
                    ),
                    "allowed_actions": view == "semantic-review" and all(
                        set(item.get("allowedActions", [])) <= {"accept", "reject"}
                        for item in data.get("reviewItems", [])
                    ),
                },
            }
        except Exception as exc:
            results[view] = {
                **case,
                "returned_paths": [],
                "candidate_count": 0,
                "model_tokens": 0,
                "success": False,
                "measured": False,
                "error": str(exc),
            }
    report = evaluate_vla_surface(results, gates=fixture["gates"])
    report["observations"] = results
    return report


def _normalize_path(value: str) -> str:
    return PurePosixPath(value.replace("\\", "/").strip().lstrip("./")).as_posix()


def load_benchmark_cases(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path).expanduser().resolve()
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"无法读取基准用例：{source}: {exc}") from exc
    if isinstance(data, dict):
        data = data.get("cases")
    if not isinstance(data, list):
        raise ValueError("基准文件必须是 JSON 数组，或包含 cases 数组的对象")
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"第 {index} 个用例不是对象")
        case_key = str(item.get("id") or item.get("key") or f"case-{index}").strip()
        task = str(item.get("task") or item.get("query") or "").strip()
        expected = item.get("expected_files", [])
        if not case_key or case_key in seen:
            raise ValueError(f"基准用例 id 重复或为空：{case_key}")
        if not task:
            raise ValueError(f"基准用例 {case_key} 缺少 task")
        if not isinstance(expected, list) or not all(isinstance(value, str) for value in expected):
            raise ValueError(f"基准用例 {case_key} 的 expected_files 必须是字符串数组")
        seen.add(case_key)
        cases.append(
            {
                "id": case_key,
                "task": task,
                "expected_files": list(dict.fromkeys(_normalize_path(value) for value in expected)),
            }
        )
    return cases


def _file_inventory(connection: sqlite3.Connection, project_id: str) -> dict[str, dict[str, Any]]:
    inventory: dict[str, dict[str, Any]] = {}
    for row in connection.execute(
        """
        SELECT key, label, data_json FROM nodes
        WHERE project_id=? AND layer=1 AND kind='file' ORDER BY key
        """,
        (project_id,),
    ):
        data = json_loads(row["data_json"], {})
        inventory[row["key"]] = {
            "path": row["key"],
            "label": row["label"],
            "size": max(0, int(data.get("size", 0) or 0)),
            "language": str(data.get("language") or "unknown"),
        }
    return inventory


def _filename_candidates(task: str, inventory: Mapping[str, Mapping[str, Any]], limit: int) -> list[str]:
    terms = search_terms(task)
    scored: list[tuple[float, str]] = []
    for path, info in inventory.items():
        lowered_path = path.lower()
        lowered_label = str(info.get("label") or path).lower()
        score = 0.0
        for term in terms:
            if term == lowered_path or term == lowered_label:
                score += 10.0
            elif term in lowered_path:
                score += 4.0 + min(len(term), 10) / 10
            elif term in lowered_label:
                score += 3.0
        if score > 0:
            scored.append((score, path))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [path for _, path in scored[:limit]]


def _retrieval_candidates(
    database: Database,
    project: sqlite3.Row,
    *,
    task: str,
    mode: str,
    limit: int,
    inventory: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    if mode == "full-scan":
        return list(inventory)
    if mode == "filename-search":
        return _filename_candidates(task, inventory, limit)
    if mode == "agentnavi":
        data = context_data(
            database,
            project,
            task,
            concept_limit=5,
            file_limit=limit,
            task_limit=0,
        )
        return list(
            dict.fromkeys(
                _normalize_path(str(item["path"]))
                for item in data.get("files", [])
                if item.get("path")
            )
        )
    raise ValueError(f"不支持的基准模式：{mode}")


def _retrieval_metrics(
    *,
    expected_files: Sequence[str],
    candidates: Sequence[str],
    inventory: Mapping[str, Mapping[str, Any]],
    chars_per_token: float,
) -> dict[str, Any]:
    expected = set(expected_files)
    candidate_set = set(candidates)
    hits = sorted(expected & candidate_set)
    missing = sorted(expected - candidate_set)
    unexpected = sorted(candidate_set - expected)
    total_files = len(inventory)
    candidate_count = len(candidate_set)
    candidate_bytes = sum(int(inventory.get(path, {}).get("size", 0)) for path in candidate_set)
    total_bytes = sum(int(info.get("size", 0)) for info in inventory.values())
    estimated_tokens = math.ceil(candidate_bytes / max(chars_per_token, 0.1))
    total_estimated_tokens = math.ceil(total_bytes / max(chars_per_token, 0.1))
    return {
        "expected_count": len(expected),
        "hit_count": len(hits),
        "candidate_count": candidate_count,
        "total_files": total_files,
        "precision": (len(hits) / candidate_count) if candidate_count else 0.0,
        "recall": (len(hits) / len(expected)) if expected else 1.0,
        "candidate_reduction": (1.0 - candidate_count / total_files) if total_files else 0.0,
        "candidate_bytes": candidate_bytes,
        "total_bytes": total_bytes,
        "estimated_context_tokens": estimated_tokens,
        "full_scan_estimated_tokens": total_estimated_tokens,
        "estimated_token_reduction": (
            1.0 - estimated_tokens / total_estimated_tokens
            if total_estimated_tokens
            else 0.0
        ),
        "hits": hits,
        "missing_expected": missing,
        "unexpected_candidates": unexpected,
    }


def _insert_run(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    suite: str,
    case_key: str,
    run_kind: str,
    mode: str,
    task_text: str,
    task_id: str | None,
    expected_files: Sequence[str],
    candidate_files: Sequence[str],
    metrics: Mapping[str, Any],
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    duration_ms: int | None = None,
    success: bool | None = None,
) -> str:
    run_id = f"bench_{uuid.uuid4().hex}"
    connection.execute(
        """
        INSERT INTO benchmark_runs(
            id, project_id, suite, case_key, run_kind, mode, task_text,
            task_id, expected_files_json, candidate_files_json, metrics_json,
            input_tokens, output_tokens, duration_ms, success, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            project_id,
            suite,
            case_key,
            run_kind,
            mode,
            task_text,
            task_id,
            json_dumps(list(expected_files)),
            json_dumps(list(candidate_files)),
            json_dumps(dict(metrics)),
            input_tokens,
            output_tokens,
            duration_ms,
            None if success is None else int(success),
            utc_now(),
        ),
    )
    return run_id


def evaluate_retrieval_suite(
    database: Database,
    project: sqlite3.Row,
    *,
    cases_path: str | Path,
    suite: str,
    modes: Iterable[str] = ("full-scan", "filename-search", "agentnavi"),
    file_limit: int = 12,
    chars_per_token: float = 4.0,
) -> list[dict[str, Any]]:
    selected_modes = list(dict.fromkeys(modes))
    invalid = [mode for mode in selected_modes if mode not in RETRIEVAL_MODES]
    if invalid:
        raise ValueError(f"不支持的检索基准模式：{', '.join(invalid)}")
    if file_limit <= 0:
        raise ValueError("file_limit 必须大于 0")
    cases = load_benchmark_cases(cases_path)
    with database.connect() as connection:
        inventory = _file_inventory(connection, project["id"])
    if not inventory:
        raise RuntimeError("项目尚无 L1 文件索引，请先运行 agentnavi scan")

    results: list[dict[str, Any]] = []
    for case in cases:
        for mode in selected_modes:
            candidates = _retrieval_candidates(
                database,
                project,
                task=case["task"],
                mode=mode,
                limit=file_limit,
                inventory=inventory,
            )
            metrics = _retrieval_metrics(
                expected_files=case["expected_files"],
                candidates=candidates,
                inventory=inventory,
                chars_per_token=chars_per_token,
            )
            with database.connect() as connection:
                run_id = _insert_run(
                    connection,
                    project_id=project["id"],
                    suite=suite,
                    case_key=case["id"],
                    run_kind="retrieval",
                    mode=mode,
                    task_text=case["task"],
                    task_id=None,
                    expected_files=case["expected_files"],
                    candidate_files=candidates,
                    metrics=metrics,
                )
                connection.commit()
            results.append(
                {
                    "run_id": run_id,
                    "suite": suite,
                    "case": case["id"],
                    "mode": mode,
                    "task": case["task"],
                    "expected_files": case["expected_files"],
                    "candidates": candidates,
                    "metrics": metrics,
                }
            )
    return results


def record_observed_run(
    database: Database,
    project: sqlite3.Row,
    *,
    suite: str,
    case_key: str,
    mode: str,
    task_id: str,
    expected_files: Sequence[str] = (),
    exploration_tokens: int | None = None,
    output_tokens: int | None = None,
    duration_ms: int | None = None,
    success: bool | None = None,
) -> dict[str, Any]:
    if mode not in OBSERVED_MODES:
        raise ValueError("实测模式只能是 baseline 或 agentnavi")
    normalized_expected = list(dict.fromkeys(_normalize_path(value) for value in expected_files))
    with database.connect() as connection:
        task = connection.execute(
            "SELECT * FROM tasks WHERE id=? AND project_id=?",
            (task_id, project["id"]),
        ).fetchone()
        if task is None:
            raise LookupError(f"找不到本项目任务：{task_id}")
        rows = list(
            connection.execute(
                """
                SELECT event_type, path FROM events
                WHERE task_id=? AND path IS NOT NULL ORDER BY id
                """,
                (task_id,),
            )
        )
        inventory = _file_inventory(connection, project["id"])
    observed = list(dict.fromkeys(row["path"] for row in rows if row["path"]))
    read_files = list(
        dict.fromkeys(
            row["path"]
            for row in rows
            if row["path"] and row["event_type"] == "read"
        )
    )
    expected = set(normalized_expected)
    observed_set = set(observed)
    read_set = set(read_files)
    metrics = {
        "observed_file_count": len(observed_set),
        "read_file_count": len(read_set),
        "expected_count": len(expected),
        "expected_observed_recall": (
            len(expected & observed_set) / len(expected) if expected else None
        ),
        "irrelevant_read_count": len(read_set - expected) if expected else None,
        "irrelevant_read_ratio": (
            len(read_set - expected) / len(read_set)
            if expected and read_set
            else None
        ),
        "missing_expected": sorted(expected - observed_set),
        "exploration_tokens": exploration_tokens,
        "total_project_files": len(inventory),
    }
    with database.connect() as connection:
        run_id = _insert_run(
            connection,
            project_id=project["id"],
            suite=suite,
            case_key=case_key,
            run_kind="observed",
            mode=mode,
            task_text=task["prompt"],
            task_id=task_id,
            expected_files=normalized_expected,
            candidate_files=observed,
            metrics=metrics,
            input_tokens=exploration_tokens,
            output_tokens=output_tokens,
            duration_ms=duration_ms,
            success=success,
        )
        connection.commit()
    return {
        "run_id": run_id,
        "suite": suite,
        "case": case_key,
        "mode": mode,
        "task_id": task_id,
        "observed_files": observed,
        "read_files": read_files,
        "metrics": metrics,
    }


def _latest_runs(rows: Sequence[sqlite3.Row]) -> list[sqlite3.Row]:
    latest: dict[tuple[str, str, str], sqlite3.Row] = {}
    for row in rows:
        key = (row["case_key"], row["run_kind"], row["mode"])
        if key not in latest or row["created_at"] > latest[key]["created_at"]:
            latest[key] = row
    return list(latest.values())


def _numeric_average(values: Iterable[Any]) -> float | None:
    numbers = [float(value) for value in values if isinstance(value, (int, float))]
    return mean(numbers) if numbers else None


def compare_suite(
    database: Database,
    project_id: str,
    *,
    suite: str,
    run_kind: str | None = None,
) -> dict[str, Any]:
    with database.connect() as connection:
        if run_kind:
            rows = list(
                connection.execute(
                    """
                    SELECT * FROM benchmark_runs
                    WHERE project_id=? AND suite=? AND run_kind=?
                    ORDER BY created_at
                    """,
                    (project_id, suite, run_kind),
                )
            )
        else:
            rows = list(
                connection.execute(
                    """
                    SELECT * FROM benchmark_runs
                    WHERE project_id=? AND suite=? ORDER BY created_at
                    """,
                    (project_id, suite),
                )
            )
    rows = _latest_runs(rows)
    if not rows:
        raise LookupError(f"没有基准数据：{suite}")

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    case_rows: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        metrics = json_loads(row["metrics_json"], {})
        payload = {
            "case": row["case_key"],
            "run_kind": row["run_kind"],
            "mode": row["mode"],
            "metrics": metrics,
            "input_tokens": row["input_tokens"],
            "output_tokens": row["output_tokens"],
            "duration_ms": row["duration_ms"],
            "success": None if row["success"] is None else bool(row["success"]),
        }
        grouped[(row["run_kind"], row["mode"])].append(payload)
        case_rows[(row["case_key"], row["run_kind"], row["mode"])] = payload

    summaries: list[dict[str, Any]] = []
    for (kind, mode), items in sorted(grouped.items()):
        metric_names = sorted(
            {
                key
                for item in items
                for key, value in item["metrics"].items()
                if isinstance(value, (int, float)) and not isinstance(value, bool)
            }
        )
        averages = {
            key: _numeric_average(item["metrics"].get(key) for item in items)
            for key in metric_names
        }
        summaries.append(
            {
                "run_kind": kind,
                "mode": mode,
                "cases": len(items),
                "averages": averages,
                "average_input_tokens": _numeric_average(item["input_tokens"] for item in items),
                "average_output_tokens": _numeric_average(item["output_tokens"] for item in items),
                "average_duration_ms": _numeric_average(item["duration_ms"] for item in items),
                "success_rate": _numeric_average(
                    1.0 if item["success"] else 0.0
                    for item in items
                    if item["success"] is not None
                ),
            }
        )

    comparisons: list[dict[str, Any]] = []
    for kind, baseline_mode, agent_mode, metric in (
        ("retrieval", "full-scan", "agentnavi", "estimated_context_tokens"),
        ("retrieval", "filename-search", "agentnavi", "estimated_context_tokens"),
        ("observed", "baseline", "agentnavi", "exploration_tokens"),
    ):
        all_pairs: list[dict[str, Any]] = []
        eligible_pairs: list[tuple[float, float]] = []
        for case_key in {key[0] for key in case_rows if key[1] == kind}:
            baseline = case_rows.get((case_key, kind, baseline_mode))
            agent = case_rows.get((case_key, kind, agent_mode))
            if not baseline or not agent:
                continue
            before = baseline["metrics"].get(metric)
            after = agent["metrics"].get(metric)
            if not isinstance(before, (int, float)) or not isinstance(after, (int, float)):
                continue
            baseline_recall = baseline["metrics"].get(
                "recall", baseline["metrics"].get("expected_observed_recall")
            )
            agent_recall = agent["metrics"].get(
                "recall", agent["metrics"].get("expected_observed_recall")
            )
            baseline_recall_value = (
                float(baseline_recall) if isinstance(baseline_recall, (int, float)) else None
            )
            agent_recall_value = (
                float(agent_recall) if isinstance(agent_recall, (int, float)) else None
            )
            pair = {
                "before": float(before),
                "after": float(after),
                "baseline_recall": baseline_recall_value,
                "agentnavi_recall": agent_recall_value,
                "baseline_success": baseline["success"],
                "agentnavi_success": agent["success"],
            }
            all_pairs.append(pair)
            # “少读文件”只有在必要文件召回没有明显下降时才算节省；
            # 实测 Agent 对照还必须两边都明确完成任务，不能把失败或未知算作收益。
            recall_ok = (
                baseline_recall_value is not None
                and agent_recall_value is not None
                and baseline_recall_value >= 0.95
                and agent_recall_value >= 0.95
            )
            success_ok = kind != "observed" or (
                baseline["success"] is True and agent["success"] is True
            )
            if recall_ok and success_ok:
                eligible_pairs.append((float(before), float(after)))
        if not all_pairs:
            continue
        before_total = sum(item[0] for item in eligible_pairs)
        after_total = sum(item[1] for item in eligible_pairs)
        comparisons.append(
            {
                "run_kind": kind,
                "baseline": baseline_mode,
                "agentnavi": agent_mode,
                "metric": metric,
                "paired_cases": len(all_pairs),
                "eligible_cases": len(eligible_pairs),
                "excluded_for_low_quality": len(all_pairs) - len(eligible_pairs),
                "quality_gate": "双方召回率≥0.95；实测对照还要求双方 success=true",
                "baseline_recall": _numeric_average(
                    item["baseline_recall"] for item in all_pairs
                ),
                "agentnavi_recall": _numeric_average(
                    item["agentnavi_recall"] for item in all_pairs
                ),
                "baseline_total": before_total if eligible_pairs else None,
                "agentnavi_total": after_total if eligible_pairs else None,
                "reduction": (
                    1.0 - after_total / before_total
                    if eligible_pairs and before_total
                    else None
                ),
            }
        )

    return {
        "suite": suite,
        "project_id": project_id,
        "latest_runs": len(rows),
        "summaries": summaries,
        "comparisons": comparisons,
    }
