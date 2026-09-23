"use client";

import ReactMarkdown from "react-markdown";
import { RESULTS } from "@/lib/brand";
import type { Run, Step } from "@/lib/types";

const KIND_LABEL: Record<Step["kind"], string> = {
  thought: "Рассуждение",
  tool_call: "Вызов инструмента",
  tool_result: "Результат",
  approval: "Решение человека",
  final: "Отчёт",
  error: "Ошибка",
};

function pretty(value: unknown) {
  return JSON.stringify(value, null, 2);
}

/** Секунды от начала запуска: видно, где агент думал, а где ждал человека. */
function offset(step: Step, startedAt: string) {
  const seconds = (new Date(step.at).getTime() - new Date(startedAt).getTime()) / 1000;
  return seconds >= 0 && Number.isFinite(seconds) ? `+${seconds.toFixed(1)} с` : "";
}

function StepBody({ step }: { step: Step }) {
  const c = step.content;
  if (step.kind === "thought") return <p className="step-text">{String(c.text ?? "")}</p>;
  // Главное из ответа уже показано карточками над журналом — здесь исходный текст, свёрнутым
  if (step.kind === "final")
    return (
      <details>
        <summary>{RESULTS.modelText}</summary>
        <div className="report">
          <ReactMarkdown>{String(c.text ?? "")}</ReactMarkdown>
        </div>
      </details>
    );
  if (step.kind === "tool_call") return <pre className="data">{`${step.title}(${c.args && Object.keys(c.args as object).length ? pretty(c.args) : ""})`}</pre>;
  if (step.kind === "tool_result")
    return (
      <details>
        <summary>Показать ответ {step.title}</summary>
        <pre className="data">{pretty(c.result)}</pre>
      </details>
    );
  if (step.kind === "approval") return <p className="step-text">{step.title}</p>;
  return <pre className="data">{pretty(c)}</pre>;
}

interface Props {
  run: Run;
  deciding: boolean;
  onDecide: (approve: boolean) => void;
}

export function Journal({ run, deciding, onDecide }: Props) {
  const last = run.steps[run.steps.length - 1];
  const waiting = run.status === "awaiting_approval" && last?.kind === "tool_call";

  return (
    <ol className="journal" aria-live="polite">
      {run.steps.map((step) => {
        const isGate = waiting && step.id === last.id;
        return (
          <li key={step.id} className={`step step-${step.kind}${isGate ? " step-gate" : ""}`}>
            <span className="step-kind">
              {KIND_LABEL[step.kind]}
              <time className="step-at" dateTime={step.at}>
                {offset(step, run.created_at)}
              </time>
            </span>
            <StepBody step={step} />
            {isGate && (
              <div className="gate">
                <p>
                  Это действие меняет внешнюю систему. Агент ждёт вашего решения
                  {typeof step.content.args === "object" && step.content.args && "reason" in step.content.args
                    ? `: ${String((step.content.args as { reason: unknown }).reason)}`
                    : "."}
                </p>
                <div className="gate-actions">
                  <button className="btn btn-approve" disabled={deciding} onClick={() => onDecide(true)}>
                    Разрешить
                  </button>
                  <button className="btn btn-quiet" disabled={deciding} onClick={() => onDecide(false)}>
                    Отклонить
                  </button>
                </div>
              </div>
            )}
          </li>
        );
      })}
      {run.status === "running" && (
        <li className="step step-pending">
          <span className="step-kind">Агент работает</span>
        </li>
      )}
    </ol>
  );
}
