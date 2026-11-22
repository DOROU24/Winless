"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { api, type AlertItem, type Board, type FieldDef, type Settings, type Snapshot, type UploadSummary } from "./api";

interface Toast {
  id: number;
  kind: "ok" | "error" | "info";
  text: string;
}

interface Store {
  fields: FieldDef[];
  fieldMap: Record<string, FieldDef>;
  boards: Board[];
  activeBoardId: number | null;
  activeBoard: Board | null;
  settings: Settings | null;
  snapshots: Snapshot[];
  lastUpload: UploadSummary | null;
  version: number; // растёт после любых изменений данных — по нему перезапрашивается раскладка
  openIncidentId: string | null;
  activeAlerts: number;
  unreadAlerts: number; // непрочитанные уведомления — число на колокольчике
  markAlertsRead: (ids: number[]) => Promise<boolean>;
  ai: AiProgress | null;
  popups: AlertItem[];
  critical: CriticalItem[]; // критические оповещения за последние полчаса, свежие первыми — для большого баннера
  soundReady: boolean; // браузер разрешил звук (после первого касания страницы)
  toasts: Toast[];
  uploading: boolean;
  setActiveBoard: (id: number) => Promise<void>;
  reloadBoards: () => Promise<void>;
  reloadMeta: () => Promise<void>;
  bump: () => void;
  openIncident: (id: string | null) => void;
  upload: (files: FileList | File[]) => Promise<void>;
  toast: (text: string, kind?: Toast["kind"]) => void;
  dismissPopup: (id: number) => void;
}

export interface CriticalItem {
  alert: AlertItem;
  at: number; // когда пришло в этот браузер
}

/** Сколько живёт память о критическом оповещении: баннер, затем строка «последний критический». */
export const CRITICAL_KEEP_MS = 30 * 60 * 1000;

export interface AiProgress {
  enabled: boolean;
  running: boolean;
  queuedUploads: number;
  total: number | null;
  done: number | null;
  errors: number | null;
}

const Ctx = createContext<Store | null>(null);

export function useStore() {
  const s = useContext(Ctx);
  if (!s) throw new Error("StoreProvider missing");
  return s;
}

