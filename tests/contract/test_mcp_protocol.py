from __future__ import annotations

import json
import math
import unittest

from agentnavi.mcp.protocol import (
    SCHEMA_VERSION,
    SUPPORTED_VIEWS,
    AgentNaviView,
    EntityRef,
    Error,
    Evidence,
    GraphEdge,
    Project,
    SourceState,
    Warning,
)


class VLAProtocolContractTests(unittest.TestCase):
    def make_view(self, view: str = "context") -> AgentNaviView:
        evidence = Evidence(
            kind="source-location",
            summary="命中项目架构说明",
            path="docs/architecture.md",
            line_start=12,
            line_end=18,
            layer="L1",
            source="repository",
            confidence=1.0,
        )
        entity = EntityRef(
            id="file:docs/architecture.md",
            kind="file",
            label="架构说明",
            path="docs/architecture.md",
            layer="L1",
            source="repository",
            confidence=1.0,
            evidence=(evidence,),
        )
        edge = GraphEdge(
            id="edge:file-architecture-documents-context-engine",
            source_id=entity.id,
            target_id="concept:context-engine",
            relation="documents",
            layer="L2",
            source="semantic-heuristic",
            confidence=0.78,
            evidence=(evidence,),
        )
        return AgentNaviView(
            view=view,
            project=Project(id="agentnavi", name="AgentNavi", kind="software"),
            source_state=SourceState(
                status="ready",
                revision="scan-42",
                indexed_at="2026-09-14T09:30:00Z",
            ),
            data={"entities": [entity], "edges": [edge]},
            warnings=(Warning(code="PARTIAL_L2", message="部分关系来自启发式推断"),),
            extensions={"traceId": "trace-1"},
        )

    def test_schema_and_supported_views_are_frozen(self) -> None:
        self.assertEqual(SCHEMA_VERSION, "agentnavi.vla.v1")
        self.assertEqual(
            SUPPORTED_VIEWS,
            (
                "repo-overview",
                "repo-tour",
                "architecture",
                "flow",
                "context",
                "impact",
                "history",
                "semantic-review",
            ),
        )
        for view in SUPPORTED_VIEWS:
            self.assertEqual(self.make_view(view).to_dict()["view"], view)

        with self.assertRaisesRegex(ValueError, "不支持的 VLA view"):
            self.make_view("unknown")

    def test_envelope_has_required_wire_fields_and_round_trips(self) -> None:
        payload = self.make_view().to_dict()
        self.assertEqual(
            {"schemaVersion", "view", "project", "sourceState", "data", "warnings"}
            - payload.keys(),
            set(),
        )
        self.assertEqual(payload["schemaVersion"], SCHEMA_VERSION)
        self.assertEqual(
            payload["project"],
            {"id": "agentnavi", "name": "AgentNavi", "kind": "software"},
        )
        self.assertEqual(payload["sourceState"]["indexedAt"], "2026-09-14T09:30:00Z")
        self.assertEqual(payload["traceId"], "trace-1")
        self.assertEqual(json.loads(self.make_view().to_json()), payload)

    def test_common_dtos_preserve_layer_source_confidence_and_evidence(self) -> None:
        payload = self.make_view().to_dict()
        entity = payload["data"]["entities"][0]
        edge = payload["data"]["edges"][0]
        for item in (entity, edge):
            self.assertIn("layer", item)
            self.assertIn("source", item)
            self.assertIn("confidence", item)
            self.assertIn("evidence", item)
        self.assertEqual(edge["sourceId"], "file:docs/architecture.md")
        self.assertEqual(edge["targetId"], "concept:context-engine")
        self.assertEqual(edge["id"], "edge:file-architecture-documents-context-engine")

    def test_error_is_a_json_safe_public_dto(self) -> None:
        error = Error(
            code="PROJECT_REQUIRED",
            message="请明确指定项目。",
            retryable=True,
            details={"candidateCount": 2},
        )
        self.assertEqual(
            error.to_dict(),
            {
                "code": "PROJECT_REQUIRED",
                "message": "请明确指定项目。",
                "retryable": True,
                "details": {"candidateCount": 2},
            },
        )

    def test_paths_must_be_posix_project_relative(self) -> None:
        invalid_paths = (
            "/Users/example/project/README.md",
            "C:\\project\\README.md",
            "C:/project/README.md",
            "//server/share/README.md",
            "file:///private/project/README.md",
            "../README.md",
            "docs\\README.md",
            "./docs/README.md",
        )
        for path in invalid_paths:
            with self.subTest(path=path):
                with self.assertRaisesRegex(ValueError, "POSIX 相对路径"):
                    Evidence(
                        kind="source-location",
                        summary="证据",
                        path=path,
                        layer="L1",
                        source="repository",
                        confidence=1.0,
                    )

        with self.assertRaisesRegex(ValueError, "POSIX 相对路径|不得包含绝对路径"):
            AgentNaviView(
                view="context",
                project=Project(id="agentnavi", name="AgentNavi", kind="software"),
                source_state=SourceState(status="ready"),
                data={"nextStep": {"path": "/private/project/README.md"}},
            )

    def test_private_and_raw_fields_are_rejected_recursively(self) -> None:
        forbidden_samples = (
            {"projectRoot": "/private/project"},
            {"metadata": {"logPath": "/private/events.jsonl"}},
            {"sqliteRow": {"node_id": "secret"}},
            {"rawEvents": [{"payload": "unfiltered"}]},
            {"fileContent": "完整源文件正文"},
        )
        for data in forbidden_samples:
            with self.subTest(data=data):
                with self.assertRaisesRegex(ValueError, "禁止输出字段"):
                    AgentNaviView(
                        view="context",
                        project=Project(id="agentnavi", name="AgentNavi", kind="software"),
                        source_state=SourceState(status="ready"),
                        data=data,
                    )

    def test_absolute_path_canaries_are_rejected_in_all_wire_strings(self) -> None:
        factories = (
            lambda: AgentNaviView(
                view="context",
                project=Project(id="agentnavi", name="AgentNavi", kind="software"),
                source_state=SourceState(status="ready"),
                data={"note": "内部位置是 /Users/alice/private/repo"},
            ),
            lambda: AgentNaviView(
                view="context",
                project=Project(id="agentnavi", name="AgentNavi", kind="software"),
                source_state=SourceState(status="ready"),
                data={"note": "内部位置：/数据/项目"},
            ),
            lambda: AgentNaviView(
                view="context",
                project=Project(id="agentnavi", name="AgentNavi", kind="software"),
                source_state=SourceState(status="ready"),
                data={"note": "内部位置:/Users/alice/private/repo"},
            ),
            lambda: Evidence(
                kind="source-location",
                summary="证据来自 C:\\private\\repo\\README.md",
                layer="L1",
                source="repository",
                confidence=1.0,
            ),
            lambda: Warning(code="PATH_LEAK", message="不要读取 //server/private/repo"),
            lambda: Error(
                code="INTERNAL",
                message="内部错误",
                details={"note": "位置 file:///private/repo"},
            ),
            lambda: Project(
                id="agentnavi",
                name="AgentNavi",
                kind="software",
                extensions={"note": "缓存位于 /private/cache"},
            ),
        )
        for factory in factories:
            with self.subTest(factory=factory):
                with self.assertRaisesRegex(ValueError, "不得包含绝对路径"):
                    factory()

    def test_absolute_path_canaries_cover_object_keys_but_allow_relative_path_indexes(self) -> None:
        private_keys = (
            "/private/project/file.py",
            "file:///private/project/file.py",
            "C:\\private\\project\\file.py",
            "\\\\server\\private\\file.py",
        )
        for key in private_keys:
            with self.subTest(key=key):
                with self.assertRaisesRegex(ValueError, "不得包含绝对路径"):
                    AgentNaviView(
                        view="context",
                        project=Project(id="agentnavi", name="AgentNavi", kind="software"),
                        source_state=SourceState(status="ready"),
                        data={"pathIndex": {key: {"score": 1.0}}},
                    )

        view = AgentNaviView(
            view="context",
            project=Project(id="agentnavi", name="AgentNavi", kind="software"),
            source_state=SourceState(status="ready"),
            data={"pathIndex": {"src/agentnavi/query.py": {"score": 1.0}}},
        )
        self.assertIn("src/agentnavi/query.py", view.to_dict()["data"]["pathIndex"])

    def test_evidence_line_numbers_reject_bool_and_invalid_ranges(self) -> None:
        base = {
            "kind": "source-location",
            "summary": "证据",
            "layer": "L1",
            "source": "repository",
            "confidence": 1.0,
        }
        for field, value in (("line_start", True), ("line_end", True), ("line_start", 0)):
            with self.subTest(field=field, value=value):
                with self.assertRaisesRegex(ValueError, "必须大于等于 1"):
                    Evidence(**base, **{field: value})
        with self.assertRaisesRegex(ValueError, "不能小于"):
            Evidence(**base, line_start=3, line_end=2)

    def test_serialization_is_stable_and_json_values_are_strict(self) -> None:
        first = AgentNaviView(
            view="context",
            project=Project(id="agentnavi", name="AgentNavi", kind="software"),
            source_state=SourceState(status="ready"),
            data={"z": 1, "a": {"y": 2, "b": 3}},
        )
        second = AgentNaviView(
            view="context",
            project=Project(id="agentnavi", name="AgentNavi", kind="software"),
            source_state=SourceState(status="ready"),
            data={"a": {"b": 3, "y": 2}, "z": 1},
        )
        self.assertEqual(first.to_json(), second.to_json())

        for unsafe in ({"value": object()}, {1: "non-string-key"}, {"value": math.nan}):
            with self.subTest(unsafe=unsafe):
                with self.assertRaises((TypeError, ValueError)):
                    AgentNaviView(
                        view="context",
                        project=Project(id="agentnavi", name="AgentNavi", kind="software"),
                        source_state=SourceState(status="ready"),
                        data=unsafe,
                    )

    def test_extensions_are_additive_and_cannot_override_contract(self) -> None:
        view = self.make_view()
        self.assertEqual(view.to_dict()["traceId"], "trace-1")
        localized = AgentNaviView(
            view="context",
            project=Project(id="agentnavi", name="AgentNavi", kind="software"),
            source_state=SourceState(status="ready"),
            data={},
            extensions={"说明": "一", "标签": "二", "Straße": "三", "Strasse": "四"},
        ).to_dict()
        self.assertEqual((localized["说明"], localized["标签"]), ("一", "二"))
        self.assertEqual((localized["Straße"], localized["Strasse"]), ("三", "四"))
        with self.assertRaisesRegex(ValueError, "保留字段"):
            AgentNaviView(
                view="context",
                project=Project(id="agentnavi", name="AgentNavi", kind="software"),
                source_state=SourceState(status="ready"),
                data={},
                extensions={"schemaVersion": "future"},
            )
        for key in ("SchemaVersion", "schema_version", "schema-version"):
            with self.subTest(key=key):
                with self.assertRaisesRegex(ValueError, "保留字段"):
                    AgentNaviView(
                        view="context",
                        project=Project(id="agentnavi", name="AgentNavi", kind="software"),
                        source_state=SourceState(status="ready"),
                        data={},
                        extensions={key: "future"},
                    )
        with self.assertRaisesRegex(ValueError, "保留字段"):
            Evidence(
                kind="source-location",
                summary="证据",
                layer="L1",
                source="repository",
                confidence=1.0,
                extensions={"path": "docs/README.md"},
            )
        for key in ("Path", "line_start", "line-start"):
            with self.subTest(key=key):
                with self.assertRaisesRegex(ValueError, "保留字段"):
                    Evidence(
                        kind="source-location",
                        summary="证据",
                        layer="L1",
                        source="repository",
                        confidence=1.0,
                        extensions={key: "冲突"},
                    )

    def test_graph_edges_require_traceable_evidence(self) -> None:
        with self.assertRaisesRegex(ValueError, "至少包含一个 Evidence"):
            GraphEdge(
                id="edge:a-depends-b",
                source_id="concept:a",
                target_id="concept:b",
                relation="depends_on",
                layer="L2",
                source="semantic-heuristic",
                confidence=0.78,
            )

    def test_source_state_status_is_closed(self) -> None:
        for status in ("ready", "partial", "stale"):
            self.assertEqual(SourceState(status=status).to_dict()["status"], status)
        for status in ("missing", "error", "READY", True):
            with self.subTest(status=status):
                with self.assertRaisesRegex(ValueError, "ready、partial 或 stale"):
                    SourceState(status=status)  # type: ignore[arg-type]

    def test_frozen_dtos_take_defensive_snapshots(self) -> None:
        source = {"items": [{"label": "before"}]}
        view = AgentNaviView(
            view="context",
            project=Project(id="agentnavi", name="AgentNavi", kind="software"),
            source_state=SourceState(status="ready"),
            data=source,
        )
        before = view.to_json()
        source["items"][0]["label"] = "after"
        source["items"].append({"label": "new"})
        self.assertEqual(view.to_json(), before)
        with self.assertRaises(TypeError):
            view.data["new"] = "value"  # type: ignore[index]

    def test_indexed_at_requires_rfc3339_utc(self) -> None:
        for value in ("2026-09-14 09:30:00", "2026-09-14T09:30:00+08:00", "invalid"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "RFC 3339 UTC"):
                    SourceState(status="ready", indexed_at=value)


if __name__ == "__main__":
    unittest.main()
