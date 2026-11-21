"use client";

import { useEffect, useState } from "react";
import { api, type AlertItem } from "@/lib/api";
import { useStore } from "@/lib/store";
import { fmtDate, LEVEL_LABEL } from "@/lib/format";
import { Icon } from "@/components/Icon";

// Список оповещений только для чтения: их создаёт система по правилам колонок.
export default function AlertsPage() {
  const s = useStore();
  const [items, setItems] = useState<AlertItem[] | null>(null);

  useEffect(() => {
    api.get<AlertItem[]>("/api/alerts").then(setItems);
  }, [s.version, s.activeAlerts]);

  return (
    <div className="pagewrap">
      <div className="pagehead">
        <h1>Оповещения</h1>
      </div>
      {items && items.length === 0 && (
        <div className="empty">
          <Icon name="bell" size={22} />
          <p>Оповещений ещё не было</p>
        </div>
      )}
      <ul className="alert-list">
        {items?.map((a) => (
          <li key={a.id} className={`al al--${a.level} ${a.inColumn ? "" : "is-gone"}`}
            title={a.inColumn ? "Инцидент сейчас в колонке" : "Инцидент уже не в колонке"}>
            <span className="al__lvl">{LEVEL_LABEL[a.level]}</span>
            <button className="al__main" onClick={() => s.openIncident(a.incidentId)}>
              <span className="mono">{a.incidentId}</span>
              <span className="al__head">{a.header}</span>
              <span className="al__meta">
                {a.columns.join(", ") || a.column}, {fmtDate(a.createdAt)}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
