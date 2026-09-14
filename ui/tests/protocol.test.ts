import {
  isCanonicalRelativePath,
  parseAgentNaviView,
  parseContextView,
  parsePublicError,
  parseRepositoryOverviewView,
  parseRepositoryTourView,
  parseRequestedView,
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
assert(parseContextView({ ...fixture, view: "repo-overview" }) === undefined, "Context parser 应拒绝其他视图");
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
  "inspect file:/private/file.py",
  "inspect vscode://file/private/file.py",
  "inspect vscode-insiders://file/private/file.py",
  "inspect cursor://file/private/file.py",
  "inspect custom-editor://file/private/file.py",
]) {
  assert(parseTaskQuery({ query }) === "[查询含路径，已隐藏]", "路径型 query 不应回显");
}
assert(
  parseTaskQuery({ query: "inspect https://example.com/api" }) === "inspect https://example.com/api",
  "HTTP URL 不应误判为本地路径",
);
assert(
  parseTaskQuery({ query: "inspect https://file.example.com/api" }) === "inspect https://file.example.com/api",
  "HTTP(S) 的 file host 仍应作为普通公网 URL",
);
assert(
  parsePublicError({ code: "INVALID_ARGUMENT", message: "/private/secret.py" })?.message ===
    "请求参数无效，请检查参数类型和取值。",
  "应只显示本地固定公开错误，不回显远端 message",
);

const overviewFixture = {
  schemaVersion: "agentnavi.vla.v1",
  view: "repo-overview",
  project: { id: "fixture", name: "Fixture", kind: "software" },
  sourceState: { status: "ready" },
  data: {
    purpose: {
      summary: "帮助协作者理解项目",
      evidence: [{ kind: "document", summary: "项目说明", layer: "L1", source: "repository-document", confidence: 1, path: "README.md", lineStart: 3 }],
    },
    need: {
      problem: { summary: "重复搜索", evidence: [{ kind: "document", summary: "问题", layer: "L1", source: "repository-document", confidence: 1, path: "README.md", lineStart: 8 }] },
      solution: { summary: "证据导航", evidence: [{ kind: "document", summary: "方案", layer: "L1", source: "repository-document", confidence: 1, path: "README.md", lineStart: 12 }] },
    },
    workflow: Array.from({ length: 7 }, (_, index) => ({
      step: index + 1,
      title: `动作 ${index + 1}`,
      detail: `动作 ${index + 1} 的说明`,
      evidence: [{ kind: "document", summary: "流程", layer: "L1", source: "repository-document", confidence: 1, path: "docs/architecture.md", lineStart: index + 3 }],
    })),
    modules: [{ id: "concept:core", name: "Core", summary: "核心模块", paths: ["src/core.py"], layer: "L2", source: "semantic-heuristic", confidence: 0.8, evidence: [] }],
    readingOrder: [{ position: 1, path: "README.md", reason: "先读目的", evidence: [] }],
    stats: { files: 3, concepts: 1, tasks: 0, documentsRead: 2 },
  },
  warnings: [],
};

const overview = parseRepositoryOverviewView(overviewFixture);
assert(overview?.data.workflow.length === 7, "应解析 5–7 步主流程");
assert(overview?.data.purpose.evidence[0]?.path === "README.md", "应保留规范证据路径");
assert(parseAgentNaviView(overviewFixture)?.view === "repo-overview", "通用 parser 应分派 Overview");
assert(parseRequestedView({ view: "repo-overview" }) === "repo-overview", "应识别 Overview 请求");

