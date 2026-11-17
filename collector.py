import asyncio
import logging
import re
import html
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict
from telethon import TelegramClient, events
from telethon.tl.types import User, Channel, Chat
from config import settings
from database import (
    async_session,
    Message as DbMessage, 
    save_message, 
    mark_message_edited, 
    mark_message_deleted,
    auto_track_chat,
    is_chat_tracked,
    has_user_sent_message_since,
    get_recent_messages,
    find_matching_rules,
    mark_rule_executed,
    is_silence_rule
)
from ai_service import generate_secretary_reply

logger = logging.getLogger(__name__)

async def send_user_message(chat_id: int, text: str, reply_to_msg_id: Optional[int] = None) -> bool:
    """Telethon (Userbot) orqali Yodgorning akkauntidan chatga xabar yuborish."""
    try:
        c = get_client()
        if not c.is_connected():
            await c.connect()
        await c.send_message(chat_id, text, reply_to=reply_to_msg_id)
        logger.info(f"Telethon orqali {chat_id} chatiga muvaffaqiyatli xabar yuborildi.")
        return True
    except Exception as e:
        logger.error(f"Telethon orqali xabar yuborishda xatolik ({chat_id}): {e}")
        return False

topic_cache = {}
auto_reply_ratelimit = {}
daily_greeting_tracker = {}

# Telethon client va foydalanuvchi kesh o'zgaruvchilari
client: Optional[TelegramClient] = None
cached_me: Optional[User] = None


def get_client() -> TelegramClient:
    """Telethon client obyektini faol event loop ichida yaratish yoki qaytarish."""
    global client
    if client is None:
        client = TelegramClient(
            settings.get_session_path(), 
            settings.API_ID, 
            settings.API_HASH
        )
        _register_handlers(client)
    return client


async def get_me_cached() -> Optional[User]:
    """Akkount egasi ma'lumotlarini keshdan olish (serverga takroriy get_me so'rovlarini kamaytirish uchun)."""
    global cached_me
    if cached_me is None:
        c = get_client()
        if c.is_connected() and await c.is_user_authorized():
            try:
                cached_me = await c.get_me()
            except Exception as e:
                logger.warning(f"Akkount egasi ma'lumotini olishda xatolik: {e}")
    return cached_me


class ChatSecretaryState:
    def __init__(self, chat_id: int):
        self.chat_id = chat_id
        self.is_ai_active = False           # 10 daqiqa kutishdan keyin chat AI ga o'tganmi?
        self.pending_messages = []          # [(sender_name, text, event_date, event), ...]
        self.timer_task: Optional[asyncio.Task] = None
        self.last_reply_time: Optional[datetime] = None

chat_secretary_states = {}

def get_chat_state(chat_id: int) -> ChatSecretaryState:
    if chat_id not in chat_secretary_states:
        chat_secretary_states[chat_id] = ChatSecretaryState(chat_id)
    return chat_secretary_states[chat_id]


DISMISSAL_PATTERN = re.compile(
    r'^(ok|okay|okey|okk|k|xop|xo\'?p|hop|ho\'?p|tushunarli|tushundim|yaxshi|mayli|bo\'?ldi|boldi|aha|ha|ladno|ponyatno|horosho|harasho|yasno)$',
    re.IGNORECASE
)

THANKS_PATTERN = re.compile(
    r'\b(rahmat|raxmat|tashakkur|spasibo|thanks|thank you|thx|sog\'?\s*bo\'?ling|salomat\s*bo\'?ling)\b',
    re.IGNORECASE
)

def evaluate_closing_intent(text: str) -> Optional[str]:
    """
    Xabar suhbatni tugatuvchi yoki minnatdorchilik ekanligini aniqlash:
    - Minnatdorchilik bo'lsa -> 'Arzimaydi! Sog' bo'ling 😊' qaytaradi.
    - Suhbatni tugatuvchi qisqa so'z (ok, xop, mayli...) bo'lsa -> 'IGNORE' qaytaradi.
    - Oddiy xabar yoki savol bo'lsa -> None qaytaradi (AI ga uzatiladi).
    """
    clean = re.sub(r'[^\w\s\']', '', text.strip().lower())
    clean = re.sub(r'\s+', ' ', clean).strip()

    # Agar xabarda savol belgisi yoki qo'shimcha savol so'zlari bo'lsa, tezkor yopmaymiz (AI to'liq javob bersin)
    if "?" in text or any(w in clean.split() for w in ["lekin", "ammo", "qachon", "nechi", "necha", "qayerda", "kim", "qanaqa"]):
        return None

    # 1. Minnatdorchilik bildirilganmi?
    if THANKS_PATTERN.search(clean):
        if len(clean.split()) <= 6:
            if any(w in clean for w in ["spasibo", "thanks", "thank you", "thx"]):
                return "Пожалуйста! Будьте здоровы 😊" if "spasibo" in clean else "You're welcome! Have a great day 😊"
            return "Arzimaydi! Sog' bo'ling 😊"

    # 2. Faqat suhbatni tugatuvchi qisqa so'zmi? (ok, xop, mayli, tushunarli, bo'ldi...)
    if DISMISSAL_PATTERN.match(clean):
        return "IGNORE"

    return None


