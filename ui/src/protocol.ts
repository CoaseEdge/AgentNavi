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
  return (
    value.toLowerCase().includes("file://") ||
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
  const id = text(item.id);
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
  return code && message ? { code, message } : undefined;
}

export function parseContextView(value: unknown): ContextView | undefined {
  const envelope = record(value);
  if (envelope?.schemaVersion !== SCHEMA_VERSION || envelope.view !== "context") {
    return undefined;
  }
  const project = record(envelope.project);
  const sourceState = record(envelope.sourceState);
  const data = record(envelope.data);
  const stats = record(data?.stats);
  const status = sourceState?.status;
  if (
    !project ||
    !data ||
    !stats ||
    (status !== "ready" && status !== "partial" && status !== "stale")
  ) {
    return undefined;
  }
  const projectId = text(project.id);
  const projectName = displayText(project.name);
  const projectKind = displayText(project.kind);
  if (!projectId || !projectName || !projectKind) return undefined;

  const concepts = Array.isArray(data.concepts)
    ? data.concepts.slice(0, MAX_ITEMS).map(contextConcept).filter((entry): entry is ContextConcept => Boolean(entry))
    : [];
  const files = Array.isArray(data.files)
    ? data.files.slice(0, MAX_ITEMS).map(contextFile).filter((entry): entry is ContextFile => Boolean(entry))
    : [];
  const warnings = Array.isArray(envelope.warnings)
    ? envelope.warnings.slice(0, MAX_ITEMS).map(contextWarning).filter((entry): entry is ContextWarning => Boolean(entry))
    : [];

  return {
    schemaVersion: SCHEMA_VERSION,
    view: "context",
    project: { id: projectId, name: projectName, kind: projectKind },
    sourceState: { status },
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

export function parseTaskQuery(value: unknown): string | undefined {
  const input = record(value);
  const query = text(input?.query).trim();
  return query ? safeTaskQuery(query) : undefined;
}

export function parsePublicError(value: unknown): PublicError | undefined {
  const error = record(value);
  const code = text(error?.code);
  const message = PUBLIC_ERROR_MESSAGES[code];
  return code && message ? { code, message } : undefined;
}
