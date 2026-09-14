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

const MAX_TEXT = 500;
const MAX_ITEMS = 50;
const RELATIVE_PATH = /^(?!\/)(?![A-Za-z]:[\\/])(?!.*(?:^|\/)\.\.(?:\/|$))(?!.*\\)(?!~\/)(?!file:\/\/)[^\0]+$/;

function record(value: unknown): Record<string, unknown> | undefined {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return undefined;
  }
  return value as Record<string, unknown>;
}

function text(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value.slice(0, MAX_TEXT) : fallback;
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
  if (!path || !RELATIVE_PATH.test(path)) return undefined;
  return {
    path,
    relation: text(item.relation, "related"),
    language: text(item.language, "unknown"),
  };
}

function contextConcept(value: unknown): ContextConcept | undefined {
  const item = record(value);
  if (!item) return undefined;
  const id = text(item.id);
  const label = text(item.label);
  if (!id || !label) return undefined;
  const files = Array.isArray(item.files)
    ? item.files.slice(0, MAX_ITEMS).map(contextFile).filter((entry): entry is ContextFile => Boolean(entry))
    : [];
  return {
    id,
    label,
    confidence: confidence(item.confidence),
    source: text(item.source, "unknown"),
    files,
  };
}

function contextWarning(value: unknown): ContextWarning | undefined {
  const item = record(value);
  if (!item) return undefined;
  const code = text(item.code);
  const message = text(item.message);
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
  const projectName = text(project.name);
  const projectKind = text(project.kind);
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
  return query || undefined;
}
