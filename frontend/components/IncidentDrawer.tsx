"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type IncidentDetail, type LinkItem } from "@/lib/api";
import { useStore } from "@/lib/store";
import { fmtDate, fmtValue, LEVEL_COLOR } from "@/lib/format";
import { askAssistant } from "./Assistant";
import { Icon } from "./Icon";

const KEY_CALC = [
  "calc.deadlineLeftMin",
  "calc.deadlineLeftPct",
  "calc.idleMin",
  "calc.ageMin",
  "calc.resolvedInMin",
  "calc.overdue",
  "calc.isNew",
  "calc.disappeared",
];

export function IncidentDrawer({ id }: { id: string }) {
  const s = useStore();
  const [d, setD] = useState<IncidentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [allFields, setAllFields] = useState(false);

  const load = useCallback(async () => {
    try {
      setError(null);
      setD(await api.get<IncidentDetail>(`/api/incidents/${encodeURIComponent(id)}${s.activeBoardId ? `?board_id=${s.activeBoardId}` : ""}`));
    } catch (e) {
      setError((e as Error).message);
    }
  }, [id, s.activeBoardId]);

  useEffect(() => {
    load();
  }, [load, s.version]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && s.openIncident(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [s]);



  const data = d?.data ?? {};
  const f = (k: string) => fmtValue(s.fieldMap[k], data[k]);

  return (
    <aside className="drawer" aria-label={`Инцидент ${id}`}>
      <header className="drawer__head">
        <span className="mono">{id}</span>
        <span className="grow" />
        <button className="icon-btn" onClick={() => s.openIncident(null)} aria-label="Закрыть карточку">
          <Icon name="close" />
        </button>
      </header>
      {error && <p className="error-text">{error}</p>}
      {!d && !error && <p className="muted pad">Загружаю…</p>}
      {d && (
        <div className="drawer__body">
          <h2 className="drawer__title">{String(data.header ?? "Без заголовка")}</h2>
          <div className="drawer__meta">
            {data.priority != null && <span className={`prio prio--${data.priorityId}`}>{String(data.priority)}</span>}
            <span className="chip">{String(data.state ?? "—")}</span>
            {d.disappeared && <span className="chip chip--warn">Нет в последней загрузке</span>}
          </div>
          <p className="drawer__desc">{String(data.description ?? data.shortDescription ?? "")}</p>

          <div className="drawer__actions">
            <a className="btn btn--ghost" href={d.itsmUrl} target="_blank" rel="noreferrer">
              <Icon name="external" /> Открыть в ITSM
            </a>
            {s.settings?.aiAvailable && (
              <button className="btn btn--ghost" onClick={() => askAssistant(`Кому отдать ${id}?`)}
                title="Помощник сравнит команды по похожим инцидентам и текущей очереди. Только совет, ничего не назначает">
                <Icon name="spark" /> Кому отдать?
              </button>
            )}
          </div>

          <section className="sect">
            <h3>Почему здесь</h3>
            {d.why.length === 0 ? (
              <p className="muted">На текущей доске инцидент не попал ни в одну колонку.</p>
            ) : (
              d.why.map((w) => (
                <div key={w.columnId} className="why" style={{ "--lv": LEVEL_COLOR[w.level] } as React.CSSProperties}>
                  <strong>{w.column}</strong>
                  <ul>
                    {w.reasons.length ? w.reasons.map((r, i) => <li key={i}>{r}</li>) : <li>Правила нет, подходят все</li>}
                  </ul>
                </div>
              ))
            )}
          </section>

          <section className="sect">
            <h3>Сроки и состояние</h3>
            <dl className="kv">
              <div><dt>Команда</dt><dd>{f("assignedGroupName")}</dd></div>
              <div><dt>Сотрудник</dt><dd>{f("assignedEmployee")}</dd></div>
              <div><dt>Возник</dt><dd>{fmtDate(data.submitDate, true)}</dd></div>
              <div><dt>Срок решения</dt><dd>{fmtDate(data.deadline, true)}</dd></div>
              {KEY_CALC.map((k) =>
                d.calc[k] === null || d.calc[k] === undefined || d.calc[k] === false ? null : (
                  <div key={k}>
                    <dt>{s.fieldMap[k]?.label ?? k}</dt>
                    <dd>{fmtValue(s.fieldMap[k], d.calc[k])}</dd>
                  </div>
                ),
              )}
            </dl>
          </section>

          <AiSignals d={d} />
          {d.ai && <AiBlock ai={d.ai} />}

          <section className="sect">
            <h3>
              Связи <span className="muted">{d.links.outgoing.length + d.links.incoming.length}</span>
            </h3>
            {d.links.outgoing.length + d.links.incoming.length === 0 && (
              <p className="muted">Связей нет. Их создают правила автосвязей в настройках</p>
            )}
            <LinkList title="Исходящие" items={d.links.outgoing} />
            <LinkList title="Обратные ссылки" items={d.links.incoming} />
          </section>

          <section className="sect">
            <h3>История изменений</h3>
            <ol className="timeline">
              {d.events.map((e) => (
                <li key={e.id} className={`ev ev--${e.type}`}>
                  <time title={e.snapshot ?? undefined}>{fmtDate(e.at)}</time>
                  <div>
                    {e.type === "created" && <span>Появился в выгрузке</span>}
                    {e.type === "disappeared" && <span>Пропал из выгрузки</span>}
                    {e.type === "reappeared" && <span>Снова появился в выгрузке</span>}
                    {e.type === "field_changed" && (
                      <span>
                        <b>{s.fieldMap[e.field ?? ""]?.label ?? e.field}</b>:{" "}
                        <s>{fmtValue(s.fieldMap[e.field ?? ""], e.old)}</s> → {fmtValue(s.fieldMap[e.field ?? ""], e.new)}
                      </span>
                    )}
                  </div>
                </li>
              ))}
            </ol>
          </section>

          <section className="sect">
            <button className="sect__toggle" onClick={() => setAllFields(!allFields)} aria-expanded={allFields}>
              <Icon name={allFields ? "chevronDown" : "chevron"} size={14} /> Все поля из выгрузки
            </button>
            {allFields && (
              <dl className="kv kv--all">
                {Object.keys(data).map((k) => (
                  <div key={k}>
                    <dt title={k}>{s.fieldMap[k]?.label ?? k}</dt>
                    <dd>{f(k)}</dd>
                  </div>
                ))}
              </dl>
            )}
          </section>
        </div>
      )}
    </aside>
  );
}

function LinkList({ title, items }: { title: string; items: LinkItem[] }) {
  const s = useStore();
  if (!items.length) return null;
  return (
    <div className="links">
      <h4>{title}</h4>
      <ul>
        {items.map((l) => (
          <li key={l.id} className={`lnk lnk--${l.source}`}>
            <button className="lnk__main" onClick={() => s.openIncident(l.other!)}>
              <span className="lnk__type">{l.typeLabel}</span>
              <span className="mono">{l.other}</span>
              <span className="lnk__head">{l.otherHeader}</span>
            </button>
            <div className="lnk__note">
              <span className="lnk__src">{l.sourceLabel}</span>
              {l.note && <span>{l.note}</span>}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

// Волна сбоя, приоритет и риск просрочки — сигналы ИИ, которые нужны координатору прямо сейчас
function AiSignals({ d }: { d: IncidentDetail }) {
  const s = useStore();
  const c = d.calc;
  const prio = c["ai.priorityUnderestimated"] === true;
  const risk = c["ai.overdueRisk"] === true;
  if (!d.wave && !prio && !risk) return null;
  const P = s.fieldMap["ai.suggestedPriority"];
  return (
    <section className="sect sect--signals">
      {d.wave && (
        <div className="signal">
          <strong>Волна сбоя: {d.wave.size} активных инцидента про одну проблему</strong>
          <p className="muted small">«{d.wave.title}» — вероятен общий сбой, смотрите их вместе</p>
          <ul className="signal__list">
            {d.wave.members.filter((m) => m.id !== d.id).map((m) => (
              <li key={m.id}>
                <button className="link-btn mono" onClick={() => s.openIncident(m.id)}>{m.id}</button> {m.header}
              </li>
            ))}
          </ul>
        </div>
      )}
      {prio && (
        <div className="signal">
          <strong>Приоритет, похоже, занижен: по оценке ИИ — {fmtValue(P, c["ai.suggestedPriority"])}</strong>
          {typeof c["ai.priorityReason"] === "string" && <p className="small">{c["ai.priorityReason"] as string}</p>}
        </div>
      )}
      {risk && (
        <div className="signal">
          <strong>Риск просрочки</strong>
          {typeof c["ai.overdueRiskReason"] === "string" && <p className="small">{c["ai.overdueRiskReason"] as string}</p>}
        </div>
      )}
    </section>
  );
}

function AiBlock({ ai }: { ai: NonNullable<IncidentDetail["ai"]> }) {
  const s = useStore();
  if (ai.error) {
    return (
      <section className="sect">
        <h3>ИИ</h3>
        <p className="muted">Сигналы не посчитаны: {ai.error}</p>
      </section>
    );
  }
  const a = ai.data as {
    similar?: { id: string; relation: string; explanation: string; resolved: boolean }[];
    weakSolution?: boolean | null;
    weakSolutionReason?: string;
    suggestedTeam?: string | null;
    suggestedTeamReason?: string;
    facts?: string[];
    assumptions?: string[];
  };
  return (
    <section className="sect sect--ai">
      <h3>ИИ</h3>
      {a.similar && a.similar.length > 0 ? (
        <ul className="ai-list">
          {a.similar.map((x) => (
            <li key={x.id}>
              <button className="link-btn mono" onClick={() => s.openIncident(x.id)}>
                {x.id}
              </button>{" "}
              <span className="chip">{x.relation === "same" ? (x.resolved ? "Повтор" : "Дубль") : "Связанный симптом"}</span>
              <p>{x.explanation}</p>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">Похожих инцидентов не найдено.</p>
      )}
      {a.weakSolution !== null && a.weakSolution !== undefined && (
        <p>
          <b>Решение:</b> {a.weakSolution ? "похоже на отписку" : "объяснено"}. {a.weakSolutionReason}
        </p>
      )}
      {a.suggestedTeam && (
        <p>
          <b>Предложенная команда:</b> {a.suggestedTeam}. {a.suggestedTeamReason}
        </p>
      )}
      {!!a.facts?.length && (
        <div>
          <h4>Факты</h4>
          <ul>{a.facts.map((x, i) => <li key={i}>{x}</li>)}</ul>
        </div>
      )}
      {!!a.assumptions?.length && (
        <div>
          <h4>Предположения</h4>
          <ul>{a.assumptions.map((x, i) => <li key={i}>{x}</li>)}</ul>
        </div>
      )}
    </section>
  );
}
