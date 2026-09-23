import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR.parent / ".env")

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-only-change-me")
DEBUG = os.getenv("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = ["*"]  # хакатон; в проде сузить

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "corsheaders",
    "agent",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
]

CORS_ALLOW_ALL_ORIGINS = True  # хакатон; в проде сузить

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
        # агент пишет шаги из фонового потока — даём SQLite подождать лок
        "OPTIONS": {"timeout": 20},
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "Asia/Almaty"

# --- Agent ---
# Предметная область = модуль в agent/core/domains/. Смена трека = смена этого значения.
AGENT_DOMAIN = os.getenv("AGENT_DOMAIN", "example")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
# Пусто -> api.openai.com. Любой OpenAI-совместимый провайдер подключается сменой этой строки
# (например, NVIDIA NIM: https://integrate.api.nvidia.com/v1).
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "")
# Значения по умолчанию нет: модель обязана быть задана явно и проверена
# командой `python scripts/dev.py llm`.
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "")
LLM_MOCK = os.getenv("LLM_MOCK", "0") == "1" or not OPENAI_API_KEY or not OPENAI_MODEL
AGENT_MAX_STEPS = int(os.getenv("AGENT_MAX_STEPS", "12"))
