import logging
import json
import os
import re
from typing import Dict, Any, Optional, List
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.enums import ParseMode

from config import settings
from database import (
    get_recent_messages, 
    get_tracked_chats, 
    add_tracked_chat, 
    remove_tracked_chat,
    get_chat_stats,
    get_messages_json,
    add_chat_rule,
    get_all_rules,
    get_rule_by_id,
    update_chat_rule,
    delete_chat_rule,
    is_silence_rule,
    find_chat_by_name,
    find_chat_id_by_any_means,
    add_scheduled_message,
    get_all_scheduled_messages,
    get_scheduled_message_by_id,
    toggle_scheduled_message,
    delete_scheduled_message
)
from scheduler import normalize_days, normalize_time, format_days_human
from ai_service import (
    generate_summary, 
    extract_tasks, 
    answer_question, 
    analyze_sentiment,
    chat_with_manager
)

logger = logging.getLogger(__name__)

bot = Bot(token=settings.BOT_TOKEN)
dp = Dispatcher()

# --- Xabar konteksti boshqaruvi (Reply qilinganda ma'lumotlarni eslab qolish) ---
_message_contexts: Dict[int, Dict[str, Any]] = {}
# --- Tasdiqlash kutayotgan chat xabarlari (Drafts) ---
_draft_messages: Dict[str, Dict[str, Any]] = {}

def register_notification_context(
    message_id: int, 
    chat_id: Optional[int] = None, 
    sender_name: str = "", 
    task_details: str = ""
):
    """Yuborilgan xabar kontekstini keshda saqlash (reply qilinganda aniq javob berish uchun)."""
    _message_contexts[message_id] = {
        "chat_id": chat_id,
        "sender_name": sender_name,
        "task_details": task_details
    }
    # Kesh hajmini me'yorda ushlab turish
    if len(_message_contexts) > 300:
        first_key = next(iter(_message_contexts))
        del _message_contexts[first_key]

def split_text_chunks(text: str, max_chunk_size: int = 3500) -> List[str]:
    """Uzun matnlarni Telegram xabar hajmiga mos xavfsiz qismlarga bo'lish."""
    if not text:
        return [""]
    if len(text) <= max_chunk_size:
        return [text]
    
    chunks = []
    lines = text.split("\n")
    current_chunk = []
    current_len = 0
    
    for line in lines:
        # Agar bitta qatorning o'zi max_chunk_size dan katta bo'lsa, uni bo'lib olamiz
        while len(line) > max_chunk_size:
            part = line[:max_chunk_size]
            line = line[max_chunk_size:]
            if current_chunk:
                chunks.append("\n".join(current_chunk))
                current_chunk = []
                current_len = 0
            chunks.append(part)

        line_len = len(line) + 1
        if current_len + line_len > max_chunk_size and current_chunk:
            chunks.append("\n".join(current_chunk))
            current_chunk = [line]
            current_len = line_len
        else:
            current_chunk.append(line)
            current_len += line_len
            
    if current_chunk:
        chunks.append("\n".join(current_chunk))
        
    return chunks

# --- Middleware (Faqat admin uchun himoya) ---
@dp.message.outer_middleware()
async def admin_middleware(handler, event, data):
    if settings.ADMIN_ID and event.from_user.id != settings.ADMIN_ID:
        await event.answer("Siz ushbu botdan foydalanish huquqiga ega emassiz.")
        return
    return await handler(event, data)


# --- Commands ---

@dp.message(CommandStart())
async def cmd_start(message: Message):
    text = (
        f"Wassup, {message.from_user.first_name}!\n\n"
        "Quyidagi buyruqlar yordamida ishlashingiz mumkin:\n\n"
        "📋 /chats - Kuzatilayotgan chatlarni ko'rish\n"
        "➕ /track <chat_id> - Chatni kuzatuvga qo'shish\n"
        "➖ /untrack <chat_id> - Chatni kuzatuvdan olib tashlash\n"
        "📊 /stats <chat_id> - Chat statistikasi\n"
        "📥 /export <chat_id> [kunlar] - Xabarlarni JSON shaklida yuklab olish\n\n"
        "⚡️ Qoidalar va Avto-javoblar:\n"
        "📜 /rules - Barcha faol qoidalarni ko'rish\n"
        "🗑 /delrule <id> - Qoidani o'chirish\n\n"
        "⏰ Rejalashtirilgan xabarlar (Scheduler):\n"
        "🗓 /schedules - Barcha rejalashtirilgan xabarlar ro'yxati\n"
        "❌ /delschedule <id> - Rejalashtirilgan xabarni o'chirish\n\n"
        "🤖 AI buyruqlari:\n"
        "📝 /summary <chat_id> - Xabarlar xulosasi (Summary)\n"
        "🎭 /sentiment <chat_id> - Chatdagi muhit va kayfiyat tahlili\n"
        "✅ /tasks <chat_id> - Topshiriqlarni ajratish\n"
        "❓ /ask <chat_id> <savol> - Chat bo'yicha savol berish\n\n"
        "💬 Shaxsiy AI Menejer:\n"
        "- 'ABS guruhiga \"bugun keldim\" degan xabarni dushanbadan shanbagacha 17:00 da avtomatik jo'natiladigan qil' desangiz, schedule o'rnatiladi!\n"
        "- 'Falonchi yozsa shunday deb javob ber' desangiz, qoida o'rnatiladi.\n"
        "- 'Guruhga taklif qil' yoki 'Unga yoz' desangiz, chatga xabar yuborish tayyorlanadi.\n"
        "- Eslatmalarga reply qilib darslar, tushuntirishlar so'rashingiz mumkin!"
    )
    await message.answer(text)

