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
  const [linkType, setLinkType] = useState("");
  const [source, setSource] = useState("");
  const [activeOnly, setActiveOnly] = useState(false);
  const [stats, setStats] = useState({ nodes: 0, edges: 0 });
  const [hover, setHover] = useState<GNode | null>(null);


  // Сохранённые позиции узлов и подпись данных: граф перерисовывается, только когда связи правда изменились.
  // Раньше он пересоздавался со случайной раскладкой при каждом обновлении данных (раз в 30 с, а пока
  // работает ИИ — каждые 4 с), поэтому картинка «прыгала».
  const positions = useRef<Record<string, { x: number; y: number }>>({});
  const signature = useRef("");
  const view = useRef<{ zoom: number; pan: { x: number; y: number } } | null>(null);
  const filtersKey = `${linkType}|${source}|${activeOnly}`;
  const lastFilters = useRef(filtersKey);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const qs = new URLSearchParams();
      if (linkType) qs.set("link_type", linkType);
      if (source) qs.set("source", source);
      if (activeOnly) qs.set("active_only", "true");
      const g = await api.get<{ nodes: GNode[]; edges: GEdge[] }>(`/api/graph?${qs}`);
      if (cancelled || !box.current) return;

      const filtersChanged = lastFilters.current !== filtersKey;
      lastFilters.current = filtersKey;
      const sig = JSON.stringify([
        g.nodes.map((n) => [n.id, n.level, n.disappeared, n.stateId]).sort(),
        g.edges.map((e) => [e.id, e.type, e.source]).sort(),
      ]);
      if (!filtersChanged && sig === signature.current && cy.current) return; // ничего не изменилось — не трогаем
      signature.current = sig;
      setStats({ nodes: g.nodes.length, edges: g.edges.length });

      if (filtersChanged) {
        positions.current = {}; // другой набор узлов — раскладываем заново
        view.current = null;
      } else if (cy.current) {
        cy.current.nodes().forEach((n) => {
          positions.current[n.id()] = { ...n.position() };
        });
        view.current = { zoom: cy.current.zoom(), pan: { ...cy.current.pan() } };
      }

      const cytoscape = (await import("cytoscape")).default;
      if (cancelled || !box.current) return;
      cy.current?.destroy();
      const deg: Record<string, number> = {};
      g.edges.forEach((e) => {
        deg[e.src] = (deg[e.src] ?? 0) + 1;
        deg[e.dst] = (deg[e.dst] ?? 0) + 1;
      });
      const known = g.nodes.length > 0 && g.nodes.every((n) => positions.current[n.id]);
      const anyKnown = g.nodes.some((n) => positions.current[n.id]);
      const anyKnownNew = anyKnown && !known;
      if (anyKnown && !known) {
        // новый узел ставим рядом с уже размещённым соседом, чтобы раскладка не начиналась с нуля
        for (const n of g.nodes) {
          if (positions.current[n.id]) continue;
          const e = g.edges.find((x) => (x.src === n.id && positions.current[x.dst]) || (x.dst === n.id && positions.current[x.src]));
          const base = e ? positions.current[e.src === n.id ? e.dst : e.src] : Object.values(positions.current)[0];
          positions.current[n.id] = { x: base.x + (Math.random() - 0.5) * 60, y: base.y + (Math.random() - 0.5) * 60 };
        }
      }
      const inst = cytoscape({
        container: box.current,
        elements: [
          ...g.nodes.map((n) => ({
            data: { ...n, label: n.id.replace(/^INC0*/, ""), color: n.level ? LEVEL_HEX[n.level] : NEUTRAL, size: 14 + Math.min(18, (deg[n.id] ?? 0) * 3) },
            classes: n.disappeared ? "gone" : "",
            position: positions.current[n.id],
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
        // все узлы уже размещались — оставляем их на местах; новые узлы — раскладка с фиксированным зерном
        layout: (known && !anyKnownNew
          ? { name: "preset", fit: !view.current }
          : { name: "cose", animate: false, randomize: !anyKnown, nodeRepulsion: () => 9000, idealEdgeLength: () => 60, padding: 30 }) as never,
        // Было 0.3: один щелчок колеса давал ~1,5% зума, увеличение тянулось бесконечно.
        // 4 — около 12–20% за щелчок, как в картах; на тачпаде шаг пропорционально мельче.
        wheelSensitivity: 4,
        minZoom: 0.15,
        maxZoom: 4,
      });
      if (known && view.current) {
        inst.zoom(view.current.zoom);
        inst.pan(view.current.pan);
      }
      inst.nodes().forEach((n) => {
        positions.current[n.id()] = { ...n.position() };
      });
      inst.on("dragfree", "node", (e) => {
        positions.current[e.target.id()] = { ...e.target.position() }; // пользователь передвинул узел — запоминаем
      });
      inst.on("tap", "node", (e) => s.openIncident(e.target.id()));
      inst.on("mouseover", "node", (e) => setHover(e.target.data() as GNode));
      inst.on("mouseout", "node", () => setHover(null));
      cy.current = inst;
    })();
    return () => {
      cancelled = true;
    };
  }, [linkType, source, activeOnly, s.version]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => () => cy.current?.destroy(), []);

  return (
    <div className="graph">
      <div className="graph__bar">
        <h1>Граф связей</h1>
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
