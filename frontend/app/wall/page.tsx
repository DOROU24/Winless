"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Layout, type LayoutColumn, type WallLayout, type WallTile } from "@/lib/api";
import { useStore } from "@/lib/store";
import { fmtDuration, LEVEL_LABEL } from "@/lib/format";
import { Fuse } from "@/components/ColumnView";
import { Icon } from "@/components/Icon";
import { blinkOf, isFresh, isStuck, newLabel, orderCards, orderTiles, useNow, type BlinkOpts } from "@/lib/attention";

// Экран на стене: колонки доски — плитки на сетке 12 × N. В обычном режиме без кнопок.
// В режиме настройки плитки двигаются, растягиваются, скрываются; раскладка хранится в настройках доски.

const COLS = 12;

/** Настройки экрана без рук по умолчанию: плитки по свежести, затем по приоритету; пустые — в конце. */
const WALL_DEFAULTS = { tileOrder: "fresh", cardOrder: "fresh", emptyTiles: "end", freshMin: 15, showSummary: true } as const;
type WallOpts = { tileOrder: NonNullable<WallLayout["tileOrder"]>; cardOrder: NonNullable<WallLayout["cardOrder"]>;
  emptyTiles: NonNullable<WallLayout["emptyTiles"]>; freshMin: number; showSummary: boolean };
const optsOf = (w: WallLayout): WallOpts => ({
  tileOrder: w.tileOrder ?? WALL_DEFAULTS.tileOrder, cardOrder: w.cardOrder ?? WALL_DEFAULTS.cardOrder,
  emptyTiles: w.emptyTiles ?? WALL_DEFAULTS.emptyTiles, freshMin: w.freshMin ?? WALL_DEFAULTS.freshMin,
  showSummary: w.showSummary ?? WALL_DEFAULTS.showSummary,
});
const ROW_PX = 44; // высота строки сетки при масштабе 1
const GAP_PX = 16;

function defaultTile(c: LayoutColumn): WallTile {
  const critical = c.level === "critical";
  return { x: 0, y: 0, w: 4, h: critical ? 8 : 6, hidden: false, showCards: true, maxCards: 6 };
}

/** Раскладка по умолчанию: по три плитки в ряд, ряды друг под другом. */
function autoLayout(columns: LayoutColumn[], saved: WallLayout | null): WallLayout {
  const base: WallLayout = saved
    ? { ...saved, tiles: { ...saved.tiles } }
    : { scale: 1, showClock: true, showTitle: true, tiles: {} };
  // новые колонки, которых нет в сохранённой раскладке, ставим под остальными
  let bottom = Object.values(base.tiles).reduce((m, t) => Math.max(m, t.y + t.h), 0);
  let x = 0;
  let rowH = 0;
  for (const c of columns) {
    if (base.tiles[c.id]) continue;
    const t = defaultTile(c);
    if (x + t.w > COLS) {
      bottom += rowH;
      x = 0;
      rowH = 0;
    }
    base.tiles[c.id] = { ...t, x, y: bottom };
    x += t.w;
    rowH = Math.max(rowH, t.h);
  }
  return base;
}

