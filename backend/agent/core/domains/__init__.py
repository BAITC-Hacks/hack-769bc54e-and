"""Доменный слой: один трек хакатона = один модуль в этой папке.

Ядро (`loop.py`, `llm.py`, `tools.py`) домена не знает. Смена трека = новый файл здесь
и `AGENT_DOMAIN=<имя>` в `.env`. Ничего больше менять не нужно.

Модуль домена обязан объявить:
    BRIEF: str                — доменная часть системного промпта (на английском)
    SAMPLES: list[dict]       — примеры для интерфейса: {id, label, task, input}
    plan(request, results) -> ... — следующий шаг заскриптованной модели для LLM_MOCK;
                                request — текст задачи либо превью материала:
                                (комментарий, имя инструмента, аргументы) либо None, если пора писать отчёт
и зарегистрировать инструменты декоратором `@tool` из `core.tools`.
"""
import importlib

from django.conf import settings

from . import _contract
from .. import tools

_module = None
_name: str | None = None


def load(name: str):
    """Делает домен активным: чистит реестр и перерегистрирует инструменты модуля."""
    global _module, _name
    tools.REGISTRY.clear()
    module = importlib.import_module(f"{__name__}.{name}")
    importlib.reload(module)  # реестр только что очищен — модуль обязан зарегистрироваться заново
    _contract.check(module, name)
    _module, _name = module, name
    return module


def active():
    """Активный домен. Загружается лениво по settings.AGENT_DOMAIN."""
    if _module is None or _name != settings.AGENT_DOMAIN:
        return load(settings.AGENT_DOMAIN)
    return _module
