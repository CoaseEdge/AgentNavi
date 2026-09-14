import {
  isCanonicalRelativePath,
  parseAgentNaviView,
  parseArchitectureView,
  parseContextView,
  parseFlowView,
  parseImpactView,
  parsePublicError,
  parseRepositoryOverviewView,
  parseRepositoryTourView,
  parseRequestedView,
  parseTaskQuery,
} from "../src/protocol.js";

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

function contextNavigationFixture() {
  const evidence = { kind: "mapping", summary: "真实概念映射", layer: "L2", source: "semantic-heuristic", confidence: 0.8, path: "src/membership.py" };
  const sourceConcept = { id: "concept:membership", kind: "concept", label: "会员", layer: "L2", source: "semantic-heuristic", confidence: 0.9, evidence: [evidence] };
  const file = { id: "file:membership", kind: "file", label: "membership.py", path: "src/membership.py", layer: "L1", source: "repository", confidence: 1, evidence: [{ ...evidence, layer: "L1", source: "repository-index" }] };
  const fileRelation = { id: "edge:mapping", sourceId: sourceConcept.id, targetId: file.id, relation: "implemented_by", layer: "L2", source: "semantic-heuristic", confidence: 0.8, evidence: [evidence] };
  const labels = ["它做什么", "为什么相关", "谁依赖它", "过去谁改过", "如果改它"];
  const kinds = ["purpose", "relevance", "dependents", "history", "impact"];
  return {
    revision: "context-navigation-fixture",
    readingOrder: [{
      position: 1,
      path: "src/membership.py",
      language: "python",
      why: "会员概念通过 implemented_by 关联此文件。",
      evidence: [evidence],
      nextStep: null,
      chains: [{ sourceConcept, conceptRelation: null, relatedConcept: null, fileRelation, file, evidence: [evidence] }],
      actions: kinds.map((kind, index) => ({ kind, label: labels[index], summary: `${labels[index]} 的可追溯说明`, evidence: [evidence] })),
      dependents: [],
      history: [],
    }],
  };
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
    navigation: contextNavigationFixture(),
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
const tenWarnings = structuredClone(fixture) as any;
tenWarnings.warnings = Array.from(
  { length: 10 }, (_, index) => ({ code: `WARNING_${index}`, message: `提示 ${index}`, evidence: [] }),
);
assert(parseContextView(tenWarnings)?.warnings.length === 10, "Context UI 应完整保留十项公开 warning");
const elevenWarnings = structuredClone(tenWarnings) as any;
elevenWarnings.warnings.push({ code: "WARNING_10", message: "超出固定集合", evidence: [] });
assert(parseContextView(elevenWarnings) === undefined, "Context UI 不得静默截断超预算 warning");
const unsafe = structuredClone(fixture);
unsafe.data.files[0]!.path = "/private/project.py";
assert(parseContextView(unsafe) === undefined, "应拒绝候选与导航不一致的绝对路径");
const wrongNavigationEndpoint = structuredClone(fixture);
wrongNavigationEndpoint.data.navigation.readingOrder[0]!.chains[0]!.fileRelation.targetId = "other-file";
assert(parseContextView(wrongNavigationEndpoint) === undefined, "应拒绝虚构的 Context chain endpoint");
const expandedDependent = structuredClone(fixture) as any;
expandedDependent.data.navigation.readingOrder[0]!.dependents = [{ path: "src/not-a-candidate.py", relation: "imports" }];
assert(parseContextView(expandedDependent) === undefined, "dependent 不得扩大 Context 候选集");
for (const invalidPosition of [1.5, Number.NaN, true]) {
  const invalid = structuredClone(fixture) as any;
  invalid.data.navigation.readingOrder[0].position = invalidPosition;
  assert(parseContextView(invalid) === undefined, "Context position 必须是严格整数");
}
const oneHop = structuredClone(fixture) as any;
const oneHopChain = oneHop.data.navigation.readingOrder[0].chains[0];
oneHopChain.relatedConcept = {
  ...structuredClone(oneHopChain.sourceConcept),
  id: "concept:payment",
  label: "支付",
};
oneHopChain.conceptRelation = {
  ...structuredClone(oneHopChain.fileRelation),
  id: "edge:depends",
  sourceId: oneHopChain.sourceConcept.id,
  targetId: oneHopChain.relatedConcept.id,
  relation: "depends_on",
};
oneHopChain.fileRelation.sourceId = oneHopChain.relatedConcept.id;
assert(parseContextView(oneHop) !== undefined, "应接受端点真实且 ID 不重复的一跳链");
const selfLoop = structuredClone(oneHop) as any;
selfLoop.data.navigation.readingOrder[0].chains[0].relatedConcept.id = "concept:membership";
selfLoop.data.navigation.readingOrder[0].chains[0].conceptRelation.targetId = "concept:membership";
selfLoop.data.navigation.readingOrder[0].chains[0].fileRelation.sourceId = "concept:membership";
assert(parseContextView(selfLoop) === undefined, "sourceConcept 与 relatedConcept 不得复用 ID");
const crossKindId = structuredClone(fixture) as any;
crossKindId.data.navigation.readingOrder[0].chains[0].file.id = "concept:membership";
crossKindId.data.navigation.readingOrder[0].chains[0].fileRelation.targetId = "concept:membership";
assert(parseContextView(crossKindId) === undefined, "concept 与 file 不得跨 kind 复用 ID");
const wrongActions = structuredClone(fixture) as any;
wrongActions.data.navigation.readingOrder[0].actions.reverse();
assert(parseContextView(wrongActions) === undefined, "Context actions 必须保持固定顺序与标签");

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

const impactEvidence = { kind: "physical-relation", summary: "caller.py imports focus.py", layer: "L1", source: "extractor", confidence: 1, path: "src/caller.py" };
const impactFocus = { id: "file:focus", kind: "file", label: "focus.py", path: "src/focus.py", layer: "L1", source: "repository", confidence: 1, evidence: [impactEvidence] };
const impactPeer = { id: "file:caller", kind: "file", label: "caller.py", path: "src/caller.py", layer: "L1", source: "repository", confidence: 1, evidence: [impactEvidence] };
const impactRelation = { id: "edge:imports", sourceId: "file:caller", targetId: "file:focus", relation: "imports", layer: "L1", source: "extractor", confidence: 1, evidence: [impactEvidence] };
const impactFixture = {
  schemaVersion: "agentnavi.vla.v1", view: "impact",
  project: { id: "fixture", name: "Fixture", kind: "software" }, sourceState: { status: "ready" },
  data: { layout: "incoming-focus-outgoing", revision: "impact-1", focus: { entity: impactFocus, evidence: [impactEvidence] },
    anchorFiles: [{ entity: impactFocus, mapping: null, evidence: [impactEvidence] }], focusConcepts: [],
    incoming: [{ peer: impactPeer, relation: impactRelation, viaPath: "src/focus.py", recordedOrder: 1, evidence: [impactEvidence] }], outgoing: [], semantic: [], history: [],
    testRecommendations: [], risks: [{ kind: "incoming", severity: "medium", summary: "一条入向关系", evidence: [impactEvidence] }],
    actions: [["purpose", "它做什么"], ["callers", "谁调用它"], ["dependencies", "它依赖谁"], ["change", "如果修改它"], ["history", "过去谁改过它"]].map(([kind, label]) => ({ kind, label, summary: `${label}说明`, evidence: [] })),
    stats: { files: 2, concepts: 0, tasks: 0 } }, warnings: [],
};
assert(parseImpactView(impactFixture)?.data.incoming[0]?.peer.path === "src/caller.py", "应解析固定 Impact lanes");
assert(parseAgentNaviView(impactFixture)?.view === "impact", "通用 parser 应分派 Impact");
assert(parseRequestedView({ view: "impact" }) === "impact", "应识别 Impact 请求");
const wrongImpact = structuredClone(impactFixture);
wrongImpact.data.incoming[0]!.relation.sourceId = "file:focus";
assert(parseImpactView(wrongImpact) === undefined, "应拒绝反向或伪造的物理端点");
const overflowImpact = structuredClone(impactFixture);
overflowImpact.data.risks[0]!.evidence = Array.from({ length: 4 }, () => impactEvidence);
assert(parseImpactView(overflowImpact) === undefined, "应拒绝 Impact Evidence 超过三项");
const wrongAnchorImpact = structuredClone(impactFixture);
wrongAnchorImpact.data.incoming[0]!.viaPath = "src/not-visible.py";
assert(parseImpactView(wrongAnchorImpact) === undefined, "lane viaPath 必须绑定可见 anchor");
const collidingImpact = structuredClone(impactFixture);
collidingImpact.data.incoming[0]!.peer.id = collidingImpact.data.focus.entity.id;
assert(parseImpactView(collidingImpact) === undefined, "可见实体 ID 不得跨路径复用");
const evidenceCollision = structuredClone(impactFixture);
evidenceCollision.data.anchorFiles[0]!.entity = structuredClone(evidenceCollision.data.anchorFiles[0]!.entity);
evidenceCollision.data.anchorFiles[0]!.entity.evidence[0]!.summary = "different evidence";
assert(parseImpactView(evidenceCollision) === undefined, "同一实体 ID 的 Evidence 差异必须拒绝");
const testedByImpact: any = structuredClone(impactFixture);
const testedConcept = { id: "concept:focus", kind: "concept", label: "Focus", layer: "L2", source: "semantic", confidence: .8, evidence: [impactEvidence] };
const testedMapping = { id: "edge:tested", sourceId: testedConcept.id, targetId: impactFocus.id, relation: "tested_by", layer: "L2", source: "semantic", confidence: .8, evidence: [impactEvidence] };
testedByImpact.data.focusConcepts = [{ entity: testedConcept, mapping: testedMapping, evidence: [impactEvidence] }];
assert(parseImpactView(testedByImpact)?.data.focusConcepts[0]?.mapping?.relation === "tested_by", "file focus 应接受真实 tested_by ownership");
const ownsImpact = structuredClone(testedByImpact); ownsImpact.data.focusConcepts[0]!.mapping!.relation = "owns";
assert(parseImpactView(ownsImpact) === undefined, "UI 必须拒绝未声明的 owns mapping");
const historyImpact: any = structuredClone(impactFixture);
const impactTaskEvidence = { kind: "task-relation", summary: "recorded", layer: "L3", source: "task-events", confidence: 1 };
const taskEntity = { id: "task:one", kind: "task", label: "Task", layer: "L3", source: "task-events", confidence: 1, evidence: [impactTaskEvidence] };
const taskRelation = { id: "edge:task", sourceId: taskEntity.id, targetId: impactFocus.id, relation: "modified", layer: "L3", source: "task-events", confidence: 1, evidence: [impactTaskEvidence] };
historyImpact.data.history = [{ entity: taskEntity, status: "completed", relation: taskRelation, recordedOrder: 1, evidence: [impactTaskEvidence] }];
assert(parseImpactView(historyImpact)?.data.history.length === 1, "应接受一致的 L3 task-events History");
const badHistoryImpact: any = structuredClone(historyImpact);
badHistoryImpact.data.history[0].entity.evidence = structuredClone(badHistoryImpact.data.history[0].entity.evidence);
badHistoryImpact.data.history[0].entity.evidence[0].summary = "different";
assert(parseImpactView(badHistoryImpact) === undefined, "History entity/relation/wrapper Evidence 必须一致");
const unsafeTour = structuredClone(tourFixture);
unsafeTour.data.tiers[0]!.stops[0]!.evidence[0]!.path = "/private/tour.py";
assert(parseRepositoryTourView(unsafeTour) === undefined, "无有效证据的 Tour stop 应使畸形视图被拒绝");
const duplicateTour = structuredClone(tourFixture);
duplicateTour.data.tiers[2]!.depth = "one-minute";
assert(parseRepositoryTourView(duplicateTour) === undefined, "应拒绝重复或缺失的固定 depth");
const extraTour = structuredClone(tourFixture);
extraTour.data.tiers.push(structuredClone(extraTour.data.tiers[0]!));
assert(parseRepositoryTourView(extraTour) === undefined, "应拒绝多于三档的原始 tiers");
const oversizedTour = structuredClone(tourFixture);
oversizedTour.data.tiers[0]!.stops = Array.from(
  { length: 5 },
  (_, index) => ({ ...structuredClone(tourStop), id: `purpose-${index}` }),
);
assert(parseRepositoryTourView(oversizedTour) === undefined, "应拒绝超过档位上限的 stops");
const wrongKindTour = structuredClone(tourFixture);
wrongKindTour.data.tiers[0]!.stops[0]!.kind = "symbol";
assert(parseRepositoryTourView(wrongKindTour) === undefined, "应拒绝放入错误档位的 stop kind");

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

const architectureEntity = (id: string, label: string, path: string, kind = "concept") => ({
  id, kind, label, path, layer: kind === "file" ? "L1" : "L2", source: "semantic-heuristic", confidence: 0.8,
  evidence: [tourEvidence],
});
const architectureFixture = {
  schemaVersion: "agentnavi.vla.v1",
  view: "architecture",
  project: { id: "fixture", name: "Fixture", kind: "software" },
  sourceState: { status: "ready" },
  data: {
    layout: "cognitive-components",
    summary: { text: "入口连接核心", explanationSource: "derived-presentation", evidence: [tourEvidence] },
    components: [
      { id: "cli", name: "CLI", group: "entry", responsibility: "接收请求", paths: ["src/cli.py"], entity: architectureEntity("cli", "CLI", "src/cli.py"), evidence: [tourEvidence] },
      { id: "core", name: "Core", group: "core", responsibility: "处理请求", paths: ["src/core.py"], entity: architectureEntity("core", "Core", "src/core.py"), evidence: [tourEvidence] },
    ],
    connections: [{ id: "edge:cli-core", sourceId: "cli", targetId: "core", relation: "depends_on", layer: "L2", source: "semantic-heuristic", confidence: 0.8, evidence: [tourEvidence] }],
    entryPoints: [{ path: "src/cli.py", reason: "命令入口", entity: architectureEntity("file:cli", "src/cli.py", "src/cli.py", "file"), evidence: [tourEvidence] }],
    stats: { files: 2, concepts: 2, tasks: 0, documentsRead: 2 },
  },
  warnings: [],
};
assert(parseArchitectureView(architectureFixture)?.data.connections.length === 1, "应解析固定 Architecture 布局");
assert(parseAgentNaviView(architectureFixture)?.view === "architecture", "通用 parser 应分派 Architecture");
assert(parseRequestedView({ view: "architecture" }) === "architecture", "应识别 Architecture 请求");
const missingEndpoint = structuredClone(architectureFixture);
missingEndpoint.data.connections[0]!.targetId = "missing";
assert(parseArchitectureView(missingEndpoint) === undefined, "应拒绝未包含的 Architecture endpoint");
const duplicateComponent = structuredClone(architectureFixture);
duplicateComponent.data.components[1]!.id = "cli";
duplicateComponent.data.components[1]!.entity.id = "cli";
assert(parseArchitectureView(duplicateComponent) === undefined, "应拒绝重复 component id");
for (const mutate of [
  (value: typeof architectureFixture) => { value.data.entryPoints[0]!.entity.kind = "concept"; },
  (value: typeof architectureFixture) => { value.data.entryPoints[0]!.entity.path = "src/other.py"; },
  (value: typeof architectureFixture) => { value.data.entryPoints[0]!.entity.layer = "L2"; },
  (value: typeof architectureFixture) => { value.data.components[0]!.entity.layer = "L1"; },
  (value: typeof architectureFixture) => { value.data.connections[0]!.layer = "L1"; },
  (value: typeof architectureFixture) => { value.data.entryPoints[0]!.reason = ""; },
  (value: typeof architectureFixture) => { value.data.components[0]!.name = ""; },
  (value: typeof architectureFixture) => { value.data.components[0]!.responsibility = ""; },
  (value: typeof architectureFixture) => { value.data.components[0]!.evidence = [tourEvidence, tourEvidence, tourEvidence, tourEvidence]; },
  (value: typeof architectureFixture) => { value.data.summary.text = ""; },
]) {
  const malformed = structuredClone(architectureFixture);
  mutate(malformed);
  assert(parseArchitectureView(malformed) === undefined, "应拒绝 Architecture 的空文本或错配 entry entity");
}

const flowFixture = {
  schemaVersion: "agentnavi.vla.v1",
  view: "flow",
  project: { id: "fixture", name: "Fixture", kind: "software" },
  sourceState: { status: "ready" },
  data: {
    layout: "numbered-task-flow",
    exampleTask: { title: "理解项目", source: "request" },
    steps: Array.from({ length: 5 }, (_, index) => ({
      step: index + 1,
      id: `step-${index + 1}`,
      title: `步骤 ${index + 1}`,
      purpose: `处理阶段 ${index + 1}`,
      input: index === 0 ? "用户请求" : `步骤 ${index} 的结果`,
      output: index === 4 ? "主流程结果" : `交给步骤 ${index + 2}`,
      keyFiles: index === 0 ? [{
        path: "src/cli.py", moduleId: "cli", moduleName: "CLI",
        entity: architectureEntity("file:cli", "src/cli.py", "src/cli.py", "file"),
        relation: { id: "edge:cli-file", sourceId: "cli", targetId: "file:cli", relation: "implemented_by", layer: "L2", source: "semantic-heuristic", confidence: 0.8, evidence: [tourEvidence] },
        evidence: [tourEvidence],
      }] : [],
      why: `文档列为第 ${index + 1} 步`,
      nextStep: index === 4 ? null : `步骤 ${index + 2}`,
      explanationSource: "derived-presentation",
      evidence: [tourEvidence],
    })),
    stats: { files: 2, concepts: 2, tasks: 1, documentsRead: 2 },
  },
  warnings: [],
};
assert(parseFlowView(flowFixture)?.data.steps.length === 5, "应解析连续 5–7 步 Flow");
assert(parseAgentNaviView(flowFixture)?.view === "flow", "通用 parser 应分派 Flow");
assert(parseRequestedView({ view: "flow" }) === "flow", "应识别 Flow 请求");
const discontinuousFlow = structuredClone(flowFixture);
discontinuousFlow.data.steps[2]!.step = 4;
assert(parseFlowView(discontinuousFlow) === undefined, "应拒绝不连续的 Flow step");
const duplicateFlow = structuredClone(flowFixture);
duplicateFlow.data.steps[2]!.id = "step-2";
assert(parseFlowView(duplicateFlow) === undefined, "应拒绝重复 Flow id");
const wrongNextFlow = structuredClone(flowFixture);
wrongNextFlow.data.steps[0]!.nextStep = "步骤 5";
assert(parseFlowView(wrongNextFlow) === undefined, "应拒绝未指向紧邻步骤的 nextStep");
for (const mutate of [
  (value: typeof flowFixture) => { value.data.steps[0]!.keyFiles[0]!.moduleId = ""; },
  (value: typeof flowFixture) => { value.data.steps[0]!.keyFiles[0]!.moduleName = ""; },
  (value: typeof flowFixture) => { value.data.steps[0]!.keyFiles[0]!.entity.kind = "concept"; },
  (value: typeof flowFixture) => { value.data.steps[0]!.keyFiles[0]!.entity.path = "src/other.py"; },
  (value: typeof flowFixture) => { value.data.steps[0]!.keyFiles[0]!.entity.layer = "L2"; },
  (value: typeof flowFixture) => { value.data.steps[0]!.keyFiles[0]!.relation.layer = "L1"; },
  (value: typeof flowFixture) => { value.data.steps[0]!.keyFiles[0]!.relation.sourceId = "other"; },
  (value: typeof flowFixture) => { value.data.steps[0]!.keyFiles[0]!.relation.targetId = "other"; },
  (value: typeof flowFixture) => { value.data.steps[0]!.purpose = ""; },
  (value: typeof flowFixture) => { value.data.steps[0]!.input = ""; },
  (value: typeof flowFixture) => { value.data.steps[0]!.output = ""; },
  (value: typeof flowFixture) => { value.data.steps[0]!.why = ""; },
]) {
  const malformed = structuredClone(flowFixture);
  mutate(malformed);
  assert(parseFlowView(malformed) === undefined, "应拒绝 Flow 的空文本或错配 keyFile provenance");
}
const shortFlow = structuredClone(flowFixture);
shortFlow.data.steps = shortFlow.data.steps.slice(0, 4);
assert(parseFlowView(shortFlow) === undefined, "应拒绝非空但少于 5 步的 Flow");
const emptyFlow = structuredClone(flowFixture);
emptyFlow.data.steps = [];
assert(parseFlowView(emptyFlow)?.data.steps.length === 0, "证据不足时空 Flow 合法");

const requestWithProvenance = structuredClone(flowFixture) as any;
requestWithProvenance.data.exampleTask.entity = architectureEntity("task", "Task", "src/cli.py", "file");
requestWithProvenance.data.exampleTask.evidence = [tourEvidence];
assert(parseFlowView(requestWithProvenance) === undefined, "request task 不得携带 provenance");

const historyFlow = structuredClone(flowFixture) as any;
const taskEvidence = { kind: "task-record", summary: "任务事实", layer: "L3", source: "task-events", confidence: 1 };
historyFlow.data.exampleTask = {
  title: "历史任务",
  source: "task-events",
  entity: { id: "task", kind: "task", label: "历史任务", layer: "L3", source: "task-events", confidence: 1, evidence: [taskEvidence] },
  evidence: [taskEvidence],
};
assert(parseFlowView(historyFlow)?.data.exampleTask?.source === "task-events", "应接受完整 L3 历史任务 provenance");
for (const mutate of [
  (value: any) => { delete value.data.exampleTask.entity; },
  (value: any) => { value.data.exampleTask.entity.kind = "file"; },
  (value: any) => { value.data.exampleTask.entity.layer = "L2"; },
  (value: any) => { value.data.exampleTask.entity.source = "repository"; },
  (value: any) => { value.data.exampleTask.evidence[0].layer = "L2"; },
  (value: any) => { value.data.exampleTask.evidence[0].source = "repository"; },
]) {
  const malformed = structuredClone(historyFlow);
  mutate(malformed);
  assert(parseFlowView(malformed) === undefined, "应拒绝不完整或错层的历史 task provenance");
}

console.log("protocol unit checks passed");
