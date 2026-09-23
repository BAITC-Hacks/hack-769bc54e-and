"""Домен Задачи #79-lite: умный подбор event-подрядчиков.

Главное архитектурное решение: **отбор и порядок — детерминированный код, LLM только
пишет объяснения.** Иначе не выполнить одновременно требование 5 (тот же запрос — тот
же порядок) и требование к качеству текста. Побочная выгода: ранжирование работает без
ключей, поэтому проверяющий без .env видит настоящий отбор, а не заглушку.

Инструменты:
  search_contractors  — жёсткие фильтры + скоринг, до 3 карточек с фактами совпадения;
                        при выдаче меньше трёх сам прикладывает диагностику;
  diagnose_request    — та же диагностика для гипотетического запроса.

Модель получает не профили, а посчитанные факты, поэтому объяснения не могут выйти
взаимозаменяемыми: в них числа и признаки конкретного подрядчика.
"""
import csv
import re
import datetime as dt
from functools import lru_cache
from pathlib import Path

from ..tools import RunContext, tool

DATA = Path(__file__).resolve().parents[2] / "data" / "contractors.csv"

# Окно, для которого в датасете есть календарь занятости
WINDOW_START = dt.date(2026, 9, 23)
WINDOW_END = dt.date(2026, 12, 31)

MAX_CARDS = 3
MAX_REJECTED_SHOWN = 8
SHIFT_DAYS = 14  # насколько двигаем дату при диагностике

# Веса скоринга. Фиксированные и опубликованные: от них зависит порядок карточек,
# поэтому менять их — значит менять выдачу. Сумма = 1.
WEIGHTS = {"budget": 0.35, "description": 0.30, "focus": 0.20, "languages": 0.15}

BRIEF = """\
Domain: an aggregator of event contractors in Kazakhstan (hosts, photographers, venues,
florists, bands and so on). The client has already chosen the city, the date, the event
format and the contractor category, and wants help choosing from the catalog.

How to work here:
- Call search_contractors once with the parameters from the request. It applies the hard
  filters and the ranking itself and returns at most three candidates together with the
  computed match facts. Never reorder its result: the order is part of the contract.
- When it returns fewer than three candidates, its answer already carries a `diagnosis`
  block with the ready wording. Use it; a second tool call is not needed. Call
  diagnose_request only if you want to probe a different request than the one you searched.
- Do not offer booking, applications or notifications: this service only recommends.
- Never reorder, drop or add candidates. The catalog decides who is shown and in what order;
  your job is only to say why each of them is there.
"""

# Карточки рисует интерфейс из результата search_contractors — от модели нужны только тексты
# объяснений в том же порядке. Нумерованный список одинаково читается и в разметке, и в коде.
REPORT_FORMAT = """\
Write the answer in Russian, in Markdown, in exactly this shape and nothing else.
Never write the name of a data field in the answer: words like availability_note, match,
quote, standouts, lead, diagnosis, suggestions belong to the machine, not to the reader.

1. The opening line is already written for you: reproduce the `opening` value word for word
   and add nothing to it. It already states how many were found and how many of the category
   are booked on that exact date, so two different dates never look identical.
   Guillemets «...» are reserved for literal fragments of a contractor's description.
   Never put the request, the city or the category in them: a quote the reader cannot check
   against the catalog is worthless.
2. A numbered list, one item per returned card, in the order the catalog returned them.
   Each item: `**Имя** — one or two sentences of explanation.` Two sentences maximum:
   a third one means the card no longer fits the format the task asks for. A quoted fragment
   attached with a colon or a semicolon does not count as a separate sentence.
3. If there are fewer than three cards, a closing block: first `diagnosis.headline`
   reproduced as it is — it already lists exactly which conditions blocked how many, and
   listing conditions that did not occur is a defect — then **every** line of
   the suggestions list, each as its own bullet, reproduced word for word. Not a summary
   of them, not a selection — all of them. They already contain the right numbers, dates and
   city names; inventing your own is a defect. When `diagnosis.season_note` is present, add it
   after the bullets: a thin month is the season, not a failure.
4. When there are no cards at all, there is no numbered list. Then the answer is: one plain
   sentence in human words saying that nothing fits and what the main obstacle was, then
   `diagnosis.headline` once, then the bullets. Never print `headline` twice.

Rules for the explanations:
- Build every sentence on the numbers and facts in `match`: budget headroom in percent,
  the price, the accepted formats, the working languages, the hours, and `match.quote`.
- `match.shared_words` lists the words the client's wishes and the description really share.
  When it is empty, the description matched nothing and you must not claim otherwise —
  saying "the description mentions weddings" when that word is not in `shared_words`
  is a fabrication, even if it sounds plausible. Say nothing about the description instead,
  or quote `match.quote`, which is always a literal fragment.
- Quote `match.quote` verbatim when it is present: it is an exact fragment of the contractor's
  own description. Never invent a quote and never paraphrase one. When a field is empty,
  simply leave it out — never tell the reader that data is missing and never name internal
  fields such as `match` or `quote` in the answer.
- The cards must stay distinguishable with the names removed. Two explanations that would fit
  each other equally well are a defect.
- `match.lead` is the one fact that belongs to this contractor and to no other card in the
  answer. **When it is present, the explanation must open with it**, before any price or
  format: it is the answer to "why this one and not the next". When it is null there is
  nothing to compare with — start with the price instead, and invent no ranking.
  `match.standouts` holds its other unique facts and never repeats the lead.
  Two cards opening with the same phrase is a defect.
- Forbidden: "отличный выбор", "прекрасно подойдёт", "идеальный вариант", "профессионал своего
  дела", "качественно и в срок", "не пожалеете" and any other praise that is not a fact from
  `match`. No adjectives that the data does not support.
- No booking advice, no contact details, no prices you did not get from the tool.
"""