async def run_secretary_flow(state: ChatSecretaryState, wait_seconds: int):
    """Belgilangan vaqt (10 daqiqa yoki 10 soniya) kutib, to'plangan xabarlarga BITTADA javob qaytarish."""
    try:
        await asyncio.sleep(wait_seconds)
        
        chat_id = state.chat_id
        me = await get_me_cached()
        owner_id = me.id if me else None
        
        if not state.pending_messages:
            return

        # Agar bu 10 daqiqalik kutish bo'lsa, egasi javob yozganini yoki xabarni ko'rganini tekshiramiz
        if wait_seconds >= 60:
            first_msg_date = state.pending_messages[0][2]
            latest_msg_id = state.pending_messages[-1][3].id

            # 1. Egasi javob yozganmi?
            if owner_id:
                replied = await has_user_sent_message_since(chat_id, owner_id, first_msg_date)
                if replied:
                    logger.info(f"Chat {chat_id} da egasi 10 daqiqa ichida yozgani uchun AI javobi bekor qilindi.")
                    state.is_ai_active = False
                    state.pending_messages.clear()
                    return

            # 2. Egasi chatga kirib xabarni ko'rganmi (o'qiganmi)?
            # Agar ko'rgan bo'lsa, lekin javob bermagan bo'lsa -> AI aralashmasligi kerak!
            try:
                c = get_client()
                is_seen = False
                async for d in c.iter_dialogs(limit=25):
                    if d.id == chat_id:
                        if d.unread_count == 0 or (hasattr(d, 'dialog') and d.dialog.read_inbox_max_id >= latest_msg_id):
                            is_seen = True
                        break
                
                if is_seen:
                    logger.info(f"Chat {chat_id} dagi xabarni egasi ko'rgan (o'qilgan), lekin javob yozmagan. AI Manager aralashmaydi.")
                    state.is_ai_active = False
                    state.pending_messages.clear()
                    return
            except Exception as e:
                logger.warning(f"Chat o'qilganligini tekshirishda xatolik: {e}")

        # Chat endi rasman AI holatida
        state.is_ai_active = True

        # Qoida tekshiruvi: Agar bu chat uchun sukut/yozmaslik qoidasi o'rnatilgan bo'lsa
        active_rules = await find_matching_rules(chat_id)
        if any(is_silence_rule(r) for r in active_rules):
            logger.info(f"run_secretary_flow: Chat {chat_id} uchun sukut qoidasi faol, javob yuborilmaydi.")
            state.is_ai_active = False
            state.pending_messages.clear()
            return

        # --- Rate Limit tekshiruvi (1 soatda maksimum 15 marta javob) ---
        now = datetime.now()
        if chat_id in auto_reply_ratelimit:
            limit_data = auto_reply_ratelimit[chat_id]
            if now > limit_data["reset_time"]:
                limit_data["count"] = 0
                limit_data["reset_time"] = now + timedelta(hours=1)
                
            if limit_data["count"] >= 15:
                logger.warning(f"AI Rate limit tugadi (Chat: {chat_id})")
                state.pending_messages.clear()
                return
        else:
            auto_reply_ratelimit[chat_id] = {"count": 0, "reset_time": now + timedelta(hours=1)}

        # Navbatdagi barcha xabarlarni olib, navbatni tozalaymiz
        messages_to_process = list(state.pending_messages)
        state.pending_messages.clear()

        if not messages_to_process:
            return

        # Oxirgi event (aynan shu oxirgi xabarga reply qilib javob beriladi)
        latest_sender_name, _, _, latest_event = messages_to_process[-1]

        # Agar bir nechta xabar bo'lsa, ularni chiroyli qilib raqamlab birlashtiramiz
        if len(messages_to_process) == 1:
            combined_text = messages_to_process[0][1]
        else:
            combined_text = "\n".join([f"{idx}. {m[1]}" for idx, m in enumerate(messages_to_process, 1)])

        # 1. Suhbatni tugatuvchi yoki minnatdorchilik xabarlarini tezkor tekshirish
        closing_intent = evaluate_closing_intent(combined_text)
        if closing_intent == "IGNORE":
            # "ok", "xop", "mayli", "tushunarli", "bo'ldi" kabi so'zlar kelsa -> AI JAVOB BERMAYDI!
            logger.info(f"Chat {chat_id}: Suhbatdosh tasdiq/tugatish so'zi yozdi ('{combined_text}'). AI javob yozmaydi.")
            state.is_ai_active = False  # Suhbat yakunlangan deb hisoblanadi
            return

        if closing_intent and closing_intent != "IGNORE":
            # "rahmat", "spasibo" kabi minnatdorchilik bildirilsa -> "Arzimaydi! Sog' bo'ling 😊" deb javob qaytarish!
            try:
                final_text = f"**AI Manager:**\n{closing_intent}"
                await latest_event.reply(final_text)
                state.last_reply_time = datetime.now()
                state.is_ai_active = False  # Suhbat minnatdorchilik bilan yakunlandi
                logger.info(f"Chat {chat_id}: Minnatdorchilikka javob berildi: '{closing_intent}'")
                return
            except Exception as e:
                logger.error(f"Minnatdorchilikka javob yuborishda xatolik: {e}")
                return

        # Kunlik salomlashish tekshiruvi
        today_str = datetime.now().strftime("%Y-%m-%d")
        needs_greeting = True
        if chat_id in daily_greeting_tracker and daily_greeting_tracker[chat_id] == today_str:
            needs_greeting = False

        recent_msgs = await get_recent_messages(chat_id, limit=10)
        ai_data = await generate_secretary_reply(
            sender_name=latest_sender_name, 
            question=combined_text, 
            recent_messages=recent_msgs, 
            needs_greeting=needs_greeting
        )

        if ai_data and isinstance(ai_data, dict):
            reply_text = ai_data.get("reply_text")
            has_task = ai_data.get("has_task", False)
            task_details = ai_data.get("task_details", "")

            # Agar AI javob berish shart emas deb topsa (null yoki bo'sh)
            if not reply_text or str(reply_text).strip().lower() in ["null", "none", ""]:
                logger.info(f"Chat {chat_id}: AI javob yozish shart emas deb topdi.")
                state.is_ai_active = False
                return

            try:
                final_text = f"**AI Manager:**\n{reply_text}"
                await latest_event.reply(final_text)
                auto_reply_ratelimit[chat_id]["count"] += 1
                daily_greeting_tracker[chat_id] = today_str
                state.last_reply_time = datetime.now()
                logger.info(f"Chat {chat_id} ga AI javob yubordi ({len(messages_to_process)} ta xabarga).")
            except Exception as e:
                logger.error(f"Telethon orqali javob yuborishda xatolik: {e}")

            if has_task and task_details:
                from bot import bot, register_notification_context
                try:
                    msg = (
                        f"❗️ AI Manager: Yangi muhim eslatma/topshiriq\n\n"
                        f"Chat: {chat_id}\n"
                        f"Foydalanuvchi: {latest_sender_name}\n\n"
                        f"📝 Xulosa:\n{task_details}"
                    )
                    sent_msg = await bot.send_message(settings.ADMIN_ID, msg)
                    register_notification_context(
                        sent_msg.message_id,
                        chat_id=chat_id,
                        sender_name=latest_sender_name,
                        task_details=task_details
                    )
                except Exception as e:
                    logger.error(f"Aiogram orqali bildirishnoma yuborishda xatolik: {e}")

    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.exception(f"run_secretary_flow da xatolik: {e}")


