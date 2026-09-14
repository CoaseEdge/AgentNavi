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
        problem: { summary: "重复搜索", evidence: [] },
        solution: { summary: "证据导航", evidence: [] },
      },
      workflow: Array.from({ length: 7 }, (_, index) => ({
        step: index + 1,
        title: `步骤 ${index + 1}`,
        detail: `步骤 ${index + 1}`,
        evidence: [{ kind: "document", summary: "流程", layer: "L1", source: "repository-document", confidence: 1, path: "docs/architecture.md", lineStart: index + 3 }],
      })),
      modules: [{ id: "core", name: "Core", summary: "核心模块", paths: ["src/core.py"], layer: "L2", source: "semantic-heuristic", confidence: 0.8, evidence: [] }],
      readingOrder: [{ position: 1, path: "README.md", reason: "先读目的", evidence: [] }],
      stats: { files: 3, concepts: 1, tasks: 0, documentsRead: 2 },
    },
    warnings: [],
  };
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
  assert.equal(document.querySelector("#connection-label").textContent, "已连接");
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
  assert.match(document.querySelector("#overview-reading-order").textContent, /README\.md/);
  assert.equal(document.querySelector("#connection-label").textContent, "已连接");

  applyToolResult(shell, { structuredContent: fixture() });
  assert.equal(document.querySelector("#repository-view").hidden, true);
  assert.equal(document.querySelector("#overview-workflow").children.length, 0);
  assert.equal(document.querySelector("#context-map").hidden, false);
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