export function StoreProvider({ children }: { children: React.ReactNode }) {
  const [fields, setFields] = useState<FieldDef[]>([]);
  const [boards, setBoards] = useState<Board[]>([]);
  const [activeBoardId, setActiveBoardId] = useState<number | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [lastUpload, setLastUpload] = useState<UploadSummary | null>(null);
  const [version, setVersion] = useState(0);
  const [openIncidentId, setOpen] = useState<string | null>(null);
  const [activeAlerts, setActiveAlerts] = useState(0);
  const [unreadAlerts, setUnreadAlerts] = useState(0);
  const pendingRead = useRef(0); // отметки, отправленные на сервер, но ещё не подтверждённые
  // Время прошлого опроса оповещений этим браузером: сервер отдаёт всё, что сработало после него.
  // Так окно и звук получает каждая открытая вкладка КООРАДАРа, а не только тот, кто загрузил файл.
  const alertsSince = useRef<string | null>(null);
  const [ai, setAi] = useState<AiProgress | null>(null);
  const aiBusy = useRef(false);
  const [popups, setPopups] = useState<AlertItem[]>([]);
  const [critical, setCritical] = useState<CriticalItem[]>([]);
  const [soundReady, setSoundReady] = useState(false);
  const lastSnapshot = useRef<number | null | undefined>(undefined);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [uploading, setUploading] = useState(false);
  const audio = useRef<HTMLAudioElement | null>(null);
  const toastId = useRef(0);

  const toast = useCallback((text: string, kind: Toast["kind"] = "ok") => {
    const id = ++toastId.current;
    setToasts((t) => [...t, { id, kind, text }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "error" ? 8000 : 4500);
  }, []);

  const reloadBoards = useCallback(async () => {
    const r = await api.get<{ boards: Board[]; activeBoardId: number | null }>("/api/boards");
    setBoards(r.boards);
    setActiveBoardId(r.activeBoardId ?? r.boards[0]?.id ?? null);
  }, []);

  const reloadMeta = useCallback(async () => {
    const [f, st, sn] = await Promise.all([
      api.get<FieldDef[]>("/api/fields"),
      api.get<Settings>("/api/settings"),
      api.get<Snapshot[]>("/api/snapshots"),
    ]);
    setFields(f);
    setSettings(st);
    setSnapshots(sn);
  }, []);

  const bump = useCallback(() => setVersion((v) => v + 1), []);

  const pollAlerts = useCallback(async () => {
    try {
      if (alertsSince.current === null) {
        try {
          const saved = localStorage.getItem("mts.alertsSince");
          alertsSince.current = saved && !Number.isNaN(Date.parse(saved)) ? saved : "";
        } catch { alertsSince.current = ""; }
      }
      const q = alertsSince.current ? `?since=${encodeURIComponent(alertsSince.current)}` : "";
      const r = await api.get<{ due: AlertItem[]; active: number; unread: number; now: string; ai: AiProgress; snapshotId?: number | null }>(
        `/api/alerts/due${q}`);
      if (typeof r.now === "string" && r.now) {
        alertsSince.current = r.now;
        try { localStorage.setItem("mts.alertsSince", r.now); } catch { /* без сохранения */ }
      }
      // кто-то загрузил новую выгрузку — перерисовать доску сразу, не дожидаясь планового обновления
      if (lastSnapshot.current !== undefined && r.snapshotId !== lastSnapshot.current) setVersion((v) => v + 1);
      lastSnapshot.current = r.snapshotId ?? null;
      setActiveAlerts(r.active);
      setUnreadAlerts(Math.max(0, r.unread - pendingRead.current));
      setAi(r.ai);
      const busy = !!r.ai && (r.ai.running || r.ai.queuedUploads > 0);
      if (aiBusy.current && !busy) setVersion((v) => v + 1); // ИИ закончил — перерисовать доску с новыми ai.*
      else if (busy) setVersion((v) => v + 1); // результаты пишутся по мере готовности
      aiBusy.current = busy;
      if (r.due.length) {
        // критические — в большой баннер, остальные — в обычные окна
        const crit = r.due.filter((a) => a.level === "critical");
        const rest = r.due.filter((a) => a.level !== "critical");
        if (crit.length) {
          const at = Date.now();
          setCritical((c) => {
            const known = new Set(c.map((x) => x.alert.id));
            const fresh = crit.filter((a) => !known.has(a.id)).map((alert) => ({ alert, at }));
            return [...fresh, ...c].filter((x) => at - x.at < CRITICAL_KEEP_MS).slice(0, 20);
          });
          setVersion((v) => v + 1);
        }
        if (rest.length) setPopups((p) => {
          const known = new Set(p.map((x) => x.id));
          return [...rest.filter((x) => !known.has(x.id)), ...p].slice(0, 6);
        });
        if (r.due.some((a) => a.mode === "sound")) {
          audio.current?.play().catch(() => {
            /* браузер ещё не разрешил звук — нужен первый клик по странице */
          });
        }
      }
    } catch {
      /* бэкенд недоступен — покажем при следующем действии */
    }
  }, []);

  useEffect(() => {
    if (typeof window !== "undefined" && window.location.pathname.startsWith("/login")) return; // до входа ничего не грузим
    audio.current = typeof Audio !== "undefined" ? new Audio("/alert.wav") : null;
    (async () => {
      try {
        await Promise.all([reloadBoards(), reloadMeta()]);
      } catch (e) {
        toast((e as Error).message, "error");
      }
      pollAlerts();
    })();
    // экран на стене смотрят без рук: оповещения проверяем каждые 10 секунд, доску пересчитываем раз в 30
    const poll = setInterval(pollAlerts, 10000);
    const t = setInterval(() => {
      setVersion((v) => v + 1); // правила по времени идут и без загрузок
      setCritical((c) => c.filter((x) => Date.now() - x.at < CRITICAL_KEEP_MS));
    }, 30000);
    // звук браузер разрешает только после первого касания страницы
    const unlock = () => {
      const a = audio.current;
      if (!a) return;
      a.muted = true; // беззвучный пробный запуск: браузер запоминает разрешение
      a.play().then(() => { a.pause(); a.currentTime = 0; a.muted = false; setSoundReady(true); })
        .catch(() => { a.muted = false; });
    };
    window.addEventListener("pointerdown", unlock, { once: true });
    window.addEventListener("keydown", unlock, { once: true });
    // пока ИИ анализирует, опрашиваем чаще, чтобы прогресс и результаты появлялись быстро
    const fast = setInterval(() => {
      if (aiBusy.current) pollAlerts();
    }, 4000);
    return () => {
      clearInterval(t);
      clearInterval(poll);
      clearInterval(fast);
      window.removeEventListener("pointerdown", unlock);
      window.removeEventListener("keydown", unlock);
    };
  }, [reloadBoards, reloadMeta, pollAlerts, toast]);

  const setActiveBoard = useCallback(async (id: number) => {
    setActiveBoardId(id);
    await api.patch("/api/settings", { activeBoardId: id });
  }, []);

  const upload = useCallback(
    async (files: FileList | File[]) => {
      const list = Array.from(files).sort((a, b) => a.name.localeCompare(b.name));
      if (!list.length) return;
      setUploading(true);
      try {
        for (const f of list) {
          const r = await api.upload(f);
          setLastUpload(r);
          const parts = [`новых ${r.created}`, `изменённых ${r.changed}`, `пропало ${r.disappeared}`];
          toast(`${f.name}: ${parts.join(", ")}`);
          r.warnings.slice(0, 3).forEach((w) => toast(w, "info"));
        }
        await reloadMeta();
        bump();
        aiBusy.current = true; // анализ ИИ запущен в фоне — начинаем частый опрос
        await pollAlerts();
      } catch (e) {
        toast((e as Error).message, "error");
      } finally {
        setUploading(false);
      }
    },
    [reloadMeta, bump, pollAlerts, toast],
  );

  // Отметить уведомления прочитанными: бейдж уменьшается сразу, при ошибке сервера — откат и сообщение
  const markAlertsRead = useCallback(async (ids: number[]) => {
    if (!ids.length) return true;
    pendingRead.current += ids.length;
    setUnreadAlerts((u) => Math.max(0, u - ids.length));
    try {
      const r = await api.post<{ unread: number }>("/api/alerts/read", { ids });
      pendingRead.current -= ids.length;
      setUnreadAlerts(Math.max(0, r.unread - pendingRead.current));
      return true;
    } catch (e) {
      pendingRead.current -= ids.length;
      setUnreadAlerts((u) => u + ids.length);
      toast(`Не удалось отметить уведомления прочитанными: ${(e as Error).message}`, "error");
      return false;
    }
  }, [toast]);

  // Закрытие окна только убирает его с экрана, в базу ничего не пишется.
  const dismissPopup = useCallback((id: number) => setPopups((p) => p.filter((x) => x.id !== id)), []);

  // Окно оповещения само скрывается через 6 секунд после появления
  const POPUP_MS = 6000;
  const popupTimers = useRef(new Map<number, ReturnType<typeof setTimeout>>());
  useEffect(() => {
    const timers = popupTimers.current;
    for (const p of popups) {
      if (!timers.has(p.id)) timers.set(p.id, setTimeout(() => {
        timers.delete(p.id);
        setPopups((all) => all.filter((x) => x.id !== p.id));
      }, POPUP_MS));
    }
    for (const [id, t] of timers) {
      if (!popups.some((p) => p.id === id)) { clearTimeout(t); timers.delete(id); }
    }
  }, [popups]);



  const fieldMap = useMemo(() => Object.fromEntries(fields.map((f) => [f.key, f])), [fields]);
  const activeBoard = boards.find((b) => b.id === activeBoardId) ?? null;

  const value: Store = {
    fields, fieldMap, boards, activeBoardId, activeBoard, settings, snapshots, lastUpload, version,
    openIncidentId, activeAlerts, unreadAlerts, markAlertsRead, ai, popups, critical, soundReady, toasts, uploading, setActiveBoard, reloadBoards, reloadMeta, bump,
    openIncident: setOpen, upload, toast, dismissPopup,
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