@dp.message(Command("schedules"))
async def cmd_schedules(message: Message):
    schedules = await get_all_scheduled_messages()
    if not schedules:
        await message.answer(
            "ℹ️ Hozircha rejalashtirilgan avtomatik xabarlar (schedules) yo'q.\n\n"
            "Yangi jadval yaratish uchun AI Menejerga yozishingiz mumkin, masalan:\n"
            "<i>'ABS guruhiga \"bugun keldim\" degan message ni dushanbadan shanbagacha 17:00 da avtomatik jo'natiladigan qil'</i>",
            parse_mode=ParseMode.HTML
        )
        return

    text = f"⏰ <b>Rejalashtirilgan xabarlar ({len(schedules)} ta):</b>\n\n"
    for s in schedules:
        status = "🟢 Faol" if s.is_active else "⏸ To'xtatilgan"
        h_days = format_days_human(s.days_of_week)
        text += (
            f"🔹 <b>ID:</b> {s.id}\n"
            f"📍 <b>Qayerga:</b> {s.chat_title or s.chat_id} (<code>{s.chat_id}</code>)\n"
            f"📅 <b>Kunlar:</b> {h_days}\n"
            f"🕒 <b>Vaqt:</b> {s.send_time} (Toshkent)\n"
            f"📝 <b>Matn:</b> <i>{s.message_text}</i>\n"
            f"📊 <b>Holati:</b> {status}\n"
            f"❌ <i>O'chirish:</i> /delschedule {s.id}\n\n"
        )
    await message.answer(text, parse_mode=ParseMode.HTML)

@dp.message(Command("delschedule"))
async def cmd_delschedule(message: Message):
    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        await message.answer("Xato! Format: /delschedule <id>\nMasalan: /delschedule 1")
        return

    sched_id = int(args[1])
    res = await delete_scheduled_message(sched_id)
    if res:
        await message.answer(f"✅ {sched_id}-raqamli rejalashtirilgan xabar muvaffaqiyatli o'chirildi.")
    else:
        await message.answer(f"❌ {sched_id}-raqamli jadval topilmadi.")

@dp.message(Command("rules"))
async def cmd_rules(message: Message):
    rules = await get_all_rules(only_active=True)
    if not rules:
        await message.answer("ℹ️ Hozircha birorta ham faol maxsus qoida (auto-reply) yo'q.\n\nSiz AI Menejerga: 'Falonchi yozsa shunday deb ayt' deb yozsangiz, u qoida bo'lib saqlanadi.")
        return

    text = f"📋 <b>Faol maxsus qoidalar ({len(rules)} ta):</b>\n\n"
    for r in rules:
        target = r.sender_pattern or r.chat_title or f"Chat {r.chat_id}"
        mode = "Bir martalik" if r.is_one_time else "Doimiy"
        if is_silence_rule(r):
            rep_disp = "🔇 Sukut (Xabar yozilmaydi)"
        else:
            rep_disp = r.exact_reply or 'AI orqali mos javob'
        text += (
            f"🔹 <b>ID:</b> {r.id}\n"
            f"👤 <b>Kimga/Qayerga:</b> {target}\n"
            f"📌 <b>Topshiriq:</b> {r.instruction}\n"
            f"💬 <b>Javob:</b> {rep_disp}\n"
            f"🔄 <b>Holat:</b> {mode}\n"
            f"❌ <i>O'chirish:</i> /delrule {r.id}\n\n"
        )
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("delrule"))
async def cmd_delrule(message: Message):
    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        await message.answer("Xato! Format: /delrule <rule_id>\nMasalan: /delrule 1")
        return

    rule_id = int(args[1])
    res = await delete_chat_rule(rule_id)
    if res:
        await message.answer(f"✅ {rule_id}-raqamli qoida muvaffaqiyatli o'chirildi.")
    else:
        await message.answer(f"❌ {rule_id}-raqamli qoida topilmadi.")

@dp.message(Command("chats"))
async def cmd_chats(message: Message):
    chats = await get_tracked_chats()
    if not chats:
        await message.answer("Hozircha birorta ham chat kuzatilmayapti.")
        return
    
    lines = []
    for c in chats:
        display = c.title or 'Nomsiz'
        if c.username:
            display += f" (@{c.username})"
        lines.append(f"- {display} (ID: {c.chat_id})")
    
    # Telegram xabar limiti 4096 belgi. 
    # Xavfsiz va chiroyli chiqishi uchun qatorlar bo'yicha 3500 belgidan bo'lib chiqamiz.
    chunks = []
    current_chunk = []
    current_length = 0
    max_chunk_size = 3500

    for line in lines:
        line_length = len(line) + 1  # Yangi qator (\n) hisobga olingan
        if current_length + line_length > max_chunk_size and current_chunk:
            chunks.append("\n".join(current_chunk))
            current_chunk = [line]
            current_length = line_length
        else:
            current_chunk.append(line)
            current_length += line_length

    if current_chunk:
        chunks.append("\n".join(current_chunk))

    total_chunks = len(chunks)
    for idx, chunk in enumerate(chunks, start=1):
        if total_chunks > 1:
            header = f"🔍 Kuzatilayotgan chatlar (Jami: {len(chats)} ta) [{idx}/{total_chunks}]:\n\n"
        else:
            header = f"🔍 Kuzatilayotgan chatlar (Jami: {len(chats)} ta):\n\n"
        
        await message.answer(header + chunk)