export default function WallPage() {
  const s = useStore();
  const [layout, setLayout] = useState<Layout | null>(null);
  const [wall, setWall] = useState<WallLayout | null>(null);
  const [editing, setEditing] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [clock, setClock] = useState("");

  useEffect(() => {
    if (!s.activeBoardId) return;
    api.get<Layout>(`/api/boards/${s.activeBoardId}/layout`).then((l) => {
      setLayout(l);
      // пока идёт настройка, не затираем несохранённые правки свежими данными
      if (!editing) setWall(autoLayout(l.columns, l.board.wallLayout));
    }).catch(() => undefined);
  }, [s.activeBoardId, s.version]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const tick = () => setClock(new Date().toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" }));
    tick();
    const t = setInterval(tick, 10000);
    return () => clearInterval(t);
  }, []);

  const update = useCallback((patch: Partial<WallLayout>) => {
    setWall((w) => (w ? { ...w, ...patch } : w));
    setDirty(true);
  }, []);
  const updateTile = useCallback((id: number, patch: Partial<WallTile>) => {
    setWall((w) => (w ? { ...w, tiles: { ...w.tiles, [id]: { ...w.tiles[id], ...patch } } } : w));
    setDirty(true);
  }, []);

  const save = async () => {
    if (!layout || !wall) return;
    try {
      await api.patch(`/api/boards/${layout.board.id}`, { wallLayout: wall });
      await s.reloadBoards();
      setDirty(false);
      setEditing(false);
      s.toast("Раскладка экрана сохранена");
      s.bump();
    } catch (e) {
      s.toast((e as Error).message, "error");
    }
  };
  const cancel = () => {
    if (layout) setWall(autoLayout(layout.columns, layout.board.wallLayout));
    setDirty(false);
    setEditing(false);
  };
  const reset = () => {
    if (layout) setWall(autoLayout(layout.columns, null));
    setDirty(true);
  };

  if (!layout || !wall) return <div className="wall" />;
  const scale = wall.scale;
  const visible = layout.columns.filter((c) => !wall.tiles[c.id]?.hidden);
  const hidden = layout.columns.filter((c) => wall.tiles[c.id]?.hidden);
  // В обычном режиме экран раскладывается сам: колонки с задачами — плитками в порядке раскладки,
  // пустые — короткой строкой в самом конце. В режиме настройки — точные места плиток.
  const byPlace = (a: LayoutColumn, b: LayoutColumn) =>
    (wall.tiles[a.id].y - wall.tiles[b.id].y) || (wall.tiles[a.id].x - wall.tiles[b.id].x);
  const o = optsOf(wall);
  const filled = o.emptyTiles === "keep" ? visible : visible.filter((c) => c.total > 0);
  const live = editing ? visible : orderTiles(filled, o.tileOrder, byPlace);
  const empty = editing || o.emptyTiles !== "end" ? [] : visible.filter((c) => c.total === 0).sort(byPlace);

  return (
    <div className={`wall ${editing ? "is-editing" : ""}`} style={{ "--wall-scale": scale } as React.CSSProperties}>
      {editing && (
        <div className="wall-bar" role="toolbar" aria-label="Настройка экрана">
          <strong>Настройка экрана</strong>
          <label className="wall-bar__item">
            <span>Масштаб</span>
            <input type="range" min={0.6} max={2} step={0.05} value={scale}
              onChange={(e) => update({ scale: Number(e.target.value) })} aria-label="Масштаб" />
            <em>{Math.round(scale * 100)}%</em>
          </label>
          <label className="check check--inline">
            <input type="checkbox" checked={wall.showTitle} onChange={(e) => update({ showTitle: e.target.checked })} />
            <span>Название доски</span>
          </label>
          <label className="check check--inline">
            <input type="checkbox" checked={wall.showClock} onChange={(e) => update({ showClock: e.target.checked })} />
            <span>Часы</span>
          </label>
          <label className="wall-bar__item">
            <span>Плитки</span>
            <select value={o.tileOrder} onChange={(e) => update({ tileOrder: e.target.value as WallOpts["tileOrder"] })}>
              <option value="fresh">Сначала свежие, затем по приоритету</option>
              <option value="priority">Сначала по приоритету, затем свежие</option>
              <option value="layout">Как расставлены</option>
            </select>
          </label>
          <label className="wall-bar__item">
            <span>Задачи</span>
            <select value={o.cardOrder} onChange={(e) => update({ cardOrder: e.target.value as WallOpts["cardOrder"] })}>
              <option value="fresh">Только что пришедшие сверху</option>
              <option value="priority">По приоритету</option>
              <option value="column">Как в колонке</option>
            </select>
          </label>
          <label className="wall-bar__item">
            <span>Пустые колонки</span>
            <select value={o.emptyTiles} onChange={(e) => update({ emptyTiles: e.target.value as WallOpts["emptyTiles"] })}>
              <option value="end">Строкой в конце</option>
              <option value="hide">Не показывать</option>
              <option value="keep">Плитками, как все</option>
            </select>
          </label>
          <label className="wall-bar__item">
            <span>Подсветка новых</span>
            <select value={o.freshMin} onChange={(e) => update({ freshMin: Number(e.target.value) })}>
              {[5, 15, 30, 60].map((m) => <option key={m} value={m}>{m} мин</option>)}
              <option value={0}>Выключена</option>
            </select>
          </label>
          <label className="wall-bar__item">
            <span>Мигать критической, если не взяли</span>
            <select value={blinkOf(wall).stuckMin} onChange={(e) => update({ stuckMin: Number(e.target.value) })}>
              {[5, 10, 15, 30, 60].map((m) => <option key={m} value={m}>за {m} мин</option>)}
              <option value={0}>Не мигать</option>
            </select>
          </label>
          <label className="wall-bar__item">
            <span>Мигание</span>
            <select value={blinkOf(wall).blink} onChange={(e) => update({ blink: e.target.value as BlinkOpts["blink"] })}>
              <option value="strong">Сильное — вся карточка</option>
              <option value="soft">Мягкое — рамка</option>
            </select>
          </label>
          <label className="check check--inline">
            <input type="checkbox" checked={o.showSummary} onChange={(e) => update({ showSummary: e.target.checked })} />
            <span>Сводка</span>
          </label>
          {hidden.length > 0 && (
            <div className="wall-bar__hidden">
              <span>Скрыты:</span>
              {hidden.map((c) => (
                <button key={c.id} className="pill" onClick={() => updateTile(c.id, { hidden: false })} title="Вернуть на экран">
                  <Icon name="eye" size={12} /> {c.name}
                </button>
              ))}
            </div>
          )}
          <span className="grow" />
          <span className="muted small wall-bar__hint">Тяните плитку за заголовок, размер — за угол. Стрелки двигают, Shift + стрелки меняют размер</span>
          <button className="btn btn--sm btn--ghost" onClick={reset}>Сбросить раскладку</button>
          <button className="btn btn--sm" onClick={cancel}>Отмена</button>
          <button className="btn btn--sm btn--primary" onClick={save} disabled={!dirty}>Сохранить</button>
        </div>
      )}

      <header className="wall__head">
        {wall.showTitle ? <h1>{layout.board.name}</h1> : <span />}
        {!editing && o.showSummary && <WallSummary columns={visible} freshMin={o.freshMin} />}
        <div className="wall__right">
          {/* кнопка всегда на месте и стоит слева от часов, чтобы их не загораживать */}
          {!editing && (
            <button className="wall-edit" onClick={() => setEditing(true)} aria-label="Настроить экран" title="Настроить экран">
              <Icon name="settings" size={18} />
            </button>
          )}
          {wall.showClock && <span className="wall__clock">{clock}</span>}
        </div>
      </header>

      <WallGrid editing={editing} scale={scale}>
        {live.map((c) => (
          <Tile key={c.id} column={c} tile={wall.tiles[c.id]} editing={editing} scale={scale} flow={!editing && !(o.tileOrder === "layout" && o.emptyTiles === "keep")}
            cardOrder={o.cardOrder} freshMin={o.freshMin} blink={blinkOf(wall)}
            onChange={(patch) => updateTile(c.id, patch)} openIncident={s.openIncident} />
        ))}
      </WallGrid>
      {empty.length > 0 && (
        <div className="wall-empty" aria-label="Колонки без задач">
          <span>Сейчас пусто:</span>
          {empty.map((c) => <span key={c.id} className="wall-empty__col" style={{ "--col": c.color } as React.CSSProperties}>{c.name}</span>)}
        </div>
      )}
      {visible.length === 0 && <p className="muted wall__empty">Все колонки скрыты. Откройте настройку экрана и верните их</p>}
    </div>
  );
}