SAMPLES = [
    # Требование 17 ТЗ: плотная категория на осеннюю дату, где ранжирование реально работает.
    # Даёт три карточки и два вызова модели вместо трёх — это же и самый быстрый сценарий.
    {
        "id": "dense",
        "label": "Ведущий на свадьбу, 15 октября",
        "task": "Нужен ведущий на свадьбу в Алматы 15 октября 2026, бюджет 900000 тенге.",
        "input": "",
    },
    # Требование 9: площадка — такой же запрос, только категория другая
    {
        "id": "venue",
        "label": "Банкетный зал, 24 октября",
        "task": "Подобрать банкетный зал в Алматы на свадьбу 24 октября 2026, бюджет 3000000 тенге.",
        "input": "",
    },
    # Требование 17: редкая категория — всего три профиля в каталоге
    {
        "id": "rare",
        "label": "Инструменталист — редкая категория",
        "task": "Нужен инструменталист на свадьбу в Алматы 15 октября 2026, бюджет 1000000 тенге.",
        "input": "",
    },
    # Требование 12, исход «кандидаты есть, но ни один не проходит»
    {
        "id": "all-busy",
        "label": "Отель на 26 декабря — все заняты",
        "task": "Подбери отель в Алматы на свадьбу 26 декабря 2026, бюджет 3000000 тенге.",
        "input": "",
    },
    # Требование 12, исход «в этом городе такой категории нет»
    {
        "id": "no-category",
        "label": "Лайв-бэнд в Астане — которого нет",
        "task": "Нужен лайв-бэнд в Астане на корпоратив 20 ноября 2026, бюджет 1500000 тенге.",
        "input": "",
    },
]


# --------------------------------------------------------------------------
# Датасет
# --------------------------------------------------------------------------


def _split(value: str) -> list[str]:
    return [p.strip() for p in (value or "").split("|") if p.strip()]


@lru_cache(maxsize=1)
def catalog() -> tuple[dict, ...]:
    """Профили из CSV. Кэшируется: файл не меняется во время работы."""
    rows = []
    with DATA.open(encoding="utf-8", newline="") as fh:
        for raw in csv.DictReader(fh):
            rows.append(
                {
                    "id": raw["id"],
                    "name": raw["anon_name"],
                    "categories": _split(raw["categories"]),
                    "city": raw["city"],
                    "price_from_kzt": int(raw["price_from_kzt"]) if raw["price_from_kzt"] else None,
                    "event_formats": _split(raw["event_formats"]),
                    "languages": _split(raw["languages"]),
                    # пусто = работа не привязана к присутствию, фильтр по часам к таким не применяется
                    "max_hours": int(raw["max_hours"]) if raw["max_hours"] else None,
                    "busy_dates": frozenset(_split(raw["busy_dates"])),
                    "description": raw["description"],
                    "synthetic": raw["synthetic"] == "True",
                    "price_imputed": raw["price_imputed"] == "True",
                    "city_imputed": raw["city_imputed"] == "True",
                }
            )
    return tuple(rows)


def _stems(value: str) -> list[str]:
    """Основы слов: «свадьбу» и «свадьба» должны совпадать без морфологии."""
    return [w[:5] for w in re.findall(r"[^\W\d_]+", value.lower()) if len(w) > 2]


def _match_by_stems(text: str, options) -> str | None:
    """Самый длинный вариант, все основы которого встречаются в тексте.

    Длинные проверяются первыми, иначе «Ведущий» перехватит «Ведущего церемонии».
    """
    low = text.lower()
    for option in sorted(options, key=lambda o: (-len(_stems(o)), -len(o))):
        if all(stem in low for stem in _stems(option)):
            return option
    return None


@lru_cache(maxsize=1)
def vocabulary() -> dict:
    """Допустимые значения полей, выведенные из каталога.

    Один источник правды: из него собираются enum в схеме инструмента, проверка
    входа и словари для формы интерфейса.
    """
    cities, categories, formats, languages = set(), set(), set(), set()
    for profile in catalog():
        cities.add(profile["city"])
        categories.update(profile["categories"])
        formats.update(profile["event_formats"])
        languages.update(profile["languages"])
    return {
        "cities": sorted(cities),
        "categories": sorted(categories),
        "event_formats": sorted(formats),
        "languages": sorted(languages),
    }


def _normalize(value: str | None, options: list[str]) -> str | None:
    """Приводит значение к каноническому виду каталога.

    Модель иногда присылает «Ведущие» вместо «Ведущий». Молча искать такую категорию
    нельзя: поиск вернёт ноль и скажет «в Алматы нет ведущих», что неправда.
    """
    if not value:
        return None
    value = value.strip()
    if value in options:
        return value
    lowered = {o.lower(): o for o in options}
    if value.lower() in lowered:
        return lowered[value.lower()]
    return _match_by_stems(value, options)


def _parse_date(value: str) -> dt.date:
    return dt.date.fromisoformat(value.strip())


# --------------------------------------------------------------------------
# Фильтры и скоринг
# --------------------------------------------------------------------------

# Слова самой формулировки запроса. Если их не выбросить, «подбери ведущего, формат
# классический» поднимает профиль со словом «форматы» в описании — совпадение с
# канцеляритом запроса, а не со смыслом пожеланий.
_STOP = {
    "и", "в", "на", "для", "с", "по", "до", "от", "не", "или", "а", "но", "мы", "наш",
    "это", "как", "что", "все", "его", "их", "мероприятие", "мероприятия",
    "нужен", "нужна", "нужно", "нужны", "требуется", "хочу", "хотим", "ищу", "ищем",
    "подбери", "подобрать", "подберите", "подбор", "подбора", "найди", "найти",
    "посоветуй", "порекомендуй",
    "тенге", "бюджет", "бюджета", "формат", "формата", "форматы", "категория", "город",
    "дата", "число", "часов", "часа", "человек", "гостей",
    "января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
    "сентября", "октября", "ноября", "декабря",
}


def _words(text: str) -> set[str]:
    cleaned = "".join(c.lower() if c.isalnum() else " " for c in text)
    # Числа выбрасываем: «2026» и «900000» не несут смысла для близости описаний
    return {w for w in cleaned.split() if len(w) > 3 and not w.isdigit() and w not in _STOP}