@dp.message(Command("track"))
async def cmd_track(message: Message):
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Xato! To'g'ri format: /track <chat_id> [chat_nomi]")
        return
    
    try:
        chat_id = int(args[1])
        title = " ".join(args[2:]) if len(args) > 2 else str(chat_id)
        await add_tracked_chat(chat_id, title)
        await message.answer(f"✅ {title} ({chat_id}) kuzatuvga qayta qo'shildi!")
    except ValueError:
        await message.answer("Chat ID raqam bo'lishi kerak!")

@dp.message(Command("untrack"))
async def cmd_untrack(message: Message):
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Xato! To'g'ri format: /untrack <chat_id>")
        return
    
    try:
        chat_id = int(args[1])
        res = await remove_tracked_chat(chat_id)
        if res:
            await message.answer(f"❌ {chat_id} chatidan xabar yig'ish to'xtatildi!")
        else:
            # Agar bazada hali yo'q bo'lsa
            await message.answer("Bu chat topilmadi yoki allaqachon o'chirilgan.")
    except ValueError:
        await message.answer("Chat ID raqam bo'lishi kerak!")

@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Xato! To'g'ri format: /stats <chat_id>")
        return
    
    try:
        chat_id = int(args[1])
        stats = await get_chat_stats(chat_id)
        text = (
            f"📊 Chat ({chat_id}) statistikasi:\n"
            f"Jami saqlangan xabarlar: {stats['total_messages']}\n"
            f"Tahrirlangan xabarlar: {stats['edited_messages']}\n"
            f"O'chirilgan xabarlar: {stats['deleted_messages']}\n"
            f"Birinchi xabar: {stats['first_date']}\n"
            f"Oxirgi xabar: {stats['last_date']}"
        )
        await message.answer(text)
    except Exception as e:
        await message.answer(f"Xatolik yuz berdi: {e}")

@dp.message(Command("export"))
async def cmd_export(message: Message):
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Xato! Format: /export <chat_id> [kun_yoki_sana]\nMasalan: /export -100123 5\nYoki: /export -100123 01.12.2025 05.12.2025\nYoki bitta kun: /export -100123 01.01.2026")
        return
    
    try:
        chat_id = int(args[1])
        start_date = None
        end_date = None
        
        from datetime import datetime, timezone, timedelta
        import re
        date_pattern = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")
        
        if len(args) == 3:
            if date_pattern.match(args[2]):
                start_date = datetime.strptime(args[2], "%d.%m.%Y").replace(tzinfo=timezone.utc)
                end_date = start_date + timedelta(days=1)
            elif args[2].isdigit():
                days = int(args[2])
                start_date = datetime.now(timezone.utc) - timedelta(days=days)
        elif len(args) >= 4:
            if date_pattern.match(args[2]) and date_pattern.match(args[3]):
                start_date = datetime.strptime(args[2], "%d.%m.%Y").replace(tzinfo=timezone.utc)
                end_date = datetime.strptime(args[3], "%d.%m.%Y").replace(tzinfo=timezone.utc) + timedelta(days=1)
        
        wait_msg = await message.answer("JSON fayl tayyorlanmoqda... ⏳")
        
        data = await get_messages_json(chat_id, start_date, end_date)
        if not data:
            await wait_msg.edit_text("Ushbu muddatda hech qanday xabar topilmadi.")
            return
            
        # Fayl nomini generatsiya qilish
        dt_str = "filtered"
        if start_date: dt_str = start_date.strftime("%Y-%m-%d")
        if end_date: dt_str += f"_to_{end_date.strftime('%Y-%m-%d')}"
            
        filename = f"export_{chat_id}_{dt_str}.json"
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            
        document = FSInputFile(filename)
        await message.answer_document(document, caption=f"📁 {chat_id} chatidan {len(data)} ta xabar tarixi (JSON).")
        await wait_msg.delete()
        
        os.remove(filename)
        
    except ValueError:
        await message.answer("Sana formati yoki Chat ID xato kiritildi!")
    except Exception as e:
        logger.error(f"Eksportda xatolik: {e}")
        await message.answer(f"Xatolik: {e}")

# --- AI Integration Commands ---

@dp.message(Command("summary"))
async def cmd_summary(message: Message):
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Xato! To'g'ri format: /summary <chat_id>")
        return
    
    try:
        chat_id = int(args[1])
        wait_msg = await message.answer("Kuting, xabarlar yig'ilyapti va AI ga yuborilmoqda... ⏳")
        
        messages = await get_recent_messages(chat_id, limit=200)
        
        if not messages:
            await wait_msg.edit_text("Bu chatdan xabarlar topilmadi.")
            return

        chat_title = messages[0].chat_title or str(chat_id)
        result = await generate_summary(chat_title, messages)
        
        await wait_msg.edit_text(result)
        
    except ValueError:
        await message.answer("Chat ID raqam bo'lishi kerak!")
    except Exception as e:
        logger.error(e)
        await message.answer(f"Xatolik: {e}")

