"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { FieldDef, FieldType, RuleCond, RuleGroup, RuleNode } from "@/lib/api";
import { Icon } from "./Icon";

export const OPS: Record<FieldType, string[]> = {
  number: ["eq", "neq", "gt", "gte", "lt", "lte", "between", "in", "not_in"],
  text: ["eq", "neq", "contains", "not_contains", "starts_with", "in", "not_in"],
  date: ["before", "after", "within_last_min", "older_than_min"],
  flag: ["is_true", "is_false"],
};
const ANY = ["is_empty", "not_empty"];
export const OP_LABEL: Record<string, string> = {
  eq: "равно",
  neq: "не равно",
  gt: "больше",
  gte: "не меньше",
  lt: "меньше",
  lte: "не больше",
  between: "от … до",
  in: "одно из",
  not_in: "ни одно из",
  contains: "содержит",
  not_contains: "не содержит",
  starts_with: "начинается с",
  before: "раньше",
  after: "позже",
  within_last_min: "за последние N мин",
  older_than_min: "старше N мин",
  is_true: "да",
  is_false: "нет",
  is_empty: "пусто",
  not_empty: "не пусто",
};
const NO_VALUE = new Set(["is_true", "is_false", "is_empty", "not_empty"]);
const GROUP_LABEL: Record<string, string> = {
  json: "Поля из выгрузки",
  calc: "Вычисляемые",
  hist: "По истории загрузок",
  user: "Отметки координатора",
  ai: "Сигналы ИИ",
};

export function isGroup(n: RuleNode | null | undefined): n is RuleGroup {
  return !!n && "op" in n && "children" in n;
}

export function toGroup(rule: RuleNode | null): RuleGroup {
  if (!rule) return { op: "and", children: [] };
  return isGroup(rule) ? rule : { op: "and", children: [rule] };
}

export function simplify(g: RuleGroup): RuleNode | null {
  if (!g.children.length) return null;
  return g;
}

type Errors = Record<string, string>;

export function RuleBuilder({
  value,
  onChange,
  fields,
  errors = {},
}: {
  value: RuleGroup;
  onChange: (g: RuleGroup) => void;
  fields: FieldDef[];
  errors?: Errors;
}) {
  const map = useMemo(() => Object.fromEntries(fields.map((f) => [f.key, f])), [fields]);
  return (
    <>
      <GroupEditor node={value} onChange={onChange} path="rule" depth={0} map={map} fields={fields} errors={errors} />
      {value.children.length > 0 && <p className="hint-text">Если поле у инцидента пустое, условие с ним не выполняется</p>}
    </>
  );
}

function GroupEditor({
  node,
  onChange,
  onRemove,
  path,
  depth,
  map,
  fields,
  errors,
}: {
  node: RuleGroup;
  onChange: (g: RuleGroup) => void;
  onRemove?: () => void;
  path: string;
  depth: number;
  map: Record<string, FieldDef>;
  fields: FieldDef[];
  errors: Errors;
}) {
  const setChild = (i: number, c: RuleNode) => onChange({ ...node, children: node.children.map((x, j) => (j === i ? c : x)) });
  const removeChild = (i: number) => onChange({ ...node, children: node.children.filter((_, j) => j !== i) });
  const addCond = () => onChange({ ...node, children: [...node.children, { field: "priorityId", cmp: "eq", value: 4 }] });
  const addGroup = () =>
    onChange({ ...node, children: [...node.children, { op: node.op === "and" ? "or" : "and", children: [] }] });

  return (
    <div className={`rg rg--${node.op} ${depth ? "rg--nested" : ""}`}>
      <div className="rg__head">
        <div className="seg seg--sm" role="group" aria-label="Как объединять условия">
          <button className={node.op === "and" ? "is-on" : ""} onClick={() => onChange({ ...node, op: "and" })}>
            Все условия (И)
          </button>
          <button className={node.op === "or" ? "is-on" : ""} onClick={() => onChange({ ...node, op: "or" })}>
            Любое (ИЛИ)
          </button>
        </div>
        <span className="grow" />
        {onRemove && (
          <button className="icon-btn" onClick={onRemove} aria-label="Удалить группу" title="Удалить группу">
            <Icon name="trash" size={14} />
          </button>
        )}
      </div>
      {errors[path] && <p className="error-text">{errors[path]}</p>}
      <div className="rg__items">
        {node.children.length === 0 && <p className="muted small">Условий нет, подходят все инциденты</p>}
        {node.children.map((c, i) => {
          const p = `${path}.children[${i}]`;
          return (
            <div key={i} className="rg__item">
              {i > 0 && <span className="rg__joiner">{node.op === "and" ? "и" : "или"}</span>}
              {isGroup(c) ? (
                <GroupEditor node={c} onChange={(g) => setChild(i, g)} onRemove={() => removeChild(i)} path={p}
                  depth={depth + 1} map={map} fields={fields} errors={errors} />
              ) : (
                <CondEditor node={c} onChange={(n) => setChild(i, n)} onRemove={() => removeChild(i)} map={map}
                  fields={fields} error={errors[p]} />
              )}
            </div>
          );
        })}
      </div>
      <div className="rg__add">
        <button className="btn btn--sm btn--ghost" onClick={addCond}>
          <Icon name="plus" size={14} /> Условие
        </button>
        {depth < 3 && (
          <button className="btn btn--sm btn--ghost" onClick={addGroup}>
            <Icon name="plus" size={14} /> Группа
          </button>
        )}
      </div>
    </div>
  );
}

