export const SCHEMA_VERSION = "agentnavi.vla.v1" as const;

export interface ContextFile {
  path: string;
  relation: string;
  language: string;
}

export interface ContextConcept {
  id: string;
  label: string;
  confidence: number;
  source: string;
  files: ContextFile[];
}

export interface ContextWarning {
  code: string;
  message: string;
  evidence: Evidence[];
}

export interface Evidence {
  kind: string;
  summary: string;
  layer: "L1" | "L2" | "L3";
  source: string;
  confidence: number;
  path?: string;
  lineStart?: number;
  lineEnd?: number;
}

export interface ContextView {
  schemaVersion: typeof SCHEMA_VERSION;
  view: "context";
  project: {
    id: string;
    name: string;
    kind: string;
  };
  sourceState: {
    status: "ready" | "partial" | "stale";
  };
  data: {
    stats: {
      files: number;
      concepts: number;
      tasks: number;
    };
    concepts: ContextConcept[];
    files: ContextFile[];
  };
  warnings: ContextWarning[];
}

export interface OverviewStatement {
  summary: string;
  evidence: Evidence[];
}

export interface RepositoryOverviewView {
  schemaVersion: typeof SCHEMA_VERSION;
  view: "repo-overview";
  project: ContextView["project"];
  sourceState: ContextView["sourceState"];
  data: {
    purpose: OverviewStatement;
    need: {
      problem: OverviewStatement;
      solution: OverviewStatement;
    };
    workflow: Array<{
      step: number;
      title: string;
      detail: string;
      evidence: Evidence[];
    }>;
    modules: Array<{
      id: string;
      name: string;
      summary: string;
      paths: string[];
      layer: "L2";
      source: string;
      confidence: number;
      evidence: Evidence[];
    }>;
    readingOrder: Array<{
      position: number;
      path: string;
      reason: string;
      evidence: Evidence[];
    }>;
    stats: ContextView["data"]["stats"] & { documentsRead: number };
  };
  warnings: ContextWarning[];
}

export type AgentNaviView = ContextView | RepositoryOverviewView;

export interface PublicError {
  code: string;
  message: string;
}

const MAX_TEXT = 500;
const MAX_ITEMS = 50;
const PUBLIC_ERROR_MESSAGES: Record<string, string> = {
  PROJECT_REQUIRED: "无法确定项目，请提供明确的 project ID 或 workspace。",
  PROJECT_NOT_FOUND: "找不到指定的项目，请检查 project ID。",
  INVALID_ARGUMENT: "请求参数无效，请检查参数类型和取值。",
  INTERNAL_ERROR: "AgentNavi 处理请求时发生内部错误。",
};
const URI_SCHEME = /^[A-Za-z][A-Za-z0-9+.-]*:/;
const WINDOWS_ABSOLUTE = /(?:^|[^A-Za-z0-9])(?:[A-Za-z]:[\\/]|\\\\[^\\/]+[\\/])/;
const URI_TOKEN = /(?:^|[^A-Za-z0-9+.-])([A-Za-z][A-Za-z0-9+.-]*:[^\s<>"']*)/gi;

function record(value: unknown): Record<string, unknown> | undefined {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return undefined;
  }
  return value as Record<string, unknown>;
}

function text(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value.slice(0, MAX_TEXT) : fallback;
}

function displayText(value: unknown, fallback = ""): string {
  const candidate = text(value, fallback);
  return containsPrivatePath(candidate) ? "[内容含路径，已隐藏]" : candidate;
}

function count(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value)
    ? Math.max(0, Math.trunc(value))
    : 0;
}

function confidence(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value)
    ? Math.min(1, Math.max(0, value))
    : 0;
}

function contextFile(value: unknown): ContextFile | undefined {
  const item = record(value);
  if (!item) return undefined;
  const path = text(item.path);
  if (!isCanonicalRelativePath(path)) return undefined;
  return {
    path,
    relation: displayText(item.relation, "related"),
    language: displayText(item.language, "unknown"),
  };
}

export function isCanonicalRelativePath(path: string): boolean {
  if (
    !path ||
    path !== path.trim() ||
    /[\u0000-\u001f\u007f]/.test(path) ||
    path.includes("\\") ||
    path.startsWith("/") ||
    path.startsWith("~") ||
    URI_SCHEME.test(path)
  ) {
    return false;
  }
  const parts = path.split("/");
  return !parts.some((part) => part === "" || part === "." || part === "..");
}

function containsPosixAbsolute(value: string): boolean {
  for (let index = 0; index < value.length; index += 1) {
    if (value[index] !== "/") continue;
    if (index === 0) return true;
    const previous = value[index - 1];
    if (previous === ":" && value[index + 1] === "/") continue;
    if (previous && /[A-Za-z0-9._~\-/]/.test(previous)) continue;
    return true;
  }
  return false;
}

