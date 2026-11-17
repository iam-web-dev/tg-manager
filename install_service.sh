#!/bin/bash
set -e

SERVICE_FILE="tg-manager.service"
TARGET_DIR="/etc/systemd/system"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "========================================="
echo "   Telegram Manager - Systemd O'rnatish"
echo "========================================="

if [ ! -f "$PROJECT_DIR/$SERVICE_FILE" ]; then
    echo "Xatolik: $SERVICE_FILE topilmadi!"
    exit 1
fi

echo "1. Service fayli $TARGET_DIR ga nusxalanmoqda..."
sudo cp "$PROJECT_DIR/$SERVICE_FILE" "$TARGET_DIR/$SERVICE_FILE"

echo "2. Systemd qayta yuklanmoqda..."
sudo systemctl daemon-reload

echo "3. Xizmatni avtomatik ishga tushishga qo'shish (autorun on boot)..."
sudo systemctl enable tg-manager.service

echo "4. Xizmatni hoziroq ishga tushirish..."
sudo systemctl restart tg-manager.service

echo "========================================="
echo "Muvaffaqiyatli o'rnatildi va ishga tushirildi!"
echo "Holat:"
sudo systemctl status tg-manager.service --no-pager
