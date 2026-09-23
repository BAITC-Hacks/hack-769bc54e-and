import type { RunStatus } from "./types";

/**
 * Всё, что придётся переименовать в день хакатона, — здесь.
 * Больше нигде в интерфейсе названий и доменных формулировок быть не должно.
 */
export const brand = {
  name: "Точный подрядчик",
  title: "Точный подрядчик — умный подбор для мероприятий",
  description:
    "Подбор до трёх свободных event-подрядчиков с понятным объяснением каждого результата.",
  lede: "Подбирает до трёх свободных подрядчиков по каталогу и объясняет каждый результат конкретными фактами.",
  cityLabel: "Город",
  categoryLabel: "Категория",
  dateLabel: "Дата",
  eventFormatLabel: "Формат мероприятия",
  budgetLabel: "Бюджет на подрядчика, ₸",
  durationLabel: "Длительность, часов (необязательно)",
  languageLabel: "Язык (необязательно)",
  requiredPlaceholder: "Выберите значение",
  optionalPlaceholder: "Не важно",
  budgetPlaceholder: "Например, 900000",
  durationPlaceholder: "Например, 5",
  optionsError: "Не удалось загрузить варианты формы.",
  startButton: "Подобрать подрядчиков",
  startingButton: "Подбираю",
  emptyTitle: "Здесь появится подбор",
  emptyHint:
    "Заполните пять обязательных полей. Агент покажет ход отбора и объяснит, почему каждый подрядчик попал в результат.",
  editRequest: "Изменить запрос",
  hidePanel: "Свернуть панель",
} as const;

export interface ContractorRequestText {
  city: string;
  category: string;
  date: string;
  eventFormat: string;
  budget: string;
  duration: string;
  language: string;
}

/** The agent still accepts prose, so the structured form produces one stable request. */
export function buildRequestText(request: ContractorRequestText): string {
  const parts = [
    `Город: ${request.city}`,
    `Категория: ${request.category}`,
    `Дата: ${request.date}`,
    `Формат мероприятия: ${request.eventFormat}`,
    `Бюджет на подрядчика: ${request.budget} ₸`,
  ];
  if (request.duration) parts.push(`Длительность: ${request.duration} ч`);
  if (request.language) parts.push(`Язык: ${request.language}`);
  return `Подбери подрядчиков. ${parts.join(". ")}.`;
}

/** Подписи блока с карточками подбора. */
export const RESULTS = {
  title: "Кого подобрали",
  count: (shown: number, passed: number, total: number) =>
    `Подходят ${passed} из ${total} в этой категории и городе · показано ${shown}`,
  priceUnknown: "цена не указана",
  pricePrefix: "от",
  writing: "Модель пишет объяснение…",
  factsNote: "Без модели: собрано из фактов каталога",
  quoteLabel: "Из описания подрядчика",
  modelText: "Текст ответа модели",
} as const;

/** Подписи полоски итогов. Цифры под ними — главный аргумент в питче. */
export const SUMMARY_LABELS = {
  time: "Время",
  tools: "Инструментов",
  tokens: "Токенов",
} as const;

export const STATUS_TEXT: Record<RunStatus, string> = {
  running: "Работает",
  awaiting_approval: "Ждёт вашего решения",
  done: "Завершено",
  failed: "Остановлено с ошибкой",
};