function containsPrivatePath(value: string): boolean {
  URI_TOKEN.lastIndex = 0;
  for (const match of value.matchAll(URI_TOKEN)) {
    const token = match[1]?.replace(/[.,;!?)}\]，。；！？）】]+$/u, "");
    if (!token) continue;
    const separator = token.indexOf(":");
    const scheme = token.slice(0, separator).toLowerCase();
    if (scheme === "http" || scheme === "https") continue;
    const remainder = token.slice(separator + 1);
    if (/^\/\/file(?:[\\/]|$)/i.test(remainder)) return true;
    if (
      scheme === "file" &&
      (/^(?:[\\/]|~\/|[A-Za-z]:[\\/])/.test(remainder))
    ) return true;
  }
  return (
    value.includes("~/") ||
    WINDOWS_ABSOLUTE.test(value) ||
    containsPosixAbsolute(value)
  );
}

export function safeTaskQuery(value: string): string {
  if (containsPrivatePath(value)) {
    return "[查询含路径，已隐藏]";
  }
  return value.slice(0, MAX_TEXT);
}

function contextConcept(value: unknown): ContextConcept | undefined {
  const item = record(value);
  if (!item) return undefined;
  const id = displayText(item.id);
  const label = displayText(item.label);
  if (!id || !label) return undefined;
  const files = Array.isArray(item.files)
    ? item.files.slice(0, MAX_ITEMS).map(contextFile).filter((entry): entry is ContextFile => Boolean(entry))
    : [];
  return {
    id,
    label,
    confidence: confidence(item.confidence),
    source: displayText(item.source, "unknown"),
    files,
  };
}

function contextWarning(value: unknown): ContextWarning | undefined {
  const item = record(value);
  if (!item) return undefined;
  const code = displayText(item.code);
  const message = displayText(item.message);
  const evidence = Array.isArray(item.evidence)
    ? item.evidence.slice(0, MAX_ITEMS).map(parseEvidence).filter((entry): entry is Evidence => Boolean(entry))
    : [];
  return code && message ? { code, message, evidence } : undefined;
}

function parseEvidence(value: unknown): Evidence | undefined {
  const item = record(value);
  const layer = item?.layer;
  if (!item || (layer !== "L1" && layer !== "L2" && layer !== "L3")) return undefined;
  const kind = displayText(item.kind);
  const summary = displayText(item.summary);
  const source = displayText(item.source);
  if (!kind || !summary || !source) return undefined;
  const pathValue = item.path === undefined ? undefined : text(item.path);
  if (pathValue !== undefined && !isCanonicalRelativePath(pathValue)) return undefined;
  const lineStart = count(item.lineStart);
  const lineEnd = count(item.lineEnd);
  return {
    kind,
    summary,
    layer,
    source,
    confidence: confidence(item.confidence),
    ...(pathValue ? { path: pathValue } : {}),
    ...(lineStart > 0 ? { lineStart } : {}),
    ...(lineEnd > 0 ? { lineEnd } : {}),
  };
}

function evidenceList(value: unknown): Evidence[] {
  return Array.isArray(value)
    ? value.slice(0, MAX_ITEMS).map(parseEvidence).filter((entry): entry is Evidence => Boolean(entry))
    : [];
}

function overviewStatement(value: unknown): OverviewStatement | undefined {
  const item = record(value);
  if (!item) return undefined;
  return { summary: displayText(item.summary), evidence: evidenceList(item.evidence) };
}

function commonEnvelope(value: unknown): {
  envelope: Record<string, unknown>;
  project: ContextView["project"];
  sourceState: ContextView["sourceState"];
  data: Record<string, unknown>;
  warnings: ContextWarning[];
} | undefined {
  const envelope = record(value);
  if (envelope?.schemaVersion !== SCHEMA_VERSION) return undefined;
  const project = record(envelope.project);
  const sourceState = record(envelope.sourceState);
  const data = record(envelope.data);
  const status = sourceState?.status;
  if (!project || !data || (status !== "ready" && status !== "partial" && status !== "stale")) {
    return undefined;
  }
  const id = displayText(project.id);
  const name = displayText(project.name);
  const kind = displayText(project.kind);
  if (!id || !name || !kind) return undefined;
  const warnings = Array.isArray(envelope.warnings)
    ? envelope.warnings.slice(0, MAX_ITEMS).map(contextWarning).filter((entry): entry is ContextWarning => Boolean(entry))
    : [];
  return { envelope, project: { id, name, kind }, sourceState: { status }, data, warnings };
}

