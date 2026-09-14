import { parseContextView, parseTaskQuery } from "../src/protocol.js";

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

const fixture = {
  schemaVersion: "agentnavi.vla.v1",
  view: "context",
  project: { id: "fixture", name: "Fixture <script>", kind: "software" },
  sourceState: { status: "ready" },
  data: {
    stats: { files: 1, concepts: 1, tasks: 0 },
    concepts: [
      {
        id: "concept:membership",
        label: "会员 <img onerror=alert(1)>",
        confidence: 1.5,
        source: "semantic-heuristic",
        files: [],
      },
    ],
    files: [{ path: "src/membership.py", relation: "implemented_by", language: "python" }],
  },
  warnings: [{ code: "SOURCE_PARTIAL", message: "索引不完整" }],
};

const view = parseContextView(fixture);
assert(view?.project.name === "Fixture <script>", "应保留待 textContent 渲染的项目名");
assert(view.data.concepts[0]?.label.includes("<img") === true, "应保留纯文本概念名");
assert(view.data.concepts[0]?.confidence === 1, "置信度应限制在有效范围");
assert(view.data.files[0]?.path === "src/membership.py", "应接受 POSIX 相对路径");

assert(parseContextView({ ...fixture, schemaVersion: "future" }) === undefined, "应拒绝未知协议");
assert(parseContextView({ ...fixture, view: "impact" }) === undefined, "S04 应拒绝非 context 视图");
const unsafe = structuredClone(fixture);
unsafe.data.files[0]!.path = "/private/project.py";
assert(parseContextView(unsafe)?.data.files.length === 0, "应丢弃绝对路径");

assert(parseTaskQuery({ query: " 会员入口 " }) === "会员入口", "应规范化 query");
assert(parseTaskQuery({ query: 42 }) === undefined, "应拒绝非字符串 query");

console.log("protocol unit checks passed");