def is_bot_user(sender, sender_username: Optional[str] = None, sender_name: Optional[str] = None) -> bool:
    """Tekshirish: Ushbu foydalanuvchi botmi yoki yo'qmi."""
    if sender and getattr(sender, 'bot', False):
        return True
    if sender_username:
        u = sender_username.lower()
        if u.endswith('bot') or u.endswith('_bot'):
            return True
    if sender_name:
        n = sender_name.lower().strip()
        if n.endswith('bot') or ' bot' in n or 'robot' in n or ('@' in n and n.endswith('bot')):
            return True
    return False


def extract_message_text(message) -> str:
    """Xabar matnini yoki media tavsifini olish."""
    raw_text = getattr(message, 'message', None) or ""
    if raw_text and raw_text.strip():
        return raw_text.strip()

    # Matnsiz kelgan media xabarlar:
    if getattr(message, 'photo', None):
        return "[Rasm]"
    elif getattr(message, 'voice', None):
        return "[Ovozli xabar]"
    elif getattr(message, 'video_note', None):
        return "[Video xabar / Kruglyash]"
    elif getattr(message, 'video', None):
        return "[Video]"
    elif getattr(message, 'audio', None):
        return "[Audio]"
    elif getattr(message, 'sticker', None):
        emoji = getattr(message.file, 'emoji', '') if getattr(message, 'file', None) else ''
        return f"[Stiker {emoji}]" if emoji else "[Stiker]"
    elif getattr(message, 'document', None):
        name = getattr(message.file, 'name', '') if getattr(message, 'file', None) else ''
        return f"[Fayl: {name}]" if name else "[Fayl / Hujjat]"
    elif getattr(message, 'poll', None):
        try:
            q = message.poll.poll.question.text if hasattr(message.poll.poll.question, 'text') else getattr(message.poll.poll, 'question', "So'rovnoma")
            return f"[So'rovnoma: {q}]"
        except Exception:
            return "[So'rovnoma]"
    elif getattr(message, 'contact', None):
        return "[Kontakt]"
    elif getattr(message, 'geo', None):
        return "[Geolokatsiya]"
    elif getattr(message, 'dice', None):
        return f"[Dice: {message.dice.emoticon}]"
    elif getattr(message, 'action', None):
        return "[Xizmat xabari]"
    elif getattr(message, 'media', None):
        return "[Media]"
    return "[Bo'sh xabar]"


