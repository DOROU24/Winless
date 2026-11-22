"use client";

import { useEffect, useRef, useState } from "react";
import { useStore } from "@/lib/store";
import { Icon } from "./Icon";

// Кнопка «Загрузить JSON» с меню: файл с компьютера, готовые выгрузки тестового стенда и очистка данных для демо.

// Файлы лежат в frontend/public/demo. Порядок — как их выгружает ITSM каждые 15 минут.
// exported — момент выгрузки, к которому привязаны даты внутри файла (02:00 МСК = 23:00 UTC).
const DEMO_FILES = [
  { file: "01_winless_0200.json", time: "02:00", exported: "2026-11-21T23:00:00Z" },
  { file: "02_winless_0215.json", time: "02:15", exported: "2026-11-21T23:15:00Z" },
  { file: "03_winless_0230.json", time: "02:30", exported: "2026-11-21T23:30:00Z" },
  { file: "04_winless_0245.json", time: "02:45", exported: "2026-11-21T23:45:00Z" },
  { file: "05_winless_0300.json", time: "03:00", exported: "2026-11-22T00:00:00Z" },
];

const ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/;

/**
 * Даты выгрузки стенда — к текущему времени: файл выглядит так, будто ITSM выгрузил его только что.
 * Все даты сдвигаются на одно и то же время, поэтому сроки, время назначения и возраст инцидентов
 * относительно момента выгрузки не меняются. Пустые даты ITSM (0001-01-01) не трогаем.
 */
function toNow(records: Record<string, unknown>[], exported: string): Record<string, unknown>[] {
  const shift = Date.now() - Date.parse(exported);
  return records.map((r) => {
    const out: Record<string, unknown> = { ...r };
    for (const [k, v] of Object.entries(r)) {
      if (typeof v !== "string" || !ISO.test(v) || v.startsWith("0001")) continue;
      const t = Date.parse(v);
      if (!Number.isNaN(t)) out[k] = new Date(t + shift).toISOString().replace(/\.\d{3}Z$/, "Z");
    }
    return out;
  });
}

export function UploadMenu() {
  const s = useStore();
  const [open, setOpen] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const root = useRef<HTMLDivElement>(null);
  const toggle = useRef<HTMLButtonElement>(null);

  const close = (focus = false) => {
    setOpen(false);
    setConfirm(false);
    if (focus) toggle.current?.focus();
  };

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => { if (!root.current?.contains(e.target as Node)) close(); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") close(true); };
    window.addEventListener("pointerdown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("pointerdown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const loadDemo = async (file: string, exported: string) => {
    close();
    try {
      const r = await fetch(`/demo/${file}`, { cache: "no-store" });
      if (!r.ok) throw new Error(`Не нашёл файл ${file}`);
      const data = toNow(await r.json(), exported);
      await s.upload([new File([JSON.stringify(data)], file, { type: "application/json" })]);
    } catch (e) {
      s.toast((e as Error).message, "error");
    }
  };

  const clear = async () => {
    setBusy(true);
    try {
      const r = await s.clearData();
      s.toast(`База очищена: удалено загрузок ${r.snapshots}, инцидентов ${r.incidents}`);
      close();
    } catch (e) {
      s.toast(`Не удалось очистить базу: ${(e as Error).message}`, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="upload-menu" ref={root}>
      <input ref={input} type="file" accept=".json,application/json" multiple hidden
        onChange={(e) => {
          if (e.target.files) s.upload(e.target.files);
          e.target.value = "";
        }} />
      <button ref={toggle} className="btn btn--primary btn--pill" onClick={() => (open ? close() : setOpen(true))}
        disabled={s.uploading} aria-haspopup="menu" aria-expanded={open}>
        {s.uploading ? "Загружаю…" : "Загрузить JSON"}
        <Icon name="chevronDown" size={16} />
      </button>
      {open && (
        <div className="upload-menu__panel" role="menu">
          <button role="menuitem" className="upload-menu__item" onClick={() => { close(); input.current?.click(); }}>
            <Icon name="upload" size={16} /> С компьютера…
          </button>
          <p className="upload-menu__label">Выгрузки тестового стенда · даты на текущее время</p>
          {DEMO_FILES.map((d) => (
            <button key={d.file} role="menuitem" className="upload-menu__item" onClick={() => loadDemo(d.file, d.exported)}>
              <span className="upload-menu__time">{d.time}</span>
              <span className="upload-menu__file">{d.file}</span>
            </button>
          ))}
          <div className="upload-menu__sep" />
          {!confirm ? (
            <button role="menuitem" className="upload-menu__item upload-menu__item--danger" onClick={() => setConfirm(true)}>
              <Icon name="trash" size={16} /> Очистить базу
            </button>
          ) : (
            <div className="upload-menu__confirm" role="alertdialog" aria-label="Очистить базу">
              <p>Удалить все загрузки, инциденты, связи и уведомления у всех пользователей? Доски, колонки и правила останутся.</p>
              <div className="row">
                <button className="btn btn--sm" onClick={() => setConfirm(false)} disabled={busy}>Отмена</button>
                <button className="btn btn--sm btn--danger" onClick={clear} disabled={busy}>{busy ? "Очищаю…" : "Очистить"}</button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