const tourEvidence = { kind: "document", summary: "证据", layer: "L1", source: "repository-document", confidence: 1, path: "README.md", lineStart: 3 };
const tourStop = {
  id: "purpose",
  kind: "purpose",
  title: "是什么",
  plainLanguage: "帮助理解项目",
  technicalExplanation: "来自项目文档",
  evidence: [tourEvidence],
  entity: { id: "purpose", kind: "concept", label: "项目目的", path: "README.md", layer: "L1", source: "repository-document", confidence: 1, evidence: [tourEvidence] },
  relations: [],
};
const tourFixture = {
  schemaVersion: "agentnavi.vla.v1",
  view: "repo-tour",
  project: { id: "fixture", name: "Fixture", kind: "software" },
  sourceState: { status: "ready" },
  data: {
    tiers: [
      { depth: "one-minute", label: "1 分钟", stops: [tourStop] },
      { depth: "five-minutes", label: "5 分钟", stops: [{ ...tourStop, id: "file", kind: "file" }] },
      { depth: "source-deep-dive", label: "深入源码", stops: [{ ...tourStop, id: "symbol", kind: "symbol" }] },
    ],
    stats: { files: 3, concepts: 1, tasks: 0, documentsRead: 2 },
  },
  warnings: [],
};
assert(parseRepositoryTourView(tourFixture)?.data.tiers.length === 3, "应解析固定三档 Tour");
assert(parseAgentNaviView(tourFixture)?.view === "repo-tour", "通用 parser 应分派 Tour");
assert(parseRequestedView({ view: "repo-tour" }) === "repo-tour", "应识别 Tour 请求");
const unsafeTour = structuredClone(tourFixture);
unsafeTour.data.tiers[0]!.stops[0]!.evidence[0]!.path = "/private/tour.py";
assert(parseRepositoryTourView(unsafeTour)?.data.tiers[0]?.stops.length === 0, "无有效证据的 Tour stop 应被丢弃");

const unsafeOverview = structuredClone(overviewFixture);
unsafeOverview.data.purpose.summary = "secret at /private/project";
unsafeOverview.data.purpose.evidence[0]!.path = "../outside.md";
unsafeOverview.data.need.problem.evidence[0]!.path = "cursor://file/private/problem.md";
unsafeOverview.data.need.solution.evidence[0]!.path = "custom-editor://file/private/solution.md";
unsafeOverview.data.modules[0]!.paths = ["file:///private/source.py"];
const sanitizedOverview = parseRepositoryOverviewView(unsafeOverview);
assert(sanitizedOverview?.data.purpose.summary === "[内容含路径，已隐藏]", "应隐藏递归文本路径");
assert(sanitizedOverview?.data.purpose.evidence.length === 0, "应丢弃不规范 Evidence 路径");
assert(sanitizedOverview?.data.need.problem.evidence.length === 0, "应过滤问题中的本地 URI Evidence");
assert(sanitizedOverview?.data.need.solution.evidence.length === 0, "应过滤方案中的本地 URI Evidence");
assert(sanitizedOverview?.data.modules[0]?.paths.length === 0, "应丢弃模块中的不规范路径");

const eightSteps = structuredClone(overviewFixture.data.workflow);
eightSteps.push({ ...eightSteps[0]!, step: 8 });
const duplicateSteps = overviewFixture.data.workflow.map((step, index) => ({
  ...step,
  step: index === 4 ? 4 : step.step,
}));
const fractionalSteps = overviewFixture.data.workflow.map((step, index) => ({
  ...step,
  step: index === 0 ? 1.9 : step.step,
}));
for (const invalidWorkflow of [
  overviewFixture.data.workflow.slice(0, 4),
  eightSteps,
  duplicateSteps,
  fractionalSteps,
]) {
  const candidate = structuredClone(overviewFixture);
  candidate.data.workflow = structuredClone(invalidWorkflow);
  const parsed = parseRepositoryOverviewView(candidate);
  assert(parsed?.data.workflow.length === 0, "非法 workflow 不应展示成 5–7 步主流程");
  assert(parsed?.warnings.some((warning) => warning.code === "WORKFLOW_SHAPE_INVALID"), "非法 workflow 应返回明确提示");
}

const emptyWorkflow = structuredClone(overviewFixture);
emptyWorkflow.data.workflow = [];
assert(parseRepositoryOverviewView(emptyWorkflow)?.data.workflow.length === 0, "空 workflow 是合法的证据不足状态");

console.log("protocol unit checks passed");
