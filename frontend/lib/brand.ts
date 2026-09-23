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
  durationLabel: "Длительность, часов",
  languageLabel: "Язык",
  requiredPlaceholder: "Выберите значение",
  optionalPlaceholder: "Не важно",
  budgetPlaceholder: "Например, 900000",
  durationPlaceholder: "Например, 5",
  wishesLabel: "Пожелания",
  wishesPlaceholder: "Например: драйв и энергия, без банальных конкурсов",
  requiredLegend: "Поля со звёздочкой обязательны",
  requiredHint: "обязательное поле",
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

const MONTHS_GENITIVE = [
  "января", "февраля", "марта", "апреля", "мая", "июня",
  "июля", "августа", "сентября", "октября", "ноября", "декабря",
];

/** «2026-10-15» → «15 октября 2026». Что не разобралось — возвращается как есть. */
export function humanDate(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!m) return iso;
  return `${Number(m[3])} ${MONTHS_GENITIVE[Number(m[2]) - 1] ?? m[2]} ${m[1]}`;
}

/**
 * Запрос для человека — вместо текста, который уходит модели. Тот читается как лог;
 * этот стоит над результатом и должен читаться как заголовок.
 */
export function requestSummary(request: ContractorRequestText): string {
  const parts = [request.category, request.city, humanDate(request.date), request.eventFormat];
  if (Number(request.budget) > 0) parts.push(`до ${Number(request.budget).toLocaleString("ru-RU")} ₸`);
  if (request.duration) parts.push(`${Number(request.duration)} ч`);
  if (request.language) parts.push(request.language);
  return parts.join(" · ");
}

/** Подписи блока с карточками подбора. */
export const RESULTS = {
  title: "Результат подбора",
  priceUnknown: "цена не указана",
  pricePrefix: "от",
  writing: "Модель пишет объяснение…",
  factsNote: "Собрано из фактов каталога без модели",
  knownValues: "В каталоге есть:",
  quoteLabel: "Из описания подрядчика",
  modelText: "Текст ответа модели",
  searching: "Разбираю запрос и ищу по каталогу…",
  failedTitle: "Подбор не состоялся",
  failedHint: "Что пошло не так — в журнале ниже.",
  whyTitle: "Почему",
  whyHint: "У одного кандидата может быть несколько причин.",
  changeTitle: "Что можно сделать",
  hideIdentities: "Скрыть имена",
  showIdentities: "Показать имена",
  hiddenCardLabel: "Объяснение без имени",
  rejectedTitle: (count: number) => {
    const singular = count % 10 === 1 && count % 100 !== 11;
    const few = count % 10 >= 2 && count % 10 <= 4 && (count % 100 < 12 || count % 100 > 14);
    return `Ещё ${count} ${singular ? "кандидат не прошёл" : few ? "кандидата не прошли" : "кандидатов не прошли"}`;
  },
  hiddenRejectedLabel: (position: number) => `Кандидат ${position}`,
  responseTime: (seconds: number) => `Ответ за ${seconds.toFixed(1)} с`,
} as const;

/** Исход подбора: метка, заголовок и пояснение. Четыре вида различимы с первого взгляда (R12). */
export const OUTCOME = {
  full: {
    badge: "Полная выдача",
    title: (shown: number, passed: number) => `${shown} лучших из ${passed} подходящих`,
  },
  partial: {
    badge: "Меньше трёх",
    title: (shown: number) => (shown === 1 ? "Подходит только один" : `Подходят только ${shown}`),
  },
  none: {
    badge: "Никто не подошёл",
    title: (total: number) => `Отсеяны все кандидаты в этой категории и городе: ${total}`,
  },
  absent: {
    badge: "Нет в каталоге",
    unknownTitle: "Такого значения нет в каталоге",
  },
  passed: (passed: number, total: number) => `Прошли фильтры ${passed} из ${total} в этой категории и городе`,
} as const;

/** Причины отсева: код из бэкенда → как это сказать человеку. */
export const REJECT_REASONS = {
  busy: "заняты на эту дату",
  budget: "дороже бюджета",
  format: "не берут этот формат",
  language: "не работают на этом языке",
  duration: "не работают столько часов",
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

/**
 * Плашки честности (R22). Все флаги пришли в датасете организатора: synthetic — 13 профилей,
 * цена и город проставлены при подготовке у 18 и 8. Своих профилей команда не добавляла
 * (README, допущение 9) — поэтому ни одна подпись не говорит «командой».
 */
export const HONESTY_TEXT = {
  sectionLabel: "Происхождение данных подрядчиков",
  sourceProfile: "Настоящий профиль",
  syntheticProfile: "Синтетический профиль",
  imputedPrice: "Цена ориентировочная",
  imputedCity: "Город ориентировочный",
} as const;

export const DATE_COMPARISON = {
  openButton: "А если другая дата?",
  dateLabel: "Вторая дата",
  compareButton: "Сравнить выдачу",
  comparingButton: "Сравниваю",
  sameDate: "Выберите дату, отличную от первой",
  waiting: "Подбираем подрядчиков на вторую дату…",
  failed: "Не удалось получить выдачу на вторую дату.",
  firstTitle: (date: string) => `Выдача на ${humanDate(date)}`,
  secondTitle: (date: string) => `Выдача на ${humanDate(date)}`,
  onlyOnThisDate: "Только на этой дате",
} as const;
