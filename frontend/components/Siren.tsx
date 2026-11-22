"use client";

import { useEffect, useRef, useState } from "react";
import { plural } from "@/lib/format";

// Сирена для экрана без рук: пока на доске есть критические задачи, которые не взяли в работу,
// сирена звучит durSec секунд (края экрана в это время мигают красным), потом молчит pauseSec секунд и повторяет.
// Звук собирается в браузере (Web Audio), файл не нужен. Браузер даёт звук после первого касания страницы.

let ctx: AudioContext | null = null;
function audio(): AudioContext | null {
  if (typeof window === "undefined") return null;
  const AC = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!AC) return null;
  ctx = ctx ?? new AC();
  return ctx;
}

/** Вой сирены длиной sec секунд: частота ходит 650 → 1350 → 650 Гц каждые 0,75 с. Возвращает «выключить». */
function wail(sec: number): () => void {
  const ac = audio();
  if (!ac || ac.state !== "running") return () => undefined;
  const t0 = ac.currentTime;
  const osc = ac.createOscillator();
  const gain = ac.createGain();
  osc.type = "sawtooth";
  for (let t = 0; t < sec; t += 0.75) {
    osc.frequency.setValueAtTime(650, t0 + t);
    osc.frequency.linearRampToValueAtTime(1350, t0 + t + 0.375);
    osc.frequency.linearRampToValueAtTime(650, t0 + t + 0.75);
  }
  gain.gain.setValueAtTime(0, t0);
  gain.gain.linearRampToValueAtTime(0.18, t0 + 0.05);
  gain.gain.setValueAtTime(0.18, t0 + sec - 0.15);
  gain.gain.linearRampToValueAtTime(0, t0 + sec);
  osc.connect(gain).connect(ac.destination);
  osc.start(t0);
  osc.stop(t0 + sec);
  return () => {
    try { osc.stop(); } catch { /* уже остановлен */ }
  };
}

export function Siren({ count, durSec, pauseSec }: { count: number; durSec: number; pauseSec: number }) {
  const on = count > 0 && pauseSec > 0;
  const [sounding, setSounding] = useState(false);
  const [nextAt, setNextAt] = useState<number | null>(null);
  const [, tick] = useState(0);
  const stopRef = useRef<() => void>(() => undefined);

  // разрешение на звук: первое касание или клавиша на странице
  useEffect(() => {
    const unlock = () => { audio()?.resume().catch(() => undefined); };
    window.addEventListener("pointerdown", unlock);
    window.addEventListener("keydown", unlock);
    return () => {
      window.removeEventListener("pointerdown", unlock);
      window.removeEventListener("keydown", unlock);
    };
  }, []);

  // цикл: звучит durSec → пауза pauseSec → снова; как только невзятых не осталось — тишина
  useEffect(() => {
    if (!on) {
      setSounding(false);
      setNextAt(null);
      return;
    }
    let timer: ReturnType<typeof setTimeout>;
    let alive = true;
    const play = () => {
      if (!alive) return;
      stopRef.current = wail(durSec);
      setSounding(true);
      timer = setTimeout(() => {
        if (!alive) return;
        setSounding(false);
        setNextAt(Date.now() + pauseSec * 1000);
        timer = setTimeout(play, pauseSec * 1000);
      }, durSec * 1000);
    };
    play();
    return () => {
      alive = false;
      clearTimeout(timer);
      stopRef.current();
    };
  }, [on, durSec, pauseSec]);

  // обратный отсчёт до следующего включения
  useEffect(() => {
    if (!on || sounding) return;
    const t = setInterval(() => tick((x) => x + 1), 1000);
    return () => clearInterval(t);
  }, [on, sounding]);

  if (!on) return null;
  const left = nextAt ? Math.max(0, Math.round((nextAt - Date.now()) / 1000)) : 0;
  const what = `${count} ${plural(count, "критическая задача не взята", "критические задачи не взяты", "критических задач не взяты")} в работу`;
  const when = left >= 60 ? `${Math.ceil(left / 60)} мин` : `${left} с`;
  return (
    <>
      {sounding && <div className="siren-edge" aria-hidden />}
      <div className={`siren-label ${sounding ? "" : "is-quiet"}`} role="status">
        {sounding ? `Сирена: ${what}` : `${what} · сирена через ${when}`}
      </div>
    </>
  );
}
