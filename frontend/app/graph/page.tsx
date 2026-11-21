"use client";

import { useEffect, useRef, useState } from "react";
import type { Core } from "cytoscape";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";

interface GNode { id: string; header: string; state: string; stateId: number; priorityId: number; service: string; disappeared: boolean; level: string | null; column: string | null }
interface GEdge { id: number; src: string; dst: string; type: string; typeLabel: string; source: string; directed: boolean; note: string }

const LEVEL_HEX: Record<string, string> = { critical: "#ff0032", elevated: "#f57c00", info: "#0070e5" };
const NEUTRAL = "#9aa3ae";

export default function GraphPage() {
  const s = useStore();
  const box = useRef<HTMLDivElement>(null);
  const cy = useRef<Core | null>(null);
  const [boardId, setBoardId] = useState<number | "">("");
  const [columnId, setColumnId] = useState<number | "">("");
  const [linkType, setLinkType] = useState("");
  const [source, setSource] = useState("");
  const [activeOnly, setActiveOnly] = useState(false);
  const [stats, setStats] = useState({ nodes: 0, edges: 0 });
  const [hover, setHover] = useState<GNode | null>(null);

  const board = s.boards.find((b) => b.id === boardId);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const qs = new URLSearchParams();
      if (boardId) qs.set("board_id", String(boardId));
      if (columnId) qs.set("column_id", String(columnId));
      if (linkType) qs.set("link_type", linkType);
      if (source) qs.set("source", source);
      if (activeOnly) qs.set("active_only", "true");
      const g = await api.get<{ nodes: GNode[]; edges: GEdge[] }>(`/api/graph?${qs}`);
      if (cancelled || !box.current) return;
      setStats({ nodes: g.nodes.length, edges: g.edges.length });
      const cytoscape = (await import("cytoscape")).default;
      cy.current?.destroy();
      const deg: Record<string, number> = {};
      g.edges.forEach((e) => {
        deg[e.src] = (deg[e.src] ?? 0) + 1;
        deg[e.dst] = (deg[e.dst] ?? 0) + 1;
      });
      const inst = cytoscape({
        container: box.current,
        elements: [
          ...g.nodes.map((n) => ({
            data: { ...n, label: n.id.replace(/^INC0*/, ""), color: n.level ? LEVEL_HEX[n.level] : NEUTRAL, size: 14 + Math.min(18, (deg[n.id] ?? 0) * 3) },
            classes: n.disappeared ? "gone" : "",
          })),
          ...g.edges.map((e) => ({ data: { id: `e${e.id}`, source: e.src, target: e.dst, kind: e.source, directed: e.directed ? 1 : 0 } })),
        ],
        style: [
          { selector: "node", style: { "background-color": "data(color)", width: "data(size)", height: "data(size)", label: "data(label)", color: "#434a52", "font-size": 9, "font-family": "JetBrains Mono, monospace", "text-valign": "bottom", "text-margin-y": 4, "border-width": 0 } },
          { selector: "node.gone", style: { opacity: 0.45, "border-width": 1, "border-color": "#626c77", "border-style": "dashed" } },
          { selector: "node:selected", style: { "border-width": 3, "border-color": "#1d2023" } },
          { selector: "edge", style: { width: 1.2, "line-color": "#c9ced6", "curve-style": "bezier" } },
          { selector: "edge[kind = 'auto']", style: { "line-style": "dashed", "line-dash-pattern": [4, 4] } },
          { selector: "edge[kind = 'ai']", style: { "line-style": "dotted", width: 2, "line-color": "#12a150" } },
          { selector: "edge[directed = 1]", style: { "target-arrow-shape": "triangle", "target-arrow-color": "#626c77", "arrow-scale": 0.8 } },
        ],
        layout: { name: "cose", animate: false, nodeRepulsion: () => 9000, idealEdgeLength: () => 60, padding: 30 } as never,
        wheelSensitivity: 0.3,
      });
      inst.on("tap", "node", (e) => s.openIncident(e.target.id()));
      inst.on("mouseover", "node", (e) => setHover(e.target.data() as GNode));
      inst.on("mouseout", "node", () => setHover(null));
      cy.current = inst;
    })();
    return () => {
      cancelled = true;
    };
  }, [boardId, columnId, linkType, source, activeOnly, s.version]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => () => cy.current?.destroy(), []);

  return (
    <div className="graph">
      <div className="graph__bar">
        <h1>Граф связей</h1>
        <select value={boardId} onChange={(e) => { setBoardId(e.target.value ? Number(e.target.value) : ""); setColumnId(""); }} aria-label="Доска">
          <option value="">Все инциденты со связями</option>
          {s.boards.map((b) => <option key={b.id} value={b.id}>Доска «{b.name}»</option>)}
        </select>
        <select value={columnId} onChange={(e) => setColumnId(e.target.value ? Number(e.target.value) : "")} disabled={!board} aria-label="Колонка">
          <option value="">Все колонки</option>
          {board?.columns?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        <select value={linkType} onChange={(e) => setLinkType(e.target.value)} aria-label="Тип связи">
          <option value="">Любой тип связи</option>
          <option value="related">Связан</option>
          <option value="repeat">Повтор</option>
          <option value="double">Дубль</option>
          <option value="cause">Причина → следствие</option>
        </select>
        <select value={source} onChange={(e) => setSource(e.target.value)} aria-label="Источник связи">
          <option value="">Любой источник</option>
          <option value="auto">Автоправила</option>
          <option value="ai">ИИ</option>
        </select>
        <label className="check check--inline">
          <input type="checkbox" checked={activeOnly} onChange={(e) => setActiveOnly(e.target.checked)} />
          <span>Только активные</span>
        </label>
        <span className="muted small">{stats.nodes} узлов, {stats.edges} связей</span>
      </div>
      <div className="graph__canvas" ref={box} />
      {stats.nodes === 0 && <p className="graph__empty muted">Связей нет. Их создают правила автосвязей в настройках</p>}
      {hover && (
        <div className="graph__tip">
          <span className="mono">{hover.id}</span>
          <strong>{hover.header}</strong>
          <span className="muted small">{hover.service}</span>
          <span className="muted small">{hover.disappeared ? "Пропал из выгрузки" : hover.state}</span>
          {hover.column && <span className="muted small">Колонка «{hover.column}»</span>}
        </div>
      )}
      <div className="graph__legend">
        <span><i style={{ background: LEVEL_HEX.critical }} />критический</span>
        <span><i style={{ background: LEVEL_HEX.elevated }} />повышенный</span>
        <span><i style={{ background: LEVEL_HEX.info }} />информационный</span>
        <span><i style={{ background: NEUTRAL }} />вне колонок</span>
        <span><b className="ln ln--auto" />автоправило</span>
        <span><b className="ln ln--ai" />ИИ</span>
      </div>
    </div>
  );
}