async def _get_chat_info(event):
    try:
        chat = await event.get_chat()
    except Exception as e:
        logger.warning(f"Chat ma'lumotini olishda xatolik: {e}")
        return None, None

    if not chat:
        return None, None

    chat_id = event.chat_id
    
    # Kanallarni ajratish (Kanal bo'lsa va megagroup bo'lmasa = bu ommaviy kanal)
    is_broadcast_channel = getattr(chat, 'broadcast', False)
    if is_broadcast_channel:
        return None, None
        
    chat_title = getattr(chat, 'title', None) or getattr(chat, 'username', None) or 'Shaxsiy Chat'
    
    # Avtomatik ravishda chatni bazaga yozib qoyamiz (qidirish oson bo'lishi uchun)
    if isinstance(chat, Channel):
        chat_type = "supergroup"
    elif isinstance(chat, Chat):
        chat_type = "group"
    else:
        chat_type = "user"
        
    chat_username = getattr(chat, 'username', None)
    try:
        await auto_track_chat(chat_id, chat_title, chat_type, chat_username)
    except Exception as e:
        logger.error(f"auto_track_chat xatosi: {e}")

    return chat, chat_title


async def message_handler(event: events.NewMessage.Event):
    """Yangi xabarlarni ushlab, bazaga saqlash."""
    try:
        chat, chat_title = await _get_chat_info(event)
        if not chat:
            return # Kanallar saqlanmaydi
        
        chat_id = event.chat_id
        if not await is_chat_tracked(chat_id):
            return

        try:
            sender = await event.get_sender()
        except Exception:
            sender = None

        sender_id = event.sender_id
        sender_name = None
        sender_username = getattr(sender, 'username', None)
        
        if isinstance(sender, User):
            sender_name = f"{sender.first_name or ''} {sender.last_name or ''}".strip()
        elif sender:
            sender_name = getattr(sender, 'title', str(sender_id))

        text = extract_message_text(event.message)
        reply_to = event.message.reply_to_msg_id
        
        topic_id = None
        topic_title = None
        if event.message.reply_to and getattr(event.message.reply_to, 'forum_topic', False):
            topic_id = event.message.reply_to.reply_to_top_id or event.message.reply_to.reply_to_msg_id

        if topic_id:
            cache_key = f"{chat_id}_{topic_id}"
            if cache_key in topic_cache:
                topic_title = topic_cache[cache_key]
            else:
                try:
                    c = get_client()
                    topic_msg = await c.get_messages(chat_id, ids=topic_id)
                    if topic_msg and getattr(topic_msg, 'action', None) and hasattr(topic_msg.action, 'title'):
                        topic_title = topic_msg.action.title
                    else:
                        topic_title = f"Topic {topic_id}"
                    topic_cache[cache_key] = topic_title
                except Exception as e:
                    logger.warning(f"Topic nomini olib bo'lmadi: {e}")

        saved = await save_message(
            chat_id=chat_id,
            message_id=event.message.id,
            chat_title=chat_title,
            sender_id=sender_id,
            sender_name=sender_name,
            sender_username=sender_username,
            text=text,
            date=event.date,
            reply_to_msg_id=reply_to,
            topic_id=topic_id,
            topic_title=topic_title
        )

        if saved:
            logger.info(f"Yangi xabar saqlandi: [{chat_title}] {sender_name or sender_id}: {text[:40]}")
        
        # --- AUTO-REPLY LOGIC (SECRETARY) ---
        me = await get_me_cached()

        # 1. Agar xabarni egasining o'zi (Yodgor) yozsa:
        # AI rejimini darhol o'chirish va kutayotgan timerlarni bekor qilish!
        if me and sender_id == me.id:
            state = get_chat_state(chat_id)
            state.is_ai_active = False
            if state.timer_task and not state.timer_task.done():
                state.timer_task.cancel()
                state.timer_task = None
            state.pending_messages.clear()
            logger.info(f"Chat {chat_id} da egasi yozdi: AI rejimi to'xtatildi.")
            return

        # 2. Suhbatdosh yozgan bo'lsa tekshiramiz:
        should_reply = False
        is_bot = is_bot_user(sender, sender_username, sender_name)
        if me and sender_id != me.id and not is_bot:
            # --- 1-BOSQICH: Yodgor tomonidan tayinlangan maxsus qoidalar (ChatRules) bormi? ---
            try:
                matched_rules = await find_matching_rules(chat_id, sender_name, sender_username)
                if matched_rules:
                    # 1. Sukut / yozmaslik (DO_NOT_REPLY / SILENCE) qoidalari bormi?
                    silence_rules = [r for r in matched_rules if is_silence_rule(r)]
                    if silence_rules:
                        for s_rule in silence_rules:
                            logger.info(f"Chat {chat_id} da sukut qoidasi (ID: {s_rule.id}) ishladi: AI xabar yozmaydi.")
                            if s_rule.is_one_time:
                                await mark_rule_executed(s_rule.id)
                        # Barcha kutayotgan timerlarni bekor qilish va to'xtatish
                        state = get_chat_state(chat_id)
                        state.is_ai_active = False
                        state.pending_messages.clear()
                        if state.timer_task and not state.timer_task.done():
                            state.timer_task.cancel()
                            state.timer_task = None
                        return  # Hech qanday javob yozilmaydi, oddiy auto-reply ga ham o'tilmaydi!

                    # 2. Oddiy avto-javob qoidalari
                    for rule in matched_rules:
                        reply_to_send = rule.exact_reply
                        if not reply_to_send:
                            recent_msgs = await get_recent_messages(chat_id, limit=5)
                            ai_res = await generate_secretary_reply(
                                sender_name=sender_name or "Foydalanuvchi",
                                question=f"[VAZIFA/KO'RSATMA: {rule.instruction}]\nKelgan xabar: {text}",
                                recent_messages=recent_msgs,
                                needs_greeting=False
                            )
                            reply_to_send = ai_res.get("reply_text") if ai_res else None

                        if reply_to_send:
                            final_msg = f"**AI Manager:**\n{reply_to_send}"
                            await event.reply(final_msg)
                            await mark_rule_executed(rule.id)
                            logger.info(f"Chat {chat_id} da maxsus qoida (ID: {rule.id}) bajarildi: '{reply_to_send}'")

                            from bot import bot
                            try:
                                notify_text = (
                                    f"⚡️ <b>AI Manager: Maxsus topshiriq bajarildi!</b>\n\n"
                                    f"Chat: <b>{chat_title}</b>\n"
                                    f"Foydalanuvchi: <b>{sender_name or sender_id}</b>\n"
                                    f"Topshiriq: <i>{rule.instruction}</i>\n\n"
                                    f"📨 <b>Yuborilgan javob:</b>\n{reply_to_send}"
                                )
                                await bot.send_message(settings.ADMIN_ID, notify_text, parse_mode="HTML")
                            except Exception as ne:
                                logger.error(f"Topshiriq haqida adminga xabar berishda xatolik: {ne}")

                            state = get_chat_state(chat_id)
                            state.pending_messages.clear()
                            return
            except Exception as rule_err:
                logger.error(f"Maxsus qoidalarni bajarishda xatolik: {rule_err}")

            # --- 2-BOSQICH: Oddiy suhbatdosh uchun auto-reply shartlari ---
            if event.is_private:
                should_reply = True
            else:
                if event.message.mentioned:
                    should_reply = True
                elif event.is_reply:
                    try:
                        reply_msg = await event.get_reply_message()
                        if reply_msg and reply_msg.sender_id == me.id:
                            should_reply = True
                    except Exception:
                        pass

        if should_reply:
            state = get_chat_state(chat_id)

            # Agar oxirgi AI javobidan keyin 1 soatdan ko'p vaqt o'tgan bo'lsa,
            # yangi suhbat boshlangani uchun yana egasiga 10 daqiqa imkon beriladi
            if state.is_ai_active and state.last_reply_time:
                if datetime.now() - state.last_reply_time > timedelta(hours=1):
                    state.is_ai_active = False

            # Yangi kelgan xabarni navbatga qo'shamiz
            state.pending_messages.append((sender_name or str(sender_id), text, event.date, event))

            if not state.is_ai_active:
                # 1-bosqich: Chat hali AI rejimiga o'tmagan.
                # Egasi javob yozishini 10 daqiqa (600 soniya) kutamiz.
                if not state.timer_task or state.timer_task.done():
                    logger.info(f"Chat {chat_id}: 10 daqiqa egasining javobi kutilmoqda...")
                    state.timer_task = asyncio.create_task(run_secretary_flow(state, wait_seconds=600))
            else:
                # 2-bosqich: Chat allaqachon AI rejimida!
                # 10 daqiqa kutilmaydi!
                # 10 soniya kutamiz (ketma-ket xabarlarni bittaga to'plash uchun debounce)
                if state.timer_task and not state.timer_task.done():
                    state.timer_task.cancel()
                logger.info(f"Chat {chat_id}: AI faol, 10 soniya yangi xabarlar kutilmoqda...")
                state.timer_task = asyncio.create_task(run_secretary_flow(state, wait_seconds=10))
                        
    except Exception as e:
        logger.error(f"Xabarni qayta ishlashda xatolik: {e}", exc_info=True)


