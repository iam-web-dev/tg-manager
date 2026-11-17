#!/bin/bash
# Telegram Manager boshqaruv skripti

SERVICE_NAME="tg-manager"

case "$1" in
    start)
        echo "Bot ishga tushirilmoqda..."
        sudo systemctl start $SERVICE_NAME
        sudo systemctl status $SERVICE_NAME --no-pager
        ;;
    stop)
        echo "Bot to'xtatilmoqda..."
        sudo systemctl stop $SERVICE_NAME
        ;;
    restart)
        echo "Bot qayta ishga tushirilmoqda..."
        sudo systemctl restart $SERVICE_NAME
        sudo systemctl status $SERVICE_NAME --no-pager
        ;;
    status)
        sudo systemctl status $SERVICE_NAME --no-pager
        ;;
    logs)
        echo "Jonli loglar (chiqish uchun Ctrl+C bosing):"
        sudo journalctl -u $SERVICE_NAME -f -n 50
        ;;
    enable)
        echo "Avtomatik ishga tushish (autorun) yoqilmoqda..."
        sudo systemctl enable $SERVICE_NAME
        ;;
    disable)
        echo "Avtomatik ishga tushish o'chirilmoqda..."
        sudo systemctl disable $SERVICE_NAME
        ;;
    *)
        echo "Foydalanish: ./manage.sh {start|stop|restart|status|logs|enable|disable}"
        echo ""
        echo "Buyruqlar tavsifi:"
        echo "  start   - Botni fonda ishga tushirish"
        echo "  stop    - Botni to'xtatish"
        echo "  restart - Botni qayta ishga tushirish"
        echo "  status  - Bot holatini tekshirish"
        echo "  logs    - Bot loglarini real vaqtda ko'rish"
        echo "  enable  - Server yoqilganda avtomatik ishga tushishini yoqish"
        echo "  disable - Avtomatik ishga tushishini o'chirish"
        exit 1
        ;;
esac