def _stemmed(text: str) -> dict[str, str]:
    """Основа -> первое встреченное слово с этой основой.

    Русский склоняется: «интерактив» в пожелании и «интерактивом» в описании — одно и то же.
    Точное совпадение слов такие пары теряет, а с ними теряется вся «близость по смыслу»
    из требования 2.
    """
    out: dict[str, str] = {}
    for word in _words(text):
        out.setdefault(word[:5], word)
    return out


def _noise(req: dict) -> set[str]:
    """Слова самих параметров запроса.

    «Ведущий на свадьбу в Алматы» совпадёт с описанием любого ведущего из Алматы —
    это не признак, а эхо запроса, и объяснение из него выходит пустым.
    """
    parts = [req.get(k) or "" for k in ("city", "category", "event_format", "language")]
    return _words(" ".join(parts))


QUOTE_MAX_CHARS = 180


# Тире здесь не разделитель: «Сон Гоку — один из самых востребованных» это одна фраза,
# а разрез по тире оставлял обрубок, начинающийся со строчной буквы.
# Закрывающая кавычка после точки не должна склеивать две фразы в одну
_SENTENCE_SPLIT = re.compile(r'(?<=[.!?])[»"\']*\s+|[\n•·]+')
# Приветствие узнаём по началу фразы, контактную обвязку — в любом месте
_GREETING = re.compile(r"^(меня зовут|приветству|здравствуй|добрый день|привет)", re.I)
_CONTACT = re.compile(
    r"связ\w*\s+со\s+мной|телефон|whatsapp|инстаграм|instagram|заключаем договор"
    r"|подробную информацию|пишите|звоните|директ|по\s+ссылке",
    re.I,
)


def _sentences(text: str) -> list[str]:
    """Куски описания, пригодные на роль цитаты.

    Разбиение только по «. » оставляло «Что вас ждёт: • Разработанный» и обрывки
    с КАПСом — такая «цитата» подрывает доверие вместо того, чтобы его создавать.
    """
    out = []
    for piece in _SENTENCE_SPLIT.split(text):
        piece = piece.strip(' \t-–—•·«»"\'')
        if _CONTACT.search(piece):
            continue
        if _GREETING.match(piece):
            # «Меня зовут X — я профессиональный ведущий и сценарист»: само представление
            # доказательством не является, а хвост после тире вполне.
            tail = re.split(r"\s[–—-]\s", piece, maxsplit=1)
            piece = tail[1].strip() if len(tail) > 1 else ""
        if len(piece) < 20:
            continue
        out.append(piece)
    return out


