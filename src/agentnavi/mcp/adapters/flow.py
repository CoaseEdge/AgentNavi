"""Flow Core 数据到编号任务流与文本的严格投影。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..protocol import AgentNaviView
from .architecture import _stats
from .repo_overview import (
    _evidence_list,
    _evidence_reference,
    _mapping,
    _path,
    _project,
    _sequence,
    _source_state,
    _text,
)
from .repo_tour import _entity, _relation


def _flow_payload(core_data: Mapping[str, Any]) -> dict[str, Any]:
    data = _mapping(core_data, "flow")
    if data.get("layout") != "numbered-task-flow":
        raise ValueError("flow.layout 无效。")
    raw_task = data.get("exampleTask")
    task = None
    if raw_task is not None:
        item = _mapping(raw_task, "flow.exampleTask")
        task = {
            "title": _text(item.get("title"), "exampleTask.title"),
            "source": _text(item.get("source"), "exampleTask.source", limit=120),
            **({"entity": _entity(item.get("entity"), "exampleTask.entity")} if item.get("entity") is not None else {}),
            **({"evidence": _evidence_list(item.get("evidence", []), "exampleTask.evidence")} if item.get("evidence") is not None else {}),
        }
    raw_steps = _sequence(data.get("steps", []), "flow.steps")
    if raw_steps and not 5 <= len(raw_steps) <= 7:
        raise ValueError("flow.steps 必须为空或 5–7 步。")
    steps = []
    ids: set[str] = set()
    all_paths: set[str] = set()
    for index, raw in enumerate(raw_steps):
        item = _mapping(raw, "flow.steps[]")
        position = item.get("step")
        if isinstance(position, bool) or not isinstance(position, int) or position != index + 1:
            raise ValueError("flow.steps 必须连续编号。")
        step_id = _text(item.get("id"), "flow.step.id", limit=240)
        title = _text(item.get("title"), "flow.step.title", limit=240)
        if not step_id or step_id in ids or item.get("explanationSource") != "derived-presentation":
            raise ValueError("flow step id/source 无效。")
        ids.add(step_id)
        next_step = item.get("nextStep")
        expected_next = (
            _mapping(raw_steps[index + 1], "flow.steps[]").get("title")
            if index + 1 < len(raw_steps) else None
        )
        if next_step != expected_next:
            raise ValueError("flow.nextStep 必须指向紧邻步骤，末步必须为 null。")
        raw_files = _sequence(item.get("keyFiles", []), "flow.step.keyFiles")
        if len(raw_files) > 3:
            raise ValueError("flow.step.keyFiles 超过上限。")
        key_files = []
        for raw_file in raw_files:
            file_item = _mapping(raw_file, "flow.step.keyFiles[]")
            path = _path(file_item.get("path"), "keyFile.path")
            evidence = _evidence_list(file_item.get("evidence", []), "keyFile.evidence")
            if path in all_paths or not evidence:
                raise ValueError("flow keyFile path/evidence 无效。")
            all_paths.add(path)
            key_files.append({
                "path": path,
                "moduleId": _text(file_item.get("moduleId"), "keyFile.moduleId", limit=240),
                "moduleName": _text(file_item.get("moduleName"), "keyFile.moduleName", limit=240),
                "entity": _entity(file_item.get("entity"), "keyFile.entity"),
                "relation": _relation(file_item.get("relation"), "keyFile.relation"),
                "evidence": evidence,
            })
        evidence = _evidence_list(item.get("evidence", []), "flow.step.evidence")
        if not evidence:
            raise ValueError("flow.step.evidence 不得为空。")
        steps.append({
            "step": position,
            "id": step_id,
            "title": title,
            "purpose": _text(item.get("purpose"), "flow.step.purpose"),
            "input": _text(item.get("input"), "flow.step.input"),
            "output": _text(item.get("output"), "flow.step.output"),
            "keyFiles": key_files,
            "why": _text(item.get("why"), "flow.step.why"),
            "nextStep": _text(next_step, "flow.step.nextStep", limit=240) if next_step is not None else None,
            "explanationSource": "derived-presentation",
            "evidence": evidence,
        })
    if len(all_paths) > 18:
        raise ValueError("flow keyFiles 总数超过上限。")
    return {
        "layout": "numbered-task-flow",
        "exampleTask": task,
        "steps": steps,
        "stats": _stats(data.get("stats")),
    }


def flow_view(core_data: Mapping[str, Any]) -> AgentNaviView:
    source_state, warnings = _source_state(core_data)
    return AgentNaviView(
        view="flow",
        project=_project(core_data),
        source_state=source_state,
        data=_flow_payload(core_data),
        warnings=tuple(warnings),
    )


def flow_text(core_data: Mapping[str, Any]) -> str:
    project = _project(core_data)
    payload = _flow_payload(core_data)
    _, warnings = _source_state(core_data)
    task = payload["exampleTask"]
    lines = ["[AgentNavi 任务流]", f"项目：{project.name}（{project.id}）"]
    lines.append(f"任务：{task['title']}" if task else "任务：暂无可展示示例")
    for step in payload["steps"]:
        files = "、".join(item["path"] for item in step["keyFiles"]) or "未命中关键文件"
        lines.extend([
            "",
            f"{step['step']}. {step['title']}：{step['purpose']}",
            f"   输入：{step['input']}",
            f"   输出：{step['output']}",
            f"   关键源码：{files}",
            f"   为什么：{step['why']}",
            f"   下一步：{step['nextStep'] or '交给 Agent'}",
            f"   证据：{_evidence_reference(step['evidence'])}",
        ])
    if warnings:
        lines.extend(["", "提示："])
        lines.extend(f"- {warning.code}：{warning.message}" for warning in warnings)
    return "\n".join(lines)


__all__ = ["flow_text", "flow_view"]