@dp.message(Command("tasks"))
async def cmd_tasks(message: Message):
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Xato! To'g'ri format: /tasks <chat_id>")
        return
    
    try:
        chat_id = int(args[1])
        wait_msg = await message.answer("Topshiriqlar axtarilmoqda... ⏳")
        
        messages = await get_recent_messages(chat_id, limit=200)
        if not messages:
            await wait_msg.edit_text("Bu chatdan xabarlar topilmadi.")
            return

        chat_title = messages[0].chat_title or str(chat_id)
        result = await extract_tasks(chat_title, messages)
        
        await wait_msg.edit_text(result)
    except ValueError:
        await message.answer("Chat ID raqam bo'lishi kerak!")

@dp.message(Command("ask"))
async def cmd_ask(message: Message):
    args = message.text.split()
    if len(args) < 3:
        await message.answer("Xato! To'g'ri format: /ask <chat_id> <savolingiz>")
        return
    
    try:
        chat_id = int(args[1])
        question = " ".join(args[2:])
        
        wait_msg = await message.answer("AI o'ylamoqda... ⏳")
        
        messages = await get_recent_messages(chat_id, limit=300)
        if not messages:
            await wait_msg.edit_text("Bu chatdan xabarlar topilmadi.")
            return

        chat_title = messages[0].chat_title or str(chat_id)
        result = await answer_question(chat_title, messages, question)
        
        await wait_msg.edit_text(result)
    except ValueError:
        await message.answer("Chat ID raqam bo'lishi kerak!")

@dp.message(Command("sentiment"))
async def cmd_sentiment(message: Message):
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Xato! To'g'ri format: /sentiment <chat_id>")
        return
    
    try:
        chat_id = int(args[1])
        wait_msg = await message.answer("Muhit va hissiyotlar tahlil qilinmoqda... ⏳")
        
        messages = await get_recent_messages(chat_id, limit=200)
        
        if not messages:
            await wait_msg.edit_text("Bu chatdan xabarlar topilmadi.")
            return

        chat_title = messages[0].chat_title or str(chat_id)
        result = await analyze_sentiment(chat_title, messages)
        
        await wait_msg.edit_text(result)
        
    except ValueError:
        await message.answer("Chat ID raqam bo'lishi kerak!")
    except Exception as e:
        logger.error(e)
        await message.answer(f"Xatolik yuz berdi: {e}")

# --- Shaxsiy AI Menejer Muloqoti (Reply va Umumiy xabarlar) ---

