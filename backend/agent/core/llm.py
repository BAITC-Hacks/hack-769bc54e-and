"""Тонкая обёртка над LLM. Весь остальной код не знает, какой провайдер внутри.

chat() -> {"content": str | None, "tool_calls": [...], "usage": {"prompt", "completion"}}

Провайдер задаётся тремя переменными: OPENAI_API_KEY, OPENAI_MODEL, OPENAI_BASE_URL.
Любой OpenAI-совместимый endpoint (NVIDIA NIM и т.п.) подключается без правок кода.
"""
import json
import uuid

from django.conf import settings

_client = None


def chat(messages: list[dict], tool_schemas: list[dict]) -> dict:
    if settings.LLM_MOCK:
        return _mock_chat(messages)
    return _openai_chat(messages, tool_schemas)


def _openai_chat(messages, tool_schemas):
    global _client
    if _client is None:
        from openai import OpenAI

        _client = OpenAI(
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_BASE_URL,
            timeout=60,
            max_retries=2,
        )
    resp = _client.chat.completions.create(
        model=settings.OPENAI_MODEL,
        messages=messages,
        tools=tool_schemas,
        parallel_tool_calls=False,  # по одному действию за шаг — проще пауза на approval и читаемее журнал
    )
    msg = resp.choices[0].message
    calls = []
    for tc in msg.tool_calls or []:
        try:
            args = json.loads(tc.function.arguments or "{}")
        except json.JSONDecodeError:
            args = {}
        calls.append({"id": tc.id, "name": tc.function.name, "arguments": args})
    return {"content": msg.content, "tool_calls": calls, "usage": _usage(resp)}


def _usage(resp) -> dict:
    """Расход токенов. Нужен и для контроля бюджета, и как цифра для питча."""
    u = getattr(resp, "usage", None)
    return {
        "prompt": getattr(u, "prompt_tokens", 0) or 0,
        "completion": getattr(u, "completion_tokens", 0) or 0,
    }


# --------------------------------------------------------------------------
# Mock: заскриптованная "модель". Нужна, чтобы (1) пилить интерфейс без ключа и лимитов,
# (2) дать проверяющему запустить проект без наших ключей (п. 5.6.6 Положения),
# (3) иметь запасное демо, если на площадке ляжет сеть.
# Сам сценарий живёт в домене — здесь только механика. Это НЕ агент.
# --------------------------------------------------------------------------


def _mock_chat(messages):
    from . import domains

    raw, results = _replay(messages)
    step = domains.active().plan(raw, results)
    if step is None:
        return {"content": _mock_report(results), "tool_calls": [], "usage": _NO_USAGE}
    comment, name, arguments = step
    return {
        "content": comment,
        "tool_calls": [{"id": "call_" + uuid.uuid4().hex[:8], "name": name, "arguments": arguments}],
        "usage": _NO_USAGE,
    }


_NO_USAGE = {"prompt": 0, "completion": 0}


def _replay(messages) -> tuple[str, dict]:
    """Текст запроса и результаты уже выполненных инструментов: {имя: результат}.

    Когда сырой материал есть — домену интереснее его превью, когда нет — сама задача.
    """
    user = next(m["content"] for m in messages if m["role"] == "user")
    raw = user.split("<raw_material>")[-1] if "<raw_material>" in user else user
    names, results = {}, {}
    for m in messages:
        for tc in m.get("tool_calls") or []:
            names[tc["id"]] = tc["function"]["name"]
        if m["role"] == "tool":
            results[names.get(m["tool_call_id"], "?")] = json.loads(m["content"])
    return raw, results


def _mock_report(results: dict) -> str:
    body = "\n".join(
        f"### {name}\n```json\n{json.dumps(value, ensure_ascii=False, indent=2)}\n```"
        for name, value in results.items()
    )
    return (
        "## Отчёт (mock-режим)\n\n"
        "Настоящая модель выключена (`LLM_MOCK=1`), ниже — сырые результаты вызванных инструментов.\n\n"
        + body
    )
