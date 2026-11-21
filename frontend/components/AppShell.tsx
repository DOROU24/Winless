"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { api, type Board } from "@/lib/api";
import { useStore } from "@/lib/store";
import { LEVEL_LABEL, plural, relTime } from "@/lib/format";
import { Icon } from "./Icon";
import { Assistant } from "./Assistant";
import { IncidentDrawer } from "./IncidentDrawer";
import { Modal } from "./Modal";

const NAV = [
  { href: "/", label: "Доска", icon: "board" },
  { href: "/graph", label: "Граф связей", icon: "graph" },
  { href: "/settings", label: "Настройки", icon: "settings" },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const s = useStore();
  const [dragging, setDragging] = useState(false);
  const dragDepth = useRef(0);

  useEffect(() => {
    const onEnter = (e: DragEvent) => {
      if (!e.dataTransfer?.types.includes("Files")) return;
      dragDepth.current++;
      setDragging(true);
    };
    const onLeave = () => {
      dragDepth.current = Math.max(0, dragDepth.current - 1);
      if (!dragDepth.current) setDragging(false);
    };
    const onOver = (e: DragEvent) => {
      if (e.dataTransfer?.types.includes("Files")) e.preventDefault();
    };
    const onDrop = (e: DragEvent) => {
      if (!e.dataTransfer?.files.length) return;
      e.preventDefault();
      dragDepth.current = 0;
      setDragging(false);
      s.upload(e.dataTransfer.files);
    };
    window.addEventListener("dragenter", onEnter);
    window.addEventListener("dragleave", onLeave);
    window.addEventListener("dragover", onOver);
    window.addEventListener("drop", onDrop);
    return () => {
      window.removeEventListener("dragenter", onEnter);
      window.removeEventListener("dragleave", onLeave);
      window.removeEventListener("dragover", onOver);
      window.removeEventListener("drop", onDrop);
    };
  }, [s.upload]); // eslint-disable-line react-hooks/exhaustive-deps

  if (path.startsWith("/wall") || path.startsWith("/itsm")) {
    return (
      <>
        {children}
        {s.openIncidentId && <IncidentDrawer id={s.openIncidentId} />}
        <Popups />
      </>
    );
  }

  return (
    <div className="shell">
      <Sidebar />
      <div className="main">
        <TopBar />
        <main className="page">{children}</main>
      </div>
      {s.openIncidentId && <IncidentDrawer id={s.openIncidentId} />}
      <Popups />
      <Toasts />
      {dragging && (
        <div className="drop-overlay" aria-hidden>
          <div className="drop-overlay__box">
            <Icon name="upload" size={28} />
            <p>Отпустите файл, чтобы загрузить снимок</p>
            <span>Можно сразу несколько: загрузим по порядку имён</span>
          </div>
        </div>
      )}
    </div>
  );
}

function Sidebar() {
  const path = usePathname();
  const router = useRouter();
  const s = useStore();
  const [creating, setCreating] = useState(false);

  return (
    <aside className="side">
      <div className="side__brand">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img className="side__logo" src="/brand/logo.svg" alt="МТС" width={32} height={32} />
        <span>Пульт координатора</span>
      </div>
      <nav className="side__nav" aria-label="Разделы">
        {NAV.map((n) => (
          <Link key={n.href} href={n.href} className={`side__link ${path === n.href ? "is-active" : ""}`}>
            <Icon name={n.icon} />
            <span>{n.label}</span>
          </Link>
        ))}
        <a href="/wall" target="_blank" rel="noreferrer" className="side__link">
          <Icon name="wall" />
          <span>Экран на стене</span>
          <Icon name="external" size={13} className="side__ext" />
        </a>
      </nav>
      <div className="side__section">
        <div className="side__head">
          <span>Доски</span>
          <button className="icon-btn" onClick={() => setCreating(true)} aria-label="Новая доска" title="Новая доска">
            <Icon name="plus" />
          </button>
        </div>
        <ul className="side__boards">
          {s.boards.map((b) => (
            <li key={b.id}>
              <button
                className={`side__board ${b.id === s.activeBoardId ? "is-active" : ""}`}
                onClick={async () => {
                  await s.setActiveBoard(b.id);
                  if (path !== "/") router.push("/");
                }}
              >
                <Icon name="folder" size={14} />
                <span>{b.name}</span>
                <em>{b.columns?.length ?? 0}</em>
              </button>
            </li>
          ))}
        </ul>
      </div>
      <SnapshotInfo />
      {creating && <NewBoardDialog onClose={() => setCreating(false)} />}
    </aside>
  );
}