def _quote(description: str, signal: set[str], fallback: set[str] | None = None) -> str | None:
    """Точный фрагмент описания как доказательство под объяснением.

    Выбирается детерминированно и возвращается дословно, поэтому модель физически
    не может его придумать: ей остаётся только процитировать.
    """
    best, best_key = None, None
    for index, sentence in enumerate(_sentences(description)):
        stems = set(_stemmed(sentence))
        overlap = len(stems & signal)
        # Без пожеланий ориентируемся на то, говорит ли фраза о самой услуге,
        # иначе в цитату попадает вежливая пустота вроде «учтём все ваши пожелания»
        if not signal and fallback:
            overlap = len(stems & fallback)
        # Порядок важности: попадание в пожелания, затем целая фраза без обрезки,
        # затем конкретика в цифрах — годы опыта и количество мероприятий.
        key = (
            overlap,
            len(sentence) <= QUOTE_MAX_CHARS,
            any(c.isdigit() for c in sentence),
            -index,
        )
        if best_key is None or key > best_key:
            best, best_key = sentence, key
    if best is None:
        return None
    quote = best if best in description else best.rstrip(".")
    if len(quote) > QUOTE_MAX_CHARS:
        # Режем по границе слова: обрезок всё равно обязан быть дословным куском описания
        quote = quote[:QUOTE_MAX_CHARS].rsplit(" ", 1)[0]
    if quote not in description:
        return None
    letters = [c for c in quote if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.3:
        # Описание целиком капсом — не повод остаться без доказательства.
        # Приводим к обычному виду; дословность проверяется без учёта регистра.
        quote = quote.lower()
    return quote[0].upper() + quote[1:] if quote[:1].islower() else quote


def _reasons(profile: dict, req: dict) -> list[dict]:
    """ВСЕ причины, по которым профиль не проходит. Пусто — проходит.

    Именно все, а не первая: иначе диагностика врёт. Подрядчик, который и занят,
    и дороже бюджета, при показе одной причины выглядит так, будто хватит сдвинуть дату.
    """
    out = []
    if req.get("date") and req["date"] in profile["busy_dates"]:
        out.append({"code": "busy", "detail": f"занят {req['date']}"})
    if req.get("event_format") and req["event_format"] not in profile["event_formats"]:
        out.append({"code": "format", "detail": f"не берёт формат «{req['event_format']}»"})
    if req.get("budget_kzt") and profile["price_from_kzt"] and profile["price_from_kzt"] > req["budget_kzt"]:
        over = round((profile["price_from_kzt"] / req["budget_kzt"] - 1) * 100)
        price = f"{profile['price_from_kzt']:,}".replace(",", " ")
        out.append({"code": "budget", "detail": f"цена от {price} ₸ — на {over}% выше бюджета"})
    if req.get("language") and req["language"] not in profile["languages"]:
        out.append({"code": "language", "detail": f"не работает на языке «{req['language']}»"})
    if req.get("duration_hours") and profile["max_hours"] is not None and profile["max_hours"] < req["duration_hours"]:
        out.append(
            {"code": "duration", "detail": f"работает до {profile['max_hours']} ч при запросе {req['duration_hours']} ч"}
        )
    return out


def _score(profile: dict, req: dict) -> tuple[float, dict]:
    """Оценка 0..1 и факты совпадения, на которых потом строится объяснение."""
    facts: dict = {}

    budget = req.get("budget_kzt")
    price = profile["price_from_kzt"]
    if budget and price:
        headroom = 1 - price / budget
        facts["budget"] = {
            "price_from_kzt": price,
            "budget_kzt": budget,
            "headroom_percent": round(headroom * 100),
        }
        budget_score = max(0.0, min(1.0, headroom))
    else:
        budget_score = 0.5

    noise_stems = {w[:5] for w in _noise(req)}
    wish_stems = {k: v for k, v in _stemmed(req.get("free_text") or "").items() if k not in noise_stems}
    described = _stemmed(profile["description"])
    matched = sorted(described[stem] for stem in wish_stems if stem in described)
    facts["shared_words"] = matched[:5]
    facts["quote"] = _quote(
        profile["description"], set(wish_stems), fallback={req["category"][:5].lower()}
    )
    description_score = min(1.0, len(matched) / 3)

    # Чем уже специализация, тем точнее попадание в конкретный формат
    formats = profile["event_formats"]
    focus_score = 1 / len(formats) if formats else 0.0
    facts["format"] = {"requested": req.get("event_format"), "accepts": formats}

    langs = profile["languages"]
    facts["languages"] = langs
    language_score = min(1.0, len(langs) / 3)

    facts["duration"] = {
        "max_hours": profile["max_hours"],
        "requested": req.get("duration_hours"),
        "not_time_bound": profile["max_hours"] is None,
    }

    total = (
        WEIGHTS["budget"] * budget_score
        + WEIGHTS["description"] * description_score
        + WEIGHTS["focus"] * focus_score
        + WEIGHTS["languages"] * language_score
    )
    return round(total, 6), facts


def _standouts(profile: dict, peers: list[dict], req: dict) -> list[str]:
    """Чем этот подрядчик отличается от остальных прошедших отбор.

    Сначала то, что связано с запросом: клиенту, которому нужен казахский, «единственный
    с английским» не аргумент. Потом всё остальное.
    """
    if len(peers) < 2:
        return []
    ranked: list[tuple[int, str]] = []

    prices = [p["price_from_kzt"] for p in peers if p["price_from_kzt"]]
    if profile["price_from_kzt"] and prices and profile["price_from_kzt"] == min(prices):
        if len(prices) == len([p for p in prices if p == min(prices)]) == 1 or prices.count(min(prices)) == 1:
            ranked.append((0, f"дешевле всех подходящих: {_money(profile['price_from_kzt'])}"))

    wanted = req.get("language")
    for language in profile["languages"]:
        if sum(language in p["languages"] for p in peers) == 1:
            ranked.append((0 if language == wanted else 1, f"{language} — только здесь"))

    if len(profile["event_formats"]) == 1:
        ranked.append((0, f"берёт только «{profile['event_formats'][0]}»"))
    elif len(profile["event_formats"]) == min(len(p["event_formats"]) for p in peers):
        ranked.append((1, "самая узкая специализация из подходящих"))

    hours = [p["max_hours"] for p in peers if p["max_hours"] is not None]
    if profile["max_hours"] is not None and hours and profile["max_hours"] == max(hours) and len(set(hours)) > 1:
        ranked.append((1 if not req.get("duration_hours") else 0,
                       f"дольше всех на площадке: до {profile['max_hours']} ч"))

    counts = [len(p["languages"]) for p in peers]
    if len(profile["languages"]) == max(counts) and len(set(counts)) > 1:
        ranked.append((1, f"больше всех языков: {', '.join(profile['languages'])}"))

    ranked.sort(key=lambda item: item[0])
    return [text for _, text in ranked][:3]


def _keep_unique_standouts(entries: list[tuple[dict, dict]]) -> None:
    """Оставляет каждой карточке то, чего нет у соседей, и назначает ведущий факт.

    Экстремум, общий для двоих, ничего не выделяет. А когда уникального признака нет,
    честнее сравнить с первой карточкой, чем писать «2-й по совокупности условий»:
    порядковый номер — не факт о подрядчике.
    """
    if len(entries) < 2:
        for _, facts in entries:
            facts["standouts"] = []
            facts["lead"] = None
        return

    seen: dict[str, int] = {}
    for _, facts in entries:
        for line in facts.get("standouts", []):
            seen[line] = seen.get(line, 0) + 1

    leader = entries[0][0]
    taken: set[str] = set()
    for profile, facts in entries:
        unique = [line for line in facts.get("standouts", []) if seen[line] == 1 and line not in taken]
        if unique:
            facts["lead"] = unique[0]
            taken.add(unique[0])
            facts["standouts"] = unique[1:]
        else:
            facts["lead"] = _difference_from(profile, leader)
            facts["standouts"] = []


def _difference_from(profile: dict, leader: dict) -> str | None:
    """Чем карточка отличается от первой в выдаче. Это факт, а не порядковый номер."""
    if profile["id"] == leader["id"]:
        return None
    price, lead_price = profile["price_from_kzt"], leader["price_from_kzt"]
    if price and lead_price and price != lead_price:
        delta = round(abs(price / lead_price - 1) * 100)
        if delta:
            side = "дороже" if price > lead_price else "дешевле"
            return f"{side} первого на {delta}%: {_money(price)}"
    hours, lead_hours = profile["max_hours"], leader["max_hours"]
    if hours is not None and lead_hours is not None and hours != lead_hours:
        return f"до {hours} ч против {lead_hours} у первого"
    extra = [l for l in profile["languages"] if l not in leader["languages"]]
    if extra:
        return f"добавляет язык: {', '.join(extra)}"
    return None


def _card(profile: dict, score: float, facts: dict) -> dict:
    return {
        "id": profile["id"],
        "name": profile["name"],
        "categories": profile["categories"],
        "city": profile["city"],
        "price_from_kzt": profile["price_from_kzt"],
        "description": profile["description"][:200],
        "score": score,
        "match": facts,
        # Флаги честности: показываются в карточке, чтобы не выдавать проставленные данные за настоящие
        "flags": {
            "synthetic": profile["synthetic"],
            "price_imputed": profile["price_imputed"],
            "city_imputed": profile["city_imputed"],
        },
    }


def _shortlist(req: dict) -> tuple[list[dict], list[dict], list[dict]]:
    """(в городе и категории, прошедшие фильтры, отсеянные с причинами)."""
    pool = [
        p
        for p in catalog()
        if p["city"] == req["city"] and req["category"] in p["categories"]
    ]
    passed, rejected = [], []
    for p in pool:
        why = _reasons(p, req)
        if why:
            rejected.append({"id": p["id"], "name": p["name"], "reasons": why})
        else:
            passed.append(p)
    return pool, passed, rejected


# --------------------------------------------------------------------------
# Инструменты
# --------------------------------------------------------------------------

def _request(city, category, date, event_format, budget_kzt, duration_hours, language, free_text) -> dict:
    vocab = vocabulary()
    resolved_city = _normalize(city, vocab["cities"])
    resolved_category = _normalize(category, vocab["categories"])
    req = {
        "city": resolved_city or (city or "").strip(),
        "category": resolved_category or (category or "").strip(),
        "date": (date or "").strip(),
        "event_format": _normalize(event_format, vocab["event_formats"]) or (event_format or "").strip(),
        "budget_kzt": int(budget_kzt),
        "duration_hours": duration_hours,
        "language": _normalize(language, vocab["languages"]),
        "free_text": free_text or "",
    }
    # Что не удалось привести к каталогу — фиксируем явно, чтобы не выдать
    # «в этом городе такой категории нет» за ответ на несуществующее значение.
    unknown = {}
    if not resolved_city:
        unknown["city"] = (city or "").strip()
    if not resolved_category:
        unknown["category"] = (category or "").strip()
    if unknown:
        req["_unknown"] = unknown
    return req


def _search_schema() -> dict:
    """Схема с enum из каталога: так модель физически не может прислать «Ведущие»."""
    vocab = vocabulary()
    return {
        "type": "object",
        "properties": {
            "city": {"type": "string", "enum": vocab["cities"], "description": "City from the catalog"},
            "category": {
                "type": "string",
                "enum": vocab["categories"],
                "description": "Contractor category exactly as in the catalog",
            },
            "date": {"type": "string", "description": "Event date, YYYY-MM-DD"},
            "event_format": {
                "type": "string",
                "enum": vocab["event_formats"],
                "description": "Event format from the catalog",
            },
            "budget_kzt": {"type": "integer", "description": "Budget ceiling per contractor, KZT"},
            "duration_hours": {"type": "integer", "description": "Optional. Hours on site"},
            "language": {
                "type": "string",
                "enum": vocab["languages"],
                "description": "Optional. Working language the client insists on",
            },
            "free_text": {
                "type": "string",
                "description": "Optional. The client's wishes verbatim, used for description relevance",
            },
        },
        "required": ["city", "category", "date", "event_format", "budget_kzt"],
    }


_SEARCH_SCHEMA = _search_schema()


@tool(
    "search_contractors",
    "Search the contractor catalog. Applies the hard filters (city, category, availability on "
    "the date, event format, budget, duration, language), ranks what is left with a fixed "
    "deterministic formula and returns at most three candidates with the computed match facts. "
    "The order is deterministic — never reorder the result. Also reports which candidates were "
    "filtered out and why.",
    _SEARCH_SCHEMA,
)
def search_contractors(
    ctx: RunContext,
    city: str,
    category: str,
    date: str,
    event_format: str,
    budget_kzt: int,
    duration_hours: int | None = None,
    language: str | None = None,
    free_text: str | None = None,
):
    # Близость по описанию считаем от дословного текста человека, а не от аргумента модели:
    # иначе пересказ пожеланий моделью менял бы порядок карточек (требование 5).
    req = _request(city, category, date, event_format, budget_kzt, duration_hours,
                   language, ctx.task or free_text)
    try:
        requested = _parse_date(req["date"])
    except ValueError:
        return {"error": f"дата «{req['date']}» не в формате ГГГГ-ММ-ДД"}

    unknown = req.pop("_unknown", None)
    if unknown:
        field, value = next(iter(unknown.items()))
        known = vocabulary()["cities" if field == "city" else "categories"]
        return {
            "outcome": f"unknown_{field}",
            "request": req,
            "cards": [],
            "note": f"значения «{value}» нет в каталоге",
            "known_values": known,
            "diagnosis": {
                "suggestions": [
                    f"«{value}» — не {'город' if field == 'city' else 'категория'} из каталога",
                    "допустимые значения: " + ", ".join(known),
                ]
            },
        }

    out_of_window = not (WINDOW_START <= requested <= WINDOW_END)
    pool, passed, rejected = _shortlist(req)

    if not pool:
        return {
            "outcome": "no_category_in_city",
            "request": req,
            "in_city_and_category": 0,
            "cards": [],
            "note": f"в городе {req['city']} нет ни одного подрядчика категории «{req['category']}»",
            "opening": f"В городе {req['city']} нет ни одного подрядчика категории «{req['category']}».",
            "diagnosis": _diagnose(req, requested),
        }

    scored = [(*_score(p, req), p) for p in passed]
    # При равном счёте порядок решал идентификатор, и на вопрос «почему он выше» честным
    # ответом было «по id». Сначала сравниваем по смыслу: дешевле, шире по языкам, уже
    # специализация. Id остаётся последним, чтобы порядок всё равно был воспроизводим.
    scored.sort(
        key=lambda item: (
            -item[0],
            item[2]["price_from_kzt"] or 10**12,
            -len(item[2]["languages"]),
            len(item[2]["event_formats"]),
            item[2]["id"],
        )
    )
    top = scored[:MAX_CARDS]
    for value, facts, profile in top:
        facts["standouts"] = _standouts(profile, passed, req)
    _keep_unique_standouts([(profile, facts) for _, facts, profile in top])
    cards = [_card(profile, value, facts) for value, facts, profile in top]

    busy_now = sum(1 for p in pool if req["date"] in p["busy_dates"])
    availability = (
        f"в категории «{req['category']}» по городу {req['city']} всего {len(pool)}, "
        f"на {_human_date(req['date'])} заняты {busy_now}"
    )
    result = {
        "outcome": "matched" if cards else "all_filtered_out",
        "request": req,
        "in_city_and_category": len(pool),
        "passed_filters": len(passed),
        # Требование 16: разницу между датами видно и тогда, когда карточек всё равно три
        "availability_note": availability,
        # Готовая первая строка ответа. Собрана кодом по той же причине, что и подсказки:
        # когда модель сочиняет её сама, она то берёт запрос в кавычки, то теряет занятость.
        "opening": (
            f"Нашлось {_people(len(cards))}. {availability[0].upper()}{availability[1:]}"
            if cards
            else f"Подходящих не нашлось. {availability[0].upper()}{availability[1:]}"
        ),
        "cards": cards,
        "rejected": rejected[:MAX_REJECTED_SHOWN],
        "rejected_by_reason": _count_reasons(rejected),
    }
    if out_of_window:
        result["warning"] = (
            f"дата вне окна {WINDOW_START}–{WINDOW_END}, для неё в каталоге нет данных о занятости"
        )
    if len(cards) < MAX_CARDS:
        result["fewer_than_three"] = True
        # Диагностика приезжает сразу, а не вторым вызовом: объяснение, почему подходящих
        # мало, обязано быть в ответе всегда, а не когда модель догадается спросить.
        # Заодно это один вызов модели вместо двух — требование 13 про время.
        result["diagnosis"] = _diagnose(req, requested)
    return result


def _count_reasons(rejected: list[dict]) -> dict:
    """Сколько кандидатов задето каждой причиной. Один может попасть в несколько."""
    counts: dict[str, int] = {}
    for r in rejected:
        for reason in r["reasons"]:
            counts[reason["code"]] = counts.get(reason["code"], 0) + 1
    return counts


def _diagnose(req: dict, requested: dt.date) -> dict:
    """Почему подходящих меньше трёх и что изменить. Возвращается и отдельным
    инструментом, и вместе с поиском — чтобы результат не зависел от того,
    догадается ли модель сделать второй вызов."""
    pool, passed, rejected = _shortlist(req)
    if not pool:
        elsewhere = {}
        for p in catalog():
            if req["category"] in p["categories"] and p["city"] != req["city"]:
                elsewhere[p["city"]] = elsewhere.get(p["city"], 0) + 1
        where = sorted(elsewhere)
        return {
            "outcome": "no_category_in_city",
            "in_city_and_category": 0,
            "category_available_in": where,
            "note": f"категории «{req['category']}» в городе {req['city']} нет вообще",
            "headline": f"категория «{req['category']}» в городе {req['city']}: в каталоге 0",
            "suggestions": [
                f"в городе {req['city']} категории «{req['category']}» нет ни одного подрядчика"
            ]
            + (
                [f"в городе {city} их {elsewhere[city]}" for city in where]
                if where
                else [f"категории «{req['category']}» нет и в других городах каталога"]
            ),
        }

    passed_now = len(passed)
    relaxed = {
        key: _relaxed(req, key, passed_now)
        for key in ("date", "budget_kzt", "event_format", "language", "duration_hours")
    }
    nearby = _nearby_dates(req, requested, passed_now)
    budget_needed = _budget_needed(req)
    season = _season(req)
    # Всё время ответа съедает модель: инструменты отрабатывают за миллисекунду. Поэтому
    # в диагностику не попадает ничего, что уже вернул поиск, и ничего, из чего мы сами
    # собрали готовую строку: помесячные средние живут в season_note, отсев — в rejected поиска.
    return {
        "outcome": "matched" if passed else "all_filtered_out",
        "in_city_and_category": len(pool),
        "passed_filters": len(passed),
        "blocked_by": _count_reasons(rejected),
        # Сколько кандидатов появится, если снять ровно одно условие. None — оно не задано.
        "if_relaxed": relaxed,
        "single_change_helps": any(bool(v) for v in relaxed.values()),
        "budget_needed_kzt": budget_needed,
        # Готовые формулировки: модель их вплетает, а не сочиняет — меньше выдумок и быстрее ответ
        "headline": _headline(req, len(pool), passed_now, _count_reasons(rejected)),
        "suggestions": _suggestions(req, relaxed, nearby, budget_needed, len(pool), passed_now),
        "season_note": _season_note(req, season),
    }


_MONTHS_GENITIVE = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля", 5: "мая", 6: "июня",
    7: "июля", 8: "августа", 9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}


