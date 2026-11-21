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
  popups: AlertItem[];
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
  const [popups, setPopups] = useState<AlertItem[]>([]);
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
      const r = await api.get<{ due: AlertItem[]; active: number }>("/api/alerts/due");
      setActiveAlerts(r.active);
      if (r.due.length) {
        setPopups((p) => {
          const known = new Set(p.map((x) => x.id));
          return [...r.due.filter((x) => !known.has(x.id)), ...p].slice(0, 6);
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
    audio.current = typeof Audio !== "undefined" ? new Audio("/alert.wav") : null;
    (async () => {
      try {
        await Promise.all([reloadBoards(), reloadMeta()]);
      } catch (e) {
        toast((e as Error).message, "error");
      }
      pollAlerts();
    })();
    const t = setInterval(() => {
      pollAlerts();
      setVersion((v) => v + 1); // правила по времени идут и без загрузок
    }, 30000);
    return () => clearInterval(t);
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
        await pollAlerts();
      } catch (e) {
        toast((e as Error).message, "error");
      } finally {
        setUploading(false);
      }
    },
    [reloadMeta, bump, pollAlerts, toast],
  );

  // Закрытие окна только убирает его с экрана, в базу ничего не пишется.
  const dismissPopup = useCallback((id: number) => setPopups((p) => p.filter((x) => x.id !== id)), []);



  const fieldMap = useMemo(() => Object.fromEntries(fields.map((f) => [f.key, f])), [fields]);
  const activeBoard = boards.find((b) => b.id === activeBoardId) ?? null;

  const value: Store = {
    fields, fieldMap, boards, activeBoardId, activeBoard, settings, snapshots, lastUpload, version,
    openIncidentId, activeAlerts, popups, toasts, uploading, setActiveBoard, reloadBoards, reloadMeta, bump,
    openIncident: setOpen, upload, toast, dismissPopup,
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
