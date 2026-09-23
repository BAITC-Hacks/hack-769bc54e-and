/**
 * Форма запроса на подбор. Бэкенд принимает запрос текстом, агент разбирает его в параметры
 * search_contractors — поэтому форма собирает из полей однозначную фразу с датой в ISO
 * и бюджетом числом, которую одинаково читают и модель, и mock-разбор.
 */

// Словари каталога. Временно здесь: B2 заменит их на GET /api/options.
export const CITIES = ["Алматы", "Астана", "Зарубежье"] as const;
export const CATEGORIES = [
  "Банкетный зал", "Ведущий", "Ведущий церемонии", "Видеограф", "Декоратор",
  "Загородная площадка", "Инструменталист", "Лайв-бэнд", "Национальный ансамбль", "Отель",
  "Подарки и сувениры", "Ресторан", "Танцевальный коллектив", "Флорист", "Фото и видеобудки",
  "Фотограф", "Шоу-программа",
] as const;
export const FORMATS = ["свадьба", "той", "корпоратив", "конференция", "юбилей", "день рождения"] as const;
export const LANGUAGES = ["русский", "казахский", "английский"] as const;

/** Окно, для которого в датасете есть календарь занятости. */
export const DATE_MIN = "2026-09-23";
export const DATE_MAX = "2026-12-31";

export interface RequestForm {
  city: string;
  category: string;
  date: string; // ГГГГ-ММ-ДД из <input type="date">
  format: string;
  budget: string; // строкой, чтобы поле можно было очистить
  hours: string;
  language: string;
  wishes: string;
}

export const EMPTY_FORM: RequestForm = {
  city: "", category: "", date: "", format: "", budget: "", hours: "", language: "", wishes: "",
};

export type RequiredKey = "city" | "category" | "date" | "format" | "budget";

export const REQUIRED_LABELS: Record<RequiredKey, string> = {
  category: "категорию",
  city: "город",
  date: "дату",
  format: "формат",
  budget: "бюджет",
};

const positiveInt = (v: string) => /^\d+$/.test(v) && Number(v) > 0;

/** Какие из пяти обязательных полей ещё не заполнены. Пусто — можно отправлять. */
export function missing(form: RequestForm): RequiredKey[] {
  const out: RequiredKey[] = [];
  if (!form.category) out.push("category");
  if (!form.city) out.push("city");
  if (!/^\d{4}-\d{2}-\d{2}$/.test(form.date)) out.push("date");
  if (!form.format) out.push("format");
  if (!positiveInt(form.budget)) out.push("budget");
  return out;
}

/** Длительность необязательна, но если введена — целое число часов. */
export function hoursInvalid(form: RequestForm): boolean {
  return form.hours !== "" && !(positiveInt(form.hours) && Number(form.hours) <= 24);
}

/**
 * Текст запроса для агента. Подписей полей («город», «формат») нет намеренно: бэкенд ищет
 * общие слова запроса и описания, и подпись совпала бы с описанием как ложный признак.
 * «мероприятие», «бюджет», «тенге», «хочу» — в стоп-листе бэкенда. Пожелания идут последними:
 * по ним считается релевантность описания.
 */
export function toTask(form: RequestForm): string {
  const parts = [
    `Подбор: ${form.category}`,
    form.city,
    form.date,
    `мероприятие — ${form.format}`,
    `бюджет ${Number(form.budget)} тенге`,
  ];
  if (form.hours) parts.push(`${Number(form.hours)} ч`);
  if (form.language) parts.push(form.language);
  let task = parts.join(", ") + ".";
  const wishes = form.wishes.trim();
  if (wishes) task += ` Хочу: ${wishes}`;
  return task;
}

// ---------- Разбор примера в поля формы ----------

const stems = (s: string) => (s.toLowerCase().match(/\p{L}+/gu) ?? []).filter((w) => w.length > 2).map((w) => w.slice(0, 5));

/** Самый длинный вариант, все основы которого есть в тексте: «Ведущий церемонии» раньше «Ведущий». */
function pick(text: string, options: readonly string[]): string {
  const low = text.toLowerCase();
  const sorted = [...options].sort((a, b) => stems(b).length - stems(a).length || b.length - a.length);
  return sorted.find((o) => stems(o).every((s) => low.includes(s))) ?? "";
}

const MONTHS: Record<string, number> = {
  января: 1, февраля: 2, марта: 3, апреля: 4, мая: 5, июня: 6,
  июля: 7, августа: 8, сентября: 9, октября: 10, ноября: 11, декабря: 12,
};

/** Заполнить форму из текста примера с бэкенда (SAMPLES). Чего не нашли — остаётся пустым. */
export function fromText(text: string): RequestForm {
  const low = text.toLowerCase();
  let date = low.match(/\b20\d\d-\d\d-\d\d\b/)?.[0] ?? "";
  const m = low.match(new RegExp(`(\\d{1,2})\\s+(${Object.keys(MONTHS).join("|")})\\s+(20\\d\\d)`));
  if (!date && m) date = `${m[3]}-${String(MONTHS[m[2]]).padStart(2, "0")}-${m[1].padStart(2, "0")}`;
  const budget = text.replace(/[\s ]/g, "").match(/\d{5,9}/)?.[0] ?? "";
  const hours = low.match(/(\d{1,2})\s*(?:час|ч(?!\p{L}))/u)?.[1] ?? "";

  return {
    city: pick(text, CITIES),
    category: pick(text, CATEGORIES),
    date,
    format: pick(text, FORMATS),
    budget,
    hours,
    language: pick(text, LANGUAGES),
    wishes: "",
  };
}
