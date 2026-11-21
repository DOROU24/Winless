"use client";

import { useEffect, useState } from "react";
import { api, type LinkRule, type Settings } from "@/lib/api";
import { useStore } from "@/lib/store";
import { Icon } from "@/components/Icon";
import { FieldPicker } from "@/components/RuleBuilder";

const LINK_TYPES: Record<string, string> = { related: "Связан", repeat: "Повтор", double: "Дубль", cause: "Причина → следствие" };

export default function SettingsPage() {
  const s = useStore();
  const [st, setSt] = useState<Settings | null>(null);
  const [tmpl, setTmpl] = useState("");
  const [rules, setRules] = useState<LinkRule[]>([]);
  const [teams, setTeams] = useState<{ team: string; scope: string }[]>([]);
  const [confirmReset, setConfirmReset] = useState<string | null>(null);
  const [minSimilar, setMinSimilar] = useState(3);

  const load = async () => {
    const [x, r] = await Promise.all([api.get<Settings>("/api/settings"), api.get<LinkRule[]>("/api/link-rules")]);
    setSt(x);
    setTmpl(x.itsmUrlTemplate);
    setTeams(x.teams);
    setMinSimilar(x.assistantMinSimilar ?? 3);
    setRules(r);
  };
  useEffect(() => {
    load();
  }, []);

  const saveTmpl = async () => {
    await api.patch("/api/settings", { itsmUrlTemplate: tmpl });
    await s.reloadMeta();
    s.toast("Шаблон ссылки сохранён");
  };

  const patchRule = async (r: LinkRule, body: Record<string, unknown>) => {
    try {
      const nr = await api.patch<LinkRule>(`/api/link-rules/${r.id}`, body);
      setRules((rs) => rs.map((x) => (x.id === r.id ? nr : x)));
      s.bump();
      s.toast("Автосвязи пересчитаны");
    } catch (e) {
      s.toast((e as Error).message, "error");
    }
  };
  const addRule = async () => {
    const nr = await api.post<LinkRule>("/api/link-rules", { name: "Один продукт за 12 часов", fields: ["productId"], windowMin: 720 });
    setRules((rs) => [...rs, nr]);
    s.bump();
  };
  const delRule = async (r: LinkRule) => {
    await api.del(`/api/link-rules/${r.id}`);
    setRules((rs) => rs.filter((x) => x.id !== r.id));
    s.bump();
  };

  const saveTeams = async () => {
    await api.patch("/api/settings", { teams });
    s.toast("Таблица команд сохранена");
  };

  const resetBoard = async (template: string) => {
    if (!s.activeBoard) return;
    await api.post(`/api/boards/${s.activeBoard.id}/reset`, { template });
    await s.reloadBoards();
    s.bump();
    setConfirmReset(null);
    s.toast(`Доска «${s.activeBoard.name}» собрана заново из шаблона`);
  };

  if (!st) return <div className="pagewrap muted">Загружаю…</div>;

  return (
    <div className="pagewrap pagewrap--narrow">
      <div className="pagehead">
        <h1>Настройки</h1>
      </div>

      <section className="panel">
        <h2>Ссылка в ITSM</h2>
        <p className="muted">
          Подставьте <code>{"{id}"}</code> (номер инцидента) или <code>{"{sysId}"}</code>. По умолчанию ведёт на заглушку
          внутри приложения.
        </p>
        <div className="row">
          <input className="grow" value={tmpl} onChange={(e) => setTmpl(e.target.value)} placeholder="https://itsm.example/incident/{id}" />
          <button className="btn" onClick={saveTmpl}>Сохранить</button>
        </div>
      </section>

      <section className="panel">
        <h2>Помощник: подсказка команды</h2>
        <p className="muted">
          Медиану времени решения по команде помощник показывает, только если у неё набралось столько похожих решённых
          инцидентов. Меньше — пишет «мало данных».
        </p>
        <div className="row">
          <label className="row">
            <span>Минимум похожих</span>
            <input type="number" min={1} max={50} value={minSimilar} style={{ width: 80 }}
              onChange={(e) => setMinSimilar(Number(e.target.value) || 1)} />
          </label>
          <button className="btn" onClick={async () => {
            await api.patch("/api/settings", { assistantMinSimilar: minSimilar });
            s.toast("Сохранено");
          }}>Сохранить</button>
        </div>
      </section>

      <section className="panel">
        <h2>Автосвязи</h2>
        <p className="muted">
          Связывает инциденты, у которых совпадают поля, а время возникновения отличается не больше окна. Пересчитывается
          при каждой загрузке.
        </p>
        <ul className="lr-list">
          {rules.map((r) => (
            <li key={r.id} className={`lr ${r.enabled ? "" : "is-off"}`}>
              <label className="switch">
                <input type="checkbox" checked={r.enabled} onChange={(e) => patchRule(r, { enabled: e.target.checked })} />
                <span aria-hidden />
                <span className="sr-only">Включено</span>
              </label>
              <div className="lr__main">
                <input className="lr__name" defaultValue={r.name} onBlur={(e) => e.target.value !== r.name && patchRule(r, { name: e.target.value })} aria-label="Название правила" />
                <div className="row row--wrap">
                  <span className="muted small">Совпадают</span>
                  {r.fields.map((f) => (
                    <span key={f} className="pill is-on">
                      {s.fieldMap[f]?.label ?? f}
                      {r.fields.length > 1 && (
                        <button onClick={() => patchRule(r, { fields: r.fields.filter((x) => x !== f) })} aria-label={`Убрать ${f}`}>
                          <Icon name="close" size={11} />
                        </button>
                      )}
                    </span>
                  ))}
                  <FieldPicker value="" placeholder="ещё поле" fields={s.fields.filter((f) => f.group === "json" && !r.fields.includes(f.key))}
                    onChange={(k) => patchRule(r, { fields: [...r.fields, k] })} />
                </div>
                <div className="row row--wrap">
                  <span className="muted small">Окно</span>
                  <input type="number" min={0} className="num" defaultValue={r.windowMin !== null ? r.windowMin / 60 : ""} placeholder="без окна"
                    onBlur={(e) => patchRule(r, e.target.value === "" ? { noWindow: true } : { windowMin: Math.round(Number(e.target.value) * 60) })} aria-label="Окно, часов" />
                  <span className="muted small">ч</span>
                  <span className="muted small">Тип связи</span>
                  <select value={r.linkType} onChange={(e) => patchRule(r, { linkType: e.target.value })}>
                    {Object.entries(LINK_TYPES).map(([k, v]) => (
                      <option key={k} value={k}>{v}</option>
                    ))}
                  </select>
                </div>
              </div>
              <button className="icon-btn" onClick={() => delRule(r)} aria-label="Удалить правило">
                <Icon name="trash" size={14} />
              </button>
            </li>
          ))}
        </ul>
        <button className="btn btn--sm" onClick={addRule}>
          <Icon name="plus" size={14} /> Правило автосвязи
        </button>
      </section>

      <section className="panel">
        <h2>Команды и зоны ответственности</h2>
        <p className="muted">По этой таблице ИИ подсказывает, какой команде отдать инцидент{st.aiAvailable ? "" : " (нужна языковая модель)"}.</p>
        <table className="teams">
          <thead>
            <tr><th>Команда</th><th>За что отвечает</th><th /></tr>
          </thead>
          <tbody>
            {teams.map((t, i) => (
              <tr key={i}>
                <td><input value={t.team} onChange={(e) => setTeams(teams.map((x, j) => (j === i ? { ...x, team: e.target.value } : x)))} aria-label="Команда" /></td>
                <td><input value={t.scope} onChange={(e) => setTeams(teams.map((x, j) => (j === i ? { ...x, scope: e.target.value } : x)))} aria-label="Зона ответственности" /></td>
                <td><button className="icon-btn" onClick={() => setTeams(teams.filter((_, j) => j !== i))} aria-label="Удалить строку"><Icon name="trash" size={14} /></button></td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="row">
          <button className="btn btn--sm" onClick={() => setTeams([...teams, { team: "", scope: "" }])}><Icon name="plus" size={14} /> Строка</button>
          <button className="btn btn--sm btn--primary" onClick={saveTeams}>Сохранить таблицу</button>
        </div>
      </section>

      <section className="panel panel--danger">
        <h2>Сброс</h2>
        <div className="row row--wrap">
          {[["focus", "Фокус внимания"], ["mts", "Задание МТС"]].map(([k, n]) => (
            <button key={k} className="btn" onClick={() => (confirmReset === k ? resetBoard(k) : setConfirmReset(k))}>
              {confirmReset === k ? `Заменить колонки «${s.activeBoard?.name}»?` : `Собрать текущую доску из шаблона «${n}»`}
            </button>
          ))}
        </div>
        <p className="small muted">
          Загруженные данные приложение не меняет и не удаляет. Чтобы начать с нуля, остановите бэкенд и удалите файл
          <code>backend/data.sqlite3</code>.
        </p>
      </section>
    </div>
  );
}
