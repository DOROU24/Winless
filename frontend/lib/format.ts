import type { FieldDef, Level } from "./api";

export const LEVEL_LABEL: Record<Level, string> = {
  critical: "Критический",
  elevated: "Повышенный",
  info: "Информационный",
};
export const LEVEL_COLOR: Record<Level, string> = {
  critical: "var(--crit)",
  elevated: "var(--elev)",
  info: "var(--info)",
};
export const ALERT_LABEL = { off: "Выключено", popup: "Всплывающее окно", sound: "Окно и звук" } as const;

const dtf = new Intl.DateTimeFormat("ru-RU", {
  day: "2-digit",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
});
const dtfFull = new Intl.DateTimeFormat("ru-RU", {
  day: "2-digit",
  month: "long",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

export function fmtDate(v: unknown, full = false): string {
  if (!v || typeof v !== "string") return "—";
  const d = new Date(v);
  if (isNaN(d.getTime())) return String(v);
  return (full ? dtfFull : dtf).format(d).replace(".", "");
}

export function fmtDuration(min: number | null | undefined): string {
  if (min === null || min === undefined || isNaN(min)) return "—";
  const neg = min < 0;
  let m = Math.abs(Math.round(min));
  const d = Math.floor(m / 1440);
  m -= d * 1440;
  const h = Math.floor(m / 60);
  m -= h * 60;
  const parts = d ? [`${d} д`, h ? `${h} ч` : ""] : h ? [`${h} ч`, m ? `${m} мин` : ""] : [`${m} мин`];
  const s = parts.filter(Boolean).join(" ");
  return neg ? `−${s}` : s;
}

export function fmtNum(v: number): string {
  return Number.isInteger(v) ? String(v) : v.toFixed(1).replace(".", ",");
}

export function fmtValue(f: FieldDef | undefined, v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (!f) return String(v);
  if (f.type === "flag") return v ? "да" : "нет";
  if (f.type === "date") return fmtDate(v);
  if (f.type === "number" && typeof v === "number") {
    if (f.key.endsWith("Min")) return fmtDuration(v);
    if (f.key.endsWith("Pct")) return `${fmtNum(v)}%`;
    const lab = f.labels[String(v)];
    return lab ? `${lab}` : fmtNum(v);
  }
  const lab = f.labels[String(v)];
  return lab ? `${v} · ${lab}` : String(v);
}

export function relTime(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "";
  const diff = (now - new Date(iso).getTime()) / 60000;
  if (diff < 1) return "только что";
  if (diff < 60) return `${Math.round(diff)} мин назад`;
  if (diff < 1440) return `${Math.round(diff / 60)} ч назад`;
  return fmtDate(iso);
}

export function plural(n: number, one: string, few: string, many: string) {
  const a = Math.abs(n) % 100;
  const b = a % 10;
  if (a > 10 && a < 20) return many;
  if (b > 1 && b < 5) return few;
  if (b === 1) return one;
  return many;
}