def _plural(count: int, one: str, few: str, many: str) -> str:
    """Русские числительные: 1 подрядчик, 2 подрядчика, 5 подрядчиков."""
    tail, hundred = count % 10, count % 100
    if 11 <= hundred <= 14 or tail == 0 or tail >= 5:
        form = many
    elif tail == 1:
        form = one
    else:
        form = few
    return f"{count} {form}"


def _people(count: int) -> str:
    return _plural(count, "подрядчик", "подрядчика", "подрядчиков")


def _money(amount: int) -> str:
    return f"{amount:,}".replace(",", " ") + " ₸"


def _human_date(iso: str) -> str:
    day = dt.date.fromisoformat(iso)
    return f"{day.day} {_MONTHS_GENITIVE[day.month]}"


def _suggestions(req: dict, relaxed: dict, nearby: list[dict], budget_needed: int | None,
                 pool_size: int, passed_now: int) -> list[str]:
    """Изменения запроса, каждое из которых РЕАЛЬНО добавляет кандидатов.

    Про формат не советуем ничего: «не берут этот формат» — это законный исход по ТЗ,
    а не помеха, которую клиенту предлагают обойти.
    """
    out = []
    if nearby:
        best = nearby[0]
        out.append(
            f"перенести дату на {_human_date(best['date'])} — подойдёт {_people(best['available'])}"
        )
    if budget_needed and relaxed.get("budget_kzt"):
        out.append(
            f"поднять бюджет до {_money(budget_needed)} — добавится {_people(relaxed['budget_kzt'])}"
        )
    if relaxed.get("language"):
        out.append(f"снять требование по языку — добавится {_people(relaxed['language'])}")
    if relaxed.get("duration_hours"):
        out.append(f"снять требование по длительности — добавится {_people(relaxed['duration_hours'])}")

    if not out:
        if passed_now and pool_size <= MAX_CARDS:
            # Редкая категория: показали всех, кто есть. Это не неудача, а исчерпанность.
            out.append(
                f"это все подрядчики категории «{req['category']}» в городе {req['city']}: "
                f"их {pool_size}, показаны все подходящие"
            )
        elif passed_now:
            out.append("остальные кандидаты не проходят по условиям запроса")
        else:
            out.append(
                "ни одно одиночное послабление не помогает: кандидаты не проходят "
                "сразу по нескольким условиям"
            )
    if not passed_now:
        # Когда карточки есть, совет ехать в другой город — шум
        out += _elsewhere_note(req)
    return out


