"use client";

import { Fragment, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";
import { Icon } from "./Icon";

// Помощник координатора: собирает колонки из фразы, отвечает на вопросы по истории, подсказывает команду.
// Сам ничего не меняет: изменения приходят карточкой, применяет их человек.

interface Brief { id: string; header: string | null; priority: string | null; team: string | null }
interface Proposal {
  id: string;
  kind: "create_column" | "update_column" | "create_board";
  title: string;
  boardId?: number;
  boardName?: string;
  columnId?: number;
  columnName?: string;
  ruleText?: string;
  settings?: string[];
  changes?: string[];
  count?: number;
  inColumn?: number;
  countBefore?: number;
  note?: string;
  sample?: Brief[];
  assumptions: string[];
  columns?: { name: string; ruleText: string; settings: string[]; count: number; inColumn: number }[];
  column?: Record<string, unknown>;
  board?: Record<string, unknown>;
  status?: "applied" | "dismissed";
}
interface Msg { role: "user" | "assistant"; content: string; proposals?: Proposal[]; steps?: string[]; error?: boolean }

const STORE_KEY = "mts.assistant.v1";
const EXAMPLES = [
  "Создай колонку с инцидентами, которые переоткрывали два раза и больше и которые ещё не закрыты",
  "Через какие команды инциденты чаще всего переназначаются?",
  "Что изменилось с прошлой загрузки?",
  "Какие проблемы чаще всего возвращались за последнюю неделю?",
  "Сделай доску для отслеживания возможных массовых сбоев",
];

export function askAssistant(text: string) {
  window.dispatchEvent(new CustomEvent("assistant:ask", { detail: text }));
}

export function Assistant() {
  const s = useStore();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [front, setFront] = useState(false); // поверх карточки инцидента (открыли кнопкой «Кому отдать?»)
  const askedFor = useRef<string | null>(null);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const field = useRef<HTMLTextAreaElement>(null);
  const loaded = useRef(false);

  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(STORE_KEY);
      if (raw) setMsgs(JSON.parse(raw));
    } catch { /* без сохранения — не страшно */ }
    loaded.current = true;
  }, []);
  useEffect(() => {
    if (!loaded.current) return;
    try { sessionStorage.setItem(STORE_KEY, JSON.stringify(msgs.slice(-40))); } catch { /* пусто */ }
    box.current?.scrollTo({ top: box.current.scrollHeight, behavior: "smooth" });
  }, [msgs, busy]);

  const send = async (text: string) => {
    const q = text.trim();
    if (!q || busy) return;
    const next: Msg[] = [...msgs, { role: "user", content: q }];
    setMsgs(next);
    setInput("");
    setBusy(true);
    try {
      const r = await api.post<{ reply: string; proposals: Proposal[]; steps: { label: string }[] }>(
        "/api/assistant/chat",
        {
          messages: next.filter((m) => !m.error).map((m) => ({ role: m.role, content: historyText(m) })),
          boardId: s.activeBoardId,
          incidentId: s.openIncidentId,
        },
      );
      setMsgs([...next, { role: "assistant", content: r.reply, proposals: r.proposals,
        steps: [...new Set(r.steps.map((x) => x.label))] }]);
    } catch (e) {
      setMsgs([...next, { role: "assistant", content: (e as Error).message, error: true }]);
    } finally {
      setBusy(false);
    }
  };

  // открыли другую задачу (например, номер из ответа) — она встаёт поверх помощника
  useEffect(() => {
    if (s.openIncidentId && s.openIncidentId !== askedFor.current) setFront(false);
  }, [s.openIncidentId]);

  // пока панель открыта, всплывающие оповещения сдвигаются левее, чтобы не закрывать диалог
  useEffect(() => {
    document.body.classList.toggle("assistant-open", open);
    return () => document.body.classList.remove("assistant-open");
  }, [open]);

  useEffect(() => {
    const onAsk = (e: Event) => {
      setOpen(true);
      setFront(true);
      askedFor.current = s.openIncidentId;
      send((e as CustomEvent<string>).detail);
    };
    window.addEventListener("assistant:ask", onAsk);
    return () => window.removeEventListener("assistant:ask", onAsk);
  });

  const setStatus = (pid: string, status: Proposal["status"]) =>
    setMsgs((all) => all.map((m) => m.proposals ? { ...m, proposals: m.proposals.map((p) => p.id === pid ? { ...p, status } : p) } : m));

  const apply = async (p: Proposal) => {
    try {
      const r = await api.post<{ boardId: number }>("/api/assistant/apply", { proposal: p });
      setStatus(p.id, "applied");
      await s.reloadBoards();
      if (r.boardId && r.boardId !== s.activeBoardId) await s.setActiveBoard(r.boardId);
      s.bump();
      s.toast(p.kind === "create_board" ? "Доска создана" : p.kind === "create_column" ? "Колонка добавлена" : "Колонка изменена");
      router.push("/");
    } catch (e) {
      s.toast((e as Error).message, "error");
    }
  };

  const edit = (p: Proposal) => {
    setInput(`Измени предложение «${p.title}»: `);
    setTimeout(() => field.current?.focus(), 0);
  };

  return (
    <>
      <button className={`btn assistant-launch ${open ? "is-on" : ""}`} onClick={() => setOpen(!open)}
        aria-expanded={open} aria-controls="assistant">
        <Icon name="spark" />
        Помощник
      </button>
      {open && (
        <aside id="assistant" className={`assistant ${front ? "assistant--front" : ""}`} aria-label="Помощник координатора">
          <header className="assistant__head">
            <Icon name="spark" />
            <strong>Помощник</strong>
            <span className="grow" />
            {msgs.length > 0 && (
              <button className="btn btn--sm btn--ghost" onClick={() => setMsgs([])} disabled={busy}>Новый диалог</button>
            )}
            <button className="icon-btn" onClick={() => setOpen(false)} aria-label="Закрыть помощника">
              <Icon name="close" />
            </button>
          </header>
          <div className="assistant__body" ref={box}>
            {!s.settings?.aiAvailable && (
              <p className="assistant__warn">Помощнику нужна языковая модель: задайте LLM_API_KEY в файле .env на сервере.</p>
            )}
            {msgs.length === 0 && (
              <div className="assistant__intro">
                <p>Опишите словами, что хотите видеть, или спросите про историю инцидентов. Я ничего не меняю сам: изменения
                  покажу карточкой, а применить их сможете только вы.</p>
                <div className="assistant__examples">
                  {s.openIncidentId && (
                    <button onClick={() => send(`Кому отдать ${s.openIncidentId}?`)}>Кому отдать {s.openIncidentId}?</button>
                  )}
                  {EXAMPLES.map((e) => <button key={e} onClick={() => send(e)}>{e}</button>)}
                </div>
              </div>
            )}
            {msgs.map((m, i) => (
              <div key={i} className={`amsg amsg--${m.role} ${m.error ? "amsg--error" : ""}`}>
                {m.role === "user" ? <p>{m.content}</p> : <Markdown text={m.content} />}
                {m.proposals?.map((p) => (
                  <ProposalCard key={p.id} p={p} onApply={() => apply(p)} onEdit={() => edit(p)}
                    onDismiss={() => setStatus(p.id, "dismissed")} />
                ))}
                {!!m.steps?.length && <p className="amsg__steps">{m.steps.join(" · ")}</p>}
              </div>
            ))}
            {busy && (
              <div className="amsg amsg--assistant amsg--busy" aria-live="polite">
                <span className="ai-dot" aria-hidden /> Смотрю данные…
              </div>
            )}
          </div>
          <form className="assistant__input" onSubmit={(e) => { e.preventDefault(); send(input); }}>
            <textarea ref={field} value={input} rows={2} placeholder="Например: включи звук для колонки «Критические»"
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input); } }} />
            <button className="btn btn--primary" disabled={busy || !input.trim()} aria-label="Отправить">
              <Icon name="chevron" />
            </button>
          </form>
        </aside>
      )}
    </>
  );
}