async def message_edited_handler(event: events.MessageEdited.Event):
    """Tahrirlangan xabarlarni ushlab olish va adminga bildirishnoma yuborish."""
    try:
        chat, chat_title = await _get_chat_info(event)
        if not chat: return
        
        chat_id = event.chat_id
        if not await is_chat_tracked(chat_id): return
        
        text = extract_message_text(event.message)
        if not text: return

        # Sender ma'lumotlarini olish
        try:
            sender = await event.get_sender()
        except Exception:
            sender = None
            
        sender_name = None
        sender_username = getattr(sender, 'username', None) if sender else None
        if isinstance(sender, User):
            sender_name = f"{sender.first_name or ''} {sender.last_name or ''}".strip()
            if sender.username:
                sender_name += f" (@{sender.username})"
        elif sender:
            sender_name = getattr(sender, 'title', str(event.sender_id))

        # AGAR SENDER BOT BO'LSA - bildirishnoma chiqarmaymiz!
        if is_bot_user(sender, sender_username, sender_name):
            logger.debug(f"Message {event.message.id} edit ignored: sender is a bot ({sender_name}).")
            return

        old_text, msg = await mark_message_edited(
            chat_id=chat_id, 
            message_id=event.message.id, 
            new_text=text,
            edited_date=datetime.now(timezone.utc)
        )

        # Bazadagi avval saqlangan sender ham bot bo'lsa - chiqarmaymiz!
        if msg and is_bot_user(None, msg.sender_username, msg.sender_name):
            logger.debug(f"Message {event.message.id} edit ignored: saved sender was a bot ({msg.sender_name}).")
            return

        if old_text and old_text.strip() != text.strip():
            logger.info(f"Message {event.message.id} in {chat_id} was EDITED from '{old_text[:30]}' to '{text[:30]}'.")
            
            # Agar sender_name topilmagan bo'lsa, bazadagi saqlangan sender_name dan olamiz
            if not sender_name and msg:
                sender_name = msg.sender_name or (f"@{msg.sender_username}" if msg.sender_username else f"User {msg.sender_id}")
            if not sender_name:
                sender_name = f"User {event.sender_id}" if event.sender_id else "Noma'lum"

            from bot import bot
            if settings.ADMIN_ID and bot:
                try:
                    notify_text = (
                        f"✏️ <b>Xabar tahrirlandi!</b>\n\n"
                        f"📍 <b>Chat:</b> {html.escape(chat_title or str(chat_id))}\n"
                        f"👤 <b>Kim:</b> {html.escape(sender_name)}\n\n"
                        f"🔻 <b>Eski xabar:</b>\n"
                        f"<i>{html.escape(old_text)}</i>\n\n"
                        f"🔺 <b>Yangi tahrirlangan xabar:</b>\n"
                        f"<i>{html.escape(text)}</i>"
                    )
                    await bot.send_message(settings.ADMIN_ID, notify_text, parse_mode="HTML")
                except Exception as be:
                    logger.error(f"Tahrirlangan xabar haqida adminga yuborishda xatolik: {be}")

    except Exception as e:
        logger.error(f"Error editing message: {e}")