function SnapshotInfo() {
  const { snapshots } = useStore();
  const last = snapshots[0];
  if (!last) return <div className="side__foot">Данных пока нет. Загрузите JSON-выгрузку из ITSM.</div>;
  return (
    <div className="side__foot">
      <div>
        Загрузок: {snapshots.length}, последняя {relTime(last.uploadedAt)}
      </div>
      <div className="side__file" title={last.filename}>
        {last.filename}
      </div>
    </div>
  );
}

export function NewBoardDialog({ onClose }: { onClose: () => void }) {
  const s = useStore();
  const router = useRouter();
  const [templates, setTemplates] = useState<{ key: string; name: string; description: string; columns: string[] }[]>([]);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api.get<typeof templates>("/api/templates").then(setTemplates);
  }, []);
  const create = async (key: string) => {
    setBusy(true);
    try {
      const b = await api.post<Board>("/api/boards/from-template", { template: key, name: name.trim() || undefined });
      await s.reloadBoards();
      await s.setActiveBoard(b.id);
      s.toast(`Доска «${b.name}» создана`);
      onClose();
      router.push("/");
    } catch (e) {
      s.toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title="Новая доска" onClose={onClose}>
      <label className="field">
        <span>Название</span>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Как в шаблоне" autoFocus />
      </label>
      <div className="tpl-list">
        {templates.map((t) => (
          <button key={t.key} className="tpl" onClick={() => create(t.key)} disabled={busy}>
            <strong>{t.name}</strong>
            <span>{t.description}</span>
            {t.columns.length > 0 && <em>{t.columns.join(", ")}</em>}
          </button>
        ))}
      </div>
    </Modal>
  );
}

function TopBar() {
  const s = useStore();
  const input = useRef<HTMLInputElement>(null);
  const last = s.lastUpload;
  const snap = s.snapshots[0];
  return (
    <header className="top">
      <div className="top__status">
        {snap ? (
          <>
            <span>
              Снимок {relTime(snap.uploadedAt)}, {snap.count} {plural(snap.count, "инцидент", "инцидента", "инцидентов")}
            </span>
            {(last ?? snap) && (
              <span className="top__delta">
                <b className="d-new">+{(last ?? snap).created ?? 0}</b> новых
                <b className="d-chg">{(last ?? snap).changed ?? 0}</b> изменено
                <b className="d-gone">{(last ?? snap).disappeared ?? 0}</b> пропало
              </span>
            )}
          </>
        ) : (
          <span className="muted">Снимков нет</span>
        )}
        {s.ai?.enabled && (s.ai.running || s.ai.queuedUploads > 0) && (
          <span className="chip chip--ai" title="Языковая модель анализирует новые и изменённые инциденты">
            <span className="ai-dot" aria-hidden />
            ИИ анализирует{s.ai.total ? `: ${s.ai.done ?? 0} из ${s.ai.total}` : "…"}
          </span>
        )}
      </div>
      <div className="top__actions">
        <Assistant />
        <input
          ref={input}
          type="file"
          accept=".json,application/json"
          multiple
          hidden
          onChange={(e) => {
            if (e.target.files) s.upload(e.target.files);
            e.target.value = "";
          }}
        />
        <button className="btn btn--primary" onClick={() => input.current?.click()} disabled={s.uploading}>
          <Icon name="upload" />
          {s.uploading ? "Загружаю…" : "Загрузить JSON"}
        </button>
        <Link href="/alerts" className="bell" aria-label={`Оповещения по инцидентам в колонках: ${s.activeAlerts}`}
          title="Оповещения по инцидентам, которые сейчас лежат в колонках">
          <Icon name="bell" size={18} />
          {s.activeAlerts > 0 && <b>{s.activeAlerts > 99 ? "99+" : s.activeAlerts}</b>}
        </Link>
      </div>
    </header>
  );
}

function Popups() {
  const s = useStore();
  if (!s.popups.length) return null;
  return (
    <div className="popups" role="alert" aria-live="assertive">
      {s.popups.map((a) => (
        <div key={a.id} className={`popup popup--${a.level}`}>
          <div className="popup__head">
            <span className="popup__level">{LEVEL_LABEL[a.level]}</span>
            <span className="popup__col">{a.columns.join(", ") || a.column}</span>
            <button className="icon-btn" onClick={() => s.dismissPopup(a.id)} aria-label="Закрыть окно">
              <Icon name="close" size={14} />
            </button>
          </div>
          <button className="popup__body" onClick={() => s.openIncident(a.incidentId)}>
            <span className="mono">{a.incidentId}</span>
            <span>{a.header}</span>
          </button>
          {a.repeatMin && (
            <p className="popup__foot muted">Повтор каждые {a.repeatMin} мин, пока инцидент в колонке</p>
          )}
        </div>
      ))}
    </div>
  );
}

function Toasts() {
  const { toasts } = useStore();
  return (
    <div className="toasts" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className={`toast toast--${t.kind}`}>
          {t.text}
        </div>
      ))}
    </div>
  );
}
