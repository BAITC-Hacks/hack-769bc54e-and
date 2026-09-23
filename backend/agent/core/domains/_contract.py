"""Проверка, что доменный модуль объявил всё, что от него ждёт ядро.

Ошибка здесь лучше, чем пустой экран на демо: домен пишется в спешке, и забыть
SAMPLES или plan() легко.
"""
from .. import tools

REQUIRED = ("BRIEF", "SAMPLES", "plan")
SAMPLE_KEYS = {"id", "label", "task", "input"}


class DomainError(RuntimeError):
    pass


def check(module, name: str) -> None:
    missing = [attr for attr in REQUIRED if not hasattr(module, attr)]
    if missing:
        raise DomainError(f"домен '{name}' не объявил: {', '.join(missing)}")
    if not module.BRIEF.strip():
        raise DomainError(f"домен '{name}': BRIEF пустой")
    for i, sample in enumerate(module.SAMPLES):
        if not SAMPLE_KEYS <= set(sample):
            raise DomainError(f"домен '{name}': в SAMPLES[{i}] нет полей {SAMPLE_KEYS - set(sample)}")
    if not tools.REGISTRY:
        raise DomainError(f"домен '{name}' не зарегистрировал ни одного инструмента через @tool")
