import uuid

from django.db import models


class Run(models.Model):
    """Один запуск агента: задача -> цикл шагов -> отчёт."""

    class Status(models.TextChoices):
        RUNNING = "running"
        AWAITING_APPROVAL = "awaiting_approval"
        DONE = "done"
        FAILED = "failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.TextField()
    input_text = models.TextField(blank=True)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.RUNNING)
    # Полная история в формате OpenAI chat — благодаря ей запуск можно ставить на паузу и продолжать
    messages = models.JSONField(default=list)
    # Очередь tool calls, которые модель запросила, но мы ещё не выполнили
    pending = models.JSONField(default=list)
    llm_calls = models.IntegerField(default=0)
    # Расход токенов за запуск: контроль бюджета на площадке и цифра «сколько стоит один разбор» для питча
    prompt_tokens = models.IntegerField(default=0)
    completion_tokens = models.IntegerField(default=0)
    final_report = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class Step(models.Model):
    """Запись в журнале агента. Это то, что видит пользователь (и жюри)."""

    class Kind(models.TextChoices):
        THOUGHT = "thought"
        TOOL_CALL = "tool_call"
        TOOL_RESULT = "tool_result"
        APPROVAL = "approval"
        FINAL = "final"
        ERROR = "error"

    run = models.ForeignKey(Run, related_name="steps", on_delete=models.CASCADE)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    title = models.CharField(max_length=200, blank=True)
    content = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
