# Инструкция по установке DocCheck (репозиторий `qwen-coder`) на сервер

Система анализа и проверки рабочей документации по инженерным системам
(электроснабжение, электроосвещение, АПС/СОУЭ/АПТ, СКС/ЛВС/ВОЛС, видеонаблюдение,
СКУД, охранная сигнализация, АСУ/АСУТП).

---

## 1. Требования к серверу

| Параметр | Значение |
|---|---|
| Гипервизор | Proxmox VE (LXC или VM) |
| Гостевая ОС | Debian 12 или Ubuntu 22.04+ |
| CPU | 8 ядер (AMD FX-8120 или аналог) |
| RAM | 24 ГБ |
| Диск | SSD 60 ГБ |
| GPU | не требуется (опционально HD 7950 3 ГБ — ускорения не даёт, модели работают на CPU) |
| Python | 3.10+ (устанавливается скриптом) |

## 2. Публикация кода на GitHub (один раз)

```bash
cd /path/to/qwen-coder
git add -A
git commit -m "DocCheck: полная версия"
git branch -M main
git remote add origin https://github.com/<ВАШ_ЛОГИН>/qwen-coder.git
git push -u origin main
```

## 3. Быстрая установка — ОДИН скрипт

На сервере (от root):

```bash
# вариант 1: скачать скрипт из репозитория
git clone https://github.com/<ВАШ_ЛОГИН>/qwen-coder.git /tmp/doccheck-src
sudo bash /tmp/doccheck-src/install.sh

# вариант 2: неинтерактивно (для автоматизации)
sudo REPO_URL=https://github.com/<ВАШ_ЛОГИН>/qwen-coder.git \
     ADMIN_PASSWORD='ВашНадёжныйПароль' \
     bash install.sh -y
```

Скрипт выполняет 8 шагов:
1. Проверка ОС и зависимостей (python3, venv, git).
2. Клонирование/обновление репозитория в `/opt/doccheck`.
3. Создание виртуального окружения + `pip install -r requirements.txt`.
4. Генерация `.env` (SECRET_KEY, пароль администратора, режимы LLM).
5. Инициализация БД SQLite: пользователи, база НТД (15 документов), бесплатные модели ИИ.
6. Прогон unit-тестов и smoke-тестов.
7. Установка systemd-сервиса `doccheck` (автозапуск, Gunicorn, порт 5000).
8. Опционально: Nginx reverse-proxy + Let's Encrypt HTTPS + Ollama с локальными моделями.

## 4. Ручная установка (альтернатива)

```bash
git clone https://github.com/<ВАШ_ЛОГИН>/qwen-coder.git /opt/doccheck
cd /opt/doccheck
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env        # задайте SECRET_KEY и ADMIN_PASSWORD
venv/bin/python -m app.seed_ntd          # инициализация БД
venv/bin/gunicorn -c gunicorn.conf.py wsgi:app   # запуск: http://IP:5000
```

## 5. Доступ к системе

- URL: `http://<IP-сервера>:5000` (или `https://<домен>` после настройки Nginx).
- **Логин администратора:** `admin`
- **Пароль:** задается при установке; если не меняли — по умолчанию `Admin123!`
  (настоятельно рекомендуем сменить сразу после первого входа).

## 6. Полезные команды

```bash
systemctl status doccheck        # состояние сервиса
systemctl restart doccheck       # перезапуск
journalctl -u doccheck -f        # живые логи
venv/bin/python scripts/smoke_test.py   # повторный прогон тестов
```

Обновление до новой версии:

```bash
cd /opt/doccheck && sudo bash install.sh   # скрипт сам сделает git pull и пересборку
```

## 7. Режимы работы и модели ИИ

| Режим | Что используется | Настройка |
|---|---|---|
| `local` | Локальные модели через Ollama (`http://127.0.0.1:11434/v1`) | ставится скриптом (шаг 8) |
| `cloud` | Облачные API (OpenRouter, Groq, DeepSeek…) | `CLOUD_LLM_BASE_URL`, `CLOUD_LLM_API_KEY` в `.env`; добавление/удаление моделей — вручную через веб-страницу «Модели ИИ» или API `/api/models` |
| `hybrid` | Локальные + облачные одновременно | оба набора переменных |

Бесплатные модели, рекомендуемые для CPU-сервера (24 ГБ RAM, без GPU):

| Модель | Размер | RAM | Назначение |
|---|---|---|---|
| `qwen2.5:3b-instruct` (Ollama) | ~2 ГБ | ~4 ГБ | основной анализ текстов/проверок |
| `phi3:mini` (Ollama) | ~2.2 ГБ | ~4 ГБ | альтернатива, рассуждения |
| `deepseek-r1:1.5b` (Ollama) | ~1.1 ГБ | ~2 ГБ | простые расчетные задачи |
| `llama3.2:3b` (Ollama) | ~2 ГБ | ~4 ГБ | универсальный резерв |
| OpenRouter `meta-llama/llama-3.1-8b:free` | облако | — | тяжёлые проверки, QC |
| Groq `llama-3.3-70b-versatile` (бесплатный тариф) | облако | — | финальная экспертиза отчёта |

Команда установки локальных моделей:

```bash
ollama pull qwen2.5:3b-instruct
ollama pull phi3:mini
```

## 8. База НТД

При инициализации в БД загружаются 15 нормативных документов
(123-ФЗ, СП 3.13130.2009, СП 6.13130.2021, СП 76.13330.2016, СП 484/486.1311500.2020,
ПУЭ изд. 7, ГОСТ 21.208-2013, ГОСТ Р 21.101-2026, ГОСТ 21.210-2014, ГОСТ Р 21.703-2020,
ГОСТ 31565-2012, ГОСТ Р 53246-2025, ГОСТ Р 58238-2018, СП 48.13330.2019).
Перед каждой проверкой планировщик автоматически сверяет актуальность документов
(интервал настраивается переменной `NTD_CHECK_INTERVAL_HOURS`).

## 9. Форматы входных данных и выгрузки

- Вход: `xls/xlsx`, `doc/docx`, `pdf`, `dwg/dxf`.
- Выгрузка результатов проверок и отчётов: `xlsx`, `docx`.
- Ведомости: объёмов работ и оборудования/материалов — `xlsx`.

## 10. Устранение неполадок

| Проблема | Решение |
|---|---|
| Порт 5000 занят | измените `PORT` в `.env` и `gunicorn.conf.py`, `systemctl restart doccheck` |
| Не отвечает Ollama | `systemctl status ollama`, `ollama list` |
| Ошибка БД | удалите `/opt/doccheck/data/doccheck.db` и выполните `python -m app.seed_ntd` (данные проверок будут потеряны) |
| 502 за Nginx | проверьте `sock`/порт в `gunicorn.conf.py` и `proxy_pass` в конфиге Nginx |
