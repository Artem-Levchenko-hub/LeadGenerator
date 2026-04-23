#!/usr/bin/env bash
# update.sh — обновление уже установленного Stenvik Leads на VPS.
# Обновляет код (rsync сделал до нас), ставит новые зависимости, мигрирует БД,
# рестартит сервис.

set -euo pipefail

APP_DIR="${STENVIK_APP_DIR:-$HOME/stenvik-leads}"
SERVICE_NAME="stenvik-leads"

cd "$APP_DIR"

echo "[1/3] Обновляю Python-зависимости..."
.venv/bin/pip install --quiet --upgrade pip wheel
.venv/bin/pip install --quiet -r requirements.txt

echo "[2/3] Миграция БД..."
.venv/bin/python -m app.migrate

echo "[3/3] Рестарт сервиса..."
sudo systemctl restart "$SERVICE_NAME"
sleep 2
if sudo systemctl is-active --quiet "$SERVICE_NAME"; then
    echo "  ✓ $SERVICE_NAME запущен."
else
    echo "  ✗ Сервис не стартовал. Логи:"
    sudo journalctl -u "$SERVICE_NAME" --no-pager -n 30
    exit 1
fi

PORT="${STENVIK_PORT:-8080}"
curl -sf "http://127.0.0.1:$PORT/health" > /dev/null && echo "  ✓ /health OK" || {
    echo "  ✗ Health не прошёл"
    exit 1
}

echo "[ok] Обновлено."
