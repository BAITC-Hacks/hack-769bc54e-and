"""Реестр инструментов агента. Ядро, домена не знает.

Инструменты объявляются в доменном модуле (`core/domains/<трек>.py`) декоратором @tool
и попадают сюда при его импорте. Схема параметров автоматически уходит в LLM.

Контракт инструмента:
- сигнатура `fn(ctx: RunContext, **args) -> dict` (JSON-сериализуемый);
- наружу не бросает исключений — ошибку возвращает моделью читаемым dict;
- никаких сетевых запросов без таймаута;
- `requires_approval=True` -> агент встаёт на паузу и ждёт человека.
"""
from dataclasses import dataclass
from typing import Callable


@dataclass
class RunContext:
    """Всё, что инструмент знает о запуске. Сырой материал — недоверенные данные."""

    run_id: str
    input_text: str
    # Постановка задачи дословно. Нужна там, где результат обязан зависеть от того,
    # что написал человек, а не от того, как это перескажет модель.
    task: str = ""


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    fn: Callable
    requires_approval: bool = False


REGISTRY: dict[str, Tool] = {}

NO_ARGS: dict = {"type": "object", "properties": {}}


def tool(name: str, description: str, parameters: dict | None = None, requires_approval: bool = False):
    """Декоратор регистрации. Описание — на английском, его читает модель."""

    def wrap(fn):
        REGISTRY[name] = Tool(
            name=name,
            description=description,
            parameters=parameters or NO_ARGS,
            fn=fn,
            requires_approval=requires_approval,
        )
        return fn

    return wrap


def schemas() -> list[dict]:
    """Описание инструментов в формате OpenAI tool calling."""
    return [
        {
            "type": "function",
            "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
        }
        for t in REGISTRY.values()
    ]


def execute(name: str, args: dict, ctx: RunContext) -> dict:
    t = REGISTRY.get(name)
    if not t:
        return {"error": f"unknown tool: {name}"}
    try:
        return t.fn(ctx, **args)
    except TypeError as e:  # модель прислала не те аргументы — пусть увидит ошибку и исправится
        return {"error": f"bad arguments: {e}"}
    except Exception as e:  # noqa: BLE001 — инструмент не должен ронять весь запуск
        return {"error": f"{type(e).__name__}: {e}"}
