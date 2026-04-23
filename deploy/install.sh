#!/usr/bin/env bash
# install.sh — первичная установка Stenvik Leads на Debian/Ubuntu VPS.
# IP-РЕЖИМ: приложение слушает напрямую 0.0.0.0:8080 без nginx и без SSL.
# Доступ: http://<IP-сервера>:8080/
#
# Идемпотентен: можно запускать повторно.
#
# Обязательные env-переменные:
#   STENVIK_APP_SECRET  — секрет для подписи сессий (генерируется deploy.py автоматом)
#
# Опциональные:
#   STENVIK_APP_DIR     — директория приложения (по умолчанию ~/stenvik-leads)
#   STENVIK_PORT        — публичный порт (по умолчанию 8080)
#   YANDEX_DISK_TOKEN   — токен Я.Диска (если нужен лидогенератору)

set -euo pipefail

# ==== Параметры ====
APP_SECRET="${STENVIK_APP_SECRET:?STENVIK_APP_SECRET env var required}"
APP_DIR="${STENVIK_APP_DIR:-$HOME/stenvik-leads}"
PORT="${STENVIK_PORT:-8080}"
YANDEX_TOKEN="${YANDEX_DISK_TOKEN:-}"

CURRENT_USER="$(whoami)"
SERVICE_NAME="stenvik-leads"
PUBLIC_IP="$(curl -s https://api.ipify.org 2>/dev/null || curl -s ifconfig.me 2>/dev/null || echo '<unknown>')"

echo "========================================"
echo " Stenvik Leads installer (IP-mode)"
echo "========================================"
echo "  App dir:   $APP_DIR"
echo "  User:      $CURRENT_USER"
echo "  Port:      $PORT"
echo "  Public IP: $PUBLIC_IP"
echo "========================================"
echo

# ==== 1. Системные пакеты ====
echo "[1/6] Проверяю системные пакеты..."
NEEDED_PKGS=()
for pkg in python3-venv python3-pip python3-dev build-essential; do
    if ! dpkg -l "$pkg" 2>/dev/null | grep -q "^ii"; then
        NEEDED_PKGS+=("$pkg")
    fi
done
if [ ${#NEEDED_PKGS[@]} -gt 0 ]; then
    echo "  Устанавливаю: ${NEEDED_PKGS[*]}"
    sudo apt-get update
    sudo apt-get install -y "${NEEDED_PKGS[@]}"
else
    echo "  Все системные пакеты уже стоят."
fi

# ==== 2. Python venv + deps ====
echo "[2/6] Python venv и зависимости..."
cd "$APP_DIR"
if [ ! -d .venv ]; then
    python3 -m venv .venv
    echo "  Создан .venv"
fi
.venv/bin/pip install --quiet --upgrade pip wheel
.venv/bin/pip install --quiet -r requirements.txt
echo "  Python-зависимости обновлены."

# ==== 3. .env ====
echo "[3/6] .env..."
if [ ! -f .env ]; then
    cat > .env <<EOF
# Stenvik Leads — переменные окружения
app_secret=$APP_SECRET
database_url=sqlite:///./data/leads.db
yandex_disk_token=$YANDEX_TOKEN
EOF
    chmod 600 .env
    echo "  .env создан."
else
    if ! grep -q "^app_secret=" .env; then
        echo "app_secret=$APP_SECRET" >> .env
    fi
    echo "  .env уже есть — оставляю."
fi

# ==== 4. Миграция БД ====
echo "[4/6] Миграция БД..."
mkdir -p data
.venv/bin/python -m app.migrate
echo "  БД готова."

# ==== 5. Systemd сервис ====
echo "[5/6] Systemd сервис..."
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=Stenvik Leads — CRM для продажников
After=network.target

[Service]
Type=notify
User=$CURRENT_USER
Group=$CURRENT_USER
WorkingDirectory=$APP_DIR
Environment="PATH=$APP_DIR/.venv/bin"
Environment="STENVIK_BIND=0.0.0.0:$PORT"
Environment="STENVIK_WORKERS=2"
EnvironmentFile=$APP_DIR/.env
ExecStart=$APP_DIR/.venv/bin/gunicorn app.main:app -c $APP_DIR/deploy/gunicorn.conf.py
Restart=always
RestartSec=5
KillSignal=SIGTERM
TimeoutStopSec=30

NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=false
ReadWritePaths=$APP_DIR/data $APP_DIR/.venv
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"
sleep 3
if sudo systemctl is-active --quiet "$SERVICE_NAME"; then
    echo "  ✓ Сервис $SERVICE_NAME запущен."
else
    echo "  ✗ Сервис не стартовал. Логи:"
    sudo journalctl -u "$SERVICE_NAME" --no-pager -n 30
    exit 1
fi

# ==== 6. Firewall (если ufw активен, открываем порт) ====
echo "[6/6] Firewall..."
if command -v ufw > /dev/null 2>&1; then
    if sudo ufw status | grep -q "Status: active"; then
        sudo ufw allow "$PORT/tcp" > /dev/null
        echo "  ✓ ufw: порт $PORT открыт."
    else
        echo "  ufw установлен, но не активен — пропускаю."
    fi
else
    echo "  ufw не установлен — пропускаю."
fi

# ==== Проверка извне (локальный curl) ====
sleep 1
if curl -sf "http://127.0.0.1:$PORT/health" > /dev/null; then
    echo "  ✓ /health отвечает на 127.0.0.1:$PORT"
else
    echo "  ✗ /health не отвечает"
    sudo journalctl -u "$SERVICE_NAME" --no-pager -n 30
    exit 1
fi

echo
echo "========================================"
echo " ✓ УСТАНОВЛЕНО"
echo "========================================"
echo "  Заходи из браузера:"
echo "     http://$PUBLIC_IP:$PORT/"
echo
echo "  Первый зашедший → супер-админ."
echo
echo "  Команды на VPS:"
echo "     sudo systemctl status $SERVICE_NAME"
echo "     sudo journalctl -u $SERVICE_NAME -f"
echo "     sudo systemctl restart $SERVICE_NAME"
echo "========================================"