_REASON_LABELS = {
    "busy": "заняты на эту дату",
    "format": "не берут этот формат",
    "budget": "дороже бюджета",
    "language": "не работают на нужном языке",
    "duration": "не тянут по длительности",
}


def _headline(req: dict, pool_size: int, passed_now: int, blocked: dict) -> str:
    """Одна строка о том, кто кого отсеял. Собирается кодом: перечислять причины,
    которых не было, модель не должна."""
    head = (
        f"категория «{req['category']}», город {req['city']}: в каталоге {pool_size}, "
        f"подходят {passed_now}"
    )
    if not blocked:
        return head
    parts = [f"{_REASON_LABELS.get(code, code)} — {count}" for code, count in sorted(blocked.items())]
    return head + "; " + ", ".join(parts)


def _elsewhere_note(req: dict) -> list[str]:
    """Где ещё есть эта категория. Для редких категорий это единственный полезный совет."""
    elsewhere = {}
    for profile in catalog():
        if req["category"] in profile["categories"] and profile["city"] != req["city"]:
            elsewhere[profile["city"]] = elsewhere.get(profile["city"], 0) + 1
    if not elsewhere:
        return []
    return [
        "в других городах эта категория тоже есть: "
        + ", ".join(f"{city} — {count}" for city, count in sorted(elsewhere.items()))
    ]


