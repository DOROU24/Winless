import { useEffect, useState } from "react";
import type { CardData, LayoutColumn } from "./api";

// Всё, что помогает заметить важное без нажатий: свежие инциденты, порядок колонок.

/** Сколько минут инцидент считается «только что пришедшим»: подсветка и место наверху колонки. */
export const FRESH_MIN = 15;

export function minutesSince(iso: string | null | undefined, now = Date.now()): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isNaN(t) ? null : Math.max(0, (now - t) / 60000);
}

export function isFresh(c: CardData, now = Date.now(), freshMin = FRESH_MIN): boolean {
  const m = minutesSince(c.firstSeenAt, now);
  return c.isNew && m !== null && m < freshMin;
}

/** «пришёл 3 мин назад» — для свежих, «новый» — для остальных новых. */
export function newLabel(c: CardData, now = Date.now(), freshMin = FRESH_MIN): string {
  const m = minutesSince(c.firstSeenAt, now);
  if (m === null || m >= freshMin) return "новый";
  return m < 1 ? "только что" : `пришёл ${Math.floor(m)} мин назад`;
}

/** Мигание без рук: какие задачи мигают и как. Значения по умолчанию — как просили эксперты. */
export const BLINK_DEFAULTS = { stuckMin: 10, blink: "strong" } as const;
export type BlinkOpts = { stuckMin: number; blink: "strong" | "soft" };
export function blinkOf(w: { stuckMin?: number; blink?: "strong" | "soft" } | null | undefined): BlinkOpts {
  return { stuckMin: w?.stuckMin ?? BLINK_DEFAULTS.stuckMin, blink: w?.blink ?? BLINK_DEFAULTS.blink };
}

/** Критическая задача, которую не распределили: в колонке «критический» и в «Назначен» дольше stuckMin минут. */
export function isStuck(c: CardData, level: string, o: BlinkOpts): boolean {
  return o.stuckMin > 0 && level === "critical" && c.idleMin != null && c.idleMin >= o.stuckMin;
}

/** Колонки с задачами — слева в своём порядке, пустые — в конце. */
export function nonEmptyFirst<T extends Pick<LayoutColumn, "total">>(cols: T[]): T[] {
  return [...cols.filter((c) => c.total > 0), ...cols.filter((c) => c.total === 0)];
}

/** Только что пришедшие — наверх колонки (самые свежие первыми), остальные — по сортировке колонки. */
export function freshFirst(cards: CardData[], now = Date.now(), freshMin = FRESH_MIN): CardData[] {
  const fresh = cards.filter((c) => isFresh(c, now, freshMin))
    .sort((a, b) => arrived(b) - arrived(a) || (b.priorityId ?? 0) - (a.priorityId ?? 0));
  return fresh.length ? [...fresh, ...cards.filter((c) => !isFresh(c, now, freshMin))] : cards;
}

const arrived = (c: CardData) => Date.parse(c.firstSeenAt ?? "") || 0;
const LEVEL_RANK = { critical: 2, elevated: 1, info: 0 } as const;

/** Задачи в плитке экрана: свежие сверху / по приоритету (при равном — свежие выше) / как в колонке. */
export function orderCards(cards: CardData[], mode: "fresh" | "priority" | "column", now: number, freshMin: number): CardData[] {
  if (mode === "column") return cards;
  if (mode === "priority") {
    return [...cards].sort((a, b) => (b.priorityId ?? 0) - (a.priorityId ?? 0) || arrived(b) - arrived(a));
  }
  return freshFirst(cards, now, freshMin);
}

/** Самая свежая задача в колонке и самый высокий приоритет в ней — ключи порядка плиток. */
function newest(c: LayoutColumn) { return c.cards.reduce((m, k) => Math.max(m, arrived(k)), 0); }
function topPriority(c: LayoutColumn) { return c.cards.reduce((m, k) => Math.max(m, k.priorityId ?? 0), 0); }

/**
 * Порядок плиток экрана. fresh — сначала колонка, куда задача пришла позже всех, при равенстве — где выше приоритет;
 * priority — наоборот; layout — как расставлены вручную. Последний ключ всегда — место в раскладке.
 */
export function orderTiles(cols: LayoutColumn[], mode: "fresh" | "priority" | "layout",
  byPlace: (a: LayoutColumn, b: LayoutColumn) => number): LayoutColumn[] {
  const prio = (a: LayoutColumn, b: LayoutColumn) =>
    topPriority(b) - topPriority(a) || LEVEL_RANK[b.level] - LEVEL_RANK[a.level];
  const fresh = (a: LayoutColumn, b: LayoutColumn) => newest(b) - newest(a);
  return [...cols].sort((a, b) =>
    mode === "fresh" ? fresh(a, b) || prio(a, b) || byPlace(a, b)
      : mode === "priority" ? prio(a, b) || fresh(a, b) || byPlace(a, b)
        : byPlace(a, b));
}

/** Текущее время, которое обновляется само: подписи «N мин назад» и подсветка гаснут без перезагрузки. */
export function useNow(stepMs = 30000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), stepMs);
    return () => clearInterval(t);
  }, [stepMs]);
  return now;
}
