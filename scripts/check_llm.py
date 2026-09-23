#!/usr/bin/env python3
"""Проверка провайдера модели до того, как это станет проблемой на площадке.

    python scripts/dev.py llm

Что делает:
  1. читает .env и показывает, куда и чем будем ходить;
  2. запрашивает список моделей — сразу видно, жив ли ключ и что он умеет;
  3. делает один реальный вызов с tool calling — то, на чём держится весь агент,
     и что поддерживают не все модели;
  4. печатает расход токенов за эту проверку.

Кредиты конечны ($50 на команду), поэтому проверка стоит долю цента: один короткий запрос.
"""
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

PING_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "ping",
            "description": "Health probe. Call it once with text='ok'.",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    }
]


def main() -> int:
    import django

    django.setup()
    from django.conf import settings

    print(f"endpoint   {settings.OPENAI_BASE_URL}")
    print(f"модель     {settings.OPENAI_MODEL or '(не задана!)'}")
    print(f"ключ       {'задан, ' + settings.OPENAI_API_KEY[:7] + '…' if settings.OPENAI_API_KEY else 'НЕ ЗАДАН'}")

    if not settings.OPENAI_API_KEY:
        print("\nFAIL: OPENAI_API_KEY пуст. Пропишите его в .env.")
        return 1
    if not settings.OPENAI_MODEL:
        print("\nFAIL: OPENAI_MODEL пуст. Возьмите имя из списка ниже и пропишите в .env.")

    from openai import OpenAI

    client = OpenAI(
        api_key=settings.OPENAI_API_KEY,
        base_url=settings.OPENAI_BASE_URL,
        timeout=30,
        max_retries=1,
    )

    try:
        names = sorted(m.id for m in client.models.list().data)
        print(f"\nдоступно моделей: {len(names)}")
        for name in names[:40]:
            print(f"  {'-> ' if name == settings.OPENAI_MODEL else '   '}{name}")
        if len(names) > 40:
            print(f"   … ещё {len(names) - 40}")
        if settings.OPENAI_MODEL and settings.OPENAI_MODEL not in names:
            print(f"\nВНИМАНИЕ: '{settings.OPENAI_MODEL}' нет в списке. Возможно, провайдер его всё равно примет.")
    except Exception as e:  # noqa: BLE001 — интересует любой отказ провайдера
        print(f"\nсписок моделей недоступен: {type(e).__name__}: {e}")
        print("(не критично: не все провайдеры отдают /models)")

    if not settings.OPENAI_MODEL:
        return 1

    print("\nпробный вызов с tool calling…")
    try:
        resp = client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=[{"role": "user", "content": "Call the ping tool with text='ok'."}],
            tools=PING_TOOL,
            parallel_tool_calls=False,
        )
    except Exception as e:  # noqa: BLE001
        print(f"FAIL: {type(e).__name__}: {e}")
        print("\nЕсли дело в parallel_tool_calls — уберите этот параметр в backend/agent/core/llm.py.")
        return 1

    msg = resp.choices[0].message
    calls = msg.tool_calls or []
    usage = getattr(resp, "usage", None)
    spent = (getattr(usage, "prompt_tokens", 0) or 0) + (getattr(usage, "completion_tokens", 0) or 0)

    if not calls:
        print(f"FAIL: модель не вызвала инструмент, ответила текстом: {(msg.content or '')[:200]}")
        print("Такая модель для агента не годится — возьмите другую.")
        return 1

    print(f"OK: модель вызвала {calls[0].function.name}({calls[0].function.arguments})")
    print(f"потрачено токенов: {spent}")
    print("\nГотово: можно ставить LLM_MOCK=0.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
