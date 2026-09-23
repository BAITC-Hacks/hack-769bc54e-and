"use client";

import { useEffect, useState } from "react";
import { Journal } from "@/components/Journal";
import { api } from "@/lib/api";
import { SUMMARY_LABELS, STATUS_TEXT, brand } from "@/lib/brand";
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
  const [task, setTask] = useState("");
  const [input, setInput] = useState("");
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

  async function start() {
    setError("");
    setBusy(true);
    try {
      setRun(await api.startRun(task, input));
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
                onClick={() => {
                  setTask(s.task);
                  setInput(s.input);
                }}
              >
                {s.label}
              </button>
            ))}
          </div>
        </div>

        <label htmlFor="task">{brand.taskLabel}</label>
        <textarea
          id="task"
          rows={3}
          value={task}
          onChange={(e) => setTask(e.target.value)}
          placeholder={brand.taskPlaceholder}
        />

        <label htmlFor="input">{brand.inputLabel}</label>
        <textarea id="input" className="mono" rows={16} value={input} onChange={(e) => setInput(e.target.value)} spellCheck={false} />

        <button className="btn btn-primary" onClick={start} disabled={busy || !task.trim() || !!active}>
          {busy ? brand.startingButton : brand.startButton}
        </button>

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
