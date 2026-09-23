/**
 * Карточки подбора. Кто показан и в каком порядке — решает search_contractors (детерминированно),
 * модель даёт только тексты объяснений: нумерованный список `**Имя** — объяснение`
 * (REPORT_FORMAT в backend/agent/core/domains/contractors.py). Здесь они сводятся вместе.
 */
import type { ContractorCard, Run, SearchResult } from "./types";

function isSearchResult(value: unknown): value is SearchResult {
  if (typeof value !== "object" || value === null) return false;
  const r = value as { outcome?: unknown; cards?: unknown };
  return typeof r.outcome === "string" && Array.isArray(r.cards);
}

/** Последний успешный ответ search_contractors в журнале. Ответ с ошибкой — не результат. */
export function findSearchResult(run: Run): SearchResult | null {
  for (let i = run.steps.length - 1; i >= 0; i--) {
    const step = run.steps[i];
    if (step.kind === "tool_result" && step.title === "search_contractors" && isSearchResult(step.content.result)) {
      return step.content.result;
    }
  }
  return null;
}

export interface ReportItem {
  /** Пусто, если модель не выделила имя жирным */
  name: string;
  text: string;
}

export interface ReportParts {
  intro: string;
  items: ReportItem[];
  outro: string;
}

export const EMPTY_REPORT: ReportParts = { intro: "", items: [], outro: "" };

const ITEM = /^\d+[.)]\s+(.*)$/;
// «**Имя** — текст», «**Имя**: текст», «**Имя** (Ведущий) — текст»
const NAMED = /^\*\*(.+?)\*\*(?:[^—–:]{0,40}[—–:])?\s*(.*)$/;

/** Разобрать ответ модели: строка до списка, пункты списка, строки после него. */
export function parseReport(text: string): ReportParts {
  const intro: string[] = [];
  const outro: string[] = [];
  const items: ReportItem[] = [];
  let inCode = false;
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (line.startsWith("```")) {
      inCode = !inCode;
      continue;
    }
    if (inCode || !line) continue;
    const item = line.match(ITEM);
    if (item) {
      const named = item[1].match(NAMED);
      items.push(named ? { name: named[1].trim(), text: named[2].trim() } : { name: "", text: item[1].trim() });
    } else {
      (items.length ? outro : intro).push(line);
    }
  }
  return { intro: intro.join("\n\n"), items, outro: outro.join("\n\n") };
}

const norm = (s: string) => s.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();

/**
 * Объяснение для каждой карточки. Сначала по имени: имена в каталоге уникальны, и чужое
 * объяснение под карточкой хуже, чем никакого (R14). По позиции — только если модель
 * не выделила имя, а порядок по контракту совпадает с порядком карточек.
 */
export function explanations(cards: ContractorCard[], items: ReportItem[]): (string | null)[] {
  return cards.map((card, i) => {
    const byName = items.find((it) => it.name && norm(it.name) === norm(card.name));
    if (byName) return byName.text || null;
    const atPosition = items[i];
    return atPosition && !atPosition.name ? atPosition.text || null : null;
  });
}

export const kzt = (n: number) => `${n.toLocaleString("ru-RU")} ₸`;

/** Цитата обрезана бэкендом по длине — многоточие показывает, что фраза не закончена. */
export const quoteText = (quote: string) => (/[.!?…»]$/.test(quote) ? quote : `${quote}…`);

const capitalize = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/**
 * Объяснение без модели — только из посчитанных фактов. Нужно в mock-режиме: проверяющий
 * без ключей видит настоящий отбор, и карточка без объяснения выглядела бы сломанной.
 */
export function factsLine(card: ContractorCard): string {
  const { budget, format, languages, duration } = card.match;
  const out: string[] = [];
  if (budget) {
    const { price_from_kzt: price, budget_kzt: ceiling, headroom_percent: headroom } = budget;
    out.push(
      headroom > 0
        ? `Цена от ${kzt(price)} — на ${headroom}% ниже бюджета ${kzt(ceiling)}.`
        : `Цена от ${kzt(price)} — ровно в бюджет ${kzt(ceiling)}.`,
    );
  }
  const bits: string[] = [];
  if (format.requested)
    bits.push(`работает с форматом «${format.requested}»${format.accepts.length === 1 ? " и только с ним" : ""}`);
  if (languages.length) bits.push(`языки: ${languages.join(", ")}`);
  if (duration.requested && duration.max_hours) bits.push(`до ${duration.max_hours} ч при запросе ${duration.requested}`);
  else if (duration.requested && duration.not_time_bound) bits.push("работа не привязана к часам");
  if (bits.length) out.push(`${capitalize(bits.join("; "))}.`);
  return out.join(" ");
}
