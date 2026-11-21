"use client";

import { useEffect, useState } from "react";
import { api, type Layout } from "@/lib/api";
import { useStore } from "@/lib/store";
import { fmtDuration } from "@/lib/format";
import { Fuse } from "@/components/ColumnView";

// Вид для экрана на стене: названия колонок, счётчики и карточки уровня «критический». Без кнопок.
export default function WallPage() {
  const s = useStore();
  const [layout, setLayout] = useState<Layout | null>(null);
  const [clock, setClock] = useState("");

  useEffect(() => {
    if (!s.activeBoardId) return;
    api.get<Layout>(`/api/boards/${s.activeBoardId}/layout`).then(setLayout).catch(() => undefined);
  }, [s.activeBoardId, s.version]);

  useEffect(() => {
    const tick = () => setClock(new Date().toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" }));
    tick();
    const t = setInterval(tick, 10000);
    return () => clearInterval(t);
  }, []);

  if (!layout) return <div className="wall" />;
  return (
    <div className="wall">
      <header className="wall__head">
        <h1>{layout.board.name}</h1>
        <span className="wall__clock">{clock}</span>
      </header>
      <div className="wall__cols">
        {layout.columns.map((c) => (
          <section key={c.id} className={`wcol wcol--${c.level}`} style={{ "--col": c.color } as React.CSSProperties}>
            <header>
              <h2>{c.name}</h2>
              <strong>{c.total}</strong>
            </header>
            {c.level === "critical" && (
              <ul>
                {c.cards.slice(0, 8).map((k) => (
                  <li key={k.id} className="wcard">
                    <Fuse pct={k.deadlineLeftPct} overdue={k.overdue} />
                    <div>
                      <span className="mono">{k.id}</span>
                      <p>{k.header}</p>
                      {k.deadlineLeftMin !== null && (
                        <em className={k.overdue ? "is-over" : ""}>
                          {k.overdue ? `просрочен на ${fmtDuration(-k.deadlineLeftMin)}` : `до срока ${fmtDuration(k.deadlineLeftMin)}`}
                        </em>
                      )}
                    </div>
                  </li>
                ))}
                {c.total > 8 && <li className="wcard wcard--more">ещё {c.total - 8}</li>}
              </ul>
            )}
          </section>
        ))}
      </div>
    </div>
  );
}
