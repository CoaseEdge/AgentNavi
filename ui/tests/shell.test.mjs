import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { parseHTML } from "linkedom";

import {
  AgentNaviAppLifecycle,
  applyToolInput,
  applyToolResult,
} from "../.test-dist/src/bridge.js";
import { AgentNaviShell } from "../.test-dist/src/shell.js";

const html = await readFile(new URL("../index.html", import.meta.url), "utf8");

function contextNavigationFixture() {
  const evidence = { kind: "mapping", summary: "真实映射 <script>alert(1)</script>", layer: "L2", source: "semantic-heuristic", confidence: 0.8, path: "src/membership.py" };
  const sourceConcept = { id: "concept:membership", kind: "concept", label: "会员 <img onerror=alert(1)>", layer: "L2", source: "semantic-heuristic", confidence: 0.9, evidence: [evidence] };
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
      actions: kinds.map((kind, index) => ({ kind, label: labels[index], summary: `${labels[index]} 说明 <script>alert(1)</script>`, evidence: [evidence] })),
      dependents: [],
      history: [],
    }],
  };
}

function fixture() {
  return {
    schemaVersion: "agentnavi.vla.v1",
    view: "context",
    project: { id: "fixture", name: "Fixture <script>", kind: "software" },
    sourceState: { status: "ready" },
    data: {
      stats: { files: 1, concepts: 1, tasks: 0 },
      concepts: [
        {
          id: "concept:membership",
          label: "会员 <img src=x onerror=alert(1)>",
          confidence: 0.9,
          source: "semantic-heuristic",
          files: [
            { path: "src/membership.py", relation: "implemented_by", language: "python" },
          ],
        },
      ],
      files: [{ path: "src/membership.py", relation: "matched", language: "python" }],
      navigation: contextNavigationFixture(),
    },
    warnings: [{ code: "SOURCE_PARTIAL", message: "索引不完整" }],
  };
}

