#!/usr/bin/env bash
# Запускать НА СЕРВЕРЕ из ~/bots/qarzdorlik после копирования файлов:
#   bash deploy/install_on_server.sh
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || { echo "Нет .env (см. .env.example)"; exit 1; }
chmod 600 .env
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --no-cache-dir -r requirements.txt
sudo cp deploy/qarzdorlik-bot.service /etc/systemd/system/qarzdorlik-bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now qarzdorlik-bot
sleep 5
systemctl is-active qarzdorlik-bot
journalctl -u qarzdorlik-bot -n 15 --no-pager
