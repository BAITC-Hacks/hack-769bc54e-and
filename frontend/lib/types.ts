export type RunStatus = "running" | "awaiting_approval" | "done" | "failed";
export type StepKind = "thought" | "tool_call" | "tool_result" | "approval" | "final" | "error";

export interface Step {
  id: number;
  kind: StepKind;
  title: string;
  content: Record<string, unknown>;
  at: string;
}

export interface Run {
  id: string;
  task: string;
  status: RunStatus;
  final_report: string;
  created_at: string;
  tokens: { prompt: number; completion: number };
  steps: Step[];
}

export interface Sample {
  id: string;
  label: string;
  task: string;
  input: string;
}

export interface ContractorOptions {
  cities: string[];
  categories: string[];
  event_formats: string[];
  languages: string[];
}

export interface ContractorFlags {
  synthetic: boolean;
  price_imputed: boolean;
  city_imputed: boolean;
}

export interface Health {
  ok: boolean;
  mock: boolean;
  /** null в mock-режиме: настоящая модель не используется */
  model: string | null;
  domain: string;
}

// ---------- Результат search_contractors (backend/agent/core/domains/contractors.py) ----------
// Приходит не из views.py, а внутри шага журнала: Step.content.result у tool_result.

export type SearchOutcome =
  | "matched"
  | "all_filtered_out"
  | "no_category_in_city"
  | "unknown_city"
  | "unknown_category";

/** Коды причин отсева из _reasons: по ним считается rejected_by_reason. */
export type RejectReason = "busy" | "format" | "budget" | "language" | "duration";

/** Диагностика приходит вместе с поиском, когда карточек меньше трёх. */
export interface Diagnosis {
  /** Готовые формулировки «что изменить» — показываются дословно */
  suggestions: string[];
  /** Бедный месяц — это сезон, а не сбой; null, если месяц не выделяется */
  season_note?: string | null;
}

/** Посчитанные факты совпадения: на них модель строит объяснение. */
export interface CardMatch {
  /** Нет, если у профиля не указана цена */
  budget?: { price_from_kzt: number; budget_kzt: number; headroom_percent: number };
  shared_words: string[];
  /** Точный фрагмент описания; null, если общих слов с запросом нет */
  quote: string | null;
  format: { requested: string | null; accepts: string[] };
  languages: string[];
  duration: { max_hours: number | null; requested: number | null; not_time_bound: boolean };
}

export interface ContractorCard {
  id: string;
  name: string;
  categories: string[];
  city: string;
  price_from_kzt: number | null;
  description: string;
  score: number;
  match: CardMatch;
  flags: ContractorFlags;
}

export interface SearchResult {
  outcome: SearchOutcome;
  /** Нет при unknown_city и unknown_category */
  in_city_and_category?: number;
  /** Нет при outcome = no_category_in_city */
  passed_filters?: number;
  cards: ContractorCard[];
  /** Сколько отсеянных задела каждая причина; у одного кандидата их может быть несколько */
  rejected_by_reason?: Partial<Record<RejectReason, number>>;
  diagnosis?: Diagnosis;
  note?: string;
  warning?: string;
  fewer_than_three?: boolean;
}
