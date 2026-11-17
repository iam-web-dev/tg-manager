import asyncio
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Union

from config import settings
from database import (
    get_all_scheduled_messages, 
    mark_schedule_sent, 
    ScheduledMessage
)

logger = logging.getLogger(__name__)

DAY_NAMES_UZ = {
    0: "Dushanba",
    1: "Seshanba",
    2: "Chorshanba",
    3: "Payshanba",
    4: "Juma",
    5: "Shanba",
    6: "Yakshanba"
}

DAY_SHORT_UZ = {
    0: "Dush",
    1: "Sesh",
    2: "Chor",
    3: "Pay",
    4: "Jum",
    5: "Shan",
    6: "Yak"
}

DAYS_MAP = {
    "mon": 0, "monday": 0, "dush": 0, "dushanba": 0,
    "tue": 1, "tues": 1, "tuesday": 1, "sesh": 1, "seshanba": 1,
    "wed": 2, "wednesday": 2, "chor": 2, "chorshanba": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3, "pay": 3, "payshanba": 3,
    "fri": 4, "friday": 4, "jum": 4, "juma": 4,
    "sat": 5, "saturday": 5, "shan": 5, "shanba": 5,
    "sun": 6, "sunday": 6, "yak": 6, "yakshanba": 6
}

def normalize_days(days_input: Union[List[str], str]) -> str:
    """
    Kunlar ro'yxati yoki matnini raqamli ko'rinishga keltirish ('0,1,2,3,4,5').
    0 - Dushanba, 6 - Yakshanba.
    """
    if isinstance(days_input, list):
        parsed = set()
        for item in days_input:
            item_str = str(item).strip().lower()
            if item_str.isdigit() and 0 <= int(item_str) <= 6:
                parsed.add(int(item_str))
            elif item_str in DAYS_MAP:
                parsed.add(DAYS_MAP[item_str])
        if parsed:
            return ",".join(str(d) for d in sorted(parsed))
        return "0,1,2,3,4,5,6"

    text = str(days_input).strip().lower()

    if any(phrase in text for phrase in ["har kuni", "har kun", "harkuni", "harkun", "every day", "daily", "barcha kun"]):
        return "0,1,2,3,4,5,6"
    if any(phrase in text for phrase in ["ish kun", "ishkun", "weekdays"]):
        return "0,1,2,3,4"
    if any(phrase in text for phrase in ["dam olish", "damolish", "weekend"]):
        return "5,6"

    # Oraliq: dushanbadan shanbagacha, dush-shan, mon-sat
    if any(phrase in text for phrase in [
        "dushanbadan shanbagacha", "dushanba-shanba", "dush-shan", "mon-sat", "monday to saturday"
    ]):
        return "0,1,2,3,4,5"
    if any(phrase in text for phrase in [
        "dushanbadan jumagacha", "dushanba-juma", "dush-jum", "mon-fri", "monday to friday"
    ]):
        return "0,1,2,3,4"
    if any(phrase in text for phrase in [
        "dushanbadan yakshanbagacha", "dushanba-yakshanba", "mon-sun"
    ]):
        return "0,1,2,3,4,5,6"

    # Vergul yoki bo'sh joy orqali ajratilgan kunlar
    items = re.split(r'[,;\s]+', text)
    parsed = set()
    for item in items:
        clean = item.strip()
        if clean.isdigit() and 0 <= int(clean) <= 6:
            parsed.add(int(clean))
        elif clean in DAYS_MAP:
            parsed.add(DAYS_MAP[clean])
        else:
            for k, v in DAYS_MAP.items():
                if k in clean:
                    parsed.add(v)
                    break

    if parsed:
        return ",".join(str(d) for d in sorted(parsed))
    return "0,1,2,3,4,5,6"


def format_days_human(days_str: str) -> str:
    """Raqamli kunlar satrini ('0,1,2,3,4,5') o'zbek tilidagi chiroyli nomlarga aylantirish."""
    try:
        days = [int(d) for d in days_str.split(",") if d.strip().isdigit()]
    except Exception:
        return days_str

    if not days:
        return "Kunlar belgilanmagan"

    days_set = set(days)
    if days_set == {0, 1, 2, 3, 4, 5, 6}:
        return "Har kuni (Dushanba - Yakshanba)"
    if days_set == {0, 1, 2, 3, 4, 5}:
        return "Dushanba - Shanba"
    if days_set == {0, 1, 2, 3, 4}:
        return "Ish kunlari (Dushanba - Juma)"
    if days_set == {5, 6}:
        return "Dam olish kunlari (Shanba - Yakshanba)"

    return ", ".join(DAY_NAMES_UZ.get(d, str(d)) for d in sorted(days_set))


