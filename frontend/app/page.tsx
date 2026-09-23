"use client";

import { useEffect, useState, type FormEvent } from "react";
import { Journal } from "@/components/Journal";
import { api } from "@/lib/api";
import { FORM, SUMMARY_LABELS, STATUS_TEXT, brand } from "@/lib/brand";
import {
  CATEGORIES, CITIES, DATE_MAX, DATE_MIN, EMPTY_FORM, FORMATS, LANGUAGES,
  REQUIRED_LABELS, fromText, hoursInvalid, missing, toTask, type RequestForm, type RequiredKey,
} from "@/lib/request";
import type { Health, Run, Sample } from "@/lib/types";

/** Итог запуска цифрами. Это то, что называют в питче: «столько-то вместо столько-то». */
function Summary({ run }: { run: Run }) {
  const last = run.steps[run.steps.length - 1];
  if (!last) return null;
  const seconds = (new Date(last.at).getTime() - new Date(run.created_at).getTime()) / 1000;
  const tokens = run.tokens.prompt + run.tokens.completion;
  const cells: [string, string][] = [
    [SUMMARY_LABELS.time, `${seconds.toFixed(1)} с`],
    [SUMMARY_LABELS.tools, String(run.steps.filter((s) => s.kind === "tool_call").length)],
  ];
  if (tokens > 0) cells.push([SUMMARY_LABELS.tokens, tokens.toLocaleString("ru-RU")]);

  return (
    <dl className="summary">
      {cells.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export default function Page() {
  const [form, setForm] = useState<RequestForm>(EMPTY_FORM);
  // Подсказку о пустых полях показываем только после попытки отправить, а не с порога
  const [tried, setTried] = useState(false);
  const [samples, setSamples] = useState<Sample[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [busy, setBusy] = useState(false);
  const [deciding, setDeciding] = useState(false);
  const [error, setError] = useState("");
  // После запуска форма уезжает: на проекторе журнал должен занимать весь экран
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    api.samples().then(setSamples).catch(() => undefined);
    api
      .health()
      .then(setHealth)
      .catch(() => setError("Бэкенд недоступен. Запустите его: python scripts/dev.py backend (порт 8000)."));
  }, []);

  // Пока агент работает — опрашиваем раз в секунду. Проще и надёжнее SSE/WebSocket для одного дня.
  const active = run && (run.status === "running" || run.status === "awaiting_approval");
  useEffect(() => {
    if (!run?.id || !active) return;
    let failures = 0;
    const timer = setInterval(() => {
      api
        .getRun(run.id)
        .then((fresh) => {
          failures = 0;
          setRun(fresh);
        })
        .catch((e: Error) => {
          // Упавший бэкенд не опрашиваем вечно: три ошибки подряд — останавливаемся
          if (++failures >= 3) {
            clearInterval(timer);
            setError(`${e.message}. Опрос остановлен, обновите страницу.`);
          }
        });
    }, 1000);
    return () => clearInterval(timer);
  }, [run?.id, active]);

  const set = (key: keyof RequestForm) => (e: { target: { value: string } }) =>
    setForm((f) => ({ ...f, [key]: e.target.value }));
  const gaps = missing(form);
  const badHours = hoursInvalid(form);
  const invalid = (key: RequiredKey) => tried && gaps.includes(key);

  async function start(e: FormEvent) {
    e.preventDefault();
    setTried(true);
    if (gaps.length || badHours) return;
    setError("");
    setBusy(true);
    try {
      setRun(await api.startRun(toTask(form), ""));
      setCollapsed(true);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function decide(approve: boolean) {
    if (!run) return;
    setDeciding(true);
    try {
      await api.decide(run.id, approve);
      setRun(await api.getRun(run.id));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setDeciding(false);
    }
  }

  const focused = !!run && collapsed;
  const shellClass = focused ? "shell shell-focused" : run ? "shell" : "shell shell-intro";

  return (
    <main className={shellClass}>
      <section className="brief">
        <header>
          <h1>{brand.name}</h1>
          <p className="lede">{brand.lede}</p>
        </header>

        <div className="samples">
          <span id="samples-label">Попробовать на примере</span>
          <div role="group" aria-labelledby="samples-label">
            {samples.map((s) => (
              <button
                key={s.id}
                className="chip"
                type="button"
                onClick={() => {
                  setForm(fromText(s.task));
                  setTried(false);
                }}
              >
                {s.label}
              </button>
            ))}
          </div>
        </div>

        <form className="request" onSubmit={start} noValidate>
          <div className="field">
            <label htmlFor="category">{FORM.category}</label>
            <select id="category" value={form.category} onChange={set("category")} aria-invalid={invalid("category")}>
              <option value="">{FORM.categoryAny}</option>
              {CATEGORIES.map((c) => <option key={c}>{c}</option>)}
            </select>
          </div>
          <div className="field">
            <label htmlFor="city">{FORM.city}</label>
            <select id="city" value={form.city} onChange={set("city")} aria-invalid={invalid("city")}>
              <option value="">{FORM.cityAny}</option>
              {CITIES.map((c) => <option key={c}>{c}</option>)}
            </select>
          </div>
          <div className="field">
            <label htmlFor="date">{FORM.date}</label>
            <input id="date" type="date" min={DATE_MIN} max={DATE_MAX} value={form.date} onChange={set("date")} aria-invalid={invalid("date")} />
          </div>
          <div className="field">
            <label htmlFor="format">{FORM.format}</label>
            <select id="format" value={form.format} onChange={set("format")} aria-invalid={invalid("format")}>
              <option value="">{FORM.formatAny}</option>
              {FORMATS.map((f) => <option key={f}>{f}</option>)}
            </select>
          </div>
          <div className="field field-wide">
            <label htmlFor="budget">{FORM.budget}</label>
            <input
              id="budget"
              inputMode="numeric"
              placeholder={FORM.budgetPlaceholder}
              value={form.budget ? Number(form.budget).toLocaleString("ru-RU") : ""}
              onChange={(e) => setForm((f) => ({ ...f, budget: e.target.value.replace(/\D/g, "").slice(0, 9) }))}
              aria-invalid={invalid("budget")}
            />
          </div>
          <div className="field">
            <label htmlFor="hours">{FORM.hours}</label>
            <input id="hours" type="number" min={1} max={24} placeholder={FORM.hoursPlaceholder} value={form.hours} onChange={set("hours")} aria-invalid={badHours} />
          </div>
          <div className="field">
            <label htmlFor="language">{FORM.language}</label>
            <select id="language" value={form.language} onChange={set("language")}>
              <option value="">{FORM.languageAny}</option>
              {LANGUAGES.map((l) => <option key={l}>{l}</option>)}
            </select>
          </div>
          <div className="field field-wide">
            <label htmlFor="wishes">{FORM.wishes}</label>
            <textarea
              id="wishes"
              rows={2}
              value={form.wishes}
              onChange={set("wishes")}
              placeholder={FORM.wishesPlaceholder}
            />
          </div>

          {tried && (gaps.length > 0 || badHours) && (
            <p className="error field-wide" role="alert">
              {gaps.length > 0 && `${FORM.fillIn} ${gaps.map((k) => REQUIRED_LABELS[k]).join(", ")}.`}
              {gaps.length > 0 && badHours && " "}
              {badHours && FORM.badHours}
            </p>
          )}

          <button type="submit" className="btn btn-primary field-wide" disabled={busy || !!active}>
            {busy ? brand.startingButton : brand.startButton}
          </button>
        </form>

        {error && <p className="error" role="alert">{error}</p>}
        {health && (
          <p className="meta">
            {health.mock
              ? `Mock-режим: модель выключена, шаги заскриптованы. Домен: ${health.domain}. Задайте OPENAI_API_KEY и OPENAI_MODEL в .env.`
              : `Модель: ${health.model}. Домен: ${health.domain}`}
          </p>
        )}
      </section>

      <section className="case" aria-label="Журнал работы агента">
        {run ? (
          <>
            <div className="case-top">
              <span className="mark">{brand.name}</span>
              <button className="btn-link" onClick={() => setCollapsed((c) => !c)}>
                {collapsed ? brand.editRequest : brand.hidePanel}
              </button>
            </div>
            <div className={`status status-${run.status}`}>
              <strong>{STATUS_TEXT[run.status]}</strong>
              <span>{run.task}</span>
              {run.tokens.prompt + run.tokens.completion > 0 && (
                <span className="tokens">
                  {run.tokens.prompt + run.tokens.completion} токенов
                </span>
              )}
            </div>
            {run.status === "done" && <Summary run={run} />}
            <Journal run={run} deciding={deciding} onDecide={decide} />
          </>
        ) : (
          <div className="empty">
            <h2>{brand.emptyTitle}</h2>
            <p>{brand.emptyHint}</p>
          </div>
        )}
      </section>
    </main>
  );
}
