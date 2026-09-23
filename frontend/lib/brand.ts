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
  wishesLabel: "Пожелания (необязательно)",
  wishesPlaceholder: "Например: драйв и энергия, без банальных конкурсов",
  missingPrefix: "Осталось заполнить:",
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
  wishes: string;
}

/**
 * The agent still accepts prose, so the structured form produces one stable request.
 * Без подписей полей («Город:», «Формат:»): бэкенд ищет общие слова запроса и описания,
 * и подпись совпала бы с описанием как ложный признак — а это меняет и объяснение, и порядок.
 * «мероприятие», «бюджет», «тенге», «хочу» — в стоп-листе бэкенда. Пожелания идут последними:
 * только по ним и должна считаться релевантность описания.
 */
export function buildRequestText(request: ContractorRequestText): string {
  const parts = [
    `Подбор: ${request.category}`,
    request.city,
    request.date,
    `мероприятие — ${request.eventFormat}`,
    `бюджет ${Number(request.budget)} тенге`,
  ];
  if (request.duration) parts.push(`${Number(request.duration)} ч`);
  if (request.language) parts.push(request.language);
  const wishes = request.wishes.trim();
  return `${parts.join(", ")}.${wishes ? ` Хочу: ${wishes}` : ""}`;
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

/** Как называть незаполненное обязательное поле в подсказке под кнопкой. */
export const MISSING_LABELS = {
  city: "город",
  category: "категорию",
  date: "дату",
  eventFormat: "формат",
  budget: "бюджет",
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

export const HONESTY_TEXT = {
  sectionLabel: "Происхождение данных подрядчиков",
  sourceProfile: "Исходный профиль",
  syntheticProfile: "Добавлен командой",
  imputedPrice: "Цена ориентировочная",
  imputedCity: "Город указан командой",
} as const;
