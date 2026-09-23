#!/usr/bin/env python3
"""Сквозная проверка запущенного приложения. Только стандартная библиотека.

Проходит основной сценарий так, как его пройдёт технический эксперт:
health -> примеры -> запуск агента -> подтверждение действия -> готовый отчёт.

    python scripts/verify.py [--api http://localhost:8000] [--timeout 120]

Код возврата 0 — сценарий прошёл, 1 — нет.
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request

TERMINAL = {"done", "failed"}


def call(api: str, path: str, payload: dict | None = None) -> object:
    url = f"{api}/api{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read() or b"null")


def wait_for_backend(api: str, timeout: float) -> dict:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        try:
            return call(api, "/health")
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            last = str(e)
            time.sleep(1)
    raise SystemExit(f"FAIL: бэкенд {api} не ответил за {timeout:.0f} с ({last})")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    api = args.api.rstrip("/")

    health = wait_for_backend(api, args.timeout)
    mode = "mock (без ключа)" if health["mock"] else f"модель {health['model']}"
    print(f"health      ok    домен={health['domain']}, {mode}")

    samples = call(api, "/samples")
    if not samples:
        print("FAIL: домен не отдал ни одного примера")
        return 1
    sample = samples[0]
    print(f"samples     ok    {len(samples)} шт., берём «{sample['label']}»")

    run = call(api, "/runs", {"task": sample["task"], "input": sample["input"]})
    print(f"run         ok    id={run['id']}")

    deadline = time.time() + args.timeout
    approved = False
    while time.time() < deadline:
        run = call(api, f"/runs/{run['id']}")
        if run["status"] == "awaiting_approval":
            if approved:
                print("FAIL: агент повторно просит подтверждение")
                return 1
            pending = [s for s in run["steps"] if s["kind"] == "tool_call"][-1]
            print(f"approval    ok    агент остановился на «{pending['title']}», подтверждаем")
            call(api, f"/runs/{run['id']}/approve", {"approve": True})
            approved = True
        elif run["status"] in TERMINAL:
            break
        time.sleep(1)
    else:
        print(f"FAIL: агент не закончил за {args.timeout:.0f} с, статус {run['status']}")
        return 1

    if run["status"] != "done":
        error = [s for s in run["steps"] if s["kind"] == "error"]
        print(f"FAIL: статус {run['status']}; {error[-1]['content'] if error else 'без подробностей'}")
        return 1
    if not run["final_report"].strip():
        print("FAIL: отчёт пустой")
        return 1

    kinds = [s["kind"] for s in run["steps"]]
    print(f"report      ok    шагов: {len(kinds)} ({', '.join(dict.fromkeys(kinds))})")
    if not approved:
        print("note        подтверждение человека в этом сценарии не потребовалось")
    print("\nOK: основной сценарий пройден")
    return 0


if __name__ == "__main__":
    sys.exit(main())
