"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, type AlertItem } from "@/lib/api";
import { useStore } from "@/lib/store";
import { fmtDate, LEVEL_LABEL } from "@/lib/format";
import { Icon } from "./Icon";

// Панель уведомлений: выпадает из колокольчика в правом верхнем углу, страница под ней остаётся рабочей.
// Уведомления, которые попали в видимую часть панели, помечаются прочитанными сразу (оптимистично),
// а подсветка «новое» снимается чуть позже закрытия — чтобы было видно, что пришло.

const HIGHLIGHT_AFTER_CLOSE_MS = 1200;
type LoadState = "loading" | "ready" | "error";

export function NotificationsBell() {
  const s = useStore();
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<LoadState>("loading");
  const [items, setItems] = useState<AlertItem[]>([]);
  const [fresh, setFresh] = useState<Set<number>>(new Set()); // прочитаны в эту сессию, подсветка ещё держится
  const btn = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const clearTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const queued = useRef(new Set<number>());
  const failed = useRef(new Set<number>()); // отметка не дошла до сервера — до следующего открытия не повторяем
  const flushTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setState("loading");
    try {
      const r = await api.get<{ items: AlertItem[]; unread: number }>("/api/alerts?limit=100");
      setItems(r.items);
      setState("ready");
    } catch {
      if (!quiet) setState("error");
    }
  }, []);

  const close = useCallback((returnFocus: boolean) => {
    setOpen(false);
    if (returnFocus) btn.current?.focus();
    if (clearTimer.current) clearTimeout(clearTimer.current);
    clearTimer.current = setTimeout(() => setFresh(new Set()), HIGHLIGHT_AFTER_CLOSE_MS);
  }, []);

  const toggle = () => {
    if (open) return close(true);
    if (clearTimer.current) clearTimeout(clearTimer.current);
    setFresh(new Set());
    failed.current.clear();
    setOpen(true);
    load();
  };

  // фокус в панель при открытии; верх панели — ровно по низу шапки (на телефоне шапка в две строки)
  useEffect(() => {
    if (!open) return;
    const place = () => {
      const top = document.querySelector(".top")?.getBoundingClientRect().bottom;
      if (panel.current && top !== undefined) panel.current.style.top = `${Math.max(0, Math.round(top))}px`;
    };
    place();
    panel.current?.focus();
    window.addEventListener("resize", place);
    return () => window.removeEventListener("resize", place);
  }, [open]);

  // Esc и клик вне панели
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopPropagation(); // закрываем только панель, карточку инцидента под ней не трогаем
      close(true);
    };
    const onDown = (e: PointerEvent) => {
      const t = e.target as Node;
      if (panel.current?.contains(t) || btn.current?.contains(t)) return;
      close(false); // кликнули по странице — фокус остаётся там, куда кликнули
    };
    window.addEventListener("keydown", onKey, true);
    document.addEventListener("pointerdown", onDown, true);
    return () => {
      window.removeEventListener("keydown", onKey, true);
      document.removeEventListener("pointerdown", onDown, true);
    };
  }, [open, close]);

  // пришли новые уведомления, пока панель открыта, — тихо обновляем список
  const prevUnread = useRef(s.unreadAlerts);
  useEffect(() => {
    if (open && s.unreadAlerts > prevUnread.current) load(true);
    prevUnread.current = s.unreadAlerts;
  }, [s.unreadAlerts, open, load]);

  // отправка отметок пачкой: оптимистично в интерфейсе, при ошибке — откат
  const flush = useCallback(async () => {
    const ids = [...queued.current];
    queued.current.clear();
    if (!ids.length) return;
    const ok = await s.markAlertsRead(ids);
    if (!ok) {
      ids.forEach((id) => failed.current.add(id));
      const back = new Set(ids);
      setItems((all) => all.map((a) => (back.has(a.id) ? { ...a, read: false, readAt: null } : a)));
    }
  }, [s]);

  const markVisible = useCallback((all: number[]) => {
    const ids = all.filter((id) => !failed.current.has(id));
    if (!ids.length) return;
    const now = new Date().toISOString();
    const set = new Set(ids);
    setItems((all) => all.map((a) => (set.has(a.id) ? { ...a, read: true, readAt: now } : a)));
    setFresh((f) => new Set([...f, ...ids]));
    ids.forEach((id) => queued.current.add(id));
    if (flushTimer.current) clearTimeout(flushTimer.current);
    flushTimer.current = setTimeout(flush, 250);
  }, [flush]);

  // какие непрочитанные видны в области прокрутки панели
  useEffect(() => {
    if (!open || state !== "ready" || !list.current) return;
    const root = list.current;
    const seen: number[] = [];
    const io = new IntersectionObserver((entries) => {
      for (const e of entries) {
        if (!e.isIntersecting) continue;
        const id = Number((e.target as HTMLElement).dataset.id);
        seen.push(id);
        io.unobserve(e.target);
      }
      markVisible(seen.splice(0));
    }, { root, threshold: 0.6 });
    root.querySelectorAll<HTMLElement>("[data-unread='1']").forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, [open, state, items, markVisible]);

  // закрыли панель, пока отметки ещё ждут отправки, — отправляем сразу
  useEffect(() => {
    if (!open && queued.current.size) {
      if (flushTimer.current) clearTimeout(flushTimer.current);
      flush();
    }
  }, [open, flush]);

  const unread = s.unreadAlerts;
  return (
    <>
      <button ref={btn} className={`bell ${open ? "is-open" : ""}`} onClick={toggle} aria-expanded={open}
        aria-controls="notif-panel" aria-haspopup="dialog"
        aria-label={unread ? `Уведомления: ${unread} непрочитанных` : "Уведомления"} title="Уведомления">
        <Icon name="bell" size={30} />
        {unread > 0 && <b>{unread > 99 ? "99+" : unread}</b>}
      </button>
      {open && (
        <div id="notif-panel" ref={panel} className="notif" role="dialog" aria-label="Уведомления" tabIndex={-1}>
          <header className="notif__head">
            <h2>Уведомления</h2>
            {unread > 0 && <span className="notif__unread">{unread} новых</span>}
            <span className="grow" />
            <button className="icon-btn" onClick={() => close(true)} aria-label="Закрыть уведомления">
              <Icon name="close" />
            </button>
          </header>
          <div className="notif__list" ref={list}>
            {state === "loading" && (
              <div className="notif__state" aria-live="polite">
                {[0, 1, 2].map((i) => <div key={i} className="notif__skeleton" />)}
              </div>
            )}
            {state === "error" && (
              <div className="notif__state notif__state--msg" role="alert">
                <Icon name="warning" size={22} />
                <p>Не удалось загрузить уведомления</p>
                <button className="btn btn--sm" onClick={() => load()}>Повторить</button>
              </div>
            )}
            {state === "ready" && items.length === 0 && (
              <div className="notif__state notif__state--msg">
                <Icon name="bell" size={22} />
                <p>Уведомлений пока нет</p>
                <span className="muted small">Они появятся, когда инцидент попадёт в колонку с оповещением</span>
              </div>
            )}
            {state === "ready" && items.map((a) => {
              const isNew = !a.read || fresh.has(a.id);
              const cols = [...new Set(a.columns)].join(", ") || a.column;
              return (
                <button key={a.id} data-id={a.id} data-unread={a.read ? "0" : "1"}
                  className={`ncard ncard--${a.level} ${isNew ? "is-new" : ""} ${a.inColumn ? "" : "is-gone"}`}
                  onClick={() => { s.openIncident(a.incidentId); close(false); }}>
                  <span className="ncard__top">
                    <span className="ncard__level">{LEVEL_LABEL[a.level]}</span>
                    {isNew && <i className="ncard__dot" aria-label="новое" />}
                    <span className="grow" />
                    <time className="ncard__time">{fmtDate(a.createdAt)}</time>
                  </span>
                  <span className="ncard__title">{a.header || "Без заголовка"}</span>
                  <span className="ncard__id">{a.incidentId}</span>
                  <span className="ncard__meta" title={cols}>
                    {cols} · {a.inColumn ? "сейчас в колонке" : "уже не в колонке"}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      )}
    </>
  );
}
