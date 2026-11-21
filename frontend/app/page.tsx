"use client";

import { useCallback, useEffect, useState } from "react";
import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import { SortableContext, arrayMove, horizontalListSortingStrategy, sortableKeyboardCoordinates } from "@dnd-kit/sortable";
import { api, type Board, type Layout, type LayoutColumn } from "@/lib/api";
import { useStore } from "@/lib/store";
import { ColumnView } from "@/components/ColumnView";
import { ColumnEditor } from "@/components/ColumnEditor";
import { Icon } from "@/components/Icon";
import { NewBoardDialog } from "@/components/AppShell";
import { Modal } from "@/components/Modal";

export default function BoardPage() {
  const s = useStore();
  const [layout, setLayout] = useState<Layout | null>(null);
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState<LayoutColumn | "new" | null>(null);
  const [boardSettings, setBoardSettings] = useState(false);
  const [newBoard, setNewBoard] = useState(false);
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const load = useCallback(async () => {
    if (!s.activeBoardId) return;
    setLoading(true);
    try {
      setLayout(await api.get<Layout>(`/api/boards/${s.activeBoardId}/layout`));
    } catch (e) {
      s.toast((e as Error).message, "error");
    } finally {
      setLoading(false);
    }
  }, [s.activeBoardId]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    load();
  }, [load, s.version]);

  const onDragEnd = async (e: DragEndEvent) => {
    if (!layout || !e.over || e.active.id === e.over.id) return;
    const ids = layout.columns.map((c) => c.id);
    const next = arrayMove(ids, ids.indexOf(Number(e.active.id)), ids.indexOf(Number(e.over.id)));
    setLayout({ ...layout, columns: next.map((id) => layout.columns.find((c) => c.id === id)!) });
    await api.patch(`/api/boards/${layout.board.id}`, { columnOrder: next });
    await s.reloadBoards();
    s.bump(); // в режиме «только в первой» порядок влияет на раскладку
  };

  const toggleCollapse = async (c: LayoutColumn) => {
    setLayout((l) => l && { ...l, columns: l.columns.map((x) => (x.id === c.id ? { ...x, collapsed: !x.collapsed } : x)) });
    await api.patch(`/api/boards/${c.boardId}/columns/${c.id}`, { collapsed: !c.collapsed });
  };

  if (!s.activeBoardId || !layout) {
    return <div className="board-empty">{loading ? "Раскладываю инциденты…" : ""}</div>;
  }
  const board = layout.board;
  const noData = layout.incidentCount === 0;

  return (
    <div className="board">
      <div className="board__head">
        <div className="board__title">
          <h1>{board.name}</h1>
          <span className="muted">
            {board.mode === "first" ? "Каждый инцидент только в одной колонке" : "Инцидент может быть в нескольких колонках"}
          </span>
        </div>
        <div className="board__tools">
          <button className="btn" onClick={() => setEditing("new")}>
            <Icon name="plus" /> Колонка
          </button>
          <button className="btn btn--ghost" onClick={() => setBoardSettings(true)}>
            <Icon name="settings" /> Доска
          </button>
        </div>
      </div>

      {noData && (
        <div className="notice">
          <Icon name="upload" size={18} />
          <div>
            <strong>Загрузите первый снимок.</strong> Перетащите JSON-выгрузку в окно или нажмите «Загрузить JSON». Для
            демо возьмите файлы из папки <code>demo_data</code> по порядку.
          </div>
        </div>
      )}

      {layout.columns.length === 0 ? (
        <div className="board-blank">
          <p>Добавьте колонку или выберите шаблон.</p>
          <div className="row">
            <button className="btn btn--primary" onClick={() => setEditing("new")}>
              <Icon name="plus" /> Добавить колонку
            </button>
            <button className="btn" onClick={() => setNewBoard(true)}>
              Выбрать шаблон
            </button>
          </div>
        </div>
      ) : (
        <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
          <SortableContext items={layout.columns.map((c) => c.id)} strategy={horizontalListSortingStrategy}>
            <div className="columns">
              {layout.columns.map((c, i) => (
                <ColumnView
                  key={c.id}
                  index={i}
                  column={c}
                  onEdit={() => setEditing(c)}
                  onToggle={() => toggleCollapse(c)}
                />
              ))}
            </div>
          </SortableContext>
        </DndContext>
      )}

      {editing && (
        <ColumnEditor
          boardId={board.id}
          column={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            setEditing(null);
            await s.reloadBoards();
            s.bump();
          }}
        />
      )}
      {boardSettings && <BoardSettings board={board} onClose={() => setBoardSettings(false)} />}
      {newBoard && <NewBoardDialog onClose={() => setNewBoard(false)} />}
    </div>
  );
}

function BoardSettings({ board, onClose }: { board: Board; onClose: () => void }) {
  const s = useStore();
  const [name, setName] = useState(board.name);
  const [mode, setMode] = useState(board.mode);
  const [hide, setHide] = useState(board.hideDisappeared);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const save = async () => {
    try {
      await api.patch(`/api/boards/${board.id}`, { name, mode, hideDisappeared: hide });
      await s.reloadBoards();
      s.bump();
      s.toast("Настройки доски сохранены");
      onClose();
    } catch (e) {
      s.toast((e as Error).message, "error");
    }
  };
  const duplicate = async () => {
    const b = await api.post<Board>(`/api/boards/${board.id}/duplicate`);
    await s.reloadBoards();
    await s.setActiveBoard(b.id);
    s.bump();
    s.toast(`Создана копия «${b.name}»`);
    onClose();
  };
  const remove = async () => {
    try {
      await api.del(`/api/boards/${board.id}`);
      await s.reloadBoards();
      s.bump();
      s.toast(`Доска «${board.name}» удалена`);
      onClose();
    } catch (e) {
      s.toast((e as Error).message, "error");
    }
  };

  return (
    <Modal
      title="Настройки доски"
      onClose={onClose}
      footer={
        <>
          <button className="btn btn--danger-ghost" onClick={() => (confirmDelete ? remove() : setConfirmDelete(true))}>
            <Icon name="trash" /> {confirmDelete ? "Точно удалить доску?" : "Удалить доску"}
          </button>
          <span className="grow" />
          <button className="btn" onClick={duplicate}>
            <Icon name="copy" /> Дублировать
          </button>
          <button className="btn btn--primary" onClick={save}>
            Сохранить
          </button>
        </>
      }
    >
      <label className="field">
        <span>Название</span>
        <input value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <fieldset className="field">
        <legend>Если инцидент подходит под несколько колонок</legend>
        <div className="seg">
          <button className={mode === "all" ? "is-on" : ""} onClick={() => setMode("all")}>
            Показывать во всех
          </button>
          <button className={mode === "first" ? "is-on" : ""} onClick={() => setMode("first")}>
            Только в первой по порядку
          </button>
        </div>
      </fieldset>
      <label className="check">
        <input type="checkbox" checked={hide} onChange={(e) => setHide(e.target.checked)} />
        <span>
          Скрывать инциденты, которых нет в последней загрузке
          <small>Они остаются в базе для истории, графа и поиска повторов</small>
        </span>
      </label>
    </Modal>
  );
}