function overviewFixture() {
  return {
    schemaVersion: "agentnavi.vla.v1",
    view: "repo-overview",
    project: { id: "fixture", name: "Fixture", kind: "software" },
    sourceState: { status: "ready" },
    data: {
      purpose: {
        summary: "帮助协作者理解项目 <script>alert(1)</script>",
        evidence: [{ kind: "document", summary: "说明", layer: "L1", source: "repository-document", confidence: 1, path: "README.md", lineStart: 4 }],
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
      modules: [{ id: "core", name: "Core", summary: "核心模块", paths: ["src/core.py"], layer: "L2", source: "semantic-heuristic", confidence: 0.8, evidence: [] }],
      readingOrder: [{ position: 1, path: "README.md", reason: "先读目的", evidence: [] }],
      stats: { files: 3, concepts: 1, tasks: 0, documentsRead: 2 },
    },
    warnings: [],
  };
}

function tourFixture() {
  const evidence = { kind: "document", summary: "仓库证据", layer: "L1", source: "repository-document", confidence: 1, path: "README.md", lineStart: 3 };
  const stop = (id, kind, title) => ({
    id,
    kind,
    title,
    plainLanguage: `${title}的讲人话说明 <script>alert(1)</script>`,
    technicalExplanation: `${title}的技术说明`,
    evidence: [evidence],
    entity: { id, kind: "concept", label: title, path: "README.md", layer: "L1", source: "repository-document", confidence: 1, evidence: [evidence] },
    relations: [],
  });
  return {
    schemaVersion: "agentnavi.vla.v1",
    view: "repo-tour",
    project: { id: "fixture", name: "Fixture", kind: "software" },
    sourceState: { status: "ready" },
    data: {
      tiers: [
        { depth: "one-minute", label: "1 分钟", stops: [stop("purpose", "purpose", "是什么")] },
        { depth: "five-minutes", label: "5 分钟", stops: [stop("file", "file", "关键文件")] },
        { depth: "source-deep-dive", label: "深入源码", stops: [stop("symbol", "symbol", "PublicModel")] },
      ],
      stats: { files: 3, concepts: 1, tasks: 0, documentsRead: 2 },
    },
    warnings: [],
  };
}

function architectureFixture() {
  const evidence = { kind: "physical-edge", summary: "imports", layer: "L1", source: "ast-import", confidence: 1, path: "src/cli.py" };
  const entity = (id, label, path, kind = "concept") => ({ id, kind, label, path, layer: kind === "file" ? "L1" : "L2", source: "semantic-heuristic", confidence: 0.8, evidence: [evidence] });
  return {
    schemaVersion: "agentnavi.vla.v1",
    view: "architecture",
    project: { id: "fixture", name: "Fixture", kind: "software" },
    sourceState: { status: "ready" },
    data: {
      layout: "cognitive-components",
      summary: { text: "入口 <script>alert(1)</script> 连接核心", explanationSource: "derived-presentation", evidence: [evidence] },
      components: [
        { id: "cli", name: "CLI", group: "entry", responsibility: "接收请求", paths: ["src/cli.py"], entity: entity("cli", "CLI", "src/cli.py"), evidence: [evidence] },
        { id: "core", name: "Core", group: "core", responsibility: "处理请求", paths: ["src/core.py"], entity: entity("core", "Core", "src/core.py"), evidence: [evidence] },
      ],
      connections: [{ id: "edge", sourceId: "cli", targetId: "core", relation: "depends_on", layer: "L2", source: "semantic-heuristic", confidence: 0.8, evidence: [evidence] }],
      entryPoints: [{ path: "src/cli.py", reason: "入口", entity: entity("file:cli", "src/cli.py", "src/cli.py", "file"), evidence: [evidence] }],
      stats: { files: 2, concepts: 2, tasks: 0, documentsRead: 2 },
    },
    warnings: [],
  };
}

function flowFixture() {
  const evidence = { kind: "document", summary: "执行流程", layer: "L1", source: "repository-document", confidence: 1, path: "docs/architecture.md" };
  return {
    schemaVersion: "agentnavi.vla.v1",
    view: "flow",
    project: { id: "fixture", name: "Fixture", kind: "software" },
    sourceState: { status: "ready" },
    data: {
      layout: "numbered-task-flow",
      exampleTask: { title: "理解 <script>alert(1)</script> 项目", source: "request" },
      steps: Array.from({ length: 5 }, (_, index) => ({
        step: index + 1,
        id: `step-${index + 1}`,
        title: `步骤 ${index + 1}`,
        purpose: `阶段 ${index + 1}`,
        input: index === 0 ? "用户请求" : `步骤 ${index} 的结果`,
        output: index === 4 ? "主流程结果" : `交给步骤 ${index + 2}`,
        keyFiles: [],
        why: `项目文档列为第 ${index + 1} 步`,
        nextStep: index === 4 ? null : `步骤 ${index + 2}`,
        explanationSource: "derived-presentation",
        evidence: [evidence],
      })),
      stats: { files: 2, concepts: 2, tasks: 1, documentsRead: 2 },
    },
    warnings: [],
  };
}

function impactFixture() {
  const evidence = { kind: "physical-relation", summary: "imports", layer: "L1", source: "extractor", confidence: 1, path: "src/caller.py" };
  const focus = { id: "file:focus", kind: "file", label: "focus.py <script>alert(1)</script>", path: "src/focus.py", layer: "L1", source: "repository", confidence: 1, evidence: [evidence] };
  const caller = { id: "file:caller", kind: "file", label: "caller.py", path: "src/caller.py", layer: "L1", source: "repository", confidence: 1, evidence: [evidence] };
  const relation = { id: "edge:imports", sourceId: caller.id, targetId: focus.id, relation: "imports", layer: "L1", source: "extractor", confidence: 1, evidence: [evidence] };
  const concept = { id: "concept:focus", kind: "concept", label: "Focus concept", layer: "L2", source: "semantic", confidence: .8, evidence: [evidence] };
  const mapping = { id: "edge:tested", sourceId: concept.id, targetId: focus.id, relation: "tested_by", layer: "L2", source: "semantic", confidence: .8, evidence: [evidence] };
  return { schemaVersion: "agentnavi.vla.v1", view: "impact", project: { id: "fixture", name: "Fixture", kind: "software" }, sourceState: { status: "ready" },
    data: { layout: "incoming-focus-outgoing", revision: "impact-1", focus: { entity: focus, evidence: [evidence] }, anchorFiles: [{ entity: focus, mapping: null, evidence: [evidence] }], focusConcepts: [{ entity: concept, mapping, evidence: [evidence] }], incoming: [{ peer: caller, relation, viaPath: focus.path, recordedOrder: 1, evidence: [evidence] }], outgoing: [], semantic: [], history: [], testRecommendations: [],
      risks: [{ kind: "incoming", severity: "medium", summary: "调用方可能受影响", evidence: [evidence] }], actions: [["purpose", "它做什么"], ["callers", "谁调用它"], ["dependencies", "它依赖谁"], ["change", "如果修改它"], ["history", "过去谁改过它"]].map(([kind, label]) => ({ kind, label, summary: `${label}说明`, evidence: [evidence] })), stats: { files: 2, concepts: 0, tasks: 0 } }, warnings: [] };
}

function historyFixture() {
  const taskEvidence = { kind: "task-record", summary: "任务 task-1：任务记录", layer: "L3", source: "task-events", confidence: 1 };
  const relationEvidence = { kind: "task-relation", summary: "任务 task-1 的关系 edge:1 记录 modified", layer: "L3", source: "task-events", confidence: 1, path: "src/a.py" };
  const task = { id: "task:1", kind: "task", label: "修改 A <script>alert(1)</script>", layer: "L3", source: "task-events", confidence: 1, evidence: [taskEvidence] };
  const file = { id: "file:a", kind: "file", label: "a.py", path: "src/a.py", layer: "L1", source: "repository", confidence: 1, evidence: [{ kind: "repository-file", summary: "文件", layer: "L1", source: "repository", confidence: 1, path: "src/a.py" }] };
  const relation = { id: "edge:1", sourceId: task.id, targetId: file.id, relation: "modified", layer: "L3", source: "task-events", confidence: 1, evidence: [relationEvidence] };
  const timeline = { entity: task, taskId: "task-1", status: "completed", summary: "完成修改", createdAt: "2026-09-15T09:00:00Z", updatedAt: "2026-09-15T10:00:00Z", closedAt: "2026-09-15T10:00:00Z", sortTime: "2026-09-15T10:00:00Z", relations: [{ entity: file, relation, recordedOrder: 1, evidence: [relationEvidence] }], evidence: [taskEvidence] };
  const disclaimer = "按 L3 任务关系聚合展示，不是原始工具调用的无损还原，也不据此推断因果。";
  return { schemaVersion: "agentnavi.vla.v1", view: "history", project: { id: "fixture", name: "Fixture", kind: "software" }, sourceState: { status: "ready" }, data: { layout: "task-timeline-story", revision: "history-1", selectedMode: "timeline", disclaimer, timeline: [timeline], story: [{ id: "story-1", title: "修改 A <script>alert(1)</script>", summary: "完成修改", sortTime: timeline.sortTime, task, groups: [{ relation: "modified", paths: ["src/a.py"], concepts: [], evidence: [relationEvidence] }], explanationSource: "l3-aggregation", disclaimer, evidence: [taskEvidence] }], taskDetail: timeline, stats: { files: 1, tasks: 1, displayedTasks: 1, displayedRelations: 1 } }, warnings: [] };
}

function setup() {
  const { document } = parseHTML(html);
  globalThis.document = document;
  return { document, shell: new AgentNaviShell() };
}

test("bridge renders a successful result as text and preserves concept-file evidence", () => {
  const { document, shell } = setup();
  applyToolInput(shell, { query: "会员入口" });
  applyToolResult(shell, { structuredContent: fixture() });

  assert.equal(document.querySelector("#context-map").hidden, false);
  assert.equal(document.querySelector("#task-query").textContent, "会员入口");
  assert.equal(
    document.querySelector(".concept-node strong").textContent,
    "会员 <img src=x onerror=alert(1)>",
  );
  assert.equal(document.querySelector(".concept-node img"), null);
  assert.equal(document.querySelector(".concept-file-links .relation-label").textContent, "implemented_by");
  assert.equal(document.querySelector(".concept-file-links code").textContent, "src/membership.py");
  assert.match(document.querySelector(".file-node .node-meta").textContent, /^候选 · matched/);
  const explain = document.querySelector(".file-explain-trigger");
  assert.equal(explain.getAttribute("aria-expanded"), "false");
  explain.click();
  assert.equal(explain.getAttribute("aria-expanded"), "true");
  assert.match(document.querySelector(".file-why").textContent, /Why/);
  assert.equal(
    document.querySelector(".context-chains li").textContent,
    "会员 <img onerror=alert(1)> —implemented_by→ src/membership.py",
  );
  assert.equal(document.querySelector(".file-explanation script"), null);
  const impact = [...document.querySelectorAll(".context-actions button")].find(
    (button) => button.textContent === "如果改它",
  );
  impact.click();
  assert.equal(impact.getAttribute("aria-pressed"), "true");
  assert.equal(document.querySelector(".context-action-summary").textContent, "[内容含路径，已隐藏]");
  assert.match(document.querySelector(".context-next-step").textContent, /Next Step/);
  assert.equal(document.querySelector("#connection-label").textContent, "已连接");
});

test("context chain renders outgoing and incoming traversal without repeating the neighbor", () => {
  for (const direction of ["outgoing", "incoming"]) {
    const payload = fixture();
    const chain = payload.data.navigation.readingOrder[0].chains[0];
    chain.sourceConcept.label = "会员";
    chain.relatedConcept = {
      ...structuredClone(chain.sourceConcept), id: "concept:payment", label: "支付",
    };
    chain.conceptRelation = {
      ...structuredClone(chain.fileRelation),
      id: "edge:depends",
      sourceId: direction === "outgoing" ? chain.sourceConcept.id : chain.relatedConcept.id,
      targetId: direction === "outgoing" ? chain.relatedConcept.id : chain.sourceConcept.id,
      relation: "depends_on",
    };
    chain.fileRelation.sourceId = chain.relatedConcept.id;
    const { document, shell } = setup();
    applyToolResult(shell, { structuredContent: payload });
    document.querySelector(".file-explain-trigger").click();
    assert.equal(
      document.querySelector(".context-chains li").textContent,
      direction === "outgoing"
        ? "会员 —depends_on→ 支付 —implemented_by→ src/membership.py"
        : "会员 ←depends_on— 支付 —implemented_by→ src/membership.py",
    );
  }
});

test("renderer registry shows repository overview with safe DOM and evidence", () => {
  const { document, shell } = setup();
  applyToolInput(shell, { view: "repo-overview" });
  assert.equal(document.querySelector("#view-title").textContent, "Repository Overview");
  assert.equal(document.querySelector("#context-map").hidden, true);
  applyToolResult(shell, { structuredContent: overviewFixture() });

  assert.equal(document.querySelector("#repository-view").hidden, false);
  assert.equal(document.querySelector("#context-map").hidden, true);
  assert.equal(document.querySelector("#view-title").textContent, "Repository Overview");
  assert.equal(document.querySelector("#overview-workflow").children.length, 7);
  assert.equal(document.querySelector("#overview-purpose script"), null);
  assert.equal(document.querySelector("#overview-purpose").textContent, "[内容含路径，已隐藏]");
  assert.equal(document.querySelector("#purpose-evidence").textContent, "证据 · README.md:4");
  assert.equal(document.querySelector("#problem-evidence").textContent, "证据 · README.md:8");
  assert.equal(document.querySelector("#solution-evidence").textContent, "证据 · README.md:12");
  assert.match(document.querySelector("#overview-workflow li strong").textContent, /01 · 动作 1/);
  assert.equal(document.querySelector("#overview-workflow li p").textContent, "动作 1 的说明");
  assert.match(document.querySelector("#overview-reading-order").textContent, /README\.md/);
  assert.equal(document.querySelector("#connection-label").textContent, "已连接");

  applyToolResult(shell, { structuredContent: fixture() });
  assert.equal(document.querySelector("#repository-view").hidden, true);
  assert.equal(document.querySelector("#overview-workflow").children.length, 0);
  assert.equal(document.querySelector("#problem-evidence").textContent, "");
  assert.equal(document.querySelector("#solution-evidence").textContent, "");
  assert.equal(document.querySelector("#context-map").hidden, false);
});

test("repository tour switches depth locally with accessible controls and safe details", () => {
  const { document, shell } = setup();
  applyToolInput(shell, { view: "repo-tour" });
  applyToolResult(shell, { structuredContent: tourFixture() });

  assert.equal(document.querySelector("#repository-tour").hidden, false);
  assert.equal(document.querySelector("#repository-view").hidden, true);
  assert.equal(document.querySelector("#view-title").textContent, "Repository Tour");
  assert.equal(document.querySelector("#tour-depth-one-minute").getAttribute("aria-pressed"), "true");
  assert.equal(document.querySelector("#tour-stops h3").textContent, "是什么");
  assert.equal(document.querySelector("#tour-stops script"), null);
  assert.equal(document.querySelector("#tour-stops details summary").textContent, "技术说明与源码证据");

  document.querySelector("#tour-depth-source-deep-dive").click();
  assert.equal(document.querySelector("#tour-depth-one-minute").getAttribute("aria-pressed"), "false");
  assert.equal(document.querySelector("#tour-depth-source-deep-dive").getAttribute("aria-pressed"), "true");
  assert.equal(document.querySelector("#tour-stops h3").textContent, "PublicModel");

  applyToolResult(shell, { structuredContent: fixture() });
  assert.equal(document.querySelector("#repository-tour").hidden, true);
  assert.equal(document.querySelector("#tour-stops").children.length, 0);
});

test("architecture uses grouped cards and only included real connections", () => {
  const { document, shell } = setup();
  applyToolInput(shell, { view: "architecture" });
  applyToolResult(shell, { structuredContent: architectureFixture() });

  assert.equal(document.querySelector("#architecture-view").hidden, false);
  assert.equal(document.querySelector("#architecture-entry").children.length, 1);
  assert.equal(document.querySelector("#architecture-core").children.length, 1);
  assert.equal(document.querySelector("#architecture-connections strong").textContent, "CLI → Core");
  assert.equal(document.querySelector("#architecture-summary script"), null);
  assert.equal(document.querySelector("#architecture-summary").textContent, "[内容含路径，已隐藏]");

  applyToolResult(shell, { structuredContent: flowFixture() });
  assert.equal(document.querySelector("#architecture-view").hidden, true);
  assert.equal(document.querySelector("#architecture-connections").children.length, 0);
});

test("flow renders a local five-step expandable timeline and clears across views", () => {
  const { document, shell } = setup();
  applyToolInput(shell, { view: "flow" });
  applyToolResult(shell, { structuredContent: flowFixture() });

  assert.equal(document.querySelector("#flow-view").hidden, false);
  assert.equal(document.querySelectorAll("#flow-steps > li").length, 5);
  assert.equal(document.querySelector("#flow-steps details summary strong").textContent, "步骤 1");
  assert.equal(document.querySelector("#flow-steps details dl dt").textContent, "作用");
  assert.match(document.querySelector("#flow-steps details dl").textContent, /证据/);
  assert.equal(document.querySelector("#flow-task script"), null);
  assert.equal(document.querySelector("#flow-task").textContent, "[内容含路径，已隐藏]");

  applyToolResult(shell, { structuredContent: fixture() });
  assert.equal(document.querySelector("#flow-view").hidden, true);
  assert.equal(document.querySelector("#flow-steps").children.length, 0);
});

test("impact renders fixed lanes, local actions, safe DOM, and clears across views", () => {
  const { document, shell } = setup();
  applyToolInput(shell, { view: "impact", query: "src/focus.py" });
  assert.equal(document.querySelector("#view-title").textContent, "Impact");
  applyToolResult(shell, { structuredContent: impactFixture() });
  assert.equal(document.querySelector("#impact-view").hidden, false);
  assert.equal(document.querySelector("#impact-incoming code").textContent, "src/caller.py");
  assert.equal(document.querySelector("#impact-focus-label").textContent, "[内容含路径，已隐藏]");
  assert.equal(document.querySelector("#impact-focus script"), null);
  assert.equal(document.querySelectorAll("#impact-actions details").length, 5);
  assert.equal(document.querySelector("#impact-anchors-title").textContent, "锚点文件");
  assert.equal(document.querySelector("#impact-concepts-title").textContent, "关联概念");
  assert.equal(document.querySelector("#impact-concepts span").textContent, "tested_by");
  assert.ok(document.querySelector("#impact-risks .impact-evidence"));
  assert.ok(document.querySelector("#impact-actions .impact-evidence"));
  applyToolResult(shell, { structuredContent: flowFixture() });
  assert.equal(document.querySelector("#impact-view").hidden, true);
  assert.equal(document.querySelector("#impact-incoming").children.length, 0);
});

test("history renders safe local Timeline and Story modes and clears across views", () => {
  const { document, shell } = setup();
  applyToolInput(shell, { view: "history" });
  applyToolResult(shell, { structuredContent: historyFixture() });
  assert.equal(document.querySelector("#history-view").hidden, false);
  assert.equal(document.querySelectorAll("#history-timeline > li").length, 1);
  assert.equal(document.querySelector("#history-timeline script"), null);
  assert.match(document.querySelector("#history-timeline").textContent, /修改 A/);
  document.querySelector("#history-mode-story").click();
  assert.equal(document.querySelector("#history-mode-story").getAttribute("aria-pressed"), "true");
  assert.equal(document.querySelector("#history-story-panel").hidden, false);
  assert.equal(document.querySelector("#history-story script"), null);
  assert.match(document.querySelector("#history-disclaimer").textContent, /不是原始工具调用的无损还原/);
  applyToolResult(shell, { structuredContent: flowFixture() });
  assert.equal(document.querySelector("#history-view").hidden, true);
  assert.equal(document.querySelector("#history-timeline").children.length, 0);
});

test("new input clears stale data before an error and keeps status perceivable", () => {
  const { document, shell } = setup();
  applyToolInput(shell, { query: "first" });
  applyToolResult(shell, { structuredContent: fixture() });
  assert.equal(document.querySelectorAll(".concept-node").length, 1);

  applyToolInput(shell, { query: "/private/secret.py" });
  assert.equal(document.querySelector("#context-map").hidden, true);
  assert.equal(document.querySelectorAll(".concept-node").length, 0);
  assert.equal(document.querySelector("#warning-panel").hidden, true);
  assert.equal(document.querySelector("#task-query").textContent, "[查询含路径，已隐藏]");
  assert.equal(document.querySelector("#connection-label").textContent, "正在查询");

  applyToolResult(shell, {
    isError: true,
    structuredContent: { code: "INVALID_ARGUMENT", message: "请求参数无效" },
  });
  const errorPanel = document.querySelector("#error-panel");
  assert.equal(errorPanel.hidden, false);
  assert.equal(errorPanel.getAttribute("role"), "alert");
  assert.equal(errorPanel.getAttribute("aria-live"), "assertive");
  assert.equal(
    document.querySelector("#error-message").textContent,
    "INVALID_ARGUMENT · 请求参数无效，请检查参数类型和取值。",
  );
  assert.equal(document.querySelector("#context-map").hidden, true);
  assert.equal(document.querySelector("#warning-panel").hidden, true);
  assert.equal(document.querySelector("#connection-state").getAttribute("aria-live"), "polite");
  assert.equal(document.querySelector("#connection-label").textContent, "查询失败");
});

test("invalid and opaque error results never leave the previous map visible", () => {
  for (const result of [
    { structuredContent: { view: "future" } },
    { isError: true, structuredContent: { detail: "/private/secret.py" } },
  ]) {
    const { document, shell } = setup();
    applyToolResult(shell, { structuredContent: fixture() });
    applyToolResult(shell, result);
    assert.equal(document.querySelector("#context-map").hidden, true);
    assert.equal(document.querySelector("#error-panel").hidden, false);
    assert.doesNotMatch(document.querySelector("#error-message").textContent, /private|secret/);
  }
});

test("connect completion never overwrites tool activity received during handshake", () => {
  const first = setup();
  const renderedLifecycle = new AgentNaviAppLifecycle(first.shell);
  renderedLifecycle.handleToolResult({ structuredContent: fixture() });
  renderedLifecycle.handleConnected("dark");
  assert.equal(first.document.querySelector("#connection-label").textContent, "已连接");
  assert.equal(first.document.documentElement.dataset.theme, "dark");
  renderedLifecycle.handleConnectionFailure();
  assert.equal(first.document.querySelector("#context-map").hidden, false);
  assert.equal(first.document.querySelector("#connection-label").textContent, "Host 已断开");
  assert.equal(first.document.querySelector("#connection-state").classList.contains("is-connected"), false);

  const second = setup();
  const pendingLifecycle = new AgentNaviAppLifecycle(second.shell);
  pendingLifecycle.handleToolInput({ query: "new task" });
  pendingLifecycle.handleConnected("light");
  assert.equal(second.document.querySelector("#connection-label").textContent, "正在查询");
  pendingLifecycle.handleConnectionFailure();
  assert.equal(second.document.querySelector("#context-map").hidden, true);
  assert.equal(second.document.querySelector("#error-panel").hidden, false);
  assert.equal(second.document.querySelector("#connection-label").textContent, "查询失败");

  const third = setup();
  const idleLifecycle = new AgentNaviAppLifecycle(third.shell);
  idleLifecycle.handleConnected("light");
  assert.equal(third.document.querySelector("#connection-label").textContent, "等待结果");
});

test("every displayed non-path string hides local path tokens recursively", () => {
  const { document, shell } = setup();
  const malicious = fixture();
  malicious.project.name = "repo at /private/project";
  malicious.project.kind = String.raw`C:\Users\alice\kind`;
  malicious.data.concepts[0].label = "file:///Users/alice/concept";
  malicious.data.concepts[0].source = String.raw`\\server\share\source`;
  malicious.data.concepts[0].files[0].relation = "from /private/relation";
  malicious.data.concepts[0].files[0].language = "~/private-language";
  malicious.data.files[0].relation = "from /private/candidate";
  malicious.data.files[0].language = String.raw`C:\private\language`;
  malicious.warnings[0].code = "file:///private/code";
  malicious.warnings[0].message = String.raw`\\server\share\warning`;
  malicious.warnings.push({
    code: "API_DOC",
    message: "参考 https://example.com/api/users",
  });

  applyToolResult(shell, { structuredContent: malicious });
  const visible = document.body.textContent;
  for (const token of ["/private", "C:\\", "\\\\server", "file://", "~/"]) {
    assert.doesNotMatch(visible, new RegExp(token.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  }
  assert.match(visible, /\[内容含路径，已隐藏\]/);
  assert.match(visible, /https:\/\/example\.com\/api\/users/);
});

test("overview hides invalid why evidence and reports malformed workflow", () => {
  const { document, shell } = setup();
  const malicious = overviewFixture();
  malicious.data.need.problem.evidence[0].path = "cursor://file/private/problem.md";
  malicious.data.need.solution.evidence[0].path = "custom-editor://file/private/solution.md";
  malicious.data.workflow[4].step = 4;

  applyToolResult(shell, { structuredContent: malicious });

  assert.equal(document.querySelector("#problem-evidence").textContent, "");
  assert.equal(document.querySelector("#solution-evidence").textContent, "");
  assert.equal(document.querySelector("#overview-workflow").children.length, 0);
  assert.match(document.querySelector("#warning-list").textContent, /WORKFLOW_SHAPE_INVALID/);
});
