"""Прогон набора кейсов через агента и таблица результатов в docs/EVAL.md.

Зачем: на техническом отборе (п. 5.6.2) и на Demo Day («результат и качество», 20 баллов)
объективная таблица сильнее слов «у нас работает». Почти никто на хакатоне её не делает.

    python backend/agent/evals/run.py                 # все кейсы из cases.json
    python backend/agent/evals/run.py --limit 5       # быстрый прогон
    python backend/agent/evals/run.py --real          # с настоящей моделью (тратит кредиты)

Формат кейса в cases.json:
    {
      "id": "короткий-идентификатор",
      "task": "что спрашиваем у агента",
      "input": "сырой материал",            // или "input_file": "путь относительно evals/"
      "expect": {
        "tools": ["имя_инструмента", ...],  // какие инструменты обязан вызвать
        "contains": ["подстрока", ...],     // что обязано быть в отчёте
        "absent": ["подстрока", ...],       // чего в отчёте быть не должно
        "tool_result_contains": ["..."],    // что обязано быть в ответе инструмента,
                                            // а не в тексте модели: детерминированная гарантия
        "approval": true                     // обязан ли остановиться на подтверждении
      },
      "compare": {                           // необязательно: второй запрос для сравнения
        "task": "тот же запрос с другой датой",
        "must_differ": true,                 // ответы обязаны отличаться
        "contains": ["подстрока", ...]       // что обязано быть во втором ответе
      }
    }
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent.parent
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")


def check(case: dict, run) -> tuple[bool, list[str]]:
    from agent.models import Step

    expect = case.get("expect") or {}
    problems = []

    called = [s.title for s in run.steps.filter(kind=Step.Kind.TOOL_CALL)]
    for name in expect.get("tools", []):
        if name not in called:
            problems.append(f"не вызван {name}")

    report = run.final_report or ""
    for needle in expect.get("contains", []):
        if needle.lower() not in report.lower():
            problems.append(f"в отчёте нет «{needle}»")
    for needle in expect.get("absent", []):
        if needle.lower() in report.lower():
            problems.append(f"в отчёте есть лишнее «{needle}»")

    needles = expect.get("tool_result_contains", [])
    if needles:
        # Проверяем данные, а не формулировку: то, что обязано быть, не должно зависеть от модели
        blob = json.dumps(
            [s.content for s in run.steps.filter(kind=Step.Kind.TOOL_RESULT)], ensure_ascii=False
        ).lower()
        for needle in needles:
            if needle.lower() not in blob:
                problems.append(f"в ответе инструмента нет «{needle}»")

    if expect.get("approval") and not run.steps.filter(kind=Step.Kind.APPROVAL).exists():
        problems.append("не запросил подтверждение человека")

    if run.status != "done":
        problems.append(f"статус {run.status}")
    return not problems, problems


def run_case(task: str, material: str, approve: bool):
    from agent.core import loop

    run = loop.create_run(task, material)
    for _ in range(3):  # подтверждаем запросы агента, пока он не закончит
        loop.advance(run.id)
        run.refresh_from_db()
        if run.status != "awaiting_approval":
            break
        loop.decide(run.id, approve=approve)
    return run


def check_compare(case: dict, run) -> list[str]:
    """Требование 16 ТЗ: тот же запрос на другую дату даёт другой ответ."""
    spec = case.get("compare")
    if not spec:
        return []
    other = run_case(spec["task"], spec.get("input", ""), case.get("approve", True))
    problems = []
    if spec.get("must_differ", True) and (other.final_report or "") == (run.final_report or ""):
        problems.append("ответ на вторую дату совпал с первым")
    for needle in spec.get("contains", []):
        if needle.lower() not in (other.final_report or "").lower():
            problems.append(f"во втором ответе нет «{needle}»")
    if other.status != "done":
        problems.append(f"второй запуск: статус {other.status}")
    return problems


def load_cases(limit: int | None) -> list[dict]:
    """Кейсы активного домена: cases.<домен>.json, иначе общий cases.json."""
    from django.conf import settings

    path = HERE / f"cases.{settings.AGENT_DOMAIN}.json"
    if not path.exists():
        path = HERE / "cases.json"
    print(f"кейсы: {path.name}")
    cases = json.loads(path.read_text(encoding="utf-8"))
    for case in cases:
        if "input_file" in case:
            case["input"] = (HERE / case["input_file"]).read_text(encoding="utf-8")
    return cases[:limit] if limit else cases


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int)
    parser.add_argument("--real", action="store_true", help="использовать настоящую модель")
    parser.add_argument("--out", default=str(BACKEND.parent / "docs/EVAL.md"))
    # Требование 13 ТЗ: ответ за разумное время, ориентир — 10 секунд
    parser.add_argument("--max-seconds", type=float, default=10.0)
    args = parser.parse_args()

    if not args.real:
        os.environ["LLM_MOCK"] = "1"

    import django

    django.setup()
    from django.conf import settings
    from agent.core import loop

    cases = load_cases(args.limit)
    if not cases:
        print("В cases.json нет кейсов. Заполните его материалом Задачи.")
        return 1

    rows, passed, tokens, slowest = [], 0, 0, 0.0
    for case in cases:
        started = time.time()
        run = run_case(case["task"], case.get("input", ""), case.get("approve", True))
        elapsed = time.time() - started
        slowest = max(slowest, elapsed)
        ok, problems = check(case, run)
        problems += check_compare(case, run)
        # Время меряем только с настоящей моделью: в mock отвечает заглушка
        if args.real and elapsed > args.max_seconds:
            problems.append(f"{elapsed:.1f} с при пороге {args.max_seconds:.0f}")
        ok = not problems
        passed += ok
        tokens += run.prompt_tokens + run.completion_tokens
        rows.append((case["id"], ok, elapsed, run.llm_calls, "; ".join(problems) or "—"))
        print(("PASS " if ok else "FAIL ") + case["id"] + ("" if ok else ": " + "; ".join(problems)))

    md = [
        "# Результаты прогона",
        "",
        f"Кейсов: {len(rows)} · прошло: {passed} · точность: **{passed / len(rows) * 100:.0f}%**",
        f"Режим: {'настоящая модель ' + settings.OPENAI_MODEL if args.real else 'mock'} · "
        f"токенов: {tokens} · самый долгий ответ: {slowest:.1f} с при пороге {args.max_seconds:.0f} с",
        "",
        "| Кейс | Результат | Время | Вызовов модели | Замечания |",
        "|---|---|---|---|---|",
        *[
            f"| {i} | {'✅' if ok else '❌'} | {sec:.1f} с | {calls} | {note} |"
            for i, ok, sec, calls, note in rows
        ],
    ]
    Path(args.out).write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"\n{passed}/{len(rows)} · отчёт: {args.out}")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