/** Сводка в шапке экрана: сколько критических, сколько пришло за час, сколько просрочено. */
function WallSummary({ columns, freshMin }: { columns: LayoutColumn[]; freshMin: number }) {
  const now = useNow();
  const seen = new Map<string, { crit: boolean; isNew: boolean; fresh: boolean; over: boolean }>();
  for (const c of columns) for (const k of c.cards) {
    const prev = seen.get(k.id);
    seen.set(k.id, {
      crit: (prev?.crit ?? false) || k.priorityId === 4,
      isNew: k.isNew, fresh: isFresh(k, now, freshMin), over: k.overdue,
    });
  }
  const all = [...seen.values()];
  const crit = all.filter((x) => x.crit).length;
  const fresh = all.filter((x) => x.isNew).length;
  const over = all.filter((x) => x.over).length;
  const justNow = all.filter((x) => x.fresh).length;
  return (
    <div className="wall-sum" aria-label="Сводка">
      <span className={`wall-sum__item ${crit ? "is-crit" : ""}`}><b>{crit}</b> критических</span>
      <span className={`wall-sum__item ${justNow ? "is-fresh" : ""}`}><b>{fresh}</b> новых за час</span>
      <span className={`wall-sum__item ${over ? "is-over" : ""}`}><b>{over}</b> просрочено</span>
    </div>
  );
}