async def message_deleted_handler(event: events.MessageDeleted.Event):
    """O'chirilgan xabarlarni qayd etish va adminga bildirishnoma yuborish."""
    try:
        chat_id = event.chat_id
        if chat_id and not await is_chat_tracked(chat_id): return
        
        deleted_ids = event.deleted_ids
        if not deleted_ids: return

        # O'chirilgan xabarlarni bazadan olish va is_deleted deb belgilash
        deleted_items = []
        chat_title_found = None

        for msg_id in deleted_ids:
            msg = await mark_message_deleted(chat_id=chat_id, message_id=msg_id)
            if msg:
                # Agar o'chirilgan xabar botga tegishli bo'lsa, bildirishnoma chiqarmaymiz
                if is_bot_user(None, msg.sender_username, msg.sender_name):
                    logger.info(f"Deleted message {msg_id} was from a bot ({msg.sender_name}). Notification skipped.")
                    continue

                chat_title_found = msg.chat_title or chat_title_found
                sender = msg.sender_name or (f"@{msg.sender_username}" if msg.sender_username else f"User {msg.sender_id}")
                deleted_items.append({
                    "id": msg_id,
                    "sender": sender or "Noma'lum",
                    "text": msg.text or "[Matnsiz/Media]",
                    "chat_title": msg.chat_title
                })
                logger.info(f"Message {msg_id} in {chat_id} was DELETED: '{msg.text[:30] if msg.text else ''}'")
            else:
                # Bazada bo'lmagan xabarlar (bot ishga tushishidan oldingi yoki keshda bo'lmagan)
                # Bular haqida 'matn bazada topilmadi' deb adminga bo'sh bildirishnoma yuborilmaydi
                logger.info(f"Message {msg_id} in {chat_id} was DELETED (not in DB). Notification skipped.")

        from bot import bot
        if settings.ADMIN_ID and bot and deleted_items:
            chat_display = chat_title_found or (f"Chat {chat_id}" if chat_id else "Noma'lum Chat")
            try:
                if len(deleted_items) == 1:
                    item = deleted_items[0]
                    notify_text = (
                        f"🗑 <b>Xabar o'chirildi!</b>\n\n"
                        f"📍 <b>Chat:</b> {html.escape(chat_display)}\n"
                        f"👤 <b>Kim:</b> {html.escape(item['sender'])}\n\n"
                        f"💬 <b>O'chirilgan xabar:</b>\n"
                        f"<i>{html.escape(item['text'])}</i>"
                    )
                else:
                    notify_text = (
                        f"🗑 <b>{len(deleted_items)} ta xabar o'chirildi!</b>\n\n"
                        f"📍 <b>Chat:</b> {html.escape(chat_display)}\n\n"
                    )
                    for idx, item in enumerate(deleted_items[:10], 1):
                        notify_text += f"{idx}. <b>{html.escape(item['sender'])}:</b> <i>{html.escape(item['text'][:120])}</i>\n"
                    if len(deleted_items) > 10:
                        notify_text += f"\n<i>... va yana {len(deleted_items) - 10} ta xabar o'chirildi.</i>"

                await bot.send_message(settings.ADMIN_ID, notify_text, parse_mode="HTML")
            except Exception as be:
                logger.error(f"O'chirilgan xabar haqida adminga bildirishnomada xatolik: {be}")

    except Exception as e:
        logger.error(f"Error deleting message: {e}")


