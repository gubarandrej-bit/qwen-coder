# -*- coding: utf-8 -*-
"""Конфигурация Gunicorn. Учитывает слабый CPU-сервер без GPU
(AMD FX-8120, 8 ядер, 24 ГБ RAM): воркеров — немного, потоки для загрузок."""

bind = "0.0.0.0:5000"
workers = 3                 # 8 ядер, но LLM-Ollama тоже нужен CPU
threads = 4
timeout = 600               # долгие запросы к локальной нейросети
graceful_timeout = 30
keepalive = 5
max_requests = 1000
max_requests_jitter = 100
worker_class = "gthread"
accesslog = "-"
errorlog = "-"
loglevel = "info"
# приложение однопроцессное по БД SQLite — избегаем конкурентной записи:
preload_app = False
