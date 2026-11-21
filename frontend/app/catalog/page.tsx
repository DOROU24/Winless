"use client";

import { useEffect, useState } from "react";
import { api, type Column, type Preset } from "@/lib/api";
import { useStore } from "@/lib/store";
import { ALERT_LABEL, LEVEL_LABEL } from "@/lib/format";
import { Icon } from "@/components/Icon";
import { Modal } from "@/components/Modal";
import { ColumnEditor } from "@/components/ColumnEditor";

export default function CatalogPage() {
  const s = useStore();
  const [items, setItems] = useState<Preset[] | null>(null);
  const [target, setTarget] = useState<Preset | null>(null);
  const [editing, setEditing] = useState<{ boardId: number; column: Column } | null>(null);

  useEffect(() => {
    api.get<Preset[]>("/api/presets").then(setItems).catch((e) => s.toast(e.message, "error"));
  }, [s.version]); // eslint-disable-line react-hooks/exhaustive-deps

  const stage1 = items?.filter((p) => p.stage === 1) ?? [];
  const stage2 = items?.filter((p) => p.stage === 2) ?? [];

  return (
    <div className="pagewrap">
      <div className="pagehead">
        <h1>Каталог готовых правил</h1>
        <p className="muted">
          Критерии из нашего исследования и кейса МТС. После добавления в колонку условия и пороги можно менять.
        </p>
      </div>
      {!items && <p className="muted">Считаю…</p>}
      <PresetTable items={stage1} onAdd={setTarget} />
      {stage2.length > 0 && (
        <>
          <h2 className="subhead">Правила на сигналах ИИ</h2>
          {!s.settings?.aiAvailable && (
            <p className="muted">Языковая модель не подключена, эти правила не срабатывают.</p>
          )}
          <PresetTable items={stage2} onAdd={setTarget} />
        </>
      )}
      {target && (
        <AddToColumn
          preset={target}
          onClose={() => setTarget(null)}
          onCreated={(boardId, column) => {
            setTarget(null);
            setEditing({ boardId, column });
          }}
        />
      )}
      {editing && (
        <ColumnEditor
          boardId={editing.boardId}
          column={editing.column}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            setEditing(null);
            await s.reloadBoards();
            s.bump();
          }}
        />
      )}
    </div>
  );
}

function PresetTable({ items, onAdd }: { items: Preset[]; onAdd: (p: Preset) => void }) {
  return (
    <ul className="catalog">
      {items.map((p) => (
        <li key={p.id} className={`cat ${p.noData ? "is-nodata" : ""}`}>
          <span className="cat__num">{p.id}</span>
          <div className="cat__main">
            <h3>{p.name}</h3>
            <p>{p.description}</p>
            <div className="cat__meta">
              <span className={`lvl lvl--${p.level}`}>{LEVEL_LABEL[p.level]}</span>
              <span>Оповещение: {ALERT_LABEL[p.alert].toLowerCase()}{p.repeatMin ? `, повтор каждые ${p.repeatMin} мин` : ""}</span>
              <span>Источник: {p.origin}</span>
            </div>
            {p.warning && <p className="hint-text">{p.warning}</p>}
          </div>
          <div className="cat__count">
            <strong>{p.count ?? "—"}</strong>
            <span>сейчас</span>
          </div>
          <button className="btn" onClick={() => onAdd(p)}>
            <Icon name="plus" /> В колонку
          </button>
        </li>
      ))}
    </ul>
  );
}

function AddToColumn({ preset, onClose, onCreated }: { preset: Preset; onClose: () => void; onCreated: (b: number, c: Column) => void }) {
  const s = useStore();
  const [boardId, setBoardId] = useState<number>(s.activeBoardId ?? s.boards[0]?.id);
  const board = s.boards.find((b) => b.id === boardId);

  const createNew = async () => {
    const c = await api.post<Column>(`/api/boards/${boardId}/columns`, {
      name: preset.name, rule: preset.rule, level: preset.level, alert: preset.alert, repeatMin: preset.repeatMin,
      presetIds: [preset.id], sortField: "calc.deadlineLeftPct", sortDir: "asc",
    });
    if (preset.warning) s.toast(preset.warning, "info");
    await s.reloadBoards();
    s.bump();
    onCreated(boardId, c);
  };
  const appendTo = async (col: Column) => {
    const rule = !col.rule
      ? preset.rule
      : "op" in col.rule && col.rule.op === "or" && col.presetIds.length
        ? { ...col.rule, children: [...col.rule.children, preset.rule] }
        : { op: "or", children: [col.rule, preset.rule] };
    const c = await api.patch<Column>(`/api/boards/${boardId}/columns/${col.id}`, {
      rule, presetIds: [...col.presetIds, preset.id],
    });
    if (preset.warning) s.toast(preset.warning, "info");
    await s.reloadBoards();
    s.bump();
    onCreated(boardId, c);
  };

  return (
    <Modal title={`Куда добавить «${preset.name}»`} onClose={onClose}>
      <label className="field">
        <span>Доска</span>
        <select value={boardId} onChange={(e) => setBoardId(Number(e.target.value))}>
          {s.boards.map((b) => (
            <option key={b.id} value={b.id}>{b.name}</option>
          ))}
        </select>
      </label>
      <p className="muted small">В существующей колонке правило добавится через ИЛИ</p>
      <div className="tpl-list">
        <button className="tpl" onClick={createNew}>
          <strong>Новая колонка с этим правилом</strong>
          <span>Уровень и оповещение возьмём из правила</span>
        </button>
        {board?.columns?.map((c) => (
          <button key={c.id} className="tpl" onClick={() => appendTo(c)}>
            <strong>Добавить в «{c.name}»</strong>
          </button>
        ))}
      </div>
    </Modal>
  );
}
