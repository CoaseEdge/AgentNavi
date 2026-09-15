export const SCHEMA_VERSION = "agentnavi.vla.v1" as const;
export const MAX_CONTEXT_WARNINGS = 10;

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

export interface ContextNavigationChain {
  sourceConcept: TourStop["entity"];
  conceptRelation: TourStop["relations"][number] | null;
  relatedConcept: TourStop["entity"] | null;
  fileRelation: TourStop["relations"][number];
  file: TourStop["entity"];
  evidence: Evidence[];
}

export interface ContextReadingItem {
  position: number;
  path: string;
  language: string;
  why: string;
  evidence: Evidence[];
  nextStep: { path: string; reason: string } | null;
  chains: ContextNavigationChain[];
  actions: Array<{
    kind: "purpose" | "relevance" | "dependents" | "history" | "impact";
    label: string;
    summary: string;
    evidence: Evidence[];
  }>;
  dependents: Array<{ path: string; relation: string }>;
  history: Array<{
    id: string;
    title: string;
    status: string;
    createdAt: string;
    relation: string;
  }>;
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
    navigation: {
      revision: string;
      readingOrder: ContextReadingItem[];
    };
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

export type TourDepth = "one-minute" | "five-minutes" | "source-deep-dive";

export interface TourStop {
  id: string;
  kind: string;
  title: string;
  plainLanguage: string;
  technicalExplanation: string;
  evidence: Evidence[];
  entity: {
    id: string;
    kind: string;
    label: string;
    path?: string;
    layer: "L1" | "L2" | "L3";
    source: string;
    confidence: number;
    evidence: Evidence[];
  };
  relations: Array<{
    id: string;
    sourceId: string;
    targetId: string;
    relation: string;
    layer: "L1" | "L2" | "L3";
    source: string;
    confidence: number;
    evidence: Evidence[];
  }>;
}

export interface RepositoryTourView {
  schemaVersion: typeof SCHEMA_VERSION;
  view: "repo-tour";
  project: ContextView["project"];
  sourceState: ContextView["sourceState"];
  data: {
    tiers: Array<{ depth: TourDepth; label: string; stops: TourStop[] }>;
    stats: ContextView["data"]["stats"] & { documentsRead: number };
  };
  warnings: ContextWarning[];
}

export interface ArchitectureComponent {
  id: string;
  name: string;
  group: "entry" | "core" | "support";
  responsibility: string;
  paths: string[];
  entity: TourStop["entity"];
  evidence: Evidence[];
}

export interface ArchitectureView {
  schemaVersion: typeof SCHEMA_VERSION;
  view: "architecture";
  project: ContextView["project"];
  sourceState: ContextView["sourceState"];
  data: {
    layout: "cognitive-components";
    summary: { text: string; explanationSource: "derived-presentation"; evidence: Evidence[] };
    components: ArchitectureComponent[];
    connections: TourStop["relations"];
    entryPoints: Array<{
      path: string;
      reason: string;
      entity: TourStop["entity"];
      evidence: Evidence[];
    }>;
    stats: ContextView["data"]["stats"] & { documentsRead: number };
  };
  warnings: ContextWarning[];
}

export interface FlowStep {
  step: number;
  id: string;
  title: string;
  purpose: string;
  input: string;
  output: string;
  keyFiles: Array<{
    path: string;
    moduleId: string;
    moduleName: string;
    entity: TourStop["entity"];
    relation: TourStop["relations"][number];
    evidence: Evidence[];
  }>;
  why: string;
  nextStep: string | null;
  explanationSource: "derived-presentation";
  evidence: Evidence[];
}

export interface FlowView {
  schemaVersion: typeof SCHEMA_VERSION;
  view: "flow";
  project: ContextView["project"];
  sourceState: ContextView["sourceState"];
  data: {
    layout: "numbered-task-flow";
    exampleTask?: {
      title: string;
      source: "request" | "request-redacted";
    } | {
      title: string;
      source: "task-events";
      entity: TourStop["entity"];
      evidence: Evidence[];
    };
    steps: FlowStep[];
    stats: ContextView["data"]["stats"] & { documentsRead: number };
  };
  warnings: ContextWarning[];
}

export interface ImpactView {
  schemaVersion: typeof SCHEMA_VERSION;
  view: "impact";
  project: ContextView["project"];
  sourceState: ContextView["sourceState"];
  data: {
    layout: "incoming-focus-outgoing";
    revision: string;
    focus: { entity: TourStop["entity"]; evidence: Evidence[] };
    anchorFiles: Array<{ entity: TourStop["entity"]; mapping: TourStop["relations"][number] | null; evidence: Evidence[] }>;
    focusConcepts: Array<{ entity: TourStop["entity"]; mapping: TourStop["relations"][number] | null; evidence: Evidence[] }>;
    incoming: ImpactLane[];
    outgoing: ImpactLane[];
    semantic: Array<{ direction: "incoming" | "outgoing"; focusConceptId: string; peer: TourStop["entity"]; relation: TourStop["relations"][number]; evidence: Evidence[] }>;
    history: Array<{ entity: TourStop["entity"]; status: string; relation: TourStop["relations"][number]; recordedOrder: number; evidence: Evidence[] }>;
    testRecommendations: Array<{ basis: "physical-tests" | "semantic-tested-by"; path: string; reason: string; sourceConcept: TourStop["entity"] | null; entity: TourStop["entity"]; relation: TourStop["relations"][number]; evidence: Evidence[] }>;
    risks: Array<{ kind: string; severity: "low" | "medium" | "high"; summary: string; evidence: Evidence[] }>;
    actions: Array<{ kind: "purpose" | "callers" | "dependencies" | "change" | "history"; label: string; summary: string; evidence: Evidence[] }>;
    stats: ContextView["data"]["stats"];
  };
  warnings: ContextWarning[];
}

export interface ImpactLane {
  peer: TourStop["entity"];
  relation: TourStop["relations"][number];
  viaPath: string;
  recordedOrder: number;
  evidence: Evidence[];
}

export interface HistoryRelationItem {
  entity: TourStop["entity"];
  relation: TourStop["relations"][number];
  recordedOrder: number;
  evidence: Evidence[];
}

export interface HistoryTimelineItem {
  entity: TourStop["entity"];
  taskId: string;
  status: string;
  summary: string;
  createdAt: string;
  updatedAt: string;
  closedAt: string | null;
  sortTime: string;
  relations: HistoryRelationItem[];
  evidence: Evidence[];
}

export interface HistoryView {
  schemaVersion: typeof SCHEMA_VERSION;
  view: "history";
  project: ContextView["project"];
  sourceState: ContextView["sourceState"];
  data: {
    layout: "task-timeline-story";
    revision: string;
    selectedMode: "timeline" | "story";
    disclaimer: string;
    timeline: HistoryTimelineItem[];
    story: Array<{
      id: string; title: string; summary: string; sortTime: string;
      task: TourStop["entity"];
      groups: Array<{ relation: "read" | "modified" | "tested" | "searched" | "affects"; paths: string[]; concepts: string[]; evidence: Evidence[] }>;
      explanationSource: "l3-aggregation";
      disclaimer: string;
      evidence: Evidence[];
    }>;
    taskDetail: HistoryTimelineItem | null;
    stats: { files: number; tasks: number; displayedTasks: number; displayedRelations: number };
  };
  warnings: ContextWarning[];
}

export type AgentNaviView =
  | ContextView
  | RepositoryOverviewView
  | RepositoryTourView
  | ArchitectureView
  | FlowView
  | ImpactView
  | HistoryView;

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

const CONTEXT_ACTIONS = [
  ["purpose", "它做什么"],
  ["relevance", "为什么相关"],
  ["dependents", "谁依赖它"],
  ["history", "过去谁改过"],
  ["impact", "如果改它"],
] as const;

function strictEvidence(value: unknown, limit = 3): Evidence[] | undefined {
  const raw = Array.isArray(value) ? value : [];
  if (raw.length === 0 || raw.length > limit) return undefined;
  const parsed = raw.map(parseEvidence).filter((entry): entry is Evidence => Boolean(entry));
  return parsed.length === raw.length ? parsed : undefined;
}

function contextChain(value: unknown, path: string): ContextNavigationChain | undefined {
  const item = record(value);
  if (!item) return undefined;
  const sourceConcept = tourEntity(item.sourceConcept);
  const parsedRelated = item.relatedConcept === null ? null : tourEntity(item.relatedConcept);
  const parsedConceptRelation = item.conceptRelation === null ? null : tourRelation(item.conceptRelation);
  if (parsedRelated === undefined || parsedConceptRelation === undefined) return undefined;
  const relatedConcept = parsedRelated;
  const conceptRelation = parsedConceptRelation;
  const fileRelation = tourRelation(item.fileRelation);
  const file = tourEntity(item.file);
  const evidence = strictEvidence(item.evidence);
  if (
    !sourceConcept || !completeEntity(sourceConcept) || sourceConcept.kind !== "concept" ||
    sourceConcept.layer !== "L2" || !file || !completeEntity(file) || file.kind !== "file" ||
    file.layer !== "L1" || file.path !== path || !fileRelation ||
    !completeRelation(fileRelation) || fileRelation.layer !== "L2" ||
    fileRelation.targetId !== file.id || file.id === sourceConcept.id || !evidence
  ) return undefined;
  if (relatedConcept === null || conceptRelation === null) {
    if (relatedConcept !== null || conceptRelation !== null || fileRelation.sourceId !== sourceConcept.id) {
      return undefined;
    }
  } else if (
    !completeEntity(relatedConcept) || relatedConcept.kind !== "concept" ||
    relatedConcept.layer !== "L2" || relatedConcept.id === sourceConcept.id ||
    file.id === relatedConcept.id || !completeRelation(conceptRelation) ||
    conceptRelation.layer !== "L2" ||
    new Set([conceptRelation.sourceId, conceptRelation.targetId]).size !== 2 ||
    ![conceptRelation.sourceId, conceptRelation.targetId].includes(sourceConcept.id) ||
    ![conceptRelation.sourceId, conceptRelation.targetId].includes(relatedConcept.id) ||
    fileRelation.sourceId !== relatedConcept.id
  ) return undefined;
  return { sourceConcept, conceptRelation, relatedConcept, fileRelation, file, evidence };
}

function contextNavigation(
  value: unknown,
  candidatePaths: Set<string>,
): ContextView["data"]["navigation"] | undefined {
  const item = record(value);
  const revision = displayText(item?.revision);
  const rawReading = Array.isArray(item?.readingOrder) ? item.readingOrder : [];
  if (!item || !nonBlank(revision) || rawReading.length > 12) return undefined;
  const paths = new Set<string>();
  const readingOrder: ContextReadingItem[] = [];
  for (let index = 0; index < rawReading.length; index += 1) {
    const raw = record(rawReading[index]);
    const rawPosition = raw?.position;
    const position = typeof rawPosition === "number" && Number.isInteger(rawPosition)
      ? rawPosition : 0;
    const path = text(raw?.path);
    const language = displayText(raw?.language);
    const why = displayText(raw?.why);
    const evidence = strictEvidence(raw?.evidence);
    const rawChains = Array.isArray(raw?.chains) ? raw.chains : [];
    const chains = rawChains.map((chain) => contextChain(chain, path)).filter(
      (chain): chain is ContextNavigationChain => Boolean(chain),
    );
    if (
      !raw || position !== index + 1 || !isCanonicalRelativePath(path) ||
      paths.has(path) || !candidatePaths.has(path) || !nonBlank(language) || !nonBlank(why) ||
      !evidence || rawChains.length === 0 || rawChains.length > 3 || chains.length !== rawChains.length
    ) return undefined;
    paths.add(path);
    const rawActions = Array.isArray(raw.actions) ? raw.actions : [];
    if (rawActions.length !== CONTEXT_ACTIONS.length) return undefined;
    const actions: ContextReadingItem["actions"] = [];
    for (let actionIndex = 0; actionIndex < CONTEXT_ACTIONS.length; actionIndex += 1) {
      const action = record(rawActions[actionIndex]);
      const [kind, label] = CONTEXT_ACTIONS[actionIndex]!;
      const summary = displayText(action?.summary);
      const rawActionEvidence = Array.isArray(action?.evidence) ? action.evidence : [];
      const actionEvidence = evidenceList(rawActionEvidence);
      if (
        action?.kind !== kind || action?.label !== label || !nonBlank(summary) ||
        rawActionEvidence.length > 3 || actionEvidence.length !== rawActionEvidence.length
      ) {
        return undefined;
      }
      actions.push({ kind, label, summary, evidence: actionEvidence });
    }
    const rawDependents = Array.isArray(raw.dependents) ? raw.dependents : [];
    if (rawDependents.length > 4) return undefined;
    const dependents = rawDependents.flatMap((value) => {
      const dependent = record(value);
      const dependentPath = text(dependent?.path);
      const relation = displayText(dependent?.relation);
      return dependent && isCanonicalRelativePath(dependentPath) && candidatePaths.has(dependentPath) && nonBlank(relation)
        ? [{ path: dependentPath, relation }] : [];
    });
    if (dependents.length !== rawDependents.length) return undefined;
    const rawHistory = Array.isArray(raw.history) ? raw.history : [];
    if (rawHistory.length > 3) return undefined;
    const history = rawHistory.flatMap((value) => {
      const historyItem = record(value);
      const id = displayText(historyItem?.id);
      const title = displayText(historyItem?.title);
      const status = displayText(historyItem?.status);
      const createdAt = displayText(historyItem?.createdAt);
      const relation = displayText(historyItem?.relation);
      return historyItem && [id, title, status, createdAt, relation].every(nonBlank)
        ? [{ id, title, status, createdAt, relation }] : [];
    });
    if (history.length !== rawHistory.length) return undefined;
    const rawNext = raw.nextStep;
    let nextStep: ContextReadingItem["nextStep"] = null;
    if (rawNext !== null) {
      const next = record(rawNext);
      const nextPath = text(next?.path);
      const reason = displayText(next?.reason);
      if (!next || !isCanonicalRelativePath(nextPath) || !candidatePaths.has(nextPath) || !nonBlank(reason)) {
        return undefined;
      }
      nextStep = { path: nextPath, reason };
    }
    readingOrder.push({
      position, path, language, why, evidence, nextStep, chains, actions, dependents, history,
    });
  }
  for (let index = 0; index < readingOrder.length; index += 1) {
    const expected = readingOrder[index + 1]?.path;
    if ((readingOrder[index]!.nextStep?.path ?? undefined) !== expected) return undefined;
  }
  return { revision, readingOrder };
}

export function parseContextView(value: unknown): ContextView | undefined {
  const rawEnvelope = record(value);
  if (
    !Array.isArray(rawEnvelope?.warnings) ||
    rawEnvelope.warnings.length > MAX_CONTEXT_WARNINGS
  ) {
    return undefined;
  }
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
  const navigation = contextNavigation(data.navigation, new Set(files.map((entry) => entry.path)));
  if (!navigation) return undefined;
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
      navigation,
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

const TOUR_DEPTHS: TourDepth[] = ["one-minute", "five-minutes", "source-deep-dive"];
const TOUR_KINDS = new Set([
  "purpose", "why", "workflow", "module", "data-structure", "task", "file",
  "history", "symbol", "dependency", "test", "task-history", "evidence",
]);
const TOUR_LIMITS: Record<TourDepth, number> = {
  "one-minute": 4,
  "five-minutes": 8,
  "source-deep-dive": 12,
};
const TOUR_KINDS_BY_DEPTH: Record<TourDepth, Set<string>> = {
  "one-minute": new Set(["purpose", "why", "workflow", "module"]),
  "five-minutes": new Set([
    "purpose", "why", "workflow", "module", "data-structure", "task", "file", "history",
  ]),
  "source-deep-dive": new Set([
    "file", "symbol", "dependency", "test", "task-history", "evidence",
  ]),
};

function tourEntity(value: unknown): TourStop["entity"] | undefined {
  const item = record(value);
  const layer = item?.layer;
  if (!item || (layer !== "L1" && layer !== "L2" && layer !== "L3")) return undefined;
  const id = displayText(item.id);
  const kind = displayText(item.kind);
  const label = displayText(item.label);
  const source = displayText(item.source);
  const path = item.path === undefined ? undefined : text(item.path);
  if (!id || !kind || !label || !source || (path !== undefined && !isCanonicalRelativePath(path))) {
    return undefined;
  }
  return {
    id,
    kind,
    label,
    ...(path ? { path } : {}),
    layer,
    source,
    confidence: confidence(item.confidence),
    evidence: evidenceList(item.evidence),
  };
}

function tourRelation(value: unknown): TourStop["relations"][number] | undefined {
  const item = record(value);
  const layer = item?.layer;
  if (!item || (layer !== "L1" && layer !== "L2" && layer !== "L3")) return undefined;
  const id = displayText(item.id);
  const sourceId = displayText(item.sourceId);
  const targetId = displayText(item.targetId);
  const relation = displayText(item.relation);
  const source = displayText(item.source);
  if (!id || !sourceId || !targetId || !relation || !source) return undefined;
  return {
    id, sourceId, targetId, relation, layer, source,
    confidence: confidence(item.confidence),
    evidence: evidenceList(item.evidence),
  };
}

function tourStop(value: unknown): TourStop | undefined {
  const item = record(value);
  const id = displayText(item?.id);
  const kind = displayText(item?.kind);
  const title = displayText(item?.title);
  const plainLanguage = displayText(item?.plainLanguage);
  const technicalExplanation = displayText(item?.technicalExplanation);
  const evidence = evidenceList(item?.evidence);
  const entity = tourEntity(item?.entity);
  if (
    !item || !id || !TOUR_KINDS.has(kind) || !title || !plainLanguage ||
    !technicalExplanation || evidence.length === 0 || !entity
  ) return undefined;
  const relations = Array.isArray(item.relations)
    ? item.relations.slice(0, 4).map(tourRelation).filter((entry): entry is TourStop["relations"][number] => Boolean(entry))
    : [];
  return { id, kind, title, plainLanguage, technicalExplanation, evidence, entity, relations };
}

export function parseRepositoryTourView(value: unknown): RepositoryTourView | undefined {
  const common = commonEnvelope(value);
  if (!common || common.envelope.view !== "repo-tour") return undefined;
  const stats = record(common.data.stats);
  const rawTiers = Array.isArray(common.data.tiers) ? common.data.tiers : [];
  if (!stats || rawTiers.length !== 3) return undefined;
  const rawDepths = rawTiers.map((tier) => record(tier)?.depth);
  if (new Set(rawDepths).size !== 3 || !TOUR_DEPTHS.every((depth) => rawDepths.includes(depth))) {
    return undefined;
  }
  const tiers = TOUR_DEPTHS.map((depth) => {
    const tier = rawTiers.map(record).find((item) => item?.depth === depth);
    if (!tier) return undefined;
    const label = displayText(tier.label);
    if (!label) return undefined;
    const rawStops = Array.isArray(tier.stops) ? tier.stops : [];
    if (rawStops.length > TOUR_LIMITS[depth]) return undefined;
    const stops = rawStops.map(tourStop).filter((entry): entry is TourStop => Boolean(entry));
    if (
      stops.length !== rawStops.length ||
      stops.some((stop) => !TOUR_KINDS_BY_DEPTH[depth].has(stop.kind))
    ) return undefined;
    return { depth, label, stops };
  });
  if (tiers.some((tier) => !tier)) return undefined;
  return {
    schemaVersion: SCHEMA_VERSION,
    view: "repo-tour",
    project: common.project,
    sourceState: common.sourceState,
    data: {
      tiers: tiers as RepositoryTourView["data"]["tiers"],
      stats: {
        files: count(stats.files), concepts: count(stats.concepts), tasks: count(stats.tasks),
        documentsRead: count(stats.documentsRead),
      },
    },
    warnings: common.warnings,
  };
}

function repositoryStats(value: unknown): RepositoryOverviewView["data"]["stats"] | undefined {
  const stats = record(value);
  if (!stats) return undefined;
  return {
    files: count(stats.files), concepts: count(stats.concepts), tasks: count(stats.tasks),
    documentsRead: count(stats.documentsRead),
  };
}

function nonBlank(value: string | undefined): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function completeEntity(entity: TourStop["entity"]): boolean {
  return nonBlank(entity.id) && nonBlank(entity.kind) && nonBlank(entity.label) &&
    nonBlank(entity.source) && entity.evidence.length > 0;
}

function completeRelation(relation: TourStop["relations"][number]): boolean {
  return nonBlank(relation.id) && nonBlank(relation.sourceId) && nonBlank(relation.targetId) &&
    nonBlank(relation.relation) && nonBlank(relation.source) && relation.evidence.length > 0;
}

export function parseArchitectureView(value: unknown): ArchitectureView | undefined {
  const common = commonEnvelope(value);
  if (!common || common.envelope.view !== "architecture" || common.data.layout !== "cognitive-components") {
    return undefined;
  }
  const summary = record(common.data.summary);
  const stats = repositoryStats(common.data.stats);
  const rawComponents = Array.isArray(common.data.components) ? common.data.components : [];
  if (!summary || summary.explanationSource !== "derived-presentation" || !stats || rawComponents.length > 8) {
    return undefined;
  }
  const components = rawComponents.flatMap((value) => {
    const item = record(value);
    const id = displayText(item?.id);
    const name = displayText(item?.name);
    const group = item?.group;
    const entity = tourEntity(item?.entity);
    const rawPaths = Array.isArray(item?.paths) ? item.paths : [];
    const paths = rawPaths.map((path) => text(path));
    const rawEvidence = Array.isArray(item?.evidence) ? item.evidence : [];
    const evidence = evidenceList(rawEvidence);
    const responsibility = displayText(item?.responsibility);
    if (
      !item || !nonBlank(id) || !nonBlank(name) || !nonBlank(responsibility) ||
      !entity || !completeEntity(entity) || entity.id !== id || entity.layer !== "L2" ||
      (group !== "entry" && group !== "core" && group !== "support") ||
      paths.length === 0 || paths.length > 3 || new Set(paths).size !== paths.length ||
      paths.some((path) => !isCanonicalRelativePath(path)) || rawEvidence.length > 3 ||
      evidence.length !== rawEvidence.length || evidence.length === 0
    ) return [];
    return [{
      id, name, group: group as ArchitectureComponent["group"],
      responsibility, paths, entity, evidence,
    }];
  });
  const componentIds = components.map((component) => component.id);
  if (components.length !== rawComponents.length || new Set(componentIds).size !== componentIds.length) {
    return undefined;
  }
  const rawConnections = Array.isArray(common.data.connections) ? common.data.connections : [];
  if (rawConnections.length > 12) return undefined;
  const connections = rawConnections.map(tourRelation).filter((edge): edge is TourStop["relations"][number] => Boolean(edge));
  const connectionIds = connections.map((edge) => edge.id);
  const included = new Set(componentIds);
  if (
    connections.length !== rawConnections.length || new Set(connectionIds).size !== connectionIds.length ||
    connections.some((edge) => edge.layer !== "L2" || !completeRelation(edge) || !included.has(edge.sourceId) || !included.has(edge.targetId))
  ) return undefined;
  const rawEntries = Array.isArray(common.data.entryPoints) ? common.data.entryPoints : [];
  if (rawEntries.length > 3) return undefined;
  const entryPoints = rawEntries.flatMap((value) => {
    const item = record(value);
    const path = text(item?.path);
    const entity = tourEntity(item?.entity);
    const evidence = evidenceList(item?.evidence);
    const reason = displayText(item?.reason);
    if (
      !item || !isCanonicalRelativePath(path) || !nonBlank(reason) || !entity ||
      !completeEntity(entity) || entity.kind !== "file" || entity.layer !== "L1" ||
      entity.path !== path || evidence.length === 0
    ) return [];
    return [{ path, reason, entity, evidence }];
  });
  if (entryPoints.length !== rawEntries.length || new Set(entryPoints.map((entry) => entry.path)).size !== entryPoints.length) {
    return undefined;
  }
  const summaryText = displayText(summary.text);
  if (!nonBlank(summaryText)) return undefined;
  return {
    schemaVersion: SCHEMA_VERSION,
    view: "architecture",
    project: common.project,
    sourceState: common.sourceState,
    data: {
      layout: "cognitive-components",
      summary: {
        text: summaryText, explanationSource: "derived-presentation",
        evidence: evidenceList(summary.evidence),
      },
      components,
      connections,
      entryPoints,
      stats,
    },
    warnings: common.warnings,
  };
}

export function parseFlowView(value: unknown): FlowView | undefined {
  const common = commonEnvelope(value);
  if (!common || common.envelope.view !== "flow" || common.data.layout !== "numbered-task-flow") {
    return undefined;
  }
  const stats = repositoryStats(common.data.stats);
  const rawSteps = Array.isArray(common.data.steps) ? common.data.steps : [];
  if (!stats || (rawSteps.length !== 0 && (rawSteps.length < 5 || rawSteps.length > 7))) return undefined;
  const ids = new Set<string>();
  const paths = new Set<string>();
  const steps: FlowStep[] = [];
  for (let index = 0; index < rawSteps.length; index += 1) {
    const item = record(rawSteps[index]);
    const step = item?.step;
    const id = displayText(item?.id);
    const title = displayText(item?.title);
    const expectedNext = index + 1 < rawSteps.length ? record(rawSteps[index + 1])?.title : null;
    const evidence = evidenceList(item?.evidence);
    if (
      !item || step !== index + 1 || !nonBlank(id) || ids.has(id) || !nonBlank(title) ||
      item.explanationSource !== "derived-presentation" || item.nextStep !== expectedNext || evidence.length === 0
    ) return undefined;
    ids.add(id);
    const rawFiles = Array.isArray(item.keyFiles) ? item.keyFiles : [];
    if (rawFiles.length > 3) return undefined;
    const keyFiles: FlowStep["keyFiles"] = [];
    for (const rawFile of rawFiles) {
      const file = record(rawFile);
      const path = text(file?.path);
      const entity = tourEntity(file?.entity);
      const relation = tourRelation(file?.relation);
      const fileEvidence = evidenceList(file?.evidence);
      const moduleId = displayText(file?.moduleId);
      const moduleName = displayText(file?.moduleName);
      if (
        !file || !isCanonicalRelativePath(path) || paths.has(path) ||
        !nonBlank(moduleId) || !nonBlank(moduleName) || !entity || !completeEntity(entity) ||
        entity.kind !== "file" || entity.layer !== "L1" || entity.path !== path ||
        !relation || relation.layer !== "L2" || !completeRelation(relation) ||
        relation.sourceId !== moduleId || relation.targetId !== entity.id ||
        relation.evidence.length === 0 || fileEvidence.length === 0
      ) {
        return undefined;
      }
      paths.add(path);
      keyFiles.push({
        path,
        moduleId,
        moduleName,
        entity,
        relation,
        evidence: fileEvidence,
      });
    }
    const purpose = displayText(item.purpose);
    const input = displayText(item.input);
    const output = displayText(item.output);
    const why = displayText(item.why);
    if (!nonBlank(purpose) || !nonBlank(input) || !nonBlank(output) || !nonBlank(why)) return undefined;
    steps.push({
      step,
      id,
      title,
      purpose,
      input,
      output,
      keyFiles,
      why,
      nextStep: item.nextStep === null ? null : displayText(item.nextStep),
      explanationSource: "derived-presentation",
      evidence,
    });
  }
  if (paths.size > 18) return undefined;
  const rawTask = common.data.exampleTask;
  let exampleTask: FlowView["data"]["exampleTask"];
  if (rawTask !== undefined && rawTask !== null) {
    const task = record(rawTask);
    const title = displayText(task?.title);
    const source = displayText(task?.source);
    if (!task || !nonBlank(title) || !nonBlank(source)) return undefined;
    if (source === "request" || source === "request-redacted") {
      if ("entity" in task || "evidence" in task) return undefined;
      exampleTask = { title, source };
    } else if (source === "task-events") {
      const entity = tourEntity(task.entity);
      const rawEvidence = Array.isArray(task.evidence) ? task.evidence : [];
      const evidence = evidenceList(rawEvidence);
      if (
        !entity || !completeEntity(entity) || entity.kind !== "task" || entity.layer !== "L3" ||
        entity.source !== "task-events" ||
        evidence.length === 0 || evidence.length !== rawEvidence.length ||
        evidence.some((entry) => entry.layer !== "L3" || entry.source !== "task-events")
      ) return undefined;
      exampleTask = { title, source, entity, evidence };
    } else {
      return undefined;
    }
  }
  return {
    schemaVersion: SCHEMA_VERSION,
    view: "flow",
    project: common.project,
    sourceState: common.sourceState,
    data: { layout: "numbered-task-flow", ...(exampleTask ? { exampleTask } : {}), steps, stats },
    warnings: common.warnings,
  };
}

export function parseImpactView(value: unknown): ImpactView | undefined {
  const common = commonEnvelope(value);
  if (!common || common.envelope.view !== "impact" || common.data.layout !== "incoming-focus-outgoing") return undefined;
  const revision = displayText(common.data.revision); const stats = record(common.data.stats);
  const impactStat = (value: unknown): number | undefined =>
    typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : undefined;
  const parsedStats = stats ? {
    files: impactStat(stats.files), concepts: impactStat(stats.concepts), tasks: impactStat(stats.tasks),
  } : undefined;
  const entityRegistry = new Map<string, string>(); const edgeRegistry = new Map<string, string>();
  const knownEvidence = new Set<string>(); const signature = (item: unknown): string => JSON.stringify(item);
  const impactEvidence = (value: unknown, limit = 3): Evidence[] | undefined => {
    const raw = Array.isArray(value) ? value : [];
    if (raw.length === 0 || raw.length > limit) return undefined;
    const parsed: Evidence[] = [];
    for (const entry of raw) {
      const item = record(entry); const layer = item?.layer;
      const kind = displayText(item?.kind); const summary = displayText(item?.summary); const source = displayText(item?.source);
      const rawConfidence = item?.confidence; const rawStart = item?.lineStart; const rawEnd = item?.lineEnd;
      const path = item?.path === undefined ? undefined : text(item.path);
      if (!item || (layer !== "L1" && layer !== "L2" && layer !== "L3") ||
          !nonBlank(kind) || !nonBlank(summary) || !nonBlank(source) ||
          typeof rawConfidence !== "number" || !Number.isFinite(rawConfidence) || rawConfidence < 0 || rawConfidence > 1 ||
          (path !== undefined && !isCanonicalRelativePath(path)) ||
          (rawStart !== undefined && (typeof rawStart !== "number" || !Number.isInteger(rawStart) || rawStart < 1)) ||
          (rawEnd !== undefined && (typeof rawEnd !== "number" || !Number.isInteger(rawEnd) || rawEnd < 1)) ||
          (rawEnd !== undefined && (rawStart === undefined || rawEnd < rawStart))) return undefined;
      parsed.push({ kind, summary, layer, source, confidence: rawConfidence,
        ...(path ? { path } : {}), ...(rawStart !== undefined ? { lineStart: rawStart } : {}),
        ...(rawEnd !== undefined ? { lineEnd: rawEnd } : {}) });
    }
    return parsed;
  };
  const parseEntity = (raw: unknown): TourStop["entity"] | undefined => {
    const item = record(raw); const normalizedEvidence = impactEvidence(item?.evidence, 8);
    const layer = item?.layer; const id = displayText(item?.id); const kind = displayText(item?.kind);
    const label = displayText(item?.label); const source = displayText(item?.source); const rawConfidence = item?.confidence;
    const path = item?.path === undefined ? undefined : text(item.path);
    if (!item || !normalizedEvidence || (layer !== "L1" && layer !== "L2" && layer !== "L3") ||
        !nonBlank(id) || !nonBlank(kind) || !nonBlank(label) || !nonBlank(source) ||
        typeof rawConfidence !== "number" || !Number.isFinite(rawConfidence) || rawConfidence < 0 || rawConfidence > 1 ||
        (path !== undefined && !isCanonicalRelativePath(path))) return undefined;
    const entity: TourStop["entity"] = { id, kind, label, layer, source, confidence: rawConfidence,
      evidence: normalizedEvidence, ...(path ? { path } : {}) };
    const sig = JSON.stringify([entity.kind, entity.label, entity.path, entity.layer, entity.source, entity.confidence, entity.evidence]);
    if ((entityRegistry.has(entity.id) && entityRegistry.get(entity.id) !== sig) || edgeRegistry.has(entity.id)) return undefined;
    entityRegistry.set(entity.id, sig); entity.evidence.forEach((item) => knownEvidence.add(signature(item))); return entity;
  };
  const parseRelation = (raw: unknown): TourStop["relations"][number] | undefined => {
    const item = record(raw); const evidence = impactEvidence(item?.evidence);
    const layer = item?.layer; const id = displayText(item?.id); const sourceId = displayText(item?.sourceId);
    const targetId = displayText(item?.targetId); const relationName = displayText(item?.relation);
    const source = displayText(item?.source); const rawConfidence = item?.confidence;
    if (!item || !evidence || (layer !== "L1" && layer !== "L2" && layer !== "L3") ||
        !nonBlank(id) || !nonBlank(sourceId) || !nonBlank(targetId) || !nonBlank(relationName) || !nonBlank(source) ||
        typeof rawConfidence !== "number" || !Number.isFinite(rawConfidence) || rawConfidence < 0 || rawConfidence > 1) return undefined;
    const relation: TourStop["relations"][number] = { id, sourceId, targetId, relation: relationName,
      layer, source, confidence: rawConfidence, evidence };
    const sig = signature(relation); if ((edgeRegistry.has(relation.id) && edgeRegistry.get(relation.id) !== sig) || entityRegistry.has(relation.id)) return undefined;
    edgeRegistry.set(relation.id, sig); evidence.forEach((entry) => knownEvidence.add(signature(entry))); return relation;
  };
  const sameEvidence = (left: Evidence[], right: Evidence[]): boolean => signature(left) === signature(right);
  const rawFocus = record(common.data.focus); const focus = parseEntity(rawFocus?.entity); const focusEvidence = impactEvidence(rawFocus?.evidence);
  if (!nonBlank(revision) || !parsedStats || parsedStats.files === undefined || parsedStats.concepts === undefined ||
      parsedStats.tasks === undefined || !rawFocus || !focus || !focusEvidence || !sameEvidence(focusEvidence, focus.evidence) ||
      !((focus.kind === "file" && focus.layer === "L1" && focus.path) || (focus.kind === "concept" && focus.layer === "L2"))) return undefined;
  const rawAnchors = Array.isArray(common.data.anchorFiles) ? common.data.anchorFiles : [];
  if (rawAnchors.length > 8) return undefined;
  const anchorFiles: ImpactView["data"]["anchorFiles"] = []; const anchorByPath = new Map<string, TourStop["entity"]>();
  for (const raw of rawAnchors) {
    const item = record(raw); const entity = parseEntity(item?.entity); const mapping = item?.mapping === null ? null : parseRelation(item?.mapping); const evidence = impactEvidence(item?.evidence);
    if (!item || !entity || entity.kind !== "file" || entity.layer !== "L1" || !entity.path || anchorByPath.has(entity.path) || mapping === undefined || !evidence) return undefined;
    if (focus.kind === "concept" ? (!mapping || mapping.layer !== "L2" || !["implemented_by", "configured_by"].includes(mapping.relation) || mapping.sourceId !== focus.id || mapping.targetId !== entity.id || !sameEvidence(evidence, mapping.evidence)) :
        (mapping !== null || entity.id !== focus.id || entity.path !== focus.path || !sameEvidence(evidence, entity.evidence))) return undefined;
    anchorFiles.push({ entity, mapping, evidence }); anchorByPath.set(entity.path, entity);
  }
  if (focus.kind === "file" && anchorFiles.length > 1) return undefined;
  const rawConcepts = Array.isArray(common.data.focusConcepts) ? common.data.focusConcepts : [];
  if (rawConcepts.length > 8) return undefined;
  const focusConcepts: ImpactView["data"]["focusConcepts"] = []; const conceptIds = new Set<string>();
  for (const raw of rawConcepts) {
    const item = record(raw); const entity = parseEntity(item?.entity); const mapping = item?.mapping === null ? null : parseRelation(item?.mapping); const evidence = impactEvidence(item?.evidence);
    if (!item || !entity || entity.kind !== "concept" || entity.layer !== "L2" || conceptIds.has(entity.id) || mapping === undefined || !evidence) return undefined;
    if (focus.kind === "concept" ? (entity.id !== focus.id || mapping !== null || !sameEvidence(evidence, entity.evidence)) :
        (!mapping || mapping.layer !== "L2" || !["implemented_by", "configured_by", "tested_by"].includes(mapping.relation) || mapping.sourceId !== entity.id || mapping.targetId !== focus.id || !sameEvidence(evidence, mapping.evidence))) return undefined;
    conceptIds.add(entity.id); focusConcepts.push({ entity, mapping, evidence });
  }
  const parseLane = (value: unknown, direction: "incoming" | "outgoing"): ImpactLane | undefined => {
    const item = record(value); const peer = parseEntity(item?.peer); const relation = parseRelation(item?.relation);
    const viaPath = text(item?.viaPath); const evidence = impactEvidence(item?.evidence); const anchor = anchorByPath.get(viaPath); const recordedOrder = item?.recordedOrder;
    if (!item || !peer || !completeEntity(peer) || peer.kind !== "file" || peer.layer !== "L1" || !peer.path ||
        !relation || relation.layer !== "L1" || !isCanonicalRelativePath(viaPath) || !anchor || !evidence || !sameEvidence(evidence, relation.evidence) ||
        typeof recordedOrder !== "number" || !Number.isInteger(recordedOrder) || recordedOrder < 1) return undefined;
    if (direction === "incoming" ? relation.sourceId !== peer.id || relation.targetId !== anchor.id : relation.sourceId !== anchor.id || relation.targetId !== peer.id) return undefined;
    return { peer, relation, viaPath, recordedOrder, evidence };
  };
  const rawIncoming = Array.isArray(common.data.incoming) ? common.data.incoming : [];
  const rawOutgoing = Array.isArray(common.data.outgoing) ? common.data.outgoing : [];
  if (rawIncoming.length > 8 || rawOutgoing.length > 8) return undefined;
  const incoming = rawIncoming.map((item) => parseLane(item, "incoming")).filter((item): item is ImpactLane => Boolean(item));
  const outgoing = rawOutgoing.map((item) => parseLane(item, "outgoing")).filter((item): item is ImpactLane => Boolean(item));
  if (incoming.length !== rawIncoming.length || outgoing.length !== rawOutgoing.length) return undefined;
  const rawSemantic = Array.isArray(common.data.semantic) ? common.data.semantic : [];
  if (rawSemantic.length > 8) return undefined;
  const semantic: ImpactView["data"]["semantic"] = [];
  for (const raw of rawSemantic) {
    const item = record(raw); const direction = item?.direction; const focusConceptId = text(item?.focusConceptId); const peer = parseEntity(item?.peer);
    const relation = parseRelation(item?.relation); const evidence = impactEvidence(item?.evidence);
    if (!item || (direction !== "incoming" && direction !== "outgoing") || !conceptIds.has(focusConceptId) || !peer || peer.kind !== "concept" ||
        peer.layer !== "L2" || focusConceptId === peer.id || !relation || relation.layer !== "L2" || !evidence || !sameEvidence(evidence, relation.evidence)) return undefined;
    const expected = direction === "outgoing" ? [focusConceptId, peer.id] : [peer.id, focusConceptId];
    if (relation.sourceId !== expected[0] || relation.targetId !== expected[1]) return undefined;
    semantic.push({ direction, focusConceptId, peer, relation, evidence });
  }
  const rawHistory = Array.isArray(common.data.history) ? common.data.history : [];
  if (rawHistory.length > 5) return undefined;
  const history: ImpactView["data"]["history"] = [];
  for (const raw of rawHistory) {
    const item = record(raw); const entity = parseEntity(item?.entity); const relation = parseRelation(item?.relation);
    const status = displayText(item?.status); const recordedOrder = item?.recordedOrder; const evidence = impactEvidence(item?.evidence);
    if (!item || !entity || !completeEntity(entity) || entity.kind !== "task" || entity.layer !== "L3" || !relation || relation.layer !== "L3" ||
        relation.source !== "task-events" || entity.source !== "task-events" || relation.sourceId !== entity.id ||
        !new Set([focus.id, ...anchorFiles.map((a) => a.entity.id), ...conceptIds]).has(relation.targetId) || !nonBlank(status) || !evidence || !sameEvidence(entity.evidence, evidence) ||
        evidence.some((entry) => entry.layer !== "L3" || entry.source !== "task-events") ||
        typeof recordedOrder !== "number" || !Number.isInteger(recordedOrder) || recordedOrder < 1 || !sameEvidence(evidence, relation.evidence)) return undefined;
    history.push({ entity, status, relation, recordedOrder, evidence });
  }
  const rawTests = Array.isArray(common.data.testRecommendations) ? common.data.testRecommendations : [];
  if (rawTests.length > 5) return undefined;
  const laneRelations = new Map([...incoming, ...outgoing].map((item) => [item.relation.id, signature(item.relation)]));
  const testRecommendations: ImpactView["data"]["testRecommendations"] = [];
  for (const raw of rawTests) {
    const item = record(raw); const basis = item?.basis; const path = text(item?.path); const reason = displayText(item?.reason);
    const entity = parseEntity(item?.entity); const relation = parseRelation(item?.relation); const evidence = impactEvidence(item?.evidence);
    const sourceConcept = item?.sourceConcept === null ? null : parseEntity(item?.sourceConcept);
    if (!item || !isCanonicalRelativePath(path) || !nonBlank(reason) || !entity || !completeEntity(entity) || entity.kind !== "file" || entity.layer !== "L1" || entity.path !== path ||
        !relation || !evidence || !sameEvidence(evidence, relation.evidence) || sourceConcept === undefined) return undefined;
    if (basis === "physical-tests" ? (sourceConcept !== null || relation.layer !== "L1" || relation.relation !== "tests" || relation.sourceId !== entity.id || !new Set(anchorFiles.map((a) => a.entity.id)).has(relation.targetId) || laneRelations.get(relation.id) !== signature(relation)) :
        basis === "semantic-tested-by" ? (!sourceConcept || !conceptIds.has(sourceConcept.id) || relation.layer !== "L2" || relation.relation !== "tested_by" || relation.sourceId !== sourceConcept.id || relation.targetId !== entity.id) : true) return undefined;
    testRecommendations.push({ basis: basis as "physical-tests" | "semantic-tested-by", path, reason, sourceConcept, entity, relation, evidence });
  }
  const rawRisks = Array.isArray(common.data.risks) ? common.data.risks : [];
  if (rawRisks.length > 5) return undefined;
  const risks: ImpactView["data"]["risks"] = [];
  for (const raw of rawRisks) {
    const item = record(raw); const kind = displayText(item?.kind); const summary = displayText(item?.summary); const severity = item?.severity; const evidence = impactEvidence(item?.evidence);
    if (!item || !nonBlank(kind) || !nonBlank(summary) || (severity !== "low" && severity !== "medium" && severity !== "high") || !evidence || evidence.some((entry) => !knownEvidence.has(signature(entry)))) return undefined;
    risks.push({ kind, summary, severity, evidence });
  }
  const expectedActions = [["purpose", "它做什么"], ["callers", "谁调用它"], ["dependencies", "它依赖谁"], ["change", "如果修改它"], ["history", "过去谁改过它"]] as const;
  const rawActions = Array.isArray(common.data.actions) ? common.data.actions : [];
  if (rawActions.length !== 5) return undefined;
  const actions: ImpactView["data"]["actions"] = [];
  for (let index = 0; index < expectedActions.length; index += 1) {
    const item = record(rawActions[index]); const [kind, label] = expectedActions[index]!; const summary = displayText(item?.summary);
    const rawEvidence = Array.isArray(item?.evidence) ? item.evidence : [];
    const evidence = rawEvidence.length === 0 ? [] : impactEvidence(rawEvidence);
    if (!item || item.kind !== kind || item.label !== label || !nonBlank(summary) || evidence === undefined || evidence.some((entry) => !knownEvidence.has(signature(entry)))) return undefined;
    actions.push({ kind, label, summary, evidence });
  }
  return { schemaVersion: SCHEMA_VERSION, view: "impact", project: common.project, sourceState: common.sourceState,
    data: { layout: "incoming-focus-outgoing", revision, focus: { entity: focus, evidence: focusEvidence }, anchorFiles, focusConcepts, incoming, outgoing, semantic, history, testRecommendations, risks, actions,
      stats: { files: parsedStats.files, concepts: parsedStats.concepts, tasks: parsedStats.tasks } }, warnings: common.warnings };
}

export function parseHistoryView(value: unknown): HistoryView | undefined {
  const rawEnvelope = record(value);
  if (!Array.isArray(rawEnvelope?.warnings) || rawEnvelope.warnings.length > MAX_CONTEXT_WARNINGS) return undefined;
  const common = commonEnvelope(value);
  if (!common || common.envelope.view !== "history" || common.data.layout !== "task-timeline-story") return undefined;
  const strictText = (entry: unknown, maximum: number): string | undefined =>
    typeof entry === "string" && entry.trim().length > 0 && entry.length <= maximum &&
      !containsPrivatePath(entry.replace(/<[^>]*>/g, ""))
      ? entry : undefined;
  const revision = strictText(common.data.revision, 120);
  const disclaimer = strictText(common.data.disclaimer, 500);
  const selectedMode = common.data.selectedMode;
  const stats = record(common.data.stats);
  const nonNegativeInteger = (entry: unknown): entry is number =>
    typeof entry === "number" && Number.isInteger(entry) && entry >= 0;
  const utcTimestamp = (entry: string): boolean => {
    const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?Z$/.exec(entry);
    const epoch = Date.parse(entry);
    if (!match || !Number.isFinite(epoch)) return false;
    const date = new Date(epoch);
    return date.getUTCFullYear() === Number(match[1]) && date.getUTCMonth() + 1 === Number(match[2]) &&
      date.getUTCDate() === Number(match[3]) && date.getUTCHours() === Number(match[4]) &&
      date.getUTCMinutes() === Number(match[5]) && date.getUTCSeconds() === Number(match[6]);
  };
  const historyEvidence = (value: unknown, maximum: number, l3 = false): Evidence[] | undefined => {
    const raw = Array.isArray(value) ? value : [];
    if (raw.length === 0 || raw.length > maximum) return undefined;
    const parsed: Evidence[] = [];
    for (const entry of raw) {
      const item = record(entry);
      const rawConfidence = item?.confidence; const start = item?.lineStart; const end = item?.lineEnd;
      const kind = strictText(item?.kind, 120); const summary = strictText(item?.summary, 500);
      const source = strictText(item?.source, 120); const layer = item?.layer;
      const path = item?.path === undefined ? undefined : strictText(item.path, 500);
      if (!item || !kind || !summary || !source || (layer !== "L1" && layer !== "L2" && layer !== "L3") ||
          (item.path !== undefined && (!path || !isCanonicalRelativePath(path))) || typeof rawConfidence !== "number" ||
          !Number.isFinite(rawConfidence) || rawConfidence < 0 || rawConfidence > 1 ||
          (start !== undefined && (typeof start !== "number" || !Number.isInteger(start) || start < 1)) ||
          (end !== undefined && (typeof end !== "number" || !Number.isInteger(end) || end < 1 || start === undefined || end < start)) ||
          (l3 && (layer !== "L3" || source !== "task-events"))) return undefined;
      parsed.push({ kind, summary, layer, source, confidence: rawConfidence, ...(path ? { path } : {}),
        ...(start !== undefined ? { lineStart: start } : {}), ...(end !== undefined ? { lineEnd: end } : {}) });
    }
    return parsed;
  };
  const historyEntity = (value: unknown): TourStop["entity"] | undefined => {
    const item = record(value); const layer = item?.layer;
    const id = strictText(item?.id, 240); const kind = strictText(item?.kind, 120);
    const label = strictText(item?.label, 240); const source = strictText(item?.source, 120);
    const path = item?.path === undefined ? undefined : strictText(item.path, 500);
    const rawEvidence = Array.isArray(item?.evidence) ? item.evidence : [];
    const evidence = historyEvidence(rawEvidence, 3, layer === "L3"); const rawConfidence = item?.confidence;
    if (!item || !id || !kind || !label || !source || !evidence ||
        (layer !== "L1" && layer !== "L2" && layer !== "L3") ||
        typeof rawConfidence !== "number" || !Number.isFinite(rawConfidence) || rawConfidence < 0 || rawConfidence > 1 ||
        (item.path !== undefined && (!path || !isCanonicalRelativePath(path)))) return undefined;
    return { id, kind, label, ...(path ? { path } : {}), layer, source, confidence: rawConfidence, evidence };
  };
  const historyRelation = (value: unknown): TourStop["relations"][number] | undefined => {
    const item = record(value); const rawConfidence = item?.confidence;
    const id = strictText(item?.id, 240); const sourceId = strictText(item?.sourceId, 240);
    const targetId = strictText(item?.targetId, 240); const relation = strictText(item?.relation, 120);
    const source = strictText(item?.source, 120); const evidence = historyEvidence(item?.evidence, 3, true);
    if (!item || !id || !sourceId || !targetId || !relation || !source || !evidence || item.layer !== "L3" ||
        typeof rawConfidence !== "number" || !Number.isFinite(rawConfidence) || rawConfidence < 0 || rawConfidence > 1) return undefined;
    return { id, sourceId, targetId, relation, layer: "L3", source, confidence: rawConfidence, evidence };
  };
  if (!nonBlank(revision) || !nonBlank(disclaimer) || (selectedMode !== "timeline" && selectedMode !== "story") ||
      !stats || !nonNegativeInteger(stats.files) || !nonNegativeInteger(stats.tasks) ||
      !nonNegativeInteger(stats.displayedTasks) || !nonNegativeInteger(stats.displayedRelations)) return undefined;
  const rawTimeline = Array.isArray(common.data.timeline) ? common.data.timeline : [];
  if (rawTimeline.length > 20) return undefined;
  const timeline: HistoryTimelineItem[] = [];
  const taskEntities = new Map<string, string>();
  const entityRegistry = new Map<string, string>();
  const edgeIds = new Set<string>();
  for (const raw of rawTimeline) {
    const item = record(raw); const rawEntity = record(item?.entity); const entity = historyEntity(item?.entity);
    const taskId = strictText(item?.taskId, 240); const status = strictText(item?.status, 40);
    const summary = strictText(item?.summary, 500); const createdAt = strictText(item?.createdAt, 64);
    const updatedAt = strictText(item?.updatedAt, 64); const sortTime = strictText(item?.sortTime, 64);
    const closedAt = item?.closedAt === null ? null : strictText(item?.closedAt, 64);
    const rawEvidence = Array.isArray(item?.evidence) ? item.evidence : [];
    const evidence = historyEvidence(rawEvidence, 3, true);
    const rawRelations = Array.isArray(item?.relations) ? item.relations : [];
    if (!item || !entity || entity.kind !== "task" || entity.layer !== "L3" || entity.source !== "task-events" ||
        !nonBlank(taskId) || !nonBlank(status) || !nonBlank(summary) || !nonBlank(createdAt) ||
        !nonBlank(updatedAt) || !nonBlank(sortTime) || (closedAt !== null && !nonBlank(closedAt)) ||
        !utcTimestamp(createdAt) || !utcTimestamp(updatedAt) || !utcTimestamp(sortTime) ||
        (closedAt !== null && !utcTimestamp(closedAt)) ||
        !rawEntity || !historyEvidence(rawEntity.evidence, 3, true) || !evidence ||
        JSON.stringify(entity.evidence) !== JSON.stringify(evidence) || rawRelations.length > 12) return undefined;
    const relations: HistoryRelationItem[] = [];
    for (const rawRelation of rawRelations) {
      const relationItem = record(rawRelation); const rawTarget = record(relationItem?.entity);
      const rawRelationValue = record(relationItem?.relation); const target = historyEntity(relationItem?.entity);
      const relation = historyRelation(relationItem?.relation); const recordedOrder = relationItem?.recordedOrder;
      const relationRawEvidence = Array.isArray(relationItem?.evidence) ? relationItem.evidence : [];
      const relationEvidence = historyEvidence(relationRawEvidence, 3, true);
      if (!relationItem || !target || !relation || relation.layer !== "L3" || relation.source !== "task-events" ||
          !["read", "modified", "tested", "searched", "affects"].includes(relation.relation) ||
          relation.sourceId !== entity.id || relation.targetId !== target.id ||
          !((target.kind === "file" && target.layer === "L1" && target.path && isCanonicalRelativePath(target.path)) ||
            (target.kind === "concept" && target.layer === "L2" && target.path === undefined)) ||
          typeof recordedOrder !== "number" || !Number.isInteger(recordedOrder) || recordedOrder < 1 ||
          !rawTarget || !historyEvidence(rawTarget.evidence, 3) || !rawRelationValue ||
          !historyEvidence(rawRelationValue.evidence, 3, true) || !relationEvidence ||
          JSON.stringify(relation.evidence) !== JSON.stringify(relationEvidence) || edgeIds.has(relation.id) ||
          target.evidence.some((entry) => target.kind === "file"
            ? entry.kind !== "repository-file" || entry.layer !== "L1" || entry.source !== target.source || entry.path !== target.path
            : entry.kind !== "semantic-node" || entry.layer !== "L2" || entry.source !== target.source || entry.path !== undefined) ||
          relationEvidence.some((entry) => !entry.summary.includes(taskId) || !entry.summary.includes(relation.id))) return undefined;
      const targetSignature = JSON.stringify(target);
      if (edgeIds.has(target.id) || (entityRegistry.has(target.id) && entityRegistry.get(target.id) !== targetSignature) || entityRegistry.has(relation.id)) return undefined;
      entityRegistry.set(target.id, targetSignature);
      edgeIds.add(relation.id);
      relations.push({ entity: target, relation, recordedOrder, evidence: relationEvidence });
    }
    const entitySignature = JSON.stringify(entity);
    if (edgeIds.has(entity.id) || (entityRegistry.has(entity.id) && entityRegistry.get(entity.id) !== entitySignature) ||
        (taskEntities.has(entity.id) && taskEntities.get(entity.id) !== entitySignature)) return undefined;
    entityRegistry.set(entity.id, entitySignature);
    taskEntities.set(entity.id, entitySignature);
    if (evidence.some((entry) => !entry.summary.includes(taskId)) || sortTime !== (closedAt ?? updatedAt ?? createdAt)) return undefined;
    timeline.push({ entity, taskId, status, summary, createdAt, updatedAt, closedAt, sortTime, relations, evidence });
  }
  const order = timeline.map((item) => [item.sortTime, item.taskId] as const);
  const sortedOrder = [...order].sort((left, right) => Date.parse(right[0]) - Date.parse(left[0]) || right[1].localeCompare(left[1]));
  if (JSON.stringify(order) !== JSON.stringify(sortedOrder) || new Set(timeline.map((item) => item.entity.id)).size !== timeline.length) return undefined;
  const rawStory = Array.isArray(common.data.story) ? common.data.story : [];
  if (rawStory.length > 12) return undefined;
  const story: HistoryView["data"]["story"] = [];
  for (const raw of rawStory) {
    const item = record(raw); const task = historyEntity(item?.task);
    const id = strictText(item?.id, 240); const title = strictText(item?.title, 240);
    const summary = strictText(item?.summary, 500); const sortTime = strictText(item?.sortTime, 64);
    const rawEvidence = Array.isArray(item?.evidence) ? item.evidence : [];
    const evidence = historyEvidence(rawEvidence, 3, true); const rawGroups = Array.isArray(item?.groups) ? item.groups : [];
    if (!item || !task || !nonBlank(id) || !nonBlank(title) || !nonBlank(summary) || !nonBlank(sortTime) ||
        item.explanationSource !== "l3-aggregation" || item.disclaimer !== disclaimer ||
        taskEntities.get(task.id) !== JSON.stringify(task) || rawGroups.length > 5 ||
        !utcTimestamp(sortTime) || !evidence || JSON.stringify(task.evidence) !== JSON.stringify(evidence)) return undefined;
    const groups: HistoryView["data"]["story"][number]["groups"] = [];
    for (const rawGroup of rawGroups) {
      const group = record(rawGroup); const relation = group?.relation;
      const paths = Array.isArray(group?.paths) ? group.paths.map((entry) => text(entry)) : [];
      const concepts = Array.isArray(group?.concepts) ? group.concepts.map((entry) => strictText(entry, 240) ?? "") : [];
      const rawGroupEvidence = Array.isArray(group?.evidence) ? group.evidence : [];
      const groupEvidence = rawGroupEvidence.length === 0 ? [] : historyEvidence(rawGroupEvidence, 3, true);
      if (!group || !["read", "modified", "tested", "searched", "affects"].includes(String(relation)) ||
          paths.length > 12 || paths.some((path) => !isCanonicalRelativePath(path)) ||
          concepts.length > 12 || concepts.some((concept) => !nonBlank(concept)) ||
          !groupEvidence) return undefined;
      groups.push({ relation: relation as "read" | "modified" | "tested" | "searched" | "affects", paths, concepts, evidence: groupEvidence });
    }
    const timelineTask = timeline.find((entry) => entry.entity.id === task.id)!;
    const expectedGroups = new Map<string, { paths: string[]; concepts: string[]; evidence: Evidence[] }>();
    for (const relationItem of timelineTask.relations) {
      const expected = expectedGroups.get(relationItem.relation.relation) ?? { paths: [], concepts: [], evidence: [] };
      const targets = relationItem.entity.kind === "file" ? expected.paths : expected.concepts;
      const target = relationItem.entity.path ?? relationItem.entity.label;
      if (!targets.includes(target)) targets.push(target);
      if (!expected.evidence.some((entry) => JSON.stringify(entry) === JSON.stringify(relationItem.evidence[0])) && expected.evidence.length < 3) expected.evidence.push(relationItem.evidence[0]!);
      expectedGroups.set(relationItem.relation.relation, expected);
    }
    for (const expected of expectedGroups.values()) {
      expected.paths.sort((left, right) => left.localeCompare(right));
      expected.concepts.sort((left, right) => left.localeCompare(right));
    }
    if (new Set(groups.map((group) => group.relation)).size !== groups.length || groups.length !== expectedGroups.size || groups.some((group) => {
      const expected = expectedGroups.get(group.relation);
      return !expected || JSON.stringify(group.paths) !== JSON.stringify(expected.paths) ||
        JSON.stringify(group.concepts) !== JSON.stringify(expected.concepts) ||
        JSON.stringify(group.evidence) !== JSON.stringify(expected.evidence);
    })) return undefined;
    story.push({ id, title, summary, sortTime, task, groups, explanationSource: "l3-aggregation", disclaimer, evidence });
  }
  if (JSON.stringify(story.map((item) => item.task.id)) !== JSON.stringify(timeline.slice(0, 12).map((item) => item.entity.id)) ||
      new Set(story.map((item) => item.id)).size !== story.length || story.some((item, index) =>
        item.title !== timeline[index]?.entity.label || item.summary !== timeline[index]?.summary || item.sortTime !== timeline[index]?.sortTime)) return undefined;
  const taskDetail = common.data.taskDetail === null ? null : (timeline.find((item) =>
    JSON.stringify(item) === JSON.stringify(common.data.taskDetail)) ?? undefined);
  if (common.data.taskDetail !== null && !taskDetail) return undefined;
  if (stats.displayedTasks !== timeline.length || stats.displayedRelations !== timeline.reduce((total, item) => total + item.relations.length, 0)) return undefined;
  return { schemaVersion: SCHEMA_VERSION, view: "history", project: common.project, sourceState: common.sourceState,
    data: { layout: "task-timeline-story", revision, selectedMode, disclaimer, timeline, story,
      taskDetail: taskDetail ?? null, stats: { files: stats.files, tasks: stats.tasks, displayedTasks: stats.displayedTasks, displayedRelations: stats.displayedRelations } },
    warnings: common.warnings };
}

export function parseAgentNaviView(value: unknown): AgentNaviView | undefined {
  return parseContextView(value) ?? parseRepositoryOverviewView(value) ?? parseRepositoryTourView(value) ??
    parseArchitectureView(value) ?? parseFlowView(value) ?? parseImpactView(value) ?? parseHistoryView(value);
}

export function parseTaskQuery(value: unknown): string | undefined {
  const input = record(value);
  const query = text(input?.query).trim();
  return query ? safeTaskQuery(query) : undefined;
}

export function parseRequestedView(value: unknown): AgentNaviView["view"] | undefined {
  const input = record(value);
  return input?.view === "context" || input?.view === "repo-overview" || input?.view === "repo-tour" ||
    input?.view === "architecture" || input?.view === "flow" || input?.view === "impact" || input?.view === "history"
    ? input.view
    : undefined;
}

export function parsePublicError(value: unknown): PublicError | undefined {
  const error = record(value);
  const code = text(error?.code);
  const message = PUBLIC_ERROR_MESSAGES[code];
  return code && message ? { code, message } : undefined;
}