def _season_note(req: dict, season: dict) -> str | None:
    """Бедная выдача в декабре — это сезон, а не сбой. Пусть так и будет сказано."""
    averages = (season or {}).get("average_free_per_month") or {}
    if len(averages) < 2 or not req.get("date"):
        return None
    try:
        month = req["date"][:7]
    except TypeError:
        return None
    if month not in averages:
        return None
    others = [v for k, v in averages.items() if k != month]
    if not others or averages[month] >= min(others):
        return None
    best_month, best_value = max(
        ((k, v) for k, v in averages.items() if k != month), key=lambda kv: kv[1]
    )
    total = season.get("total_in_category")
    return (
        f"в категории «{req['category']}» в {_month_name(month)} свободно в среднем "
        f"{averages[month]} из {total}, в {_month_name(best_month)} — {best_value}: это сезон, а не сбой"
    )


_MONTHS_PREPOSITIONAL = {
    "09": "сентябре", "10": "октябре", "11": "ноябре", "12": "декабре",
    "01": "январе", "02": "феврале", "03": "марте", "04": "апреле",
    "05": "мае", "06": "июне", "07": "июле", "08": "августе",
}


def _month_name(month: str) -> str:
    return _MONTHS_PREPOSITIONAL.get(month[-2:], month)




@tool(
    "diagnose_request",
    "Explain why a request returns fewer than three contractors and what single change to the "
    "request would help: another date within two weeks, a higher budget, or dropping the language "
    "or duration requirement. Call it whenever search_contractors returns fewer than three cards.",
    _SEARCH_SCHEMA,
)
def diagnose_request(
    ctx: RunContext,
    city: str,
    category: str,
    date: str,
    event_format: str,
    budget_kzt: int,
    duration_hours: int | None = None,
    language: str | None = None,
    free_text: str | None = None,
):
    req = _request(city, category, date, event_format, budget_kzt, duration_hours,
                   language, ctx.task or free_text)
    unknown = req.pop("_unknown", None)
    if unknown:
        field, value = next(iter(unknown.items()))
        return {"outcome": f"unknown_{field}", "note": f"значения «{value}» нет в каталоге"}
    try:
        requested = _parse_date(req["date"])
    except ValueError:
        return {"error": f"дата «{req['date']}» не в формате ГГГГ-ММ-ДД"}
    return _diagnose(req, requested)


def _nearby_dates(req: dict, requested: dt.date, passed_now: int = 0) -> list[dict]:
    """Ближайшие даты в окне ±14 дней, где подходящих становится БОЛЬШЕ, чем сейчас."""
    found = []
    for delta in range(1, SHIFT_DAYS + 1):
        for shifted in (requested + dt.timedelta(days=delta), requested - dt.timedelta(days=delta)):
            if not (WINDOW_START <= shifted <= WINDOW_END):
                continue
            probe = dict(req, date=shifted.isoformat())
            _, passed, _ = _shortlist(probe)
            if len(passed) > passed_now:
                found.append({"date": shifted.isoformat(), "available": len(passed)})
    found.sort(key=lambda item: (abs((dt.date.fromisoformat(item["date"]) - requested).days), item["date"]))
    return found[:3]


def _budget_needed(req: dict) -> int | None:
    """Минимальный бюджет, при котором хоть кто-то проходит остальные фильтры."""
    probe = dict(req, budget_kzt=None)
    _, passed, _ = _shortlist(probe)
    prices = [p["price_from_kzt"] for p in passed if p["price_from_kzt"]]
    if not prices:
        return None
    cheapest = min(prices)
    return cheapest if cheapest > req["budget_kzt"] else None