// В историю для модели уходит и то, что было в карточках: «да, только по этим трём командам» должно опираться на них
function historyText(m: Msg): string {
  if (!m.proposals?.length) return m.content;
  const extra = m.proposals.map((p) => {
    const st = p.status === "applied" ? "применено" : p.status === "dismissed" ? "отменено" : "ждёт решения";
    return `[Предложение «${p.title}» (${st}): ${p.ruleText ?? ""} ${p.changes?.join("; ") ?? ""}]`;
  });
  return `${m.content}\n${extra.join("\n")}`;
}

function ProposalCard({ p, onApply, onEdit, onDismiss }: { p: Proposal; onApply: () => void; onEdit: () => void; onDismiss: () => void }) {
  const s = useStore();
  const [busy, setBusy] = useState(false);
  const kind = { create_column: "Новая колонка", update_column: "Изменение колонки", create_board: "Новая доска" }[p.kind];
  return (
    <div className={`proposal ${p.status ? `proposal--${p.status}` : ""}`}>
      <div className="proposal__kind">{kind}{p.boardName ? ` · доска «${p.boardName}»` : ""}</div>
      <strong className="proposal__title">{p.kind === "update_column" ? `«${p.columnName}»` : p.title}</strong>
      {p.kind === "create_column" && (
        <>
          <p className="proposal__rule">{p.ruleText}</p>
          {!!p.settings?.length && <p className="muted small">{p.settings.join(" · ")}</p>}
        </>
      )}
      {p.kind === "update_column" && (
        <ul className="proposal__changes">{p.changes?.map((c, i) => <li key={i}>{c}</li>)}</ul>
      )}
      {p.kind === "create_board" && (
        <ul className="proposal__cols">
          {p.columns?.map((c, i) => (
            <li key={i}>
              <b>{c.name}</b> <span className="proposal__n">{c.inColumn}</span>
              <span className="muted small">{c.ruleText}</span>
              {!!c.settings.length && <span className="muted small">{c.settings.join(" · ")}</span>}
            </li>
          ))}
        </ul>
      )}
      {p.kind !== "create_board" && (
        <p className="proposal__count">
          {p.kind === "update_column" && p.countBefore !== undefined && p.countBefore !== p.inColumn
            ? <>В колонке было {p.countBefore}, станет <b>{p.inColumn}</b></>
            : p.count !== undefined && p.count !== p.inColumn
              ? <>Подходит {p.count}, в колонке окажется <b>{p.inColumn}</b></>
              : <>Сейчас подходит <b>{p.inColumn}</b></>}
        </p>
      )}
      {p.note && <p className="muted small">{p.note}</p>}
      {!!p.sample?.length && (
        <ul className="proposal__sample">
          {p.sample.map((x) => (
            <li key={x.id}>
              <button className="link-btn mono" onClick={() => s.openIncident(x.id)}>{x.id}</button> {x.header}
            </li>
          ))}
        </ul>
      )}
      {!!p.assumptions.length && (
        <div className="proposal__assume">
          <span>Допущения:</span>
          <ul>{p.assumptions.map((a, i) => <li key={i}>{a}</li>)}</ul>
        </div>
      )}
      {p.status ? (
        <p className="proposal__done">{p.status === "applied" ? <><Icon name="check" size={14} /> Применено</> : "Отменено"}</p>
      ) : (
        <div className="proposal__actions">
          <button className="btn btn--primary btn--sm" disabled={busy}
            onClick={async () => { setBusy(true); await onApply(); setBusy(false); }}>Применить</button>
          <button className="btn btn--sm" onClick={onEdit}>Изменить</button>
          <button className="btn btn--sm btn--ghost" onClick={onDismiss}>Отмена</button>
        </div>
      )}
    </div>
  );
}

