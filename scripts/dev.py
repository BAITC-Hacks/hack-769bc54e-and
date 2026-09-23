#!/usr/bin/env python3
"""Команды разработки. Работают всюду, где есть Python — make ставить не нужно.

    python scripts/dev.py setup       установка после clone
    python scripts/dev.py backend     запуск бэкенда на :8000
    python scripts/dev.py frontend    запуск фронтенда на :3000
    python scripts/dev.py test        тесты Django
    python scripts/dev.py typecheck   tsc --noEmit
    python scripts/dev.py check       test + typecheck
    python scripts/dev.py verify      сквозной прогон по запущенному приложению
    python scripts/dev.py llm         проверить ключ, модель и tool calling у провайдера
    python scripts/dev.py eval        прогнать кейсы из backend/agent/evals/cases.json
    python scripts/dev.py newdomain X создать backend/agent/core/domains/X.py из образца
    python scripts/dev.py hour N      почасовой ритуал: check, запись, коммит, пуш, тег

Makefile делает то же самое и просто вызывает этот файл.
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / "backend" / ".venv"
WINDOWS = os.name == "nt"


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if WINDOWS else "bin/python")


def run(cmd: list[str], cwd: Path = ROOT) -> int:
    print("$", " ".join(str(c) for c in cmd))
    return subprocess.call([str(c) for c in cmd], cwd=str(cwd))


def npm(args: list[str], cwd: Path) -> int:
    exe = shutil.which("npm")
    if not exe:
        print("FAIL: npm не найден в PATH. Нужен Node.js 22+.")
        return 1
    return run([exe, *args], cwd=cwd)


def need_venv() -> Path:
    py = venv_python()
    if not py.exists():
        print(f"FAIL: нет окружения {VENV}. Сначала: python scripts/dev.py setup")
        raise SystemExit(1)
    return py


def cmd_setup(_args: list[str]) -> int:
    env, example = ROOT / ".env", ROOT / ".env.example"
    if not env.exists() and example.exists():
        env.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
        print("создан .env из .env.example")
    if not venv_python().exists():
        if run([sys.executable, "-m", "venv", VENV]):
            return 1
    py = venv_python()
    if run([py, "-m", "pip", "install", "--quiet", "--upgrade", "pip"]):
        return 1
    if run([py, "-m", "pip", "install", "--quiet", "-r", ROOT / "backend/requirements.txt"]):
        return 1
    if run([py, ROOT / "backend/manage.py", "migrate"]):
        return 1
    return npm(["install", "--no-audit", "--no-fund"], ROOT / "frontend")


def cmd_backend(_args: list[str]) -> int:
    py = need_venv()
    run([py, ROOT / "backend/manage.py", "migrate", "-v0"])
    return run([py, ROOT / "backend/manage.py", "runserver", "8000"])


def cmd_frontend(_args: list[str]) -> int:
    return npm(["run", "dev"], ROOT / "frontend")


def cmd_test(_args: list[str]) -> int:
    return run([need_venv(), ROOT / "backend/manage.py", "test", "agent"])


def cmd_typecheck(_args: list[str]) -> int:
    return npm(["run", "typecheck"], ROOT / "frontend")


def cmd_check(_args: list[str]) -> int:
    return cmd_test([]) or cmd_typecheck([])


def cmd_verify(_args: list[str]) -> int:
    return run([sys.executable, ROOT / "scripts/verify.py"])


def cmd_llm(_args: list[str]) -> int:
    return run([need_venv(), ROOT / "scripts/check_llm.py"])


def cmd_eval(args: list[str]) -> int:
    return run([need_venv(), ROOT / "backend/agent/evals/run.py", *args])


def cmd_newdomain(args: list[str]) -> int:
    """Копия образца под новый трек — чтобы в 13:30 не вспоминать контракт домена."""
    if not args:
        print("FAIL: укажите имя, например: python scripts/dev.py newdomain logistics")
        return 1
    name = args[0]
    if not name.isidentifier() or name.startswith("_"):
        print(f"FAIL: '{name}' не годится как имя модуля Python")
        return 1
    target = ROOT / "backend/agent/core/domains" / f"{name}.py"
    if target.exists():
        print(f"FAIL: {target} уже существует")
        return 1
    source = (ROOT / "backend/agent/core/domains/example.py").read_text(encoding="utf-8")
    target.write_text(source, encoding="utf-8")
    print(f"создан {target}")
    print(f"дальше: поставьте AGENT_DOMAIN={name} в .env, перепишите BRIEF, SAMPLES, plan() и инструменты")
    return 0


def cmd_hour(args: list[str]) -> int:
    """Почасовой ритуал по п. 5.4.8 Положения: без него команду снимают с отбора."""
    number = args[0] if args else ""
    if not number.isdigit():
        print("FAIL: укажите номер часа, например: python scripts/dev.py hour 1")
        return 1
    if cmd_check([]):
        print("FAIL: проверки не прошли — чиним, потом коммитим")
        return 1
    note = input("что сделано за этот час: ").strip() or "промежуточный результат"
    hourly = ROOT / "docs/HOURLY.md"
    text = hourly.read_text(encoding="utf-8")
    marker = f"| {number} |"
    line = next((l for l in text.splitlines() if l.startswith(marker)), None)
    if line:
        cells = line.split("|")
        cells[3] = f" {note} "
        text = text.replace(line, "|".join(cells))
        hourly.write_text(text, encoding="utf-8")
    else:
        print(f"предупреждение: строка для часа {number} не найдена в docs/HOURLY.md, допишите вручную")
    tag = f"h{number}"
    for cmd in (
        ["git", "add", "-A"],
        ["git", "commit", "-m", f"час {number}: {note}"],
        ["git", "pull", "--rebase"],
        ["git", "push"],
        ["git", "tag", "-f", tag],
        ["git", "push", "-f", "origin", tag],
    ):
        if run(cmd):
            print(f"FAIL на шаге: {' '.join(cmd)}. Доделайте вручную — час без пуша стоит дисквалификации.")
            return 1
    print(f"OK: час {number} зафиксирован и запушен, тег {tag}")
    return 0


COMMANDS = {
    "setup": cmd_setup,
    "backend": cmd_backend,
    "frontend": cmd_frontend,
    "test": cmd_test,
    "typecheck": cmd_typecheck,
    "check": cmd_check,
    "verify": cmd_verify,
    "llm": cmd_llm,
    "eval": cmd_eval,
    "newdomain": cmd_newdomain,
    "hour": cmd_hour,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument("args", nargs="*", help="аргументы команды")
    parsed = parser.parse_args()
    return COMMANDS[parsed.command](parsed.args)


if __name__ == "__main__":
    sys.exit(main())