def _relaxed(req: dict, key: str, passed_now: int) -> int | None:
    """Сколько кандидатов ПРИБАВИТСЯ, если снять ровно одно условие.

    Абсолютное число здесь врёт: если подрядчик уже в выдаче, предлагать сдвинуть
    дату «чтобы освободился один» бессмысленно — он и так показан.
    """
    if not req.get(key):
        return None
    _, passed, _ = _shortlist(dict(req, **{key: None}))
    return max(0, len(passed) - passed_now)


def _season(req: dict) -> dict:
    """Загрузка категории по месяцам: бедная выдача в декабре — это сезон, а не сбой."""
    pool = [p for p in catalog() if p["city"] == req["city"] and req["category"] in p["categories"]]
    if not pool:
        return {}
    by_month: dict[str, list[int]] = {}
    day = WINDOW_START
    while day <= WINDOW_END:
        iso = day.isoformat()
        free = sum(1 for p in pool if iso not in p["busy_dates"])
        by_month.setdefault(f"{day.year}-{day.month:02d}", []).append(free)
        day += dt.timedelta(days=1)
    return {
        "total_in_category": len(pool),
        "average_free_per_month": {m: round(sum(v) / len(v), 1) for m, v in by_month.items()},
    }


# --------------------------------------------------------------------------
# Сценарий для LLM_MOCK: без ключей агент всё равно проходит весь путь
# --------------------------------------------------------------------------

_CITIES = ("Алматы", "Астана", "Зарубежье")
_FORMATS = ("свадьба", "той", "корпоратив", "конференция", "юбилей", "день рождения")
_LANGUAGES = ("русский", "казахский", "английский")


def _guess(request: str) -> dict | None:
    """Грубый разбор запроса для mock-режима. Настоящая модель делает это сама."""
    low = request.lower()
    city = _match_by_stems(request, _CITIES)
    fmt = _match_by_stems(request, _FORMATS)
    category = _match_by_stems(request, sorted({c for p in catalog() for c in p["categories"]}))
    language = _match_by_stems(request, _LANGUAGES)

    day = None
    iso = re.search(r"\b(20\d\d-\d\d-\d\d)\b", request)
    if iso:
        day = iso.group(1)
    else:
        months = {
            "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
            "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
        }
        m = re.search(r"\b(\d{1,2})\s+(" + "|".join(months) + r")\s+(20\d\d)", low)
        if m:
            day = f"{int(m.group(3))}-{months[m.group(2)]:02d}-{int(m.group(1)):02d}"

    # Пробелы убираем, чтобы «900 000» читалось как одно число; границы слова тут мешают:
    # в «900000тенге» между цифрой и буквой их нет.
    budget = re.search(r"(\d{5,9})", request.replace(" ", "").replace("\u00a0", ""))
    hours = re.search(r"\b(\d{1,2})\s*(?:ч\b|час|часа|часов)", low)

    if not (city and fmt and category and day and budget):
        return None
    return {
        "city": city,
        "category": category,
        "date": day,
        "event_format": fmt,
        "budget_kzt": int(budget.group(1)),
        "duration_hours": int(hours.group(1)) if hours else None,
        "language": language,
        "free_text": request,
    }


def plan(request: str, results: dict):
    args = _guess(request)
    if not args:
        return None
    args = {k: v for k, v in args.items() if v is not None}

    if "search_contractors" not in results:
        return "Разбираю запрос в параметры и ищу по каталогу.", "search_contractors", args

    found = results["search_contractors"]
    needs_diagnosis = found.get("outcome") != "matched" or found.get("fewer_than_three")
    if needs_diagnosis and "diagnose_request" not in results:
        return "Подходящих меньше трёх — разберусь, что именно помешало.", "diagnose_request", args
    return None


def offline_report(results: dict) -> str | None:
    """Ответ без модели, собранный из того же набора фактов.

    Проверяющий без ключа должен увидеть продукт, а не выгрузку JSON: цифры, ведущий
    факт и цитату по каждой карточке. Текст беднее, чем у модели, зато полностью
    воспроизводим — и это ровно то, что проверяется в пункте 5.6.6 Положения.
    """
    found = results.get("search_contractors")
    if not isinstance(found, dict):
        return None

    lines = []
    cards = found.get("cards") or []
    if cards:
        note = found.get("availability_note") or ""
        if note:
            note = note[0].upper() + note[1:]
        lines.append(f"Найдено {_people(len(cards))}. {note}".strip())
        lines.append("")
        for number, card in enumerate(cards, 1):
            match = card.get("match", {})
            budget = match.get("budget") or {}
            bits = [match.get("lead") or ""]
            if budget:
                bits.append(
                    f"цена от {_money(budget['price_from_kzt'])} при бюджете "
                    f"{_money(budget['budget_kzt'])}, запас {budget['headroom_percent']}%"
                )
            formats = (match.get("format") or {}).get("accepts") or []
            if formats:
                bits.append("берёт: " + ", ".join(formats))
            if match.get("languages"):
                bits.append("языки: " + ", ".join(match["languages"]))
            text = "; ".join(b for b in bits if b)
            quote = match.get("quote")
            if quote:
                text += f" «{quote}»"
            lines.append(f"{number}. **{card['name']}** — {text}")
    else:
        lines.append(found.get("note") or "Подходящих подрядчиков не нашлось.")

    diagnosis = found.get("diagnosis") or {}
    if diagnosis:
        lines.append("")
        if diagnosis.get("headline"):
            lines.append(diagnosis["headline"])
        for line in diagnosis.get("suggestions", []):
            lines.append(f"- {line}")
        if diagnosis.get("season_note"):
            lines.append(diagnosis["season_note"])

    lines.append("")
    lines.append(
        "_Ответ собран без модели: в `.env` не заданы `OPENAI_API_KEY` и `OPENAI_MODEL`. "
        "Отбор, порядок и диагностика от модели не зависят и здесь настоящие — "
        "с ключом теми же фактами объяснения формулирует LLM._"
    )
    return "\n".join(lines)
