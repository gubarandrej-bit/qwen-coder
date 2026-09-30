#!/usr/bin/env bash
# ============================================================================
#  DocCheck (репозиторий qwen-coder) — ЕДИНЫЙ скрипт установки на сервер
#  Сервер: Proxmox -> LXC/VM (Debian 12 / Ubuntu 22.04+), CPU AMD FX-8120,
#          24 ГБ RAM, SSD 60 ГБ, без GPU (или HD 7950 3 ГБ).
#
#  Что делает скрипт:
#    1. Проверяет ОС и зависимости (python3, venv, git);
#    2. Клонирует репозиторий https://github.com/<владелец>/qwen-coder
#       (или обновляет, если каталог уже существует);
#    3. Создает виртуальное окружение и ставит requirements.txt;
#    4. Создает .env (генерирует SECRET_KEY, спрашивает админский пароль,
#       URL репозитория и облачного LLM-провайдера);
#    5. Инициализирует БД SQLite с базой НТД и бесплатными моделями ИИ;
#    6. Прогоняет unit-тесты и smoke-тесты;
#    7. Устанавливает systemd-сервис doccheck (автозапуск, порт 5000);
#    8. Опционально: Nginx reverse-proxy +certbot HTTPS, Ollama + локальные
#       бесплатные модели (qwen2.5:3b, phi3:mini) для режима local/hybrid.
#
#  Запуск от root:   sudo bash install.sh
#  Неинтерактивно:   sudo ADMIN_PASSWORD='Secret123!' REPO_URL=https://github.com/me/qwen-coder.git bash install.sh -y
# ============================================================================
set -euo pipefail

# ---------------------------------------------------------------- аргументы --
ASSUME_YES=""; [ "${1:-}" = "-y" ] || [ "${1:-}" = "--yes" ] && ASSUME_YES=1
ask() {  # ask "вопрос" "значение_по_умолчанию" -> echo ответ
  local q="$1" d="${2:-}" a
  if [ -n "$ASSUME_YES" ]; then echo "$d"; return; fi
  read -r -p "$q [$d]: " a || true
  echo "${a:-$d}"
}
run_or_skip() { # в интерактивном режиме спросить подтверждение
  local msg="$1"; shift
  if [ -n "$ASSUME_YES" ]; then eval "$@"; return $?; fi
  if ask "$msg (y/n)" "y" | grep -qiE '^y'; then eval "$@"; else echo "  пропущено."; fi
}

INSTALL_DIR="${INSTALL_DIR:-/opt/doccheck}"
REPO_URL_DEFAULT="https://github.com/YOUR_USERNAME/qwen-coder.git"
REPO_URL="${REPO_URL:-$REPO_URL_DEFAULT}"
PORT="${PORT:-5000}"
ADMIN_USERNAME="${ADMIN_USERNAME:-admin}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-}"

echo "============================================================"
echo " Установка DocCheck — система проверки рабочей документации"
echo "============================================================"

# ----------------------------------------------------- 1. системные требования
[ "$(id -u)" = "0" ] || { echo "ОШИБКА: запустите от root:  sudo bash install.sh"; exit 1; }
. /etc/os-release 2>/dev/null || true
case "${ID:-}" in
  debian|ubuntu) ;;
  *) echo "ВНИМАНИЕ: скрипт тестировался на Debian 12 / Ubuntu 22.04+. Продолжаем..." ;;
esac

echo "[1/8] Системные пакеты..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git curl ca-certificates >/dev/null

PYV=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)'; then
  echo "  Python $PYV — OK"
else
  echo "ОШИБКА: требуется Python >= 3.9 (найдено $PYV). Обновите ОС/пакеты."; exit 1
fi

