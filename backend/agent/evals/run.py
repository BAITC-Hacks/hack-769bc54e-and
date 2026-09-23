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
        "approval": true                     // обязан ли остановиться на подтверждении
      }
    }
"""
import argparse
import json
import os
import sys
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

    if expect.get("approval") and not run.steps.filter(kind=Step.Kind.APPROVAL).exists():
        problems.append("не запросил подтверждение человека")

    if run.status != "done":
        problems.append(f"статус {run.status}")
    return not problems, problems


def load_cases(limit: int | None) -> list[dict]:
    cases = json.loads((HERE / "cases.json").read_text(encoding="utf-8"))
    for case in cases:
        if "input_file" in case:
            case["input"] = (HERE / case["input_file"]).read_text(encoding="utf-8")
    return cases[:limit] if limit else cases


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int)
    parser.add_argument("--real", action="store_true", help="использовать настоящую модель")
    parser.add_argument("--out", default=str(BACKEND.parent / "docs/EVAL.md"))
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

    rows, passed, tokens = [], 0, 0
    for case in cases:
        run = loop.create_run(case["task"], case.get("input", ""))
        for _ in range(3):  # подтверждаем запросы агента, пока он не закончит
            loop.advance(run.id)
            run.refresh_from_db()
            if run.status != "awaiting_approval":
                break
            loop.decide(run.id, approve=case.get("approve", True))
        ok, problems = check(case, run)
        passed += ok
        tokens += run.prompt_tokens + run.completion_tokens
        rows.append((case["id"], ok, run.llm_calls, "; ".join(problems) or "—"))
        print(("PASS " if ok else "FAIL ") + case["id"] + ("" if ok else ": " + "; ".join(problems)))

    md = [
        "# Результаты прогона",
        "",
        f"Кейсов: {len(rows)} · прошло: {passed} · точность: **{passed / len(rows) * 100:.0f}%**",
        f"Режим: {'настоящая модель ' + settings.OPENAI_MODEL if args.real else 'mock'} · токенов: {tokens}",
        "",
        "| Кейс | Результат | Вызовов модели | Замечания |",
        "|---|---|---|---|",
        *[f"| {i} | {'✅' if ok else '❌'} | {calls} | {note} |" for i, ok, calls, note in rows],
    ]
    Path(args.out).write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"\n{passed}/{len(rows)} · отчёт: {args.out}")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