function shape(cmp: string) {
  if (NO_VALUE.has(cmp)) return "none";
  if (cmp === "in" || cmp === "not_in") return "list";
  if (cmp === "between") return "range";
  if (cmp === "within_last_min" || cmp === "older_than_min") return "min";
  if (cmp === "before" || cmp === "after") return "date";
  return "scalar";
}

function defaultValue(f: FieldDef, cmp: string): unknown {
  if (NO_VALUE.has(cmp)) return undefined;
  if (cmp === "in" || cmp === "not_in") return [];
  if (cmp === "between") return [0, 100];
  if (cmp === "within_last_min" || cmp === "older_than_min") return 60;
  if (f.type === "number") return 0;
  return "";
}

function CondEditor({
  node,
  onChange,
  onRemove,
  map,
  fields,
  error,
}: {
  node: RuleCond;
  onChange: (n: RuleCond) => void;
  onRemove: () => void;
  map: Record<string, FieldDef>;
  fields: FieldDef[];
  error?: string;
}) {
  const f = map[node.field];
  const ops = f ? [...OPS[f.type], ...ANY] : ANY;

  const changeField = (key: string) => {
    const nf = map[key];
    if (!nf) return;
    const cmp = OPS[nf.type][0];
    const n: RuleCond = { field: key, cmp };
    const v = defaultValue(nf, cmp);
    if (v !== undefined) n.value = v;
    if (nf.params) n.args = Object.fromEntries(nf.params.map((p) => [p.name, p.kind === "field" ? "" : []]));
    onChange(n);
  };
  const changeOp = (cmp: string) => {
    const n: RuleCond = { ...node, cmp };
    if (shape(cmp) !== shape(node.cmp)) {
      const v = f ? defaultValue(f, cmp) : undefined;
      if (v === undefined) delete n.value;
      else n.value = v;
    }
    onChange(n);
  };

  return (
    <div className={`cond ${error ? "has-error" : ""} ${f && !f.available ? "is-unavailable" : ""}`}>
      <div className="cond__row">
        <FieldPicker value={node.field} fields={fields} onChange={changeField} />
        {f?.params && (
          <HistArgs f={f} args={node.args ?? {}} map={map} fields={fields} onChange={(args) => onChange({ ...node, args })} />
        )}
        <select className="cond__op" value={node.cmp} onChange={(e) => changeOp(e.target.value)} aria-label="Оператор">
          {ops.map((o) => (
            <option key={o} value={o}>
              {OP_LABEL[o]}
            </option>
          ))}
        </select>
        {f && !NO_VALUE.has(node.cmp) && (
          <ValueInput f={f} cmp={node.cmp} value={node.value} onChange={(v) => onChange({ ...node, value: v })} />
        )}
        <button className="icon-btn" onClick={onRemove} aria-label="Удалить условие" title="Удалить условие">
          <Icon name="close" size={14} />
        </button>
      </div>
      {f && !f.available && <p className="hint-text">{f.unavailableReason}. Условие не сработает</p>}
      {error && <p className="error-text">{error}</p>}
    </div>
  );
}