function WallGrid({ children, editing, scale }: { children: React.ReactNode; editing: boolean; scale: number }) {
  return (
    <div className={`wall-grid ${editing ? "is-editing" : ""}`}
      style={{ gridAutoRows: `${ROW_PX * scale}px`, gap: `${GAP_PX * scale}px` }}>
      {children}
    </div>
  );
}

function Tile({ column: c, tile, editing, scale, flow = false, cardOrder = "fresh", freshMin = 15, blink = blinkOf(null), onChange, openIncident }: {
  blink?: BlinkOpts;
  column: LayoutColumn;
  tile: WallTile;
  editing: boolean;
  scale: number;
  flow?: boolean; // без точного места: плитки сами встают друг за другом
  cardOrder?: WallOpts["cardOrder"];
  freshMin?: number;
  onChange: (patch: Partial<WallTile>) => void;
  openIncident: (id: string) => void;
}) {
  const ref = useRef<HTMLElement>(null);
  const [dragging, setDragging] = useState<null | "move" | "resize">(null);

  /** Размер одной ячейки сетки в пикселях (для перевода движения мыши в шаги сетки). */
  const cell = () => {
    const grid = ref.current?.parentElement;
    if (!grid) return { w: 100, h: ROW_PX * scale + GAP_PX * scale };
    const gap = GAP_PX * scale;
    return { w: (grid.clientWidth + gap) / COLS, h: ROW_PX * scale + gap };
  };

  const startDrag = (mode: "move" | "resize") => (e: React.PointerEvent) => {
    if (!editing || e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    const start = { px: e.clientX, py: e.clientY, ...tile };
    const size = cell();
    setDragging(mode);
    const move = (ev: PointerEvent) => {
      const dx = Math.round((ev.clientX - start.px) / size.w);
      const dy = Math.round((ev.clientY - start.py) / size.h);
      if (mode === "move") {
        onChange({ x: clamp(start.x + dx, 0, COLS - start.w), y: Math.max(0, start.y + dy) });
      } else {
        onChange({ w: clamp(start.w + dx, 2, COLS - start.x), h: clamp(start.h + dy, 2, 40) });
      }
    };
    const up = () => {
      setDragging(null);
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (!editing) return;
    const d = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[e.key];
    if (!d) return;
    e.preventDefault();
    if (e.shiftKey) onChange({ w: clamp(tile.w + d[0], 2, COLS - tile.x), h: clamp(tile.h + d[1], 2, 40) });
    else onChange({ x: clamp(tile.x + d[0], 0, COLS - tile.w), y: Math.max(0, tile.y + d[1]) });
  };

  // все задачи колонки; если не помещаются в плитку, список листается колёсиком или пальцем
  const now = useNow();
  const cards = orderCards(c.cards, cardOrder, now, freshMin);

  return (
    <section
      ref={ref}
      className={`wtile wtile--${c.level} has-cards ${dragging ? "is-dragging" : ""}`}
      style={{
        gridColumn: flow ? `span ${tile.w}` : `${tile.x + 1} / span ${tile.w}`,
        gridRow: flow ? `span ${tile.h}` : `${tile.y + 1} / span ${tile.h}`, "--col": c.color,
      } as React.CSSProperties}
      tabIndex={editing ? 0 : undefined}
      onKeyDown={onKey}
      aria-label={editing ? `${c.name}: колонка ${tile.x + 1}, строка ${tile.y + 1}, ширина ${tile.w}, высота ${tile.h}` : undefined}
    >
      <header className="wtile__head" onPointerDown={startDrag("move")}>
        {editing && <Icon name="grip" size={16} className="wtile__grip" />}
        <div className="wtile__title">
          <h2>{c.name}</h2>
          <span className="wtile__level">{LEVEL_LABEL[c.level].toLowerCase()}</span>
        </div>
        <strong className="wtile__count">{c.total}</strong>
      </header>

      {(
        <ul className="wtile__cards" tabIndex={0} aria-label={`Задачи колонки «${c.name}»`}
          onPointerDown={(e) => e.stopPropagation()}>
          {cards.map((k) => (
            <li key={k.id} className={`wcard ${isFresh(k, now, freshMin) ? "is-fresh" : ""} ${isStuck(k, c.level, blink) ? `is-stuck is-stuck--${blink.blink}` : ""}`}>
              <Fuse pct={k.deadlineLeftPct} overdue={k.overdue} />
              <button className="wcard__open" onClick={() => !editing && openIncident(k.id)} disabled={editing}
                aria-label={`Открыть ${k.id}`}>
                <span className="wcard__top">
                  <span className="wcard__id">{k.id}</span>
                  {k.priorityId !== null && <span className={`prio prio--${k.priorityId}`}>{k.priority}</span>}
                  {k.isNew && <span className="tag tag--new">{newLabel(k, now, freshMin)}</span>}
                  {isStuck(k, c.level, blink) && k.idleMin != null && <span className="tag tag--stuck">не взят {Math.floor(k.idleMin)} мин</span>}
                  {k.wave && <span className="tag tag--wave">волна · {k.wave.size}</span>}
                  {k.priorityUnderestimated && <span className="tag tag--warn">приоритет занижен?</span>}
                  {k.overdueRisk && <span className="tag tag--warn">риск просрочки</span>}
                </span>
                <p>{k.header}</p>
                {k.deadlineLeftMin !== null && (
                  <em className={k.overdue ? "is-over" : ""}>
                    {k.overdue ? `просрочен на ${fmtDuration(-k.deadlineLeftMin)}` : `до срока ${fmtDuration(k.deadlineLeftMin)}`}
                  </em>
                )}
              </button>
            </li>
          ))}
          {c.total === 0 && <li className="wtile__empty">Пусто</li>}
        </ul>
      )}

      {editing && (
        <div className="wtile__tools" onPointerDown={(e) => e.stopPropagation()}>
          <button className="icon-btn" onClick={() => onChange({ hidden: true })} title="Скрыть с экрана" aria-label={`Скрыть ${c.name}`}>
            <Icon name="eye" size={14} />
          </button>
        </div>
      )}
      {editing && <span className="wtile__resize" onPointerDown={startDrag("resize")} aria-hidden title="Потяните, чтобы изменить размер" />}
    </section>
  );
}

function clamp(v: number, lo: number, hi: number) {
  return Math.max(lo, Math.min(hi, v));
}