// ---- маленький markdown: абзацы, списки, таблицы, **жирный**, `код`, номера инцидентов — ссылки на карточку

function Inline({ text }: { text: string }) {
  const s = useStore();
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`|INC\d{6,})/g);
  return (
    <>
      {parts.map((x, i) => {
        if (/^\*\*[^*]+\*\*$/.test(x)) return <b key={i}><Inline text={x.slice(2, -2)} /></b>;
        if (/^`[^`]+`$/.test(x)) return <code key={i}>{x.slice(1, -1)}</code>;
        if (/^INC\d{6,}$/.test(x)) return <button key={i} className="link-btn mono" onClick={() => s.openIncident(x)}>{x}</button>;
        return <Fragment key={i}>{x}</Fragment>;
      })}
    </>
  );
}

function Markdown({ text }: { text: string }) {
  const lines = text.replace(/\r/g, "").split("\n");
  const blocks: React.ReactNode[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i].trimEnd();
    if (!line.trim()) { i++; continue; }
    if (line.trim().startsWith("|")) {
      const rows: string[][] = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) {
        const cells = lines[i].trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
        if (!cells.every((c) => /^:?-{2,}:?$/.test(c))) rows.push(cells);
        i++;
      }
      const [head, ...body] = rows;
      blocks.push(
        <div className="amd-table" key={blocks.length}>
          <table>
            <thead><tr>{head.map((c, k) => <th key={k}><Inline text={c} /></th>)}</tr></thead>
            <tbody>{body.map((r, k) => <tr key={k}>{r.map((c, j) => <td key={j}><Inline text={c} /></td>)}</tr>)}</tbody>
          </table>
        </div>,
      );
      continue;
    }
    if (/^\s*([-*•]|\d+[.)])\s+/.test(line)) {
      const ordered = /^\s*\d+[.)]/.test(line);
      const items: string[] = [];
      while (i < lines.length && /^\s*([-*•]|\d+[.)])\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*([-*•]|\d+[.)])\s+/, ""));
        i++;
      }
      const L = ordered ? "ol" : "ul";
      blocks.push(<L key={blocks.length}>{items.map((x, k) => <li key={k}><Inline text={x} /></li>)}</L>);
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() && !lines[i].trim().startsWith("|") && !/^\s*([-*•]|\d+[.)])\s+/.test(lines[i])) {
      para.push(lines[i].replace(/^#+\s*/, ""));
      i++;
    }
    blocks.push(
      <p key={blocks.length}>
        {para.map((x, k) => <Fragment key={k}>{k > 0 && <br />}<Inline text={x.trim()} /></Fragment>)}
      </p>,
    );
  }
  return <div className="amd">{blocks}</div>;
}
