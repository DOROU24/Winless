"use client";

import { useEffect, useMemo, useState } from "react";
import { api, ApiError, type AlertMode, type Column, type Level, type Preset, type RuleGroup, type RuleNode } from "@/lib/api";
import { useStore } from "@/lib/store";
import { ALERT_LABEL, LEVEL_LABEL } from "@/lib/format";
import { Icon } from "./Icon";
import { Modal } from "./Modal";
import { FieldPicker, RuleBuilder, isGroup, simplify, toGroup } from "./RuleBuilder";

const COLORS = ["#ff0032", "#f57c00", "#0070e5", "#7b4fe0", "#12a150", "#d6336c", "#626c77"];
const DEFAULT_FIELDS = ["id", "productName", "submitDate", "assignedGroupName", "assignedEmployee", "state", "deadline"];
const LEVEL_RANK: Record<Level, number> = { info: 0, elevated: 1, critical: 2 };
const ALERT_RANK: Record<AlertMode, number> = { off: 0, popup: 1, sound: 2 };

interface Preview {
  ok: boolean;
  errors: { path: string; message: string }[];
  count: number;
  items: { id: string; header: string; priority: string; state: string; why: string[] }[];
  nullSkipped?: number;
  unavailable?: string[];
}

export function ColumnEditor({
  boardId,
  column,
  onClose,
  onSaved,
}: {
  boardId: number;
  column: Column | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const s = useStore();
  const [name, setName] = useState(column?.name ?? "Новая колонка");
  const [color, setColor] = useState(column?.color ?? COLORS[2]);
  const [rule, setRule] = useState<RuleGroup>(toGroup(column?.rule ?? null));
  const [sortField, setSortField] = useState(column?.sortField ?? "calc.deadlineLeftPct");
  const [sortDir, setSortDir] = useState<"asc" | "desc">(column?.sortDir ?? "asc");
  const [limit, setLimit] = useState<string>(column?.limit != null ? String(column.limit) : "");
  const [cardFields, setCardFields] = useState<string[]>(column?.cardFields ?? DEFAULT_FIELDS);
  const [level, setLevel] = useState<Level>(column?.level ?? "info");
  const [alert, setAlert] = useState<AlertMode>(column?.alert ?? "off");
  const [repeat, setRepeat] = useState<string>(column?.repeatMin ? String(column.repeatMin) : "");
  const [groupSimilar, setGroupSimilar] = useState(column?.groupSimilar ?? false);
  const [presetIds, setPresetIds] = useState<number[]>(column?.presetIds ?? []);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [catalog, setCatalog] = useState(false);
  const [saving, setSaving] = useState(false);
  const [confirmDel, setConfirmDel] = useState(false);

  const ruleNode: RuleNode | null = useMemo(() => simplify(rule), [rule]);

  useEffect(() => {
    const t = setTimeout(async () => {
      try {
        const p = await api.post<Preview>("/api/rules/preview", { rule: ruleNode, limit: 20 });
        setPreview(p);
        setErrors(Object.fromEntries(p.errors.map((e) => [e.path, e.message])));
      } catch {
        /* показываем прошлый предпросмотр */
      }
    }, 300);
    return () => clearTimeout(t);
  }, [ruleNode]);

  const addPreset = (p: Preset) => {
    const pr = p.rule;
    setRule((cur) => {
      if (!cur.children.length) return toGroup(isGroup(pr) ? pr : { op: "and", children: [pr] });
      if (cur.op === "or" && presetIds.length) return { ...cur, children: [...cur.children, pr] };
      return { op: "or", children: [cur, pr] };
    });
    setPresetIds((ids) => [...ids, p.id]);
    if (LEVEL_RANK[p.level] > LEVEL_RANK[level]) setLevel(p.level);
    if (ALERT_RANK[p.alert] > ALERT_RANK[alert]) setAlert(p.alert);
    if (p.repeatMin && (!repeat || p.repeatMin < Number(repeat))) setRepeat(String(p.repeatMin));
    if (!column && name === "Новая колонка") setName(p.name);
    if (p.warning) s.toast(`${p.name}: ${p.warning}`, "info");
    else s.toast(`Добавлено правило «${p.name}»`);
    setCatalog(false);
  };

  const save = async () => {
    setSaving(true);
    const body = {
      name, color, rule: ruleNode, sortField: sortField || null, sortDir,
      limit: limit === "" ? null : Number(limit), noLimit: limit === "",
      cardFields, level, alert, repeatMin: repeat === "" ? null : Number(repeat), noRepeat: repeat === "",
      groupSimilar, presetIds,
    };
    try {
      if (column) await api.patch(`/api/boards/${boardId}/columns/${column.id}`, body);
      else await api.post(`/api/boards/${boardId}/columns`, body);
      s.toast(column ? `Колонка «${name}» сохранена` : `Колонка «${name}» добавлена`);
      onSaved();
    } catch (e) {
      if (e instanceof ApiError && e.body.errors.length) {
        setErrors(Object.fromEntries(e.body.errors.map((x) => [x.path, x.message])));
        s.toast("В правиле есть ошибки, они отмечены у условий", "error");
      } else s.toast((e as Error).message, "error");
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    if (!column) return;
    await api.del(`/api/boards/${boardId}/columns/${column.id}`);
    s.toast(`Колонка «${column.name}» удалена`);
    onSaved();
  };

  const sortable = s.fields.filter((f) => f.group !== "hist");
  const cardable = s.fields.filter((f) => f.group !== "hist");

  return (
    <Modal
      title={column ? `Колонка «${column.name}»` : "Новая колонка"}
      onClose={onClose}
      wide
      footer={
        <>
          {column && (
            <button className="btn btn--danger-ghost" onClick={() => (confirmDel ? remove() : setConfirmDel(true))}>
              <Icon name="trash" /> {confirmDel ? "Точно удалить?" : "Удалить колонку"}
            </button>
          )}
          <span className="grow" />
          <button className="btn" onClick={onClose}>Отмена</button>
          <button className="btn btn--primary" onClick={save} disabled={saving}>
            {saving ? "Сохраняю…" : column ? "Сохранить колонку" : "Добавить колонку"}
          </button>
        </>
      }
    >
      <div className="editor">
        <div className="editor__form">
          <div className="row row--top">
            <label className="field grow">
              <span>Название</span>
              <input value={name} onChange={(e) => setName(e.target.value)} />
            </label>
            <fieldset className="field">
              <legend>Цвет</legend>
              <div className="swatches">
                {COLORS.map((c) => (
                  <button key={c} className={`swatch ${c === color ? "is-on" : ""}`} style={{ background: c }}
                    onClick={() => setColor(c)} aria-label={`Цвет ${c}`} />
                ))}
              </div>
            </fieldset>
          </div>

          <section className="editor__sect">
            <div className="editor__sect-head">
              <h3>Правило</h3>
              <button className="btn btn--sm" onClick={() => setCatalog(true)}>
                <Icon name="catalog" size={14} /> Добавить из каталога
              </button>
            </div>
            <RuleBuilder value={rule} onChange={setRule} fields={s.fields} errors={errors} />
          </section>

          <section className="editor__sect">
            <h3>Важность и оповещения</h3>
            <fieldset className="field">
              <legend>Уровень важности</legend>
              <div className="seg">
                {(["critical", "elevated", "info"] as Level[]).map((l) => (
                  <button key={l} className={`${level === l ? "is-on" : ""} lv-${l}`} onClick={() => setLevel(l)}>
                    {LEVEL_LABEL[l]}
                  </button>
                ))}
              </div>
            </fieldset>
            <fieldset className="field">
              <legend>Оповещение, когда инцидент впервые попал в колонку</legend>
              <div className="seg">
                {(["off", "popup", "sound"] as AlertMode[]).map((a) => (
                  <button key={a} className={alert === a ? "is-on" : ""} onClick={() => setAlert(a)}>
                    {ALERT_LABEL[a]}
                  </button>
                ))}
              </div>
            </fieldset>
            {alert !== "off" && (
              <label className="field field--inline">
                <span>Повторять каждые</span>
                <input type="number" min={1} className="num" value={repeat} onChange={(e) => setRepeat(e.target.value)} placeholder="не повторять" />
                <span className="muted">мин, пока инцидент остаётся в колонке</span>
              </label>
            )}
          </section>

          <section className="editor__sect">
            <h3>Карточки</h3>
            <div className="row">
              <div className="field">
                <span>Сортировка</span>
                <div className="row">
                  <FieldPicker value={sortField} fields={sortable} onChange={setSortField} />
                  <div className="seg seg--sm">
                    <button className={sortDir === "asc" ? "is-on" : ""} onClick={() => setSortDir("asc")}>По возрастанию</button>
                    <button className={sortDir === "desc" ? "is-on" : ""} onClick={() => setSortDir("desc")}>По убыванию</button>
                  </div>
                </div>
              </div>
              <label className="field">
                <span>Лимит карточек</span>
                <input type="number" min={0} className="num" value={limit} onChange={(e) => setLimit(e.target.value)} placeholder="без лимита" />
              </label>
            </div>
            <div className="field">
              <span>Поля на карточке</span>
              <div className="chips-edit">
                {cardFields.map((k) => (
                  <span key={k} className="pill is-on">
                    {s.fieldMap[k]?.label ?? k}
                    <button onClick={() => setCardFields(cardFields.filter((x) => x !== k))} aria-label={`Убрать ${k}`}>
                      <Icon name="close" size={11} />
                    </button>
                  </span>
                ))}
                <FieldPicker value="" placeholder="Добавить поле" fields={cardable.filter((f) => !cardFields.includes(f.key))}
                  onChange={(k) => setCardFields([...cardFields, k])} />
              </div>
            </div>
            <label className={`check ${s.settings?.aiAvailable ? "" : "is-disabled"}`}>
              <input type="checkbox" checked={groupSimilar} disabled={!s.settings?.aiAvailable} onChange={(e) => setGroupSimilar(e.target.checked)} />
              <span>
                Схлопывать похожие открытые инциденты в одну строку
                <small>{s.settings?.aiAvailable ? "По сигналам ИИ" : "Нужна языковая модель (этап 2)"}</small>
              </span>
            </label>
          </section>
        </div>

        <aside className="editor__preview" aria-live="polite">
          <div className="pv__count">
            <span>Сейчас подходит</span>
            <strong>{preview ? preview.count : "…"}</strong>
          </div>
          {preview && !preview.ok && <p className="error-text">Исправьте ошибки в правиле, чтобы увидеть предпросмотр.</p>}
          {preview?.unavailable && preview.unavailable.length > 0 && (
            <p className="hint-text">Не срабатывают без данных: {preview.unavailable.join(", ")}</p>
          )}
          {!!preview?.nullSkipped && (
            <p className="hint-text">
              С пустым полем из условий: {preview.nullSkipped}
            </p>
          )}
          <ul className="pv__list">
            {preview?.items.map((i) => (
              <li key={i.id}>
                <button onClick={() => s.openIncident(i.id)}>
                  <span className="mono">{i.id}</span>
                  <span className="pv__head">{i.header}</span>
                  <span className="muted small">{[i.priority, i.state].filter(Boolean).join(", ")}</span>
                  {i.why.length > 0 && <span className="pv__why">{i.why.join(", ")}</span>}
                </button>
              </li>
            ))}
          </ul>
          {preview && preview.count > preview.items.length && (
            <p className="muted small">Показаны первые {preview.items.length}</p>
          )}
        </aside>
      </div>
      {catalog && <PresetPicker onPick={addPreset} onClose={() => setCatalog(false)} />}
    </Modal>
  );
}

export function PresetPicker({ onPick, onClose }: { onPick: (p: Preset) => void; onClose: () => void }) {
  const [items, setItems] = useState<Preset[] | null>(null);
  useEffect(() => {
    api.get<Preset[]>("/api/presets").then(setItems);
  }, []);
  return (
    <Modal title="Каталог готовых правил" onClose={onClose}>
      {!items && <p className="muted">Загружаю…</p>}
      <ul className="preset-list">
        {items?.map((p) => (
          <li key={p.id}>
            <button className={`preset ${p.noData ? "is-nodata" : ""}`} onClick={() => onPick(p)}>
              <span className="preset__num">{p.id}</span>
              <span className="preset__main">
                <strong>{p.name}</strong>
                <span>{p.description}</span>
                {p.warning && <em className="hint-text">{p.warning}</em>}
              </span>
              <span className="preset__count">{p.count ?? "—"}</span>
            </button>
          </li>
        ))}
      </ul>
    </Modal>
  );
}