export function parseContextView(value: unknown): ContextView | undefined {
  const common = commonEnvelope(value);
  if (!common || common.envelope.view !== "context") return undefined;
  const { project, sourceState, data, warnings } = common;
  const stats = record(data?.stats);
  if (!stats) return undefined;

  const concepts = Array.isArray(data.concepts)
    ? data.concepts.slice(0, MAX_ITEMS).map(contextConcept).filter((entry): entry is ContextConcept => Boolean(entry))
    : [];
  const files = Array.isArray(data.files)
    ? data.files.slice(0, MAX_ITEMS).map(contextFile).filter((entry): entry is ContextFile => Boolean(entry))
    : [];
  return {
    schemaVersion: SCHEMA_VERSION,
    view: "context",
    project,
    sourceState,
    data: {
      stats: {
        files: count(stats.files),
        concepts: count(stats.concepts),
        tasks: count(stats.tasks),
      },
      concepts,
      files,
    },
    warnings,
  };
}

export function parseRepositoryOverviewView(value: unknown): RepositoryOverviewView | undefined {
  const common = commonEnvelope(value);
  if (!common || common.envelope.view !== "repo-overview") return undefined;
  const { project, sourceState, data } = common;
  const warnings = [...common.warnings];
  const purpose = overviewStatement(data.purpose);
  const need = record(data.need);
  const problem = overviewStatement(need?.problem);
  const solution = overviewStatement(need?.solution);
  const stats = record(data.stats);
  if (!purpose || !need || !problem || !solution || !stats) return undefined;

  const rawWorkflow = Array.isArray(data.workflow) ? data.workflow : [];
  const parsedWorkflow = rawWorkflow.flatMap((value) => {
      const item = record(value);
      const rawStep = item?.step;
      const step = typeof rawStep === "number" && Number.isInteger(rawStep) ? rawStep : 0;
      const title = displayText(item?.title);
      if (!item || step < 1 || !title) return [];
      return [{ step, title, detail: displayText(item.detail), evidence: evidenceList(item.evidence) }];
    });
  const workflowIsValid = rawWorkflow.length === 0 || (
    rawWorkflow.length >= 5 &&
    rawWorkflow.length <= 7 &&
    parsedWorkflow.length === rawWorkflow.length &&
    parsedWorkflow.every((item, index) => item.step === index + 1)
  );
  const workflow = workflowIsValid ? parsedWorkflow : [];
  if (!workflowIsValid) {
    warnings.push({
      code: "WORKFLOW_SHAPE_INVALID",
      message: "主流程必须为空或包含连续编号的 5–7 步，当前结果已隐藏。",
      evidence: [],
    });
  }
  const modules = Array.isArray(data.modules)
    ? data.modules.slice(0, 8).flatMap((value) => {
      const item = record(value);
      if (!item || item.layer !== "L2") return [];
      const id = displayText(item.id);
      const name = displayText(item.name);
      if (!id || !name) return [];
      const paths = Array.isArray(item.paths)
        ? item.paths.slice(0, 3).map((path) => text(path)).filter(isCanonicalRelativePath)
        : [];
      return [{
        id,
        name,
        summary: displayText(item.summary),
        paths,
        layer: "L2" as const,
        source: displayText(item.source),
        confidence: confidence(item.confidence),
        evidence: evidenceList(item.evidence),
      }];
    })
    : [];
  const readingOrder = Array.isArray(data.readingOrder)
    ? data.readingOrder.slice(0, 7).flatMap((value) => {
      const item = record(value);
      const position = count(item?.position);
      const path = text(item?.path);
      if (!item || position < 1 || !isCanonicalRelativePath(path)) return [];
      return [{ position, path, reason: displayText(item.reason), evidence: evidenceList(item.evidence) }];
    })
    : [];

  return {
    schemaVersion: SCHEMA_VERSION,
    view: "repo-overview",
    project,
    sourceState,
    data: {
      purpose,
      need: { problem, solution },
      workflow,
      modules,
      readingOrder,
      stats: {
        files: count(stats.files),
        concepts: count(stats.concepts),
        tasks: count(stats.tasks),
        documentsRead: count(stats.documentsRead),
      },
    },
    warnings,
  };
}

export function parseAgentNaviView(value: unknown): AgentNaviView | undefined {
  return parseContextView(value) ?? parseRepositoryOverviewView(value);
}

export function parseTaskQuery(value: unknown): string | undefined {
  const input = record(value);
  const query = text(input?.query).trim();
  return query ? safeTaskQuery(query) : undefined;
}

export function parseRequestedView(value: unknown): AgentNaviView["view"] | undefined {
  const input = record(value);
  return input?.view === "context" || input?.view === "repo-overview" ? input.view : undefined;
}

export function parsePublicError(value: unknown): PublicError | undefined {
  const error = record(value);
  const code = text(error?.code);
  const message = PUBLIC_ERROR_MESSAGES[code];
  return code && message ? { code, message } : undefined;
}