# --------------------------------------------------------------- 2. код проекта
echo "[2/8] Исходный код -> $INSTALL_DIR"
if [ ! -d "$INSTALL_DIR/.git" ]; then
  if [ "$REPO_URL" = "$REPO_URL_DEFAULT" ]; then
    echo "ОШИБКА: укажите URL вашего репозитория GitHub:"
    echo "   sudo REPO_URL=https://github.com/<ваш_логин>/qwen-coder.git bash install.sh"
    echo "(репозиторий должен быть публичным или используйте токен: https://user:TOKEN@github.com/...)"
    exit 1
  fi
  mkdir -p "$(dirname "$INSTALL_DIR")"
  git clone "$REPO_URL" "$INSTALL_DIR"
else
  echo "  каталог уже содержит git-репозиторий — pulling..."
  git -C "$INSTALL_DIR" pull --ff-only || echo "  (не удалось обновить — использую текущий код)"
fi
cd "$INSTALL_DIR"

# ------------------------------------------------------------ 3. вирт. окружение
echo "[3/8] Виртуальное окружение и зависимости (pip)..."
[ -d venv ] || python3 -m venv venv
./venv/bin/pip install --quiet --upgrade pip
./venv/bin/pip install --quiet -r requirements.txt

# ------------------------------------------------------------------- 4. конфиг
echo "[4/8] Конфигурация (.env)..."
if [ ! -f .env ]; then
  SECRET=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
  if [ -z "$ADMIN_PASSWORD" ]; then
    while true; do
      ADMIN_PASSWORD=$(ask "Пароль администратора ($ADMIN_USERNAME)" "Admin123!")
      [ ${#ADMIN_PASSWORD} -ge 8 ] && break
      echo "  пароль должен быть не короче 8 символов"
    done
  fi
  CLOUD_URL=$(ask "Облачный LLM base_url (OpenRouter/Groq/Together; пусто = без облака)" "https://openrouter.ai/api/v1")
  CLOUD_KEY=$(ask "API-ключ облачного провайдера (можно оставить пустым, добавить позже через веб-интерфейс)" "")
  cat > .env <<EOF
SECRET_KEY=$SECRET
DOCHECK_MODE=local
LOCAL_LLM_BASE_URL=http://127.0.0.1:11434/v1
LOCAL_LLM_API_KEY=ollama
CLOUD_LLM_BASE_URL=$CLOUD_URL
CLOUD_LLM_API_KEY=$CLOUD_KEY
ADMIN_USERNAME=$ADMIN_USERNAME
ADMIN_PASSWORD=$ADMIN_PASSWORD
NTD_CHECK_ENABLED=1
NTD_CHECK_INTERVAL_HOURS=24
MAX_UPLOAD_MB=100
EOF
  chmod 600 .env
  echo "  .env создан."
else
  echo "  .env уже существует — оставляю без изменений."
  ADMIN_PASSWORD=$(grep -E '^ADMIN_PASSWORD=' .env | cut -d= -f2-)
fi

# ------------------------------------------------------- 5. инициализация БД/НТД
echo "[5/8] Инициализация базы данных и справочника НТД..."
# create_app() сам создает таблицы, сидирует НТД и администратора —
# достаточно один раз импортировать приложение.
./venv/bin/python - <<'PY'
from app import create_app
app = create_app()
print("  БД готова: data/doccheck.db (таблицы + база НТД + учетная запись администратора)")
PY
mkdir -p data/uploads

# ------------------------------------------------------------------ 6. тесты
echo "[6/8] Проверка корректности: unit-тесты + smoke-тесты..."
if ./venv/bin/python scripts/unit_tests.py >/tmp/doccheck_unit.log 2>&1 \
   && ./venv/bin/python scripts/smoke_test.py >/tmp/doccheck_smoke.log 2>&1; then
  tail -1 /tmp/doccheck_unit.log
  tail -1 /tmp/doccheck_smoke.log
else
  echo "ОШИБКА: тесты не пройдены. Логи: /tmp/doccheck_unit.log /tmp/doccheck_smoke.log"
  tail -20 /tmp/doccheck_unit.log /tmp/doccheck_smoke.log || true
  exit 1
fi

# ---------------------------------------------------------- 7. systemd сервис
echo "[7/8] Systemd-сервис 'doccheck' (порт $PORT, автозапуск)..."
cat > /etc/systemd/system/doccheck.service <<EOF
[Unit]
Description=DocCheck - sistema proverki rabochey dokumentatsii
After=network.target

[Service]
User=root
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=$INSTALL_DIR/.env
ExecStart=$INSTALL_DIR/venv/bin/gunicorn -c $INSTALL_DIR/gunicorn.conf.py --bind 0.0.0.0:$PORT wsgi:app
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now doccheck
sleep 2
systemctl is-active --quiet doccheck && echo "  сервис активен: http://$(hostname -I | awk '{print $1}'):$PORT" \
  || { echo "ОШИБКА запуска:"; journalctl -u doccheck -n 30 --no-pager; exit 1; }

# ------------------------------------------- 8. опции: Ollama (локальные ИИ), Nginx
echo "[8/8] Дополнительные компоненты."

if run_or_skip "Установить Ollama и бесплатные локальные модели (qwen2.5:3b, phi3:mini)? Учитывайте: на 24 ГБ RAM/CPU без GPU это медленно, но работает."; then
  curl -fsSL https://ollama.com/install.sh | sh
  systemctl enable --now ollama || true
  echo "  скачиваю модели (по ~2-3 ГБ)..."
  ollama pull qwen2.5:3b-instruct || true
  ollama pull phi3:mini || true
  echo "  Ollama: http://127.0.0.1:11434 (режим local/hybrid готов)"
fi

if run_or_skip "Настроить Nginx как reverse-proxy на порту 80?"; then
  apt-get install -y -qq nginx >/dev/null
  cat > /etc/nginx/sites-available/doccheck <<EOF
server {
    listen 80;
    server_name _;
    client_max_body_size 100M;
    location / {
        proxy_pass http://127.0.0.1:$PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_read_timeout 600s;
    }
}
EOF
  ln -sf /etc/nginx/sites-available/doccheck /etc/nginx/sites-enabled/doccheck
  rm -f /etc/nginx/sites-enabled/default
  nginx -t && systemctl reload nginx
  echo "  Nginx настроен. Для HTTPS: certbot --nginx -d ваш_домен.ru"
fi

# ---------------------------------------------------------------------- итог
IP=$(hostname -I | awk '{print $1}')
echo
echo "============================================================"
echo "  УСТАНОВКА ЗАВЕРШЕНА"
echo "------------------------------------------------------------"
echo "  Веб-интерфейс : http://$IP:${PORT}/   (или :80, если Nginx)"
echo "  Логин         : $ADMIN_USERNAME"
echo "  Пароль        : ${ADMIN_PASSWORD:-<см. .env>}"
echo "  Каталог       : $INSTALL_DIR"
echo "  Управление    : systemctl {status|restart|stop} doccheck"
echo "  Логи          : journalctl -u doccheck -f"
echo "  Режимы ИИ     : local (Ollama), cloud (OpenRouter/Groq),"
echo "                  hybrid — переключаются в веб-интерфейсе"
echo "  Смена пароля  : nano $INSTALL_DIR/.env && systemctl restart doccheck"
echo "                  (или форма смены пароля в личном кабинете)"
echo "  Облачные модели добавляются/удаляются вручную (только администратор):"
echo "    Веб-интерфейс: страница «Модели ИИ»"
echo "    API:  POST   /api/models   {\"name\":\"meta-llama/llama-3.1-8b-instruct:free\",\"provider\":\"cloud\",\"base_url\":\"...\",\"api_key\":\"...\",\"register\":true}"
echo "          DELETE /api/models/<id>?remote=1   (remote=1 — удалить и на стороне провайдера через API)"
echo "============================================================"