async def message_read_handler(event: events.MessageRead.Event):
    """Egasi chatga kirib, xabarlarni o'qiganida (ko'rganida) real vaqtda ushlab olish."""
    try:
        chat_id = event.chat_id
        max_id = event.max_id
        if not chat_id or not max_id:
            return

        state = chat_secretary_states.get(chat_id)
        if state and state.pending_messages:
            latest_msg = state.pending_messages[-1][3]
            latest_msg_id = getattr(latest_msg, 'id', None)
            if latest_msg_id and max_id >= latest_msg_id:
                logger.info(f"Chat {chat_id} dagi xabarlar egasi tomonidan ko'rildi (o'qildi). AI taymeri darhol bekor qilindi.")
                state.is_ai_active = False
                if state.timer_task and not state.timer_task.done():
                    state.timer_task.cancel()
                    state.timer_task = None
                state.pending_messages.clear()
    except Exception as e:
        logger.error(f"message_read_handler da xatolik: {e}")


def _register_handlers(c: TelegramClient):
    """Telethon client uchun barcha event handlerlarni ro'yxatdan o'tkazish."""
    c.add_event_handler(message_handler, events.NewMessage(incoming=True, outgoing=True))
    c.add_event_handler(message_edited_handler, events.MessageEdited())
    c.add_event_handler(message_deleted_handler, events.MessageDeleted())
    c.add_event_handler(message_read_handler, events.MessageRead(inbox=True))


