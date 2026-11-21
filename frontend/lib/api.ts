// Клиент API и типы. Все запросы идут на /api (Next.js проксирует их на бэкенд).

export type FieldType = "number" | "text" | "date" | "flag";

export interface FieldDef {
  key: string;
  label: string;
  type: FieldType;
  description: string;
  group: "json" | "calc" | "hist" | "ai";
  params: { name: string; label: string; kind: "field" | "values"; fieldType?: FieldType }[] | null;
  labels: Record<string, string>;
  values: (string | number)[];
  available: boolean;
  unavailableReason: string;
}

export type Level = "critical" | "elevated" | "info";
export type AlertMode = "off" | "popup" | "sound";

export interface RuleCond {
  field: string;
  cmp: string;
  value?: unknown;
  args?: Record<string, unknown>;
}
export interface RuleGroup {
  op: "and" | "or";
  children: RuleNode[];
}
export type RuleNode = RuleCond | RuleGroup;

export interface Column {
  id: number;
  boardId: number;
  position: number;
  name: string;
  color: string;
  rule: RuleNode | null;
  sortField: string | null;
  sortArgs: Record<string, unknown> | null;
  sortDir: "asc" | "desc";
  limit: number | null;
  cardFields: string[];
  level: Level;
  alert: AlertMode;
  repeatMin: number | null;
  collapsed: boolean;
  groupSimilar: boolean;
  presetIds: number[];
}

export interface Board {
  id: number;
  name: string;
  position: number;
  mode: "all" | "first";
  view: "normal" | "wall";
  hideDisappeared: boolean;
  template: string | null;
  wallLayout: WallLayout | null;
  columns?: Column[];
}

export interface WallTile {
  x: number; // колонка сетки 0–11
  y: number; // строка сетки
  w: number; // ширина в колонках сетки 1–12
  h: number; // высота в строках сетки
  hidden: boolean;
  showCards: boolean;
  maxCards: number;
}

export interface WallLayout {
  scale: number;
  showClock: boolean;
  showTitle: boolean;
  tiles: Record<string, WallTile>;
}

export interface CardData {
  id: string;
  header: string;
  priorityId: number | null;
  priority: string | null;
  stateId: number | null;
  values: Record<string, unknown>;
  why: string[];
  deadlineLeftPct: number | null;
  deadlineLeftMin: number | null;
  overdue: boolean;
  isNew: boolean;
  disappeared: boolean;
  level: Level;
  wave?: { id: string; size: number; title: string } | null;
  waveMembers?: CardData[];
  priorityUnderestimated?: boolean | null;
  overdueRisk?: boolean | null;
}

export interface LayoutColumn extends Column {
  total: number;
  hidden: number;
  ruleErrors: { path: string; message: string }[];
  cards: CardData[];
}

export interface Layout {
  board: Board;
  columns: LayoutColumn[];
  now: string;
  incidentCount: number;
}

export interface Preset {
  id: number;
  name: string;
  origin: string;
  description: string;
  rule: RuleNode;
  level: Level;
  alert: AlertMode;
  repeatMin: number | null;
  stage: number;
  count: number | null;
  noData: boolean;
  warning: string | null;
  custom: boolean;
}

export interface AlertItem {
  id: number;
  incidentId: string;
  header: string;
  priority: string | null;
  columnId: number | null;
  column: string;
  columns: string[];
  level: Level;
  mode: "popup" | "sound";
  repeatMin: number | null;
  createdAt: string;
  lastFiredAt: string | null;
  firedCount: number;
  inColumn: boolean;
}

export interface LinkItem {
  id: number;
  src: string;
  dst: string;
  type: string;
  typeLabel: string;
  directed: boolean;
  source: "auto" | "ai";
  sourceLabel: string;
  rule?: string | null;
  note: string;
  other?: string;
  otherHeader?: string;
  otherState?: string | null;
  otherDisappeared?: boolean | null;
}

export interface IncidentEvent {
  id: number;
  type: "created" | "field_changed" | "disappeared" | "reappeared";
  field: string | null;
  old: unknown;
  new: unknown;
  snapshot: string | null;
  at: string;
}

export interface IncidentDetail {
  id: string;
  data: Record<string, unknown>;
  calc: Record<string, unknown>;
  disappeared: boolean;
  why: { columnId: number; column: string; level: Level; color: string; reasons: string[] }[];
  events: IncidentEvent[];
  links: { outgoing: LinkItem[]; incoming: LinkItem[] };
  itsmUrl: string;
  wave: { id: string; size: number; title: string; members: { id: string; header: string; state: string | null }[] } | null;
  ai: { data: Record<string, unknown>; error: string | null; updatedAt: string } | null;
}

export interface Snapshot {
  id: number;
  uploadedAt: string;
  filename: string;
  count: number;
  created?: number;
  changed?: number;
  disappeared?: number;
  warnings?: string[];
}

export interface Settings {
  itsmUrlTemplate: string;
  activeBoardId: number | null;
  teams: { team: string; scope: string }[];
  aiAvailable: boolean;
  assistantMinSimilar: number;
  now: string;
}

export interface LinkRule {
  id: number;
  name: string;
  fields: string[];
  windowMin: number | null;
  filter: RuleNode | null;
  linkType: string;
  enabled: boolean;
}

export interface ApiErrorBody {
  message: string;
  errors: { path: string; message: string }[];
}

export class ApiError extends Error {
  body: ApiErrorBody;
  constructor(body: ApiErrorBody) {
    super(body.message);
    this.body = body;
  }
}

async function req<T>(method: string, url: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, headers: {} };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    init.body = JSON.stringify(body);
    (init.headers as Record<string, string>)["content-type"] = "application/json";
  }
  let r: Response;
  try {
    r = await fetch(url, init);
  } catch {
    throw new ApiError({ message: "Сервер не отвечает. Проверьте, что бэкенд запущен на порту 8000.", errors: [] });
  }
  if (!r.ok) {
    let detail: ApiErrorBody = { message: `Ошибка ${r.status}`, errors: [] };
    try {
      const j = await r.json();
      const d = j.detail ?? j;
      if (typeof d === "string") detail = { message: d, errors: [] };
      else if (d && d.message) detail = { message: d.message, errors: d.errors || [] };
    } catch {
      /* тело не JSON */
    }
    throw new ApiError(detail);
  }
  return r.json() as Promise<T>;
}

export const api = {
  get: <T>(u: string) => req<T>("GET", u),
  post: <T>(u: string, b?: unknown) => req<T>("POST", u, b ?? {}),
  patch: <T>(u: string, b: unknown) => req<T>("PATCH", u, b),
  del: <T>(u: string) => req<T>("DELETE", u),
  upload: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return req<UploadSummary>("POST", "/api/snapshots", fd);
  },
};

export interface UploadSummary {
  snapshotId: number;
  filename: string;
  count: number;
  created: number;
  changed: number;
  disappeared: number;
  reappeared: number;
  warnings: string[];
  newAlerts: number;
  autoLinks: number;
  columns: { columnId: number; name: string; count: number }[];
}
