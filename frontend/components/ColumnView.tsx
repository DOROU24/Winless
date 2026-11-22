"use client";

import { useState } from "react";
import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import type { CardData, LayoutColumn } from "@/lib/api";
import { useStore } from "@/lib/store";
import { fmtDuration, fmtValue, LEVEL_LABEL } from "@/lib/format";
import { Icon } from "./Icon";

export function ColumnView({
  column,
  onEdit,
  onToggle,
}: {
  column: LayoutColumn;
  onEdit: () => void;
  onToggle: () => void;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: column.id });
  const style = {
    transform: CSS.Translate.toString(transform),
    transition,
    "--col": column.color, // цвет только из настроек колонки, от места на доске не зависит
  } as React.CSSProperties;

  if (column.collapsed) {
    return (
      <section ref={setNodeRef} style={style} className={`col col--collapsed ${isDragging ? "is-dragging" : ""}`}>
        <button className="col__rail" onClick={onToggle} title="Развернуть колонку">
          <span className="col__count">{column.total}</span>
          <span className="col__vname">{column.name}</span>
        </button>
      </section>
    );
  }

  return (
    <section ref={setNodeRef} style={style} className={`col col--${column.level} ${isDragging ? "is-dragging" : ""}`}
      aria-label={`${column.name}: ${column.total}`}>
      <header className="col__head">
        <button className="col__grip" {...attributes} {...listeners} aria-label="Перетащить колонку" title="Перетащить колонку">
          <Icon name="grip" size={14} />
        </button>
        <h2 className="col__name" title={`${column.name}: ${column.total}`}>{column.name}</h2>
        <span className="grow" />
        <button className="col__icon" onClick={onEdit} aria-label="Настроить колонку" title="Настроить колонку">
          <Icon name="edit" size={20} />
        </button>
        <button className="col__icon" onClick={onToggle} aria-label="Свернуть колонку" title="Свернуть">
          <Icon name="chevronLeft" size={22} />
        </button>
      </header>
      <div className="col__level" title={column.alert === "off" ? "Без оповещения" : column.alert === "sound" ? "Оповещение: окно и звук" : "Оповещение: окно"}>
        {LEVEL_LABEL[column.level].toLowerCase()}
      </div>
      {column.ruleErrors.length > 0 && (
        <div className="col__error" role="alert">
          <Icon name="warning" size={14} />
          <div>
            <strong>Правило с ошибкой</strong>
            {column.ruleErrors.map((e, i) => <p key={i}>{e.message}</p>)}
            <button className="link-btn" onClick={onEdit}>Исправить правило</button>
          </div>
        </div>
      )}
      <div className="col__body">
        {column.cards.length === 0 && (
          <p className="col__empty">{column.rule ? "Сейчас ничего не подходит" : "Правила нет, подходят все"}</p>
        )}
        {column.cards.map((c) => (
          <Card key={c.id} card={c} fields={column.cardFields} />
        ))}
        {column.hidden > 0 && (
          <button className="col__more" onClick={onEdit}>
            ещё {column.hidden}
          </button>
        )}
      </div>
    </section>
  );
}

// Запал срока: полоса слева показывает, сколько отведённого времени осталось.
export function Fuse({ pct, overdue }: { pct: number | null; overdue: boolean }) {
  if (pct === null) return <span className="fuse fuse--none" title="Срок не задан" />;
  const left = Math.max(0, Math.min(100, pct));
  const tone = overdue || pct <= 0 ? "out" : pct < 10 ? "crit" : pct < 35 ? "warn" : "ok";
  return (
    <span className={`fuse fuse--${tone}`} title={overdue ? "Срок прошёл" : `Осталось ${Math.round(pct)}% срока`}>
      <i style={{ height: `${left}%` }} />
    </span>
  );
}

export function Card({ card, fields }: { card: CardData; fields: string[] }) {
  const s = useStore();
  const [open, setOpen] = useState(false);
  const shown = fields.filter((f) => f !== "id");
  const members = card.waveMembers ?? [];
  return (
    <div className="card-wrap">
    <article className={`card ${members.length ? "card--wave" : ""}`}>
      <button className="card__open" onClick={() => s.openIncident(card.id)} aria-label={`Открыть ${card.id}`}>
        <div className="card__top">
          <span className="mono card__id">{card.id}</span>
          {card.priorityId !== null && <span className={`prio prio--${card.priorityId}`}>{card.priority}</span>}
          {card.isNew && <span className="tag tag--new">новый</span>}
          {card.disappeared && <span className="tag">пропал</span>}
          {card.wave && <span className="tag tag--wave" title={`Волна сбоя: «${card.wave.title}»`}>волна · {card.wave.size}</span>}
          {card.priorityUnderestimated && <span className="tag tag--warn" title="ИИ оценивает приоритет выше текущего">приоритет занижен?</span>}
          {card.overdueRisk && <span className="tag tag--warn" title="Похожие случаи решались дольше, чем осталось до срока">риск просрочки</span>}
        </div>
        <p className="card__title">{card.header || "Без заголовка"}</p>
        {card.deadlineLeftMin !== null && card.stateId !== null && [0, 2].includes(card.stateId) && (
          <p className={`card__deadline ${card.overdue ? "is-over" : ""}`}>
            {card.overdue ? `Просрочен на ${fmtDuration(-card.deadlineLeftMin)}` : `До срока ${fmtDuration(card.deadlineLeftMin)}`}
          </p>
        )}
        <dl className="card__fields">
          {shown.map((f) => {
            const fd = s.fieldMap[f];
            const v = card.values[f];
            if (v === null || v === undefined || v === "") return null;
            return (
              <div key={f}>
                <dt>{fd?.label ?? f}</dt>
                <dd>{fmtValue(fd, v)}</dd>
              </div>
            );
          })}
        </dl>
        {card.why.length > 0 && (
          <ul className="card__why" aria-label="Почему здесь">
            {card.why.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        )}
      </button>
    </article>
    {members.length > 0 && (
      <div className="wave">
        <button className={`wave__toggle ${open ? "is-open" : ""}`} onClick={() => setOpen(!open)} aria-expanded={open}>
          <span className="wave__stack" aria-hidden><i /><i /><i /></span>
          <span className="wave__label">{open ? "Свернуть волну" : "Ещё в этой волне"}</span>
          <b className="wave__count">{members.length}</b>
          <Icon name="chevronDown" size={16} className="wave__chev" />
        </button>
        {open && (
          <div className="wave__list">
            {members.map((m) => <Card key={m.id} card={m} fields={fields} />)}
          </div>
        )}
      </div>
    )}
    </div>
  );
}