async def sync_initial_dialogs(c: TelegramClient, limit: int = 150):
    """Collector ishga tushganda akkauntdagi mavjud chatlarni bazaga sinxronlash (kanallardan tashqari)."""
    try:
        logger.info("Mavjud chatlarni sinxronizatsiya qilish boshlandi...")
        synced_count = 0
        async for dialog in c.iter_dialogs(limit=limit):
            chat = dialog.entity
            is_broadcast_channel = getattr(chat, 'broadcast', False)
            if is_broadcast_channel:
                continue

            chat_id = dialog.id
            chat_title = dialog.name or getattr(chat, 'title', None) or getattr(chat, 'username', 'Shaxsiy Chat')

            if isinstance(chat, Channel):
                chat_type = "supergroup"
            elif isinstance(chat, Chat):
                chat_type = "group"
            else:
                chat_type = "user"

            chat_username = getattr(chat, 'username', None)
            await auto_track_chat(chat_id, chat_title, chat_type, chat_username)
            synced_count += 1

        logger.info(f"Chatlar sinxronizatsiyasi yakunlandi: {synced_count} ta chat tekshirildi/saqlandi.")
    except Exception as e:
        logger.warning(f"Chatlarni dastlabki sinxronlashda xatolik (asosiy ish davom etadi): {e}")


async def start_collector():
    """Telethon clientni ishga tushirish."""
    global cached_me
    if not settings.API_ID or not settings.API_HASH:
        logger.warning("API_ID va API_HASH sozlanmagan. Telethon collector ishlamasligi mumkin!")
        return

    logger.info("Telethon Collector ishga tushmoqda...")
    c = get_client()
    
    try:
        logger.info("Connecting...")
        await c.connect()
        logger.info("Connected")

        is_auth = await c.is_user_authorized()
        logger.info(f"Authorized: {is_auth}")

        if not is_auth:
            logger.error("Telegram session authorized emas! Serverda login talab qilinadi.")
            return

        cached_me = await c.get_me()
        user_name = getattr(cached_me, 'first_name', '') or getattr(cached_me, 'username', 'Noma\'lum')
        logger.info(f"Collector akkauntga ulandi: {user_name}")

        # Mavjud chatlarni fonda bazaga yozib chiqish
        asyncio.create_task(sync_initial_dialogs(c))

        logger.info("Collector ishga tushdi: Barcha xabarlar kuzatilmoqda (Kanallardan tashqari).")
        await c.run_until_disconnected()
    except asyncio.CancelledError:
        logger.info("Collector vazifasi to'xtatildi (cancelled).")
    except Exception as e:
        logger.exception(f"Collector ishida kutilmagan xatolik yuz berdi: {e}")
    finally:
        if c.is_connected():
            logger.info("Telethon ulanishi uzilmoqda...")
            await c.disconnect()
        logger.info("Collector to'xtatildi.")


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    asyncio.run(start_collector())

