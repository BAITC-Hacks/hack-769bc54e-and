import json

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_POST

from .core import domains, loop
from .models import Run

# Материал больше этого не принимаем: защита от случайной вставки гигантского файла,
# из-за которой инструмент повесит поток прямо во время демо.
MAX_INPUT_CHARS = 200_000


def _body(request):
    try:
        return json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return {}


def _run_json(run, with_steps=True):
    data = {
        "id": str(run.id),
        "task": run.task,
        "status": run.status,
        "final_report": run.final_report,
        "created_at": run.created_at.isoformat(),
        "tokens": {"prompt": run.prompt_tokens, "completion": run.completion_tokens},
    }
    if with_steps:
        data["steps"] = [
            {"id": s.id, "kind": s.kind, "title": s.title, "content": s.content, "at": s.created_at.isoformat()}
            for s in run.steps.all()
        ]
    return data


@require_GET
def health(request):
    return JsonResponse(
        {
            "ok": True,
            "mock": settings.LLM_MOCK,
            "model": None if settings.LLM_MOCK else settings.OPENAI_MODEL,
            "domain": settings.AGENT_DOMAIN,
        }
    )


@require_GET
def samples(request):
    return JsonResponse(domains.active().SAMPLES, safe=False)


@require_POST
def runs(request):
    data = _body(request)
    task = (data.get("task") or "").strip()
    if not task:
        return JsonResponse({"error": "task is required"}, status=400)
    input_text = data.get("input") or ""
    if len(input_text) > MAX_INPUT_CHARS:
        return JsonResponse(
            {"error": f"материал слишком большой: {len(input_text)} символов, максимум {MAX_INPUT_CHARS}"},
            status=400,
        )
    run = loop.create_run(task, input_text)
    loop.start_in_background(run.id)
    return JsonResponse(_run_json(run), status=201)


@require_GET
def run_detail(request, run_id):
    return JsonResponse(_run_json(get_object_or_404(Run, pk=run_id)))


@require_POST
def approve(request, run_id):
    get_object_or_404(Run, pk=run_id)
    try:
        loop.decide(run_id, bool(_body(request).get("approve")))
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=409)
    loop.start_in_background(run_id)
    return JsonResponse({"ok": True})
