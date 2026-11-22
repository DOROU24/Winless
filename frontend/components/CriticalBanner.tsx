"use client";

import { useEffect } from "react";
import { useStore } from "@/lib/store";
import { useNow } from "@/lib/attention";
import { Icon } from "./Icon";

// Критическое оповещение для экрана, на который смотрят без рук.
// Первую минуту — крупный баннер поверх всего, потом он сам сворачивается в строку «Последний критический»,
// а через полчаса исчезает и она. Нажимать ничего не нужно; щелчок открывает инцидент.

const BANNER_MS = 60 * 1000;
const BASE_TITLE = "КООРАДАР";

export function CriticalBanner() {
  const s = useStore();
  const now = useNow(1000);
  const items = s.critical;
  const top = items[0];
  const age = top ? now - top.at : Infinity;
  const big = age < BANNER_MS;

  // заголовок вкладки мигает, пока висит баннер: видно и на свёрнутой вкладке
  useEffect(() => {
    if (!top || !big) { document.title = BASE_TITLE; return; }
    const on = Math.floor(now / 1000) % 2 === 0;
    document.title = on ? `● Критический: ${top.alert.incidentId}` : BASE_TITLE;
  }, [now, top, big]);

  if (!top) return null;
  const a = top.alert;
  const cols = [...new Set(a.columns)].join(", ") || a.column;
  const more = items.length - 1;
  const time = new Date(top.at).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  const mins = Math.floor(age / 60000);

  if (big) {
    return (
      <div className="crit-banner" role="alert" aria-live="assertive">
        <button className="crit-banner__body" onClick={() => s.openIncident(a.incidentId)}>
          <span className="crit-banner__kicker"><Icon name="warning" size={22} /> Новый критический инцидент</span>
          <span className="crit-banner__title">{a.header || "Без заголовка"}</span>
          <span className="crit-banner__meta">
            <b>{a.incidentId}</b>
            <span>{cols}</span>
            <span>пришёл в {time}</span>
            {more > 0 && <span>ещё {more} за полчаса</span>}
          </span>
        </button>
        <i className="crit-banner__timer" style={{ animationDuration: `${BANNER_MS}ms` }} key={a.id} aria-hidden />
      </div>
    );
  }

  return (
    <button className="crit-strip" onClick={() => s.openIncident(a.incidentId)} aria-live="polite">
      <Icon name="warning" size={16} />
      <span>Последний критический</span>
      <b>{a.incidentId}</b>
      <span className="crit-strip__title">{a.header}</span>
      <em>{mins < 1 ? "только что" : `${mins} мин назад`}{more > 0 ? ` · ещё ${more}` : ""}</em>
    </button>
  );
}

/** Подсказка на экране без рук: звук браузер включает только после одного касания страницы. */
export function SoundHint() {
  const s = useStore();
  if (s.soundReady) return null;
  return <div className="sound-hint">Коснитесь экрана один раз, чтобы включить звук оповещений</div>;
}