@dp.message(F.text & ~F.text.startswith("/"))
async def handle_manager_chat(message: Message):
    """
    Foydalanuvchi botga yozganida yoki bot yuborgan eslatma/topshiriq
    xabariga reply qilganda ishlaydigan aqlli AI menejer handler.
    """
    user_query = (message.text or "").strip()
    if not user_query:
        return

    replied_text = ""
    context_chat_id = None
    context_chat_title = None
    
    # 1. Reply qilingan xabarni tahlil qilish
    context_rule_id = None
    if message.reply_to_message:
        replied_msg = message.reply_to_message
        replied_text = replied_msg.text or replied_msg.caption or ""
        
        # In-memory keshdan qidirish
        cached_info = _message_contexts.get(replied_msg.message_id)
        if cached_info:
            context_chat_id = cached_info.get("chat_id")
            context_chat_title = cached_info.get("sender_name")
            if not replied_text:
                replied_text = cached_info.get("task_details") or ""
            
        # Agar keshda bo'lmasa, reply matnidan Chat ID va Foydalanuvchini aniqlash
        if not context_chat_id and replied_text:
            chat_id_match = re.search(r"Chat:\s*(-?\d+)", replied_text)
            if chat_id_match:
                try:
                    context_chat_id = int(chat_id_match.group(1))
                except ValueError:
                    pass
                    
        if not context_chat_title and replied_text:
            user_match = re.search(r"Foydalanuvchi:\s*([^\n]+)", replied_text)
            if user_match:
                context_chat_title = user_match.group(1).strip()

        # Reply xabarida Qoida ID si bormi? (ID: 3)
        if replied_text:
            rule_id_match = re.search(r"ID:\s*(\d+)", replied_text)
            if rule_id_match:
                try:
                    context_rule_id = int(rule_id_match.group(1))
                except ValueError:
                    pass

    # Foydalanuvchi matnida qoida ID si tilga olinganmi? (masalan: 3-qoida, id 3, 3 ni)
    candidate_rule_id = None
    id_match = re.search(r'\b(?:id\s*[:#]?\s*|#)?(\d+)\s*[-_ ]*(?:qoida|rule|id|ni|dagi)?\b', user_query, re.IGNORECASE)
    if id_match:
        try:
            candidate_rule_id = int(id_match.group(1))
        except ValueError:
            pass

    # 2. Agar chat_id topilsa, o'sha chatdagi so'nggi xabarlarni bazadan olish
    chat_history = None
    if context_chat_id:
        try:
            chat_history = await get_recent_messages(context_chat_id, limit=20)
            if chat_history and not context_chat_title:
                context_chat_title = chat_history[0].chat_title
        except Exception as e:
            logger.warning(f"Chat tarixini olishda xatolik: {e}")

    # Barcha mavjud faol qoidalarni olish (AI konteksti uchun)
    active_rules = []
    try:
        active_rules = await get_all_rules(only_active=True)
    except Exception as e:
        logger.warning(f"Faol qoidalarni olishda xatolik: {e}")

    # 3. Foydalanuvchiga vaqtinchalik javob
    wait_msg = await message.reply("AI o'ylamoqda... ⏳")

    try:
        ai_data = await chat_with_manager(
            user_message=message.text,
            replied_context=replied_text if replied_text else None,
            chat_history=chat_history,
            chat_title=context_chat_title,
            context_chat_id=context_chat_id,
            active_rules=active_rules,
            context_rule_id=context_rule_id or candidate_rule_id
        )

        # Agar ai_data string yoki raw JSON bo'lib qolsa, xavfsiz JSON parse qilish
        if isinstance(ai_data, str):
            try:
                clean_s = ai_data.strip()
                if "```json" in clean_s:
                    clean_s = clean_s.split("```json")[1].split("```")[0].strip()
                elif "```" in clean_s:
                    clean_s = clean_s.split("```")[1].strip()
                match = re.search(r'\{.*\}', clean_s, re.DOTALL)
                if match:
                    clean_s = match.group(0)
                parsed = json.loads(clean_s)
                if isinstance(parsed, dict):
                    ai_data = parsed
            except Exception:
                ai_data = {"action": "NONE", "reply_to_user": ai_data}

        action = ai_data.get("action", "NONE")
        reply_to_user = ai_data.get("reply_to_user", "")

        # --- ACTION: DELETE_RULE (Qoidani bekor qilish / o'chirish) ---
        if action == "DELETE_RULE":
            target_rule_id = ai_data.get("rule_id") or context_rule_id or candidate_rule_id
            if target_rule_id:
                try:
                    target_rule_id = int(target_rule_id)
                    deleted = await delete_chat_rule(target_rule_id)
                    if deleted:
                        del_text = (
                            f"🗑 <b>{target_rule_id}-raqamli qoida bekor qilindi va o'chirildi!</b>\n\n"
                            f"🫡 <i>{reply_to_user}</i>"
                        )
                        await wait_msg.edit_text(del_text, parse_mode=ParseMode.HTML)
                        return
                    else:
                        await wait_msg.edit_text(f"❌ {target_rule_id}-raqamli qoida bazadan topilmadi.")
                        return
                except Exception as de:
                    logger.error(f"Qoidani o'chirishda xatolik: {de}")
            await wait_msg.edit_text("⚠️ Qaysi qoidani o'chirish kerakligini aniqlay olmadim. Iltimos, /rules orqali qoida ID sini tekshirib ko'ring.")
            return

        # --- ACTION: UPDATE_RULE (Mavjud qoidani tahrirlash / yangilash) ---
        if action == "UPDATE_RULE":
            rule_info = ai_data.get("rule_details", {})
            target_rule_id = ai_data.get("rule_id") or rule_info.get("rule_id") or context_rule_id or candidate_rule_id
            if target_rule_id:
                try:
                    target_rule_id = int(target_rule_id)
                    instruction = rule_info.get("instruction")
                    exact_reply = rule_info.get("exact_reply")
                    sender_pattern = rule_info.get("sender_pattern")
                    is_one_time = rule_info.get("is_one_time")

                    if exact_reply == "[DO_NOT_REPLY]" or (instruction and any(w in instruction.lower() for w in ["yozma", "javob berma", "sukut", "jim"])):
                        exact_reply = "[DO_NOT_REPLY]"

                    updated = await update_chat_rule(
                        rule_id=target_rule_id,
                        instruction=instruction,
                        exact_reply=exact_reply,
                        sender_pattern=sender_pattern,
                        is_one_time=is_one_time
                    )
                    if updated:
                        mode_str = "Bir martalik" if updated.is_one_time else "Doimiy"
                        rep_disp = "🔇 Sukut (Xabar yozilmaydi)" if is_silence_rule(updated) else (updated.exact_reply or "AI orqali mos javob")
                        upd_text = (
                            f"✏️ <b>Qoida muvaffaqiyatli yangilandi!</b> (ID: {updated.id})\n\n"
                            f"👤 <b>Kimga/Qayerga:</b> {updated.sender_pattern or updated.chat_title or 'Barcha'}\n"
                            f"📌 <b>Topshiriq:</b> {updated.instruction}\n"
                            f"💬 <b>Javob:</b> {rep_disp}\n"
                            f"🔄 <b>Turi:</b> {mode_str}\n\n"
                            f"🫡 <i>{reply_to_user}</i>"
                        )
                        await wait_msg.edit_text(upd_text, parse_mode=ParseMode.HTML)
                        return
                    else:
                        await wait_msg.edit_text(f"❌ {target_rule_id}-raqamli qoida topilmadi.")
                        return
                except Exception as ue:
                    logger.error(f"Qoidani yangilashda xatolik: {ue}")
            await wait_msg.edit_text("⚠️ Qaysi qoidani tahrirlash kerakligini aniqlay olmadim. Iltimos, /rules orqali ID raqamini ko'rsating.")
            return

        # --- ACTION: SET_RULE (Avtomatik javob berish yoki sukut saqlash qoidasi) ---
        if action == "SET_RULE":
            rule_info = ai_data.get("rule_details", {})

            # Guard: Agar foydalanuvchi mavjud qoidani tahrirlash/o'zgartirishni so'ragan bo'lsa:
            is_update_intent = (context_rule_id or candidate_rule_id) and any(
                w in user_query.lower() for w in ["o'zgartir", "ozgartir", "tahrirla", "yangila", "almashtir", "update", "edit"]
            )
            if is_update_intent:
                target_id = context_rule_id or candidate_rule_id
                try:
                    target_id = int(target_id)
                    exact_rep = rule_info.get("exact_reply")
                    inst = rule_info.get("instruction") or user_query
                    if exact_rep == "[DO_NOT_REPLY]" or any(w in (inst + " " + user_query).lower() for w in ["yozma", "javob berma", "sukut", "jim"]):
                        exact_rep = "[DO_NOT_REPLY]"

                    updated = await update_chat_rule(
                        rule_id=target_id,
                        instruction=inst,
                        exact_reply=exact_rep,
                        sender_pattern=rule_info.get("sender_pattern"),
                        is_one_time=rule_info.get("is_one_time")
                    )
                    if updated:
                        mode_str = "Bir martalik" if updated.is_one_time else "Doimiy"
                        rep_disp = "🔇 Sukut (Xabar yozilmaydi)" if is_silence_rule(updated) else (updated.exact_reply or "AI orqali mos javob")
                        upd_text = (
                            f"✏️ <b>Qoida yangilandi!</b> (ID: {updated.id})\n\n"
                            f"👤 <b>Kimga/Qayerga:</b> {updated.sender_pattern or updated.chat_title or 'Barcha'}\n"
                            f"📌 <b>Topshiriq:</b> {updated.instruction}\n"
                            f"💬 <b>Javob:</b> {rep_disp}\n"
                            f"🔄 <b>Turi:</b> {mode_str}\n\n"
                            f"🫡 <i>{reply_to_user}</i>"
                        )
                        await wait_msg.edit_text(upd_text, parse_mode=ParseMode.HTML)
                        return
                except Exception as e:
                    logger.error(f"Fallback update error: {e}")

            sender_pattern = rule_info.get("sender_pattern")
            # Agar sender_pattern chat_id yoki chat_title bo'lsa, None qilamiz (butun chatga ishlashi uchun)
            if sender_pattern and (str(sender_pattern) == str(context_chat_id) or (context_chat_title and sender_pattern.strip().lower() == context_chat_title.strip().lower())):
                sender_pattern = None

            instruction = rule_info.get("instruction") or user_query
            exact_reply = rule_info.get("exact_reply")
            is_one_time = rule_info.get("is_one_time", True)

            # Sukut / yozmaslik tekshiruvi:
            is_silence = (
                exact_reply == "[DO_NOT_REPLY]" or
                any(w in (instruction + " " + user_query).lower() for w in ["yozma", "yozmaslik", "javob berma", "sukut", "jim bo'l", "mute", "aralashma"])
            )
            if is_silence:
                exact_reply = "[DO_NOT_REPLY]"
                if "bir martalik" not in user_query.lower():
                    is_one_time = False

            created_rule = await add_chat_rule(
                instruction=instruction,
                chat_id=context_chat_id,
                chat_title=context_chat_title,
                sender_pattern=sender_pattern,
                trigger_desc=instruction,
                exact_reply=exact_reply,
                is_one_time=is_one_time
            )

            rule_type_str = "Bir martalik (javob bergach o'chadi)" if is_one_time else "Doimiy"
            if is_silence:
                success_text = (
                    f"🔇 <b>Sukut saqlash qoidasi o'rnatildi!</b> (ID: {created_rule.id})\n\n"
                    f"👤 <b>Kimga/Qayerga:</b> {sender_pattern or context_chat_title or (f'Chat {context_chat_id}' if context_chat_id else 'Barcha')}\n"
                    f"📌 <b>Topshiriq:</b> {instruction}\n"
                    f"💬 <b>Holat:</b> AI bu chatga/shaxsga umuman xabar yozmaydi (Sukut)\n"
                    f"🔄 <b>Turi:</b> {rule_type_str}\n\n"
                    f"🫡 <i>{reply_to_user}</i>"
                )
            else:
                success_text = (
                    f"✅ <b>Yangi avto-javob qoidasi o'rnatildi!</b> (ID: {created_rule.id})\n\n"
                    f"👤 <b>Kimga/Qayerga:</b> {sender_pattern or context_chat_title or 'Har qanday xabar'}\n"
                    f"📌 <b>Topshiriq:</b> {instruction}\n"
                    f"💬 <b>Javob matni:</b> {exact_reply or 'AI orqali mos javob beriladi'}\n"
                    f"🔄 <b>Turi:</b> {rule_type_str}\n\n"
                    f"🫡 <i>{reply_to_user}</i>"
                )
            await wait_msg.edit_text(success_text, parse_mode=ParseMode.HTML)
            register_notification_context(
                wait_msg.message_id,
                chat_id=context_chat_id,
                sender_name=context_chat_title or "",
                task_details=success_text[:300]
            )
            return

        # --- 2. ACTION: SEND_MESSAGE (Chatga/Guruhga xabar yuborish) ---
        if action == "SEND_MESSAGE":
            send_info = ai_data.get("send_details", {})
            target_id = send_info.get("target_chat_id") or context_chat_id
            target_name = send_info.get("target_name") or context_chat_title or "Chat"
            msg_to_send = send_info.get("message_to_send") or ""

            # Agar target_id bo'lmasa, har qanday yo'l bilan qidirib topish
            if not target_id and target_name:
                found = await find_chat_id_by_any_means(target_name)
                if found:
                    target_id, target_name = found

            # Agar hanuz topilmagan bo'lsa va reply contextda chat bo'lsa
            if not target_id and context_chat_id:
                target_id = context_chat_id

            if target_id and msg_to_send:
                import uuid
                draft_id = str(uuid.uuid4())[:8]
                _draft_messages[draft_id] = {
                    "chat_id": target_id,
                    "text": msg_to_send,
                    "target_name": target_name
                }
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(text="🚀 Chatga yuborish", callback_data=f"send_draft:{draft_id}"),
                        InlineKeyboardButton(text="❌ Bekor qilish", callback_data=f"cancel_draft:{draft_id}")
                    ]
                ])
                draft_prompt = (
                    f"📤 <b>Chatga xabar yuborishga tayyor:</b>\n\n"
                    f"🎯 <b>Qayerga:</b> {target_name} (ID: <code>{target_id}</code>)\n"
                    f"📝 <b>Yuboriladigan matn:</b>\n"
                    f"------------------------\n"
                    f"{msg_to_send}\n"
                    f"------------------------\n\n"
                    f"💬 <i>{reply_to_user}</i>\n\n"
                    f"Pastdagi tugma orqali tasdiqlang:"
                )
                await wait_msg.edit_text(draft_prompt, reply_markup=keyboard, parse_mode=ParseMode.HTML)
                register_notification_context(
                    wait_msg.message_id,
                    chat_id=target_id,
                    sender_name=target_name,
                    task_details=draft_prompt[:300]
                )
                return
            elif msg_to_send:
                # Agar chat_id topilmagan bo'lsa ham xom JSON chiqmasin!
                draft_prompt = (
                    f"📤 <b>Chatga yuboriladigan taklif tayyorlandi:</b>\n\n"
                    f"📝 <b>Matn:</b>\n"
                    f"------------------------\n"
                    f"{msg_to_send}\n"
                    f"------------------------\n\n"
                    f"💬 <i>{reply_to_user}</i>\n\n"
                    f"⚠️ <i>Qaysi chatga yuborish kerakligini aniqlay olmadim. Iltimos, o'sha xabarga reply qiling yoki chat nomini yozing.</i>"
                )
                await wait_msg.edit_text(draft_prompt, parse_mode=ParseMode.HTML)
                return

        # --- 3. ACTION: SCHEDULE_MESSAGE (Avtomatik vaqtli xabar rejalashtirish) ---
        if action == "SCHEDULE_MESSAGE":
            sched_info = ai_data.get("schedule_details", {})
            target_id = sched_info.get("target_chat_id") or context_chat_id
            target_name = sched_info.get("target_name") or context_chat_title or "Chat"
            msg_to_send = sched_info.get("message_to_send") or ""
            raw_days = sched_info.get("days", "0,1,2,3,4,5,6")
            raw_time = sched_info.get("time", "17:00")

            # Chatni qidirib topish
            if not target_id and target_name:
                found = await find_chat_id_by_any_means(target_name)
                if found:
                    target_id, target_name = found

            if not target_id and context_chat_id:
                target_id = context_chat_id

            if not target_id:
                err_text = (
                    f"⚠️ <b>Rejalashtirilgan xabar tayyorlandi, ammo chat topilmadi:</b>\n\n"
                    f"🎯 <b>Nomi:</b> {target_name}\n"
                    f"📝 <b>Xabar matni:</b> {msg_to_send}\n"
                    f"🕒 <b>Vaqt:</b> {raw_time}\n\n"
                    f"💬 <i>{reply_to_user}</i>\n\n"
                    f"Iltimos, guruhning aniq nomini yoki ID sini ko'rsating (yoki tegishli chatdagi xabarga reply qilib qayta yozing)."
                )
                await wait_msg.edit_text(err_text, parse_mode=ParseMode.HTML)
                return

            if not msg_to_send:
                await wait_msg.edit_text("⚠️ Rejalashtiriladigan xabar matni bo'sh bo'lishi mumkin emas.")
                return

            norm_days = normalize_days(raw_days)
            norm_time = normalize_time(raw_time)

            new_sched = await add_scheduled_message(
                chat_id=target_id,
                message_text=msg_to_send,
                days_of_week=norm_days,
                send_time=norm_time,
                chat_title=target_name
            )

            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="⏸ To'xtatish", callback_data=f"toggle_sched:{new_sched.id}"),
                    InlineKeyboardButton(text="🗑 O'chirish", callback_data=f"del_sched:{new_sched.id}")
                ]
            ])

            human_days = format_days_human(norm_days)
            sched_text = (
                f"⏰ <b>Yangi rejalashtirilgan xabar (Schedule) yaratildi!</b> (ID: {new_sched.id})\n\n"
                f"📍 <b>Qayerga:</b> {target_name} (<code>{target_id}</code>)\n"
                f"📅 <b>Kunlar:</b> {human_days}\n"
                f"🕒 <b>Vaqt:</b> {norm_time} (Toshkent vaqti)\n"
                f"📝 <b>Xabar matni:</b>\n"
                f"------------------------\n"
                f"{msg_to_send}\n"
                f"------------------------\n\n"
                f"🫡 <i>{reply_to_user}</i>"
            )
            await wait_msg.edit_text(sched_text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
            register_notification_context(
                wait_msg.message_id,
                chat_id=target_id,
                sender_name=target_name,
                task_details=sched_text[:300]
            )
            return

        # --- 4. ACTION: NONE (Oddiy suhbat yoki tahlil) ---
        if not reply_to_user:
            reply_to_user = "Kechirasiz, javob shakllantirishda xatolik yuz berdi yoki AI dan ma'lumot kelmadi."

        chunks = split_text_chunks(reply_to_user)

        # Birinchi qism bilan kutish xabarini tahrirlash
        first_chunk = chunks[0]
        try:
            await wait_msg.edit_text(first_chunk, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            await wait_msg.edit_text(first_chunk)

        # Kontekstni yangi xabarga bog'lab saqlash (suhbat zanjiri davom eta olishi uchun)
        register_notification_context(
            wait_msg.message_id,
            chat_id=context_chat_id,
            sender_name=context_chat_title or "",
            task_details=first_chunk[:300]
        )

        # Qolgan qismlarni yangi xabarlar qilib yuborish
        for chunk in chunks[1:]:
            try:
                extra_msg = await message.answer(chunk, parse_mode=ParseMode.MARKDOWN)
            except Exception:
                extra_msg = await message.answer(chunk)

            register_notification_context(
                extra_msg.message_id,
                chat_id=context_chat_id,
                sender_name=context_chat_title or "",
                task_details=chunk[:300]
            )

    except Exception as e:
        logger.error(f"Manager chatda xatolik: {e}", exc_info=True)
        await wait_msg.edit_text(f"Xatolik yuz berdi: {e}")

# --- Callback Handlers (Xabar yuborishni tasdiqlash) ---

@dp.callback_query(F.data.startswith("send_draft:"))
async def cb_send_draft(callback: CallbackQuery):
    draft_id = callback.data.split("send_draft:")[1]
    draft = _draft_messages.get(draft_id)
    if not draft:
        await callback.answer("Bu xabar eskirgan yoki muddati o'tgan.", show_alert=True)
        return

    await callback.answer("Xabar yuborilmoqda... ⏳")
    from collector import send_user_message
    success = await send_user_message(chat_id=draft["chat_id"], text=draft["text"])
    if success:
        _draft_messages.pop(draft_id, None)
        await callback.message.edit_text(
            f"✅ <b>Xabar {draft['target_name']} ga muvaffaqiyatli yuborildi!</b>\n\n"
            f"📝 <b>Yuborilgan matn:</b>\n{draft['text']}",
            parse_mode=ParseMode.HTML
        )
    else:
        await callback.message.edit_text(
            f"❌ <b>Xatolik:</b> Xabar {draft['target_name']} ga yuborilmadi. Telethon ulanishini tekshiring.",
            parse_mode=ParseMode.HTML
        )

@dp.callback_query(F.data.startswith("cancel_draft:"))
async def cb_cancel_draft(callback: CallbackQuery):
    draft_id = callback.data.split("cancel_draft:")[1]
    _draft_messages.pop(draft_id, None)
    await callback.answer("Bekor qilindi.")
    await callback.message.edit_text("❌ Xabar yuborish bekor qilindi.")

@dp.callback_query(F.data.startswith("toggle_sched:"))
async def cb_toggle_sched(callback: CallbackQuery):
    sched_id = int(callback.data.split("toggle_sched:")[1])
    sched = await toggle_scheduled_message(sched_id)
    if not sched:
        await callback.answer("Jadval topilmadi.", show_alert=True)
        return
    status = "🟢 Faol" if sched.is_active else "⏸ To'xtatilgan"
    btn_text = "⏸ To'xtatish" if sched.is_active else "▶️ Faollashtirish"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=btn_text, callback_data=f"toggle_sched:{sched.id}"),
            InlineKeyboardButton(text="🗑 O'chirish", callback_data=f"del_sched:{sched.id}")
        ]
    ])
    await callback.message.edit_reply_markup(reply_markup=keyboard)
    await callback.answer(f"Holat: {status}")

@dp.callback_query(F.data.startswith("del_sched:"))
async def cb_del_sched(callback: CallbackQuery):
    sched_id = int(callback.data.split("del_sched:")[1])
    success = await delete_scheduled_message(sched_id)
    if success:
        await callback.answer("Jadval o'chirildi.")
        await callback.message.edit_text(f"🗑 <b>{sched_id}-raqamli rejalashtirilgan xabar o'chirildi.</b>", parse_mode=ParseMode.HTML)
    else:
        await callback.answer("Jadval topilmadi.", show_alert=True)

async def start_bot():
    if not settings.BOT_TOKEN:
        logger.warning("BOT_TOKEN sozlanmagan. Aiogram bot ishlamasligi mumkin!")
        return
    logger.info("Aiogram Bot ishga tushmoqda...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    logging.basicConfig(level=logging.INFO)
    asyncio.run(start_bot())