def normalize_time(time_str: str) -> str:
    """Vaqtni HH:MM (24-soat) formatiga keltirish."""
    clean = time_str.strip().lower()
    clean = clean.replace(".", ":")
    
    # 17:00 yoki 9:30
    match = re.search(r'(\d{1,2}):(\d{2})', clean)
    if match:
        h = int(match.group(1))
        m = int(match.group(2))
        return f"{h:02d}:{m:02d}"

    # Faqat soat raqami (masalan 17 yoki 9)
    match_h = re.search(r'\b(\d{1,2})\b', clean)
    if match_h:
        h = int(match_h.group(1))
        if 0 <= h <= 23:
            return f"{h:02d}:00"

    return "09:00"


async def start_scheduler():
    """
    Rejalashtirilgan xabarlarni doimiy fonda kuzatib, vaqti kelganda
    Telethon (Userbot) orqali yuboruvchi asinxron xizmat.
    """
    logger.info("Message Scheduler (Avtomatik xabarlar xizmati) ishga tushdi...")
    
    while True:
        try:
            # Har 20 soniyada bir marta tekshiramiz
            await asyncio.sleep(20)

            # O'zbekiston / Toshkent vaqti (UTC+5)
            # Standart tizim vaqti bo'yicha hisoblash
            now_utc = datetime.now(timezone.utc)
            
            schedules = await get_all_scheduled_messages(only_active=True)
            if not schedules:
                continue

            for sched in schedules:
                offset = sched.timezone_offset if sched.timezone_offset is not None else 5
                now_local = now_utc + timedelta(hours=offset)
                
                current_weekday = now_local.weekday() # 0..6
                current_time_str = now_local.strftime("%H:%M")
                today_date_str = now_local.strftime("%Y-%m-%d")

                # Bugun allaqachon yuborilgan bo'lsa o'tkazib yuboramiz
                if sched.last_sent_date == today_date_str:
                    continue

                try:
                    sched_days = [int(d) for d in sched.days_of_week.split(",") if d.strip().isdigit()]
                except Exception:
                    sched_days = list(range(7))

                # Vaqt va kun mos kelsa jo'natamiz
                if current_weekday in sched_days and current_time_str == sched.send_time:
                    logger.info(
                        f"⏰ Rejalashtirilgan xabar vaqti keldi! "
                        f"Chat: {sched.chat_title or sched.chat_id}, Vaqt: {sched.send_time}"
                    )

                    from collector import send_user_message
                    success = await send_user_message(
                        chat_id=sched.chat_id, 
                        text=sched.message_text
                    )

                    # Oxirgi yuborilgan sanani yangilaymiz (takrorlanmasligi uchun)
                    await mark_schedule_sent(sched.id, today_date_str)

                    # Adminga bot orqali bildirishnoma yuborish
                    from bot import bot
                    if settings.ADMIN_ID and bot:
                        try:
                            status_icon = "✅" if success else "❌ (Yuborishda xatolik)"
                            notify_text = (
                                f"⏰ <b>Avtomatik rejalashtirilgan xabar {status_icon}</b>\n\n"
                                f"📍 <b>Chat:</b> {sched.chat_title or 'Chat'} (<code>{sched.chat_id}</code>)\n"
                                f"🕒 <b>Vaqt:</b> {sched.send_time} ({format_days_human(sched.days_of_week)})\n\n"
                                f"📝 <b>Yuborilgan xabar:</b>\n"
                                f"<i>{sched.message_text}</i>"
                            )
                            await bot.send_message(settings.ADMIN_ID, notify_text, parse_mode="HTML")
                        except Exception as be:
                            logger.error(f"Scheduler admin bildirishnomasida xatolik: {be}")

        except asyncio.CancelledError:
            logger.info("Message Scheduler to'xtatildi (cancelled).")
            break
        except Exception as e:
            logger.error(f"Message Scheduler ishlashida xatolik: {e}", exc_info=True)
            await asyncio.sleep(10)
