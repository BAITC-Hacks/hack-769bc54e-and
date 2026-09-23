"""Цикл агента: LLM решает -> мы выполняем инструмент -> результат обратно в LLM -> ... -> отчёт.

Всё состояние лежит в БД (Run.messages + Run.pending), поэтому запуск можно
поставить на паузу ради человека и продолжить позже — advance() идемпотентно подхватывает с места остановки.
"""
import json
import threading
from contextlib import contextmanager

from django.conf import settings
from django.db import close_old_connections

from ..models import Run, Step
from . import domains, llm, tools
from .prompts import system_prompt


# Модели уходит только превью материала: полный текст читают инструменты через ctx.
# Так мы не платим за десятки тысяч токенов, которые по системному промпту модели и не нужны.
PREVIEW_CHARS = 800


def create_run(task: str, input_text: str = "") -> Run:
    domain = domains.active()
    user = task
    if input_text:
        # Сырьё кладём в отдельный тег и в system prompt объявляем его недоверенными данными (анти prompt-injection)
        preview = input_text[:PREVIEW_CHARS]
        rest = len(input_text) - len(preview)
        cut = f"\n[...ещё {rest} символов доступны инструментам]" if rest else ""
        user += (
            f'\n\n<raw_material preview_only="true" total_chars="{len(input_text)}">\n'
            f"{preview}{cut}\n</raw_material>"
        )
    return Run.objects.create(
        task=task,
        input_text=input_text,
        messages=[
            {"role": "system", "content": system_prompt(domain.BRIEF)},
            {"role": "user", "content": user},
        ],
    )


def start_in_background(run_id):
    """Для хакатона хватит потока. Вырастете — замените на Celery/RQ, интерфейс тот же."""

    def work():
        try:
            advance(run_id)
        finally:
            close_old_connections()

    threading.Thread(target=work, daemon=True).start()


_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
HANDOVER_TIMEOUT = 5  # секунд на передачу запуска между потоками


@contextmanager
def _exclusive(run_id):
    """Один запуск продвигает только один поток.

    Подтверждение человека и фоновый поток могут прийти одновременно; без этого
    два advance() перезапишут друг другу Run.messages.

    Ждём недолго, а не отказываем сразу: предыдущий поток может быть в миллисекунде
    от выхода (он как раз поставил запуск на паузу), и отказ подвесил бы запуск навсегда.
    Если ждать пришлось дольше — значит, другой поток действительно работает.
    """
    key = str(run_id)
    with _locks_guard:
        lock = _locks.setdefault(key, threading.Lock())
    if not lock.acquire(timeout=HANDOVER_TIMEOUT):
        yield False
        return
    try:
        yield True
    finally:
        lock.release()


def advance(run_id):
    with _exclusive(run_id) as acquired:
        if acquired:
            _advance(run_id)


def _advance(run_id):
    domains.active()  # инструменты активного домена должны быть в реестре до первого вызова модели
    run = Run.objects.get(pk=run_id)
    ctx = tools.RunContext(run_id=str(run.id), input_text=run.input_text)
    try:
        run.status = Run.Status.RUNNING
        run.save(update_fields=["status"])
        while True:
            if not _drain_pending(run, ctx):
                return  # ждём человека
            if run.llm_calls >= settings.AGENT_MAX_STEPS:
                _finish(run, Run.Status.FAILED, Step.Kind.ERROR, "Лимит шагов исчерпан", {"max_steps": settings.AGENT_MAX_STEPS})
                return

            reply = llm.chat(run.messages, tools.schemas())
            run.llm_calls += 1
            usage = reply.get("usage") or {}
            run.prompt_tokens += usage.get("prompt", 0)
            run.completion_tokens += usage.get("completion", 0)
            run.messages.append(_assistant_message(reply))

            if not reply["tool_calls"]:
                run.final_report = reply["content"] or ""
                _finish(run, Run.Status.DONE, Step.Kind.FINAL, "Отчёт готов", {"text": run.final_report})
                return

            if reply["content"]:
                Step.objects.create(run=run, kind=Step.Kind.THOUGHT, content={"text": reply["content"]})
            run.pending = reply["tool_calls"]
            run.save()
    except Exception as e:  # noqa: BLE001
        _finish(run, Run.Status.FAILED, Step.Kind.ERROR, "Сбой агента", {"error": f"{type(e).__name__}: {e}"})


def decide(run_id, approve: bool):
    """Человек ответил на запрос подтверждения."""
    run = Run.objects.get(pk=run_id)
    if run.status != Run.Status.AWAITING_APPROVAL or not run.pending:
        raise ValueError("run is not awaiting approval")
    run.pending[0]["decision"] = "approved" if approve else "rejected"
    run.status = Run.Status.RUNNING
    run.save()
    Step.objects.create(
        run=run,
        kind=Step.Kind.APPROVAL,
        title="Одобрено человеком" if approve else "Отклонено человеком",
        content={"tool": run.pending[0]["name"], "approved": approve},
    )


def _drain_pending(run, ctx) -> bool:
    """Выполняет очередь tool calls. False = встали на паузу."""
    while run.pending:
        call = run.pending[0]
        spec = tools.REGISTRY.get(call["name"])
        decision = call.get("decision")

        if spec and spec.requires_approval and not decision:
            if run.status != Run.Status.AWAITING_APPROVAL:
                Step.objects.create(run=run, kind=Step.Kind.TOOL_CALL, title=call["name"], content={"args": call["arguments"], "needs_approval": True})
                run.status = Run.Status.AWAITING_APPROVAL
                run.save()
            return False

        if decision == "rejected":
            result = {"rejected_by_human": True, "note": "The human declined this action. Do not retry it."}
        else:
            if not (spec and spec.requires_approval):  # для approval-инструментов шаг уже записан
                Step.objects.create(run=run, kind=Step.Kind.TOOL_CALL, title=call["name"], content={"args": call["arguments"]})
            result = tools.execute(call["name"], call["arguments"], ctx)

        Step.objects.create(run=run, kind=Step.Kind.TOOL_RESULT, title=call["name"], content={"result": result})
        run.messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result, ensure_ascii=False)})
        run.pending = run.pending[1:]
        run.save()
    return True


def _assistant_message(reply):
    msg = {"role": "assistant", "content": reply["content"]}
    if reply["tool_calls"]:
        msg["tool_calls"] = [
            {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": json.dumps(c["arguments"], ensure_ascii=False)}}
            for c in reply["tool_calls"]
        ]
    return msg


def _finish(run, status, kind, title, content):
    run.status = status
    run.save()
    Step.objects.create(run=run, kind=kind, title=title, content=content)
