import asyncio
import logging
import signal
import sys
from database import init_db, engine
from collector import start_collector
from bot import start_bot
from scheduler import start_scheduler
from config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

async def main():
    logger.info("=========================================")
    logger.info("Telegram Manager & AI Analyzer ishga tushmoqda")
    logger.info("=========================================")

    # Ma'lumotlar bazasini initsializatsiya qilish
    logger.info("Ma'lumotlar bazasi tayyorlanmoqda...")
    await init_db()
    logger.info("Ma'lumotlar bazasi tayyor!")

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _signal_handler():
        logger.info("To'xtatish signali (SIGINT/SIGTERM) qabul qilindi...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except (NotImplementedError, RuntimeError):
            pass

    tasks = []

    # Telethon Collector ni ishga tushirish (Agar API_ID va API_HASH kiritilgan bo'lsa)
    if settings.API_ID and settings.API_HASH:
        tasks.append(asyncio.create_task(start_collector(), name="telethon_collector"))
        tasks.append(asyncio.create_task(start_scheduler(), name="message_scheduler"))
    else:
        logger.warning("API_ID va API_HASH sozlanmagan. Telethon va Scheduler modullari ishga tushirilmadi!")

    # Aiogram Bot ni ishga tushirish (Agar BOT_TOKEN kiritilgan bo'lsa)
    if settings.BOT_TOKEN:
        tasks.append(asyncio.create_task(start_bot(), name="aiogram_bot"))
    else:
        logger.warning("BOT_TOKEN sozlanmagan. Boshqaruvchi Bot interfeysi ishga tushirilmadi!")

    if not tasks:
        logger.error("Hech qaysi modul ishga tushmadi! .env faylni tekshiring.")
        return

    stop_task = asyncio.create_task(stop_event.wait(), name="stop_listener")
    all_tasks = tasks + [stop_task]

    try:
        # Birorta task tugasa (masalan bot to'xtasa yoki stop signali kelsa), barchasini to'xtatamiz
        done, pending = await asyncio.wait(all_tasks, return_when=asyncio.FIRST_COMPLETED)
        logger.info("Barcha xizmatlarni to'xtatish boshlanmoqda...")
    except asyncio.CancelledError:
        logger.info("Asosiy tasklar bekor qilindi.")
    except Exception as e:
        logger.exception(f"Kutilmagan xatolik yuz berdi: {e}")
    finally:
        for t in all_tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*all_tasks, return_exceptions=True)
        await engine.dispose()
        logger.info("Ma'lumotlar bazasi va barcha xizmatlar toza yopildi.")

if __name__ == "__main__":
    try:
        # Windowsda xatoliklarni oldini olish uchun EventLoopPolicy
        if sys.platform == 'win32':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Tizim to'xtatildi.")

