"use client";

import { useState } from "react";

// Вход в пульт: логин и пароль выдаются заранее, регистрации нет.
export default function LoginPage() {
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!login.trim() || !password) {
      setError("Введите логин и пароль");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const r = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ login: login.trim(), password }),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => null);
        setError(j?.detail?.message ?? "Не удалось войти");
        setBusy(false);
        return;
      }
      const next = new URLSearchParams(window.location.search).get("next");
      window.location.href = next && next.startsWith("/") && !next.startsWith("/login") ? next : "/";
    } catch {
      setError("Сервер не отвечает. Попробуйте ещё раз");
      setBusy(false);
    }
  };

  return (
    <main className="login">
      <form className="login__card" onSubmit={submit} noValidate>
        <div className="login__brand">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/brand/logo.svg" alt="МТС" width={44} height={44} />
          <span className="top__name">КООРАДАР</span>
        </div>
        <h1>Вход в пульт</h1>
        <label className="field">
          <span>Логин</span>
          <input value={login} onChange={(e) => { setLogin(e.target.value); setError(null); }} autoComplete="username" autoFocus />
        </label>
        <label className="field">
          <span>Пароль</span>
          <input type="password" value={password} onChange={(e) => { setPassword(e.target.value); setError(null); }} autoComplete="current-password" />
        </label>
        {error && <p className="error-text" role="alert">{error}</p>}
        <button className="btn btn--primary login__submit" disabled={busy}>{busy ? "Вхожу…" : "Войти"}</button>
        <p className="login__hint">Логин и пароль выдаёт администратор пульта</p>
      </form>
    </main>
  );
}
