"use client";

import { useEffect, useState, type FormEvent } from "react";
import { Journal } from "@/components/Journal";
import { Results } from "@/components/Results";
import { api } from "@/lib/api";
import {
  DATE_COMPARISON, MISSING_LABELS, SUMMARY_LABELS, STATUS_TEXT, brand, buildRequestText, requestSummary,
} from "@/lib/brand";
import { differingContractorIds, findSearchResult } from "@/lib/results";
import type { ContractorOptions, Health, Run } from "@/lib/types";

interface RequestForm {
  city: string;
  category: string;
  date: string;
  eventFormat: string;
  budget: string;
  duration: string;
  language: string;
  wishes: string;
}

const EMPTY_FORM: RequestForm = {
  city: "",
  category: "",
  date: "",
  eventFormat: "",
  budget: "",
  duration: "",
  language: "",
  wishes: "",
};

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
  const [options, setOptions] = useState<ContractorOptions | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [busy, setBusy] = useState(false);
  const [deciding, setDeciding] = useState(false);
  const [error, setError] = useState("");
  const [identitiesHidden, setIdentitiesHidden] = useState(false);
  const [comparisonOpen, setComparisonOpen] = useState(false);
  const [comparisonDate, setComparisonDate] = useState("");
  const [comparisonRun, setComparisonRun] = useState<Run | null>(null);
  const [comparisonBusy, setComparisonBusy] = useState(false);
  const [primaryRequest, setPrimaryRequest] = useState<RequestForm | null>(null);
  // После запуска форма уезжает: на проекторе журнал должен занимать весь экран
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    api
      .options()
      .then(setOptions)
      .catch(() => setError(brand.optionsError));
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

  const comparisonActive = comparisonRun &&
    (comparisonRun.status === "running" || comparisonRun.status === "awaiting_approval");
  useEffect(() => {
    if (!comparisonRun?.id || !comparisonActive) return;
    let failures = 0;
    const timer = setInterval(() => {
      api
        .getRun(comparisonRun.id)
        .then((fresh) => {
          failures = 0;
          setComparisonRun(fresh);
        })
        .catch((e: Error) => {
          if (++failures >= 3) {
            clearInterval(timer);
            setError(`${e.message}. Опрос остановлен, обновите страницу.`);
          }
        });
    }, 1000);
    return () => clearInterval(timer);
  }, [comparisonRun?.id, comparisonActive]);

  async function start(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!requiredComplete) return;
    setError("");
    setBusy(true);
    try {
      const request = { ...form };
      setRun(await api.startRun(buildRequestText(request), ""));
      setPrimaryRequest(request);
      setComparisonRun(null);
      setComparisonOpen(false);
      setComparisonDate("");
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

  function openComparison() {
    const next = new Date(`${primaryRequest?.date ?? form.date}T00:00:00Z`);
    next.setUTCDate(next.getUTCDate() + 1);
    setComparisonDate(next.toISOString().slice(0, 10));
    setComparisonOpen(true);
  }

  async function compareDate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const request = primaryRequest ?? form;
    if (!comparisonDate || comparisonDate === request.date || comparisonActive) return;
    setError("");
    setComparisonBusy(true);
    try {
      setComparisonRun(await api.startRun(buildRequestText({ ...request, date: comparisonDate }), ""));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setComparisonBusy(false);
    }
  }

  const focused = !!run && collapsed;
  const shellClass = focused ? "shell shell-focused" : run ? "shell" : "shell shell-intro";
  const missing = (Object.keys(MISSING_LABELS) as (keyof typeof MISSING_LABELS)[]).filter((field) =>
    field === "budget" ? !(Number(form.budget) > 0) : !form[field],
  );
  const requiredComplete = missing.length === 0;
  const primaryResult = run ? findSearchResult(run) : null;
  const secondaryResult = comparisonRun ? findSearchResult(comparisonRun) : null;
  const differingIds = primaryResult && secondaryResult
    ? differingContractorIds(primaryResult, secondaryResult)
    : new Set<string>();
  const primaryDate = primaryRequest?.date ?? form.date;

  function update<K extends keyof RequestForm>(field: K, value: RequestForm[K]) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  return (
    <main className={shellClass}>
      <section className="brief">
        <header>
          <h1>{brand.name}</h1>
          <p className="lede">{brand.lede}</p>
        </header>

        <form className="request-form" onSubmit={start}>
          <div className="field-grid">
            <div className="field">
              <label htmlFor="city">{brand.cityLabel}</label>
              <select id="city" required value={form.city} onChange={(e) => update("city", e.target.value)}>
                <option value="">{brand.requiredPlaceholder}</option>
                {options?.cities.map((value) => <option key={value}>{value}</option>)}
              </select>
            </div>
            <div className="field">
              <label htmlFor="category">{brand.categoryLabel}</label>
              <select id="category" required value={form.category} onChange={(e) => update("category", e.target.value)}>
                <option value="">{brand.requiredPlaceholder}</option>
                {options?.categories.map((value) => <option key={value}>{value}</option>)}
              </select>
            </div>
            <div className="field">
              <label htmlFor="date">{brand.dateLabel}</label>
              <input id="date" type="date" required value={form.date} onChange={(e) => update("date", e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="event-format">{brand.eventFormatLabel}</label>
              <select id="event-format" required value={form.eventFormat} onChange={(e) => update("eventFormat", e.target.value)}>
                <option value="">{brand.requiredPlaceholder}</option>
                {options?.event_formats.map((value) => <option key={value}>{value}</option>)}
              </select>
            </div>
            <div className="field field-wide">
              <label htmlFor="budget">{brand.budgetLabel}</label>
              <input
                id="budget"
                type="number"
                min="1"
                step="1"
                required
                value={form.budget}
                placeholder={brand.budgetPlaceholder}
                onChange={(e) => update("budget", e.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="duration">{brand.durationLabel}</label>
              <input
                id="duration"
                type="number"
                min="1"
                step="1"
                value={form.duration}
                placeholder={brand.durationPlaceholder}
                onChange={(e) => update("duration", e.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="language">{brand.languageLabel}</label>
              <select id="language" value={form.language} onChange={(e) => update("language", e.target.value)}>
                <option value="">{brand.optionalPlaceholder}</option>
                {options?.languages.map((value) => <option key={value}>{value}</option>)}
              </select>
            </div>
            <div className="field field-wide">
              <label htmlFor="wishes">{brand.wishesLabel}</label>
              <textarea
                id="wishes"
                rows={2}
                value={form.wishes}
                placeholder={brand.wishesPlaceholder}
                onChange={(e) => update("wishes", e.target.value)}
              />
            </div>
          </div>

          <button className="btn btn-primary" type="submit" disabled={busy || !requiredComplete || !!active}>
            {busy ? brand.startingButton : brand.startButton}
          </button>
          {!requiredComplete && (
            <p className="meta form-missing">
              {brand.missingPrefix} {missing.map((field) => MISSING_LABELS[field]).join(", ")}
            </p>
          )}
        </form>

        {error && <p className="error" role="alert">{error}</p>}
        {health && (
          <p className="meta">
            {health.mock
              ? "Без ключа модели: отбор, порядок и диагностика настоящие, объяснения собраны из фактов каталога. Для текстов от LLM задайте OPENAI_API_KEY и OPENAI_MODEL в .env."
              : `Модель: ${health.model}`}
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
              <span>{primaryRequest ? requestSummary(primaryRequest) : run.task}</span>
              {primaryRequest?.wishes.trim() && (
                <span className="status-wishes">Пожелания: {primaryRequest.wishes.trim()}</span>
              )}
              {run.tokens.prompt + run.tokens.completion > 0 && (
                <span className="tokens">
                  {run.tokens.prompt + run.tokens.completion} токенов
                </span>
              )}
            </div>
            {run.status === "done" && <Summary run={run} />}
            {primaryResult && (
              <div className="comparison-controls">
                {!comparisonOpen ? (
                  <button className="btn btn-quiet" type="button" disabled={!!active} onClick={openComparison}>
                    {DATE_COMPARISON.openButton}
                  </button>
                ) : (
                  <form className="comparison-form" onSubmit={compareDate}>
                    <label htmlFor="comparison-date">{DATE_COMPARISON.dateLabel}</label>
                    <input
                      id="comparison-date"
                      type="date"
                      required
                      value={comparisonDate}
                      onChange={(event) => setComparisonDate(event.target.value)}
                    />
                    <button
                      className="btn btn-quiet"
                      type="submit"
                      disabled={comparisonBusy || !!comparisonActive || !comparisonDate || comparisonDate === primaryDate}
                    >
                      {comparisonBusy || comparisonActive ? DATE_COMPARISON.comparingButton : DATE_COMPARISON.compareButton}
                    </button>
                    {comparisonDate === primaryDate && <span className="meta">{DATE_COMPARISON.sameDate}</span>}
                  </form>
                )}
              </div>
            )}
            <div className={comparisonRun ? "comparison-grid" : undefined}>
              <Results
                run={run}
                mock={!!health?.mock}
                identitiesHidden={identitiesHidden}
                onToggleIdentities={() => setIdentitiesHidden((hidden) => !hidden)}
                title={comparisonRun ? DATE_COMPARISON.firstTitle(primaryDate) : undefined}
                titleId="results-primary-title"
                differingIds={differingIds}
              />
              {comparisonRun && (
                <div className="comparison-column">
                  {!secondaryResult && comparisonActive && (
                    <p className="meta comparison-waiting">{DATE_COMPARISON.waiting}</p>
                  )}
                  {!secondaryResult && comparisonRun.status === "failed" && (
                    <p className="error comparison-waiting">{DATE_COMPARISON.failed}</p>
                  )}
                  <Results
                    run={comparisonRun}
                    mock={!!health?.mock}
                    identitiesHidden={identitiesHidden}
                    onToggleIdentities={() => setIdentitiesHidden((hidden) => !hidden)}
                    title={DATE_COMPARISON.secondTitle(comparisonDate)}
                    titleId="results-comparison-title"
                    showIdentityToggle={false}
                    differingIds={differingIds}
                  />
                </div>
              )}
            </div>
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