export function FieldPicker({
  value,
  fields,
  onChange,
  filter,
  placeholder = "Выберите поле",
}: {
  value: string;
  fields: FieldDef[];
  onChange: (k: string) => void;
  filter?: (f: FieldDef) => boolean;
  placeholder?: string;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [hover, setHover] = useState<FieldDef | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const cur = fields.find((f) => f.key === value);

  useEffect(() => {
    if (!open) return;
    const h = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    window.addEventListener("mousedown", h);
    return () => window.removeEventListener("mousedown", h);
  }, [open]);

  const list = fields.filter(
    (f) => (!filter || filter(f)) && (!q || `${f.label} ${f.key}`.toLowerCase().includes(q.toLowerCase())),
  );
  const groups = ["calc", "json", "hist", "user", "ai"].map((g) => [g, list.filter((f) => f.group === g)] as const);

  return (
    <div className="fp" ref={ref}>
      <button className="fp__btn" onClick={() => setOpen(!open)} aria-haspopup="listbox" aria-expanded={open} title={cur?.description}>
        <span>{cur ? cur.label : value || placeholder}</span>
        <Icon name="chevronDown" size={13} />
      </button>
      {open && (
        <div className="fp__pop">
          <div className="input-icon">
            <Icon name="search" size={14} />
            <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Поиск поля"
              onKeyDown={(e) => e.key === "Escape" && setOpen(false)} />
          </div>
          <div className="fp__cols">
            <ul role="listbox" className="fp__list">
              {groups.map(([g, items]) =>
                items.length ? (
                  <li key={g}>
                    <div className="fp__group">{GROUP_LABEL[g]}</div>
                    <ul>
                      {items.map((f) => (
                        <li key={f.key}>
                          <button
                            role="option"
                            aria-selected={f.key === value}
                            className={`${f.key === value ? "is-on" : ""} ${f.available ? "" : "is-off"}`}
                            onMouseEnter={() => setHover(f)}
                            onFocus={() => setHover(f)}
                            onClick={() => {
                              onChange(f.key);
                              setOpen(false);
                              setQ("");
                            }}
                          >
                            <span>{f.label}</span>
                            <code>{f.key}</code>
                          </button>
                        </li>
                      ))}
                    </ul>
                  </li>
                ) : null,
              )}
              {list.length === 0 && <li className="muted pad">Нет полей по запросу</li>}
            </ul>
            <div className="fp__hint">
              {(hover ?? cur) ? (
                <>
                  <strong>{(hover ?? cur)!.label}</strong>
                  <code>{(hover ?? cur)!.key}</code>
                  <p>{(hover ?? cur)!.description}</p>
                  <p className="muted">Тип: {{ number: "число", text: "текст", date: "дата", flag: "флаг" }[(hover ?? cur)!.type]}</p>
                  {!(hover ?? cur)!.available && <p className="error-text">{(hover ?? cur)!.unavailableReason}</p>}
                </>
              ) : (
                <p className="muted">Наведите на поле, чтобы увидеть подсказку</p>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function options(f: FieldDef): { v: string | number; label: string }[] {
  const lab = Object.entries(f.labels);
  if (lab.length) {
    return lab
      .map(([code, l]) => ({ v: f.type === "number" && !isNaN(Number(code)) ? Number(code) : code, label: `${l} (${code})` }))
      .sort((a, b) => (typeof a.v === "number" && typeof b.v === "number" ? a.v - b.v : 0));
  }
  return (f.values ?? []).map((v) => ({ v, label: String(v) }));
}

function ValueInput({ f, cmp, value, onChange }: { f: FieldDef; cmp: string; value: unknown; onChange: (v: unknown) => void }) {
  const opts = options(f);
  if (cmp === "in" || cmp === "not_in") {
    const arr = Array.isArray(value) ? value : [];
    if (opts.length && opts.length <= 60) {
      return (
        <div className="multi">
          {opts.map((o) => {
            const on = arr.some((x) => String(x) === String(o.v));
            return (
              <button key={String(o.v)} className={`pill ${on ? "is-on" : ""}`}
                onClick={() => onChange(on ? arr.filter((x) => String(x) !== String(o.v)) : [...arr, o.v])}>
                {o.label}
              </button>
            );
          })}
        </div>
      );
    }
    return (
      <input className="cond__val" value={arr.join(", ")} placeholder="через запятую"
        onChange={(e) => onChange(e.target.value.split(",").map((x) => x.trim()).filter(Boolean)
          .map((x) => (f.type === "number" && !isNaN(Number(x)) ? Number(x) : x)))} />
    );
  }
  if (cmp === "between") {
    const [a, b] = Array.isArray(value) ? value : [0, 0];
    return (
      <span className="cond__between">
        <input type="number" value={String(a ?? "")} onChange={(e) => onChange([Number(e.target.value), b])} aria-label="от" />
        <span>—</span>
        <input type="number" value={String(b ?? "")} onChange={(e) => onChange([a, Number(e.target.value)])} aria-label="до" />
      </span>
    );
  }
  if (cmp === "within_last_min" || cmp === "older_than_min") {
    return <input className="cond__val cond__val--num" type="number" value={String(value ?? "")} onChange={(e) => onChange(Number(e.target.value))} aria-label="минут" />;
  }
  if (f.type === "date") {
    const iso = typeof value === "string" && value ? value : "";
    const local = iso ? new Date(iso).toISOString().slice(0, 16) : "";
    return (
      <input className="cond__val" type="datetime-local" value={local}
        onChange={(e) => onChange(e.target.value ? new Date(e.target.value + "Z").toISOString() : "")} aria-label="Дата (UTC)" />
    );
  }
  if (opts.length && Object.keys(f.labels).length) {
    return (
      <select className="cond__val" value={String(value ?? "")}
        onChange={(e) => onChange(f.type === "number" ? Number(e.target.value) : e.target.value)} aria-label="Значение">
        {opts.map((o) => (
          <option key={String(o.v)} value={String(o.v)}>
            {o.label}
          </option>
        ))}
      </select>
    );
  }
  if (f.type === "number") {
    return <input className="cond__val cond__val--num" type="number" value={String(value ?? "")} onChange={(e) => onChange(e.target.value === "" ? "" : Number(e.target.value))} aria-label="Значение" />;
  }
  const listId = `dl-${f.key}`;
  return (
    <>
      <input className="cond__val" list={opts.length ? listId : undefined} value={String(value ?? "")}
        onChange={(e) => onChange(e.target.value)} aria-label="Значение" />
      {opts.length > 0 && (
        <datalist id={listId}>
          {opts.map((o) => (
            <option key={String(o.v)} value={String(o.v)} />
          ))}
        </datalist>
      )}
    </>
  );
}

function HistArgs({ f, args, map, fields, onChange }: {
  f: FieldDef; args: Record<string, unknown>; map: Record<string, FieldDef>; fields: FieldDef[];
  onChange: (a: Record<string, unknown>) => void;
}) {
  const inner = map[String(args.field ?? "")];
  return (
    <span className="hist">
      {f.params!.map((p) =>
        p.kind === "field" ? (
          <FieldPicker key={p.name} value={String(args[p.name] ?? "")} placeholder="какое поле"
            fields={fields.filter((x) => x.group === "json" && (!p.fieldType || x.type === p.fieldType))}
            onChange={(k) => onChange({ ...args, [p.name]: k })} />
        ) : (
          <label key={p.name} className="hist__vals">
            <span>{p.label}</span>
            {inner && Object.keys(inner.labels).length ? (
              <select multiple value={((args[p.name] as unknown[]) ?? []).map(String)} aria-label={p.label}
                onChange={(e) => onChange({ ...args, [p.name]: Array.from(e.target.selectedOptions)
                  .map((o) => (inner.type === "number" ? Number(o.value) : o.value)) })}>
                {Object.entries(inner.labels).map(([code, l]) => (
                  <option key={code} value={code}>{l}</option>
                ))}
              </select>
            ) : (
              <input value={((args[p.name] as unknown[]) ?? []).join(", ")} placeholder="любое"
                onChange={(e) => onChange({ ...args, [p.name]: e.target.value.split(",").map((x) => x.trim()).filter(Boolean)
                  .map((x) => (isNaN(Number(x)) ? x : Number(x))) })} />
            )}
          </label>
        ),
      )}
    </span>
  );
}
