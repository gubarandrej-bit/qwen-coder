# -*- coding: utf-8 -*-
"""Конфигурация системы анализа и проверки рабочей документации."""
import os

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# Загрузка .env из корня проекта (если установлен python-dotenv либо файл есть) —
# значения переменных окружения имеют приоритет над файлом .env.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE_DIR, ".env"))
except ImportError:
    pass


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-in-production-please")

    # База данных SQLite (файл в ./data)
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL",
        "sqlite:///" + os.path.join(BASE_DIR, "data", "doccheck.db"),
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Загрузка файлов
    UPLOAD_FOLDER = os.environ.get(
        "UPLOAD_FOLDER", os.path.join(BASE_DIR, "data", "uploads")
    )
    ALLOWED_EXTENSIONS = {"xls", "xlsx", "doc", "docx", "pdf", "dwg", "dxf", "csv"}
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_UPLOAD_MB", "100")) * 1024 * 1024

    # Режимы работы системы: local | cloud | hybrid
    DEFAULT_MODE = os.environ.get("DOCHECK_MODE", "local")

    # Локальные LLM (Ollama / llama.cpp server / LM Studio) — OpenAI-совместимый API
    LOCAL_LLM_BASE_URL = os.environ.get("LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    LOCAL_LLM_API_KEY = os.environ.get("LOCAL_LLM_API_KEY", "ollama")

    # Облачные LLM (OpenRouter, Together, Groq, DeepInfra и др.)
    CLOUD_LLM_BASE_URL = os.environ.get("CLOUD_LLM_BASE_URL", "")
    CLOUD_LLM_API_KEY = os.environ.get("CLOUD_LLM_API_KEY", "")

    # Проверка актуальности НТД
    NTD_CHECK_ENABLED = os.environ.get("NTD_CHECK_ENABLED", "1") == "1"
    NTD_CHECK_INTERVAL_HOURS = int(os.environ.get("NTD_CHECK_INTERVAL_HOURS", "24"))

    # Учетная запись администратора по умолчанию (создается при init_db)
    ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "Admin123!")

    JSON_AS_ASCII = False
