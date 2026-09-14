import {
  isCanonicalRelativePath,
  parseContextView,
  parsePublicError,
  parseTaskQuery,
} from "../src/protocol.js";

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

const rejectedPaths = [
  "/private/file.py",
  "C:\\private\\file.py",
  "C:/private/file.py",
  "C:file.py",
  "\\\\server\\share\\file.py",
  "//server/share/file.py",
  "file:///private/file.py",
  "https://example.com/file.py",
  "custom:file.py",
  "../file.py",
  "docs/../file.py",
  "./docs/file.py",
  "docs//file.py",
  " docs/file.py",
  "docs/file.py ",
  "docs/file\n.py",
  "docs/file\u007f.py",
];
for (const path of rejectedPaths) {
  assert(!isCanonicalRelativePath(path), `应拒绝非规范路径：${JSON.stringify(path)}`);
}
assert(isCanonicalRelativePath("docs/Project Plan.md"), "应允许文件名中的普通空格");

assert(parseTaskQuery({ query: " 会员入口 " }) === "会员入口", "应规范化 query");
assert(parseTaskQuery({ query: 42 }) === undefined, "应拒绝非字符串 query");
for (const query of [
  "/private/file.py",
  "fix /private/file.py now",
  String.raw`fix C:\Users\alice\file.py`,
  String.raw`fix \\server\share\file.py`,
  "inspect file:///private/file.py",
]) {
  assert(parseTaskQuery({ query }) === "[查询含路径，已隐藏]", "路径型 query 不应回显");
}
assert(
  parseTaskQuery({ query: "inspect https://example.com/api" }) === "inspect https://example.com/api",
  "HTTP URL 不应误判为本地路径",
);
assert(
  parsePublicError({ code: "INVALID_ARGUMENT", message: "/private/secret.py" })?.message ===
    "请求参数无效，请检查参数类型和取值。",
  "应只显示本地固定公开错误，不回显远端 message",
);

console.log("protocol unit checks passed");
