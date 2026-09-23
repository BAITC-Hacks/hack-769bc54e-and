"""Домен Задачи #79-lite: умный подбор event-подрядчиков.

Главное архитектурное решение: **отбор и порядок — детерминированный код, LLM только
пишет объяснения.** Иначе не выполнить одновременно требование 5 (тот же запрос — тот
же порядок) и требование к качеству текста. Побочная выгода: ранжирование работает без
ключей, поэтому проверяющий без .env видит настоящий отбор, а не заглушку.

Инструменты:
  search_contractors  — жёсткие фильтры + скоринг, до 3 карточек с фактами совпадения;
  diagnose_request    — что изменить в запросе, если подходящих меньше трёх.

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
Write the answer in Russian, in Markdown, in exactly this shape and nothing else:

1. One opening line: how many contractors were found and for which request.
2. A numbered list, one item per returned card, in the order the catalog returned them.
   Each item: `**Имя** — one or two sentences of explanation.`
3. If there are fewer than three cards, a closing block: one line saying how many there are
   and which condition blocked whom, then **every** line of `diagnosis.suggestions`, each as its own
   bullet, reproduced word for word. Not a summary of them, not a selection — all of them.
   They already contain the right numbers, dates and city names; inventing your own is a defect.
   When `diagnosis.season_note` is present, add it after the bullets: a thin month is the season,
   not a failure.

Rules for the explanations:
- Build every sentence on the numbers and facts in `match`: budget headroom in percent,
  the price, the accepted formats, the working languages, the hours, the words the request
  and the description share, and `match.quote` when it is present.
- Quote `match.quote` verbatim when it is present: it is an exact fragment of the contractor's
  own description. Never invent a quote and never paraphrase one. When a field is empty,
  simply leave it out — never tell the reader that data is missing and never name internal
  fields such as `match` or `quote` in the answer.
- The cards must stay distinguishable with the names removed. Two explanations that would fit
  each other equally well are a defect.
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


def _parse_date(value: str) -> dt.date:
    return dt.date.fromisoformat(value.strip())


# --------------------------------------------------------------------------
# Фильтры и скоринг
# --------------------------------------------------------------------------

_STOP = {
    "и", "в", "на", "для", "с", "по", "до", "от", "не", "или", "а", "но", "мы", "наш",
    "это", "как", "что", "все", "его", "их", "мероприятие", "мероприятия", "нужен",
    "нужна", "нужно", "хочу", "тенге", "бюджет",
}


def _words(text: str) -> set[str]:
    cleaned = "".join(c.lower() if c.isalnum() else " " for c in text)
    return {w for w in cleaned.split() if len(w) > 3 and w not in _STOP}


def _noise(req: dict) -> set[str]:
    """Слова самих параметров запроса.

    «Ведущий на свадьбу в Алматы» совпадёт с описанием любого ведущего из Алматы —
    это не признак, а эхо запроса, и объяснение из него выходит пустым.
    """
    parts = [req.get(k) or "" for k in ("city", "category", "event_format", "language")]
    return _words(" ".join(parts))


QUOTE_MAX_CHARS = 180


def _sentences(text: str) -> list[str]:
    out, current = [], []
    for chunk in text.replace("\n", " ").split(". "):
        piece = chunk.strip()
        if piece:
            current.append(piece)
    for piece in current:
        out.append(piece if piece.endswith(".") else piece + ".")
    return out


def _quote(description: str, signal: set[str]) -> str | None:
    """Точный фрагмент описания как доказательство под объяснением.

    Выбирается детерминированно и возвращается дословно, поэтому модель физически
    не может его придумать: ей остаётся только процитировать.
    """
    best, best_key = None, None
    for index, sentence in enumerate(_sentences(description)):
        if len(sentence) < 8:
            continue
        overlap = len(_words(sentence) & signal)
        # Цифры в описании — это годы опыта и количество мероприятий, самая проверяемая конкретика
        key = (overlap, any(c.isdigit() for c in sentence), -index)
        if best_key is None or key > best_key:
            best, best_key = sentence, key
    if best is None:
        return None
    quote = best if best in description else best.rstrip(".")
    if len(quote) > QUOTE_MAX_CHARS:
        # Режем по границе слова: обрезок всё равно обязан быть дословным куском описания
        quote = quote[:QUOTE_MAX_CHARS].rsplit(" ", 1)[0]
    return quote if quote in description else None


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

    signal = _words(req.get("free_text") or "") - _noise(req)
    overlap = signal & _words(profile["description"])
    facts["shared_words"] = sorted(overlap)[:5]
    facts["quote"] = _quote(profile["description"], signal)
    description_score = min(1.0, len(overlap) / 4)

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
    return {
        "city": city.strip(),
        "category": category.strip(),
        "date": date.strip(),
        "event_format": event_format.strip(),
        "budget_kzt": int(budget_kzt),
        "duration_hours": duration_hours,
        "language": (language or "").strip() or None,
        "free_text": free_text or "",
    }


_SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "city": {"type": "string", "description": "Алматы, Астана or Зарубежье"},
        "category": {"type": "string", "description": "Contractor category exactly as in the catalog, e.g. Ведущий"},
        "date": {"type": "string", "description": "Event date, YYYY-MM-DD"},
        "event_format": {
            "type": "string",
            "description": "One of: свадьба, той, корпоратив, конференция, юбилей, день рождения",
        },
        "budget_kzt": {"type": "integer", "description": "Budget ceiling per contractor, KZT"},
        "duration_hours": {"type": "integer", "description": "Optional. Hours on site"},
        "language": {"type": "string", "description": "Optional: русский, казахский or английский"},
        "free_text": {"type": "string", "description": "Optional. The client's request verbatim, used for description relevance"},
    },
    "required": ["city", "category", "date", "event_format", "budget_kzt"],
}


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
    req = _request(city, category, date, event_format, budget_kzt, duration_hours, language, free_text)
    try:
        requested = _parse_date(req["date"])
    except ValueError:
        return {"error": f"дата «{req['date']}» не в формате ГГГГ-ММ-ДД"}

    out_of_window = not (WINDOW_START <= requested <= WINDOW_END)
    pool, passed, rejected = _shortlist(req)

    if not pool:
        return {
            "outcome": "no_category_in_city",
            "request": req,
            "in_city_and_category": 0,
            "cards": [],
            "note": f"в городе {req['city']} нет ни одного подрядчика категории «{req['category']}»",
            "diagnosis": _diagnose(req, requested),
        }

    scored = [(*_score(p, req), p) for p in passed]
    # Тай-брейк по id: одинаковый счёт не должен давать разный порядок между запусками
    scored.sort(key=lambda item: (-item[0], item[2]["id"]))
    cards = [_card(p, s, f) for s, f, p in scored[:MAX_CARDS]]

    result = {
        "outcome": "matched" if cards else "all_filtered_out",
        "request": req,
        "in_city_and_category": len(pool),
        "passed_filters": len(passed),
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
            "suggestions": [
                f"в городе {req['city']} категории «{req['category']}» нет ни одного подрядчика"
            ]
            + (
                [f"в городе {city} их {elsewhere[city]}" for city in where]
                if where
                else [f"категории «{req['category']}» нет и в других городах каталога"]
            ),
        }

    relaxed = {
        key: _relaxed(req, key)
        for key in ("date", "budget_kzt", "event_format", "language", "duration_hours")
    }
    nearby = _nearby_dates(req, requested)
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
        "suggestions": _suggestions(req, relaxed, nearby, budget_needed),
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


def _suggestions(req: dict, relaxed: dict, nearby: list[dict], budget_needed: int | None) -> list[str]:
    """Минимальные изменения запроса, каждое из которых само по себе даёт результат."""
    out = []
    if nearby:
        best = nearby[0]
        out.append(f"перенести дату на {_human_date(best['date'])} — освободится {_people(best['available'])}")
    if budget_needed and relaxed.get("budget_kzt"):
        out.append(
            f"поднять бюджет до {_money(budget_needed)} — тогда подойдёт {_people(relaxed['budget_kzt'])}"
        )
    if relaxed.get("language"):
        out.append(f"снять требование по языку — тогда подойдёт {_people(relaxed['language'])}")
    if relaxed.get("duration_hours"):
        out.append(f"снять требование по длительности — тогда подойдёт {_people(relaxed['duration_hours'])}")
    if relaxed.get("event_format"):
        out.append(
            f"рассмотреть тех, кто не заявил формат «{req['event_format']}» явно — "
            f"тогда подойдёт {_people(relaxed['event_format'])}"
        )
    if not out:
        out.append("ни одно одиночное послабление не помогает: кандидаты не проходят сразу по нескольким условиям")
    return out


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
    req = _request(city, category, date, event_format, budget_kzt, duration_hours, language, free_text)
    try:
        requested = _parse_date(req["date"])
    except ValueError:
        return {"error": f"дата «{req['date']}» не в формате ГГГГ-ММ-ДД"}
    return _diagnose(req, requested)


def _nearby_dates(req: dict, requested: dt.date) -> list[dict]:
    """Ближайшие даты в окне ±14 дней, где подходящих становится больше."""
    found = []
    for delta in range(1, SHIFT_DAYS + 1):
        for shifted in (requested + dt.timedelta(days=delta), requested - dt.timedelta(days=delta)):
            if not (WINDOW_START <= shifted <= WINDOW_END):
                continue
            probe = dict(req, date=shifted.isoformat())
            _, passed, _ = _shortlist(probe)
            if passed:
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


def _relaxed(req: dict, key: str) -> int | None:
    """Сколько кандидатов пройдёт, если снять ровно одно условие. None — оно не задано."""
    if not req.get(key):
        return None
    _, passed, _ = _shortlist(dict(req, **{key: None}))
    return len(passed)


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
