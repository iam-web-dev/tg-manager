from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict, Any
from sqlalchemy import (
    Column, Integer, BigInteger, String, Text, DateTime, Boolean, Index, select, desc, update
)
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base
from config import settings

Base = declarative_base()

class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(BigInteger, nullable=False, index=True)
    message_id = Column(BigInteger, nullable=False)
    chat_title = Column(String(255), nullable=True)
    sender_id = Column(BigInteger, nullable=True)
    sender_name = Column(String(255), nullable=True)
    sender_username = Column(String(255), nullable=True)
    text = Column(Text, nullable=True)
    date = Column(DateTime, nullable=False, index=True)
    reply_to_msg_id = Column(BigInteger, nullable=True)
    topic_id = Column(BigInteger, nullable=True)
    topic_title = Column(String(255), nullable=True)

    is_edited = Column(Boolean, default=False)
    edited_date = Column(DateTime, nullable=True)
    is_deleted = Column(Boolean, default=False)
    deleted_date = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_chat_message", "chat_id", "message_id", unique=True),
        Index("ix_chat_date", "chat_id", "date"),
    )

class MessageHistory(Base):
    __tablename__ = "message_history"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(BigInteger, nullable=False, index=True)
    message_id = Column(BigInteger, nullable=False, index=True)
    old_text = Column(Text, nullable=True)
    edited_date = Column(DateTime, nullable=False)

class TrackedChat(Base):
    __tablename__ = "tracked_chats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(BigInteger, unique=True, nullable=False)
    title = Column(String(255), nullable=True)
    username = Column(String(255), nullable=True)
    chat_type = Column(String(50), default="group")
    is_active = Column(Boolean, default=True)
    auto_reply = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class ChatRule(Base):
    """Foydalanuvchining muayyan chat yoki odam uchun tayinlagan maxsus ko'rsatma/qoidalari."""
    __tablename__ = "chat_rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(BigInteger, nullable=True, index=True)
    chat_title = Column(String(255), nullable=True)
    sender_pattern = Column(String(255), nullable=True) # Foydalanuvchi ismi yoki username
    trigger_desc = Column(String(255), nullable=True) # Shart tavsifi (masalan: yozsa, so'rasa)
    instruction = Column(Text, nullable=False) # AI uchun ko'rsatma yoki vazifa
    exact_reply = Column(Text, nullable=True) # Aniq tayyor javob matni (agar mavjud bo'lsa)
    is_active = Column(Boolean, default=True)
    is_one_time = Column(Boolean, default=True) # Bir marta bajarilgach o'chiriladimi?
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    executed_at = Column(DateTime, nullable=True)

class ScheduledMessage(Base):
    """Belgilangan vaqt va kunlarda avtomatik jo'natiladigan xabarlar jadvali."""
    __tablename__ = "scheduled_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(BigInteger, nullable=False, index=True)
    chat_title = Column(String(255), nullable=True)
    message_text = Column(Text, nullable=False)
    days_of_week = Column(String(100), nullable=False) # Masalan: "0,1,2,3,4,5" (0=Dushanba..6=Yakshanba)
    send_time = Column(String(10), nullable=False)     # "17:00" (HH:MM 24-soat)
    timezone_offset = Column(Integer, default=5)       # UTC+5 (O'zbekiston)
    is_active = Column(Boolean, default=True)
    last_sent_date = Column(String(20), nullable=True) # "YYYY-MM-DD"
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

# Engine and Session
engine = create_async_engine(settings.get_db_url(), echo=False)
async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

async def auto_track_chat(chat_id: int, title: str, chat_type: str = "group", username: Optional[str] = None):
    async with async_session() as session:
        stmt = select(TrackedChat).where(TrackedChat.chat_id == chat_id)
        existing = (await session.execute(stmt)).scalar_one_or_none()
        if not existing:
            chat = TrackedChat(chat_id=chat_id, title=title, chat_type=chat_type, username=username, is_active=True)
            session.add(chat)
            await session.commit()
        else:
            if existing.title != title or existing.username != username:
                existing.title = title
                existing.username = username
                await session.commit()

async def save_message(
    chat_id: int,
    message_id: int,
    chat_title: Optional[str],
    sender_id: Optional[int],
    sender_name: Optional[str],
    sender_username: Optional[str],
    text: Optional[str],
    date: datetime,
    reply_to_msg_id: Optional[int] = None,
    topic_id: Optional[int] = None,
    topic_title: Optional[str] = None
) -> bool:
    if not text:
        text = "[Bo'sh/Media xabar]"

    async with async_session() as session:
        stmt = select(Message).where(Message.chat_id == chat_id, Message.message_id == message_id)
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            if existing.text != text:
                # Add to history
                history = MessageHistory(
                    chat_id=chat_id,
                    message_id=message_id,
                    old_text=existing.text,
                    edited_date=datetime.now(timezone.utc)
                )
                session.add(history)
                # Update current
                existing.text = text
                existing.is_edited = True
                existing.edited_date = datetime.now(timezone.utc)
            if chat_title:
                existing.chat_title = chat_title
            await session.commit()
            return False

        msg = Message(
            chat_id=chat_id,
            message_id=message_id,
            chat_title=chat_title,
            sender_id=sender_id,
            sender_name=sender_name,
            sender_username=sender_username,
            text=text,
            date=date,
            reply_to_msg_id=reply_to_msg_id,
            topic_id=topic_id,
            topic_title=topic_title
        )
        session.add(msg)
        await session.commit()
        return True

async def mark_message_edited(chat_id: int, message_id: int, new_text: str, edited_date: datetime):
    async with async_session() as session:
        stmt = select(Message).where(Message.chat_id == chat_id, Message.message_id == message_id)
        msg = (await session.execute(stmt)).scalar_one_or_none()
        
        if msg and msg.text != new_text:
            old_text = msg.text
            # Eski matnni tarixga yozib qoyamiz
            history = MessageHistory(
                chat_id=chat_id,
                message_id=message_id,
                old_text=old_text,
                edited_date=edited_date
            )
            session.add(history)
            
            # Yangi matn bilan almashtiramiz
            msg.text = new_text
            msg.is_edited = True
            msg.edited_date = edited_date
            await session.commit()
            return old_text, msg
        return None, msg

async def mark_message_deleted(chat_id: Optional[int], message_id: int) -> Optional[Message]:
    async with async_session() as session:
        stmt = select(Message).where(Message.message_id == message_id)
        if chat_id:
            alt_ids = [chat_id]
            str_cid = str(chat_id)
            if str_cid.startswith("-100"):
                try:
                    alt_ids.append(int(str_cid[4:]))
                    alt_ids.append(-int(str_cid[4:]))
                except ValueError:
                    pass
            elif not str_cid.startswith("-"):
                try:
                    alt_ids.append(int(f"-100{str_cid}"))
                except ValueError:
                    pass
            stmt = stmt.where(Message.chat_id.in_(alt_ids))
        stmt = stmt.order_by(desc(Message.date)).limit(1)
        res = await session.execute(stmt)
        msg = res.scalar_one_or_none()

        # Agar chat_id bilan topilmasa, lekin message_id bo'yicha bazada bor bo'lsa:
        if not msg:
            fallback_stmt = select(Message).where(Message.message_id == message_id).order_by(desc(Message.date)).limit(1)
            res_fb = await session.execute(fallback_stmt)
            msg = res_fb.scalar_one_or_none()

        if msg:
            msg.is_deleted = True
            msg.deleted_date = datetime.now(timezone.utc)
            await session.commit()
            return msg
        return None

async def get_recent_messages(
    chat_id: int,
    limit: int = 150,
    since_date: Optional[datetime] = None
) -> List[Message]:
    async with async_session() as session:
        stmt = select(Message).where(Message.chat_id == chat_id)
        if since_date:
            stmt = stmt.where(Message.date >= since_date)
        stmt = stmt.order_by(desc(Message.date)).limit(limit)
        result = await session.execute(stmt)
        messages = list(result.scalars().all())
        messages.reverse()
        return messages

async def search_messages(
    query: str,
    chat_id: Optional[int] = None,
    limit: int = 50
) -> List[Message]:
    async with async_session() as session:
        stmt = select(Message).where(Message.text.ilike(f"%{query}%"))
        if chat_id:
            stmt = stmt.where(Message.chat_id == chat_id)
        stmt = stmt.order_by(desc(Message.date)).limit(limit)
        result = await session.execute(stmt)
        return list(result.scalars().all())

async def add_tracked_chat(chat_id: int, title: str, chat_type: str = "group") -> TrackedChat:
    async with async_session() as session:
        stmt = select(TrackedChat).where(TrackedChat.chat_id == chat_id)
        res = await session.execute(stmt)
        existing = res.scalar_one_or_none()
        if existing:
            existing.is_active = True
            existing.title = title
            await session.commit()
            return existing

        chat = TrackedChat(chat_id=chat_id, title=title, chat_type=chat_type, is_active=True)
        session.add(chat)
        await session.commit()
        return chat

async def remove_tracked_chat(chat_id: int) -> bool:
    async with async_session() as session:
        stmt = select(TrackedChat).where(TrackedChat.chat_id == chat_id)
        res = await session.execute(stmt)
        chat = res.scalar_one_or_none()
        if chat:
            chat.is_active = False
            await session.commit()
            return True
        return False

async def get_tracked_chats(only_active: bool = True) -> List[TrackedChat]:
    async with async_session() as session:
        stmt = select(TrackedChat)
        if only_active:
            stmt = stmt.where(TrackedChat.is_active == True)
        res = await session.execute(stmt)
        return list(res.scalars().all())

async def is_chat_tracked(chat_id: int) -> bool:
    async with async_session() as session:
        stmt = select(TrackedChat).where(TrackedChat.chat_id == chat_id)
        existing = (await session.execute(stmt)).scalar_one_or_none()
        if existing:
            return existing.is_active
        return True # Yangi chatlarni avtomatik qabul qilaveradi

async def has_user_sent_message_since(chat_id: int, user_id: int, since_date: datetime) -> bool:
    """Belgilangan sanadan keyin user shu chatda biron xabar yozganligini tekshiradi."""
    async with async_session() as session:
        stmt = select(Message.id).where(
            Message.chat_id == chat_id,
            Message.sender_id == user_id,
            Message.date > since_date
        ).limit(1)
        res = await session.execute(stmt)
        return res.scalar_one_or_none() is not None

async def get_chat_stats(chat_id: int) -> Dict[str, Any]:
    from sqlalchemy import func
    async with async_session() as session:
        count_stmt = select(func.count(Message.id)).where(Message.chat_id == chat_id)
        total_msgs = (await session.execute(count_stmt)).scalar() or 0

        first_stmt = select(func.min(Message.date)).where(Message.chat_id == chat_id)
        first_date = (await session.execute(first_stmt)).scalar()

        last_stmt = select(func.max(Message.date)).where(Message.chat_id == chat_id)
        last_date = (await session.execute(last_stmt)).scalar()

        del_stmt = select(func.count(Message.id)).where(Message.chat_id == chat_id, Message.is_deleted == True)
        del_msgs = (await session.execute(del_stmt)).scalar() or 0

        edit_stmt = select(func.count(Message.id)).where(Message.chat_id == chat_id, Message.is_edited == True)
        edit_msgs = (await session.execute(edit_stmt)).scalar() or 0

        return {
            "total_messages": total_msgs,
            "first_date": first_date,
            "last_date": last_date,
            "deleted_messages": del_msgs,
            "edited_messages": edit_msgs
        }

async def get_messages_json(chat_id: int, start_date: Optional[datetime] = None, end_date: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Xabarlarni barcha tahrir tarixlari bilan birga export qilish"""
    async with async_session() as session:
        stmt = select(Message).where(Message.chat_id == chat_id)
        if start_date:
            stmt = stmt.where(Message.date >= start_date)
        if end_date:
            stmt = stmt.where(Message.date <= end_date)
            
        stmt = stmt.order_by(Message.date)
        res = await session.execute(stmt)
        messages = res.scalars().all()

        output = []
        for msg in messages:
            # Har bir xabarning tahrir tarixini olish
            history_stmt = select(MessageHistory).where(
                MessageHistory.chat_id == chat_id,
                MessageHistory.message_id == msg.message_id
            ).order_by(MessageHistory.edited_date)
            
            history_res = await session.execute(history_stmt)
            edits = history_res.scalars().all()
            
            history_list = []
            for edit in edits:
                history_list.append({
                    "old_text": edit.old_text,
                    "edited_date": edit.edited_date.isoformat() if edit.edited_date else None
                })
                
            output.append({
                "message_id": msg.message_id,
                "sender_id": msg.sender_id,
                "sender_name": msg.sender_name,
                "sender_username": msg.sender_username,
                "text": msg.text,
                "date": msg.date.isoformat() if msg.date else None,
                "is_edited": msg.is_edited,
                "edited_date": msg.edited_date.isoformat() if msg.edited_date else None,
                "is_deleted": msg.is_deleted,
                "deleted_date": msg.deleted_date.isoformat() if msg.deleted_date else None,
                "reply_to_msg_id": msg.reply_to_msg_id,
                "topic_id": msg.topic_id,
                "topic_title": msg.topic_title,
                "edits_history": history_list
            })
            
        return output

async def add_chat_rule(
    instruction: str,
    chat_id: Optional[int] = None,
    chat_title: Optional[str] = None,
    sender_pattern: Optional[str] = None,
    trigger_desc: Optional[str] = None,
    exact_reply: Optional[str] = None,
    is_one_time: bool = True
) -> ChatRule:
    """Yangi avto-javob yoki ko'rsatma qoidasini saqlash."""
    async with async_session() as session:
        rule = ChatRule(
            chat_id=chat_id,
            chat_title=chat_title,
            sender_pattern=sender_pattern,
            trigger_desc=trigger_desc,
            instruction=instruction,
            exact_reply=exact_reply,
            is_active=True,
            is_one_time=is_one_time,
            created_at=datetime.now(timezone.utc)
        )
        session.add(rule)
        await session.commit()
        await session.refresh(rule)
        return rule

def is_silence_rule(rule: ChatRule) -> bool:
    """Qoida sukut saqlash / javob yozmaslik (Mute/Silence) qoidasi ekanligini tekshirish."""
    if not rule:
        return False
    if rule.exact_reply and rule.exact_reply.strip() in ["[DO_NOT_REPLY]", "[MUTE]", "NONE", "null", "none"]:
        return True
    inst = (rule.instruction or "").lower()
    silence_keywords = [
        "yozma", "yozmaslik", "javob berma", "javob qaytarma", 
        "sukut", "jim bo'l", "jim tur", "aralashma", "gapirma", 
        "mute", "do not reply", "ignore", "e'tiborsiz"
    ]
    return any(kw in inst for kw in silence_keywords)

async def get_rule_by_id(rule_id: int) -> Optional[ChatRule]:
    """Qoidani ID raqami bo'yicha olish."""
    async with async_session() as session:
        stmt = select(ChatRule).where(ChatRule.id == rule_id)
        res = await session.execute(stmt)
        return res.scalar_one_or_none()

async def update_chat_rule(
    rule_id: int,
    instruction: Optional[str] = None,
    exact_reply: Optional[str] = None,
    sender_pattern: Optional[str] = None,
    chat_id: Optional[int] = None,
    chat_title: Optional[str] = None,
    is_one_time: Optional[bool] = None,
    is_active: Optional[bool] = None
) -> Optional[ChatRule]:
    """Mavjud qoidani tahrirlash/yangilash."""
    async with async_session() as session:
        stmt = select(ChatRule).where(ChatRule.id == rule_id)
        res = await session.execute(stmt)
        rule = res.scalar_one_or_none()
        if not rule:
            return None

        if instruction is not None:
            rule.instruction = instruction
        if exact_reply is not None:
            rule.exact_reply = exact_reply
        if sender_pattern is not None:
            rule.sender_pattern = sender_pattern
        if chat_id is not None:
            rule.chat_id = chat_id
        if chat_title is not None:
            rule.chat_title = chat_title
        if is_one_time is not None:
            rule.is_one_time = is_one_time
        if is_active is not None:
            rule.is_active = is_active

        await session.commit()
        await session.refresh(rule)
        return rule

async def find_matching_rules(
    chat_id: int, 
    sender_name: Optional[str] = None, 
    sender_username: Optional[str] = None
) -> List[ChatRule]:
    """Kelgan xabar bo'yicha mos keladigan faol qoidalarni qidirish."""
    async with async_session() as session:
        stmt = select(ChatRule).where(ChatRule.is_active == True)
        res = await session.execute(stmt)
        all_active = res.scalars().all()
        
        matched = []
        s_name_lower = (sender_name or "").strip().lower()
        s_user_lower = (sender_username or "").strip().lower()

        for rule in all_active:
            # 1. Chat ID bo'yicha moslik
            if rule.chat_id and rule.chat_id == chat_id:
                pat = (rule.sender_pattern or "").strip().lower()
                # Agar sender_pattern bo'sh bo'lsa, chat_id ga teng bo'lsa, chat_title ga teng bo'lsa,
                # yoki "barcha", "hamma" kabi umumiy so'z bo'lsa -> butun chatga tegishli!
                is_chat_wide = (
                    not pat or 
                    pat == str(rule.chat_id) or 
                    (rule.chat_title and pat == rule.chat_title.strip().lower()) or
                    pat in ["barcha", "hamma", "har qanday", "all", "everyone", "chat", "guruh"]
                )
                if is_chat_wide:
                    matched.append(rule)
                else:
                    # Muayyan shaxsga tegishli qoida
                    if (s_name_lower and pat in s_name_lower) or (s_user_lower and pat in s_user_lower) or (s_user_lower and pat == f"@{s_user_lower}"):
                        matched.append(rule)
                continue

            # 2. Agar chat_id ko'rsatilmagan bo'lsa, lekin sender_pattern mos kelsa (global shaxs qoidasi)
            if not rule.chat_id and rule.sender_pattern:
                pat = rule.sender_pattern.strip().lower()
                if (s_name_lower and pat in s_name_lower) or (s_user_lower and pat in s_user_lower):
                    matched.append(rule)
                continue

        return matched

async def mark_rule_executed(rule_id: int):
    """Qoidani bajarildi deb belgilash."""
    async with async_session() as session:
        stmt = select(ChatRule).where(ChatRule.id == rule_id)
        res = await session.execute(stmt)
        rule = res.scalar_one_or_none()
        if rule:
            rule.executed_at = datetime.now(timezone.utc)
            if rule.is_one_time:
                rule.is_active = False
            await session.commit()

async def get_all_rules(only_active: bool = True) -> List[ChatRule]:
    """Barcha qoidalarni ro'yxatini olish."""
    async with async_session() as session:
        stmt = select(ChatRule)
        if only_active:
            stmt = stmt.where(ChatRule.is_active == True)
        stmt = stmt.order_by(desc(ChatRule.created_at))
        res = await session.execute(stmt)
        return list(res.scalars().all())

async def delete_chat_rule(rule_id: int) -> bool:
    """Qoidani o'chirish."""
    async with async_session() as session:
        stmt = select(ChatRule).where(ChatRule.id == rule_id)
        res = await session.execute(stmt)
        rule = res.scalar_one_or_none()
        if rule:
            await session.delete(rule)
            await session.commit()
            return True
        return False

async def find_chat_by_name(query: str) -> Optional[TrackedChat]:
    """Nom yoki username bo'yicha chatni qidirish."""
    if not query:
        return None
    clean_q = query.strip().lower()
    async with async_session() as session:
        # Aniq username yoki title bo'yicha
        stmt = select(TrackedChat).where(
            (TrackedChat.title.ilike(f"%{clean_q}%")) | 
            (TrackedChat.username.ilike(f"%{clean_q}%"))
        ).limit(1)
        res = await session.execute(stmt)
        return res.scalar_one_or_none()

async def find_chat_id_by_any_means(query: str) -> Optional[tuple]:
    """Chatni TrackedChat yoki Message jadvallaridan nomi/egasi bo'yicha topish. (chat_id, display_name) qaytaradi."""
    if not query:
        return None
    clean_q = query.strip().lower()
    async with async_session() as session:
        # 1. TrackedChat dan qidirish
        stmt = select(TrackedChat).where(
            (TrackedChat.title.ilike(f"%{clean_q}%")) | 
            (TrackedChat.username.ilike(f"%{clean_q}%"))
        ).limit(1)
        res = await session.execute(stmt)
        c = res.scalar_one_or_none()
        if c:
            return c.chat_id, c.title or str(c.chat_id)

        # 2. Messages jadvalidan sender_name yoki chat_title bo'yicha eng so'nggi xabarni topish
        msg_stmt = select(Message).where(
            (Message.sender_name.ilike(f"%{clean_q}%")) |
            (Message.sender_username.ilike(f"%{clean_q}%")) |
            (Message.chat_title.ilike(f"%{clean_q}%"))
        ).order_by(desc(Message.date)).limit(1)
        msg_res = await session.execute(msg_stmt)
        m = msg_res.scalar_one_or_none()
        if m:
            display = m.chat_title or m.sender_name or str(m.chat_id)
            return m.chat_id, display

    return None


async def add_scheduled_message(
    chat_id: int,
    message_text: str,
    days_of_week: str,
    send_time: str,
    chat_title: Optional[str] = None,
    timezone_offset: int = 5
) -> ScheduledMessage:
    """Yangi rejalashtirilgan xabar qo'shish."""
    async with async_session() as session:
        sched = ScheduledMessage(
            chat_id=chat_id,
            chat_title=chat_title,
            message_text=message_text,
            days_of_week=days_of_week,
            send_time=send_time,
            timezone_offset=timezone_offset,
            is_active=True
        )
        session.add(sched)
        await session.commit()
        await session.refresh(sched)
        return sched

async def get_all_scheduled_messages(only_active: bool = False) -> List[ScheduledMessage]:
    """Barcha rejalashtirilgan xabarlar ro'yxati."""
    async with async_session() as session:
        stmt = select(ScheduledMessage)
        if only_active:
            stmt = stmt.where(ScheduledMessage.is_active == True)
        stmt = stmt.order_by(desc(ScheduledMessage.created_at))
        res = await session.execute(stmt)
        return list(res.scalars().all())

async def get_scheduled_message_by_id(schedule_id: int) -> Optional[ScheduledMessage]:
    """ID bo'yicha jadvalni olish."""
    async with async_session() as session:
        stmt = select(ScheduledMessage).where(ScheduledMessage.id == schedule_id)
        res = await session.execute(stmt)
        return res.scalar_one_or_none()

async def toggle_scheduled_message(schedule_id: int) -> Optional[ScheduledMessage]:
    """Jadvalni faollashtirish yoki to'xtatish (toggle is_active)."""
    async with async_session() as session:
        stmt = select(ScheduledMessage).where(ScheduledMessage.id == schedule_id)
        res = await session.execute(stmt)
        sched = res.scalar_one_or_none()
        if sched:
            sched.is_active = not sched.is_active
            await session.commit()
            await session.refresh(sched)
            return sched
        return None

async def delete_scheduled_message(schedule_id: int) -> bool:
    """Jadvalni o'chirish."""
    async with async_session() as session:
        stmt = select(ScheduledMessage).where(ScheduledMessage.id == schedule_id)
        res = await session.execute(stmt)
        sched = res.scalar_one_or_none()
        if sched:
            await session.delete(sched)
            await session.commit()
            return True
        return False

async def mark_schedule_sent(schedule_id: int, date_str: str):
    """Bugun xabar yuborilganligini belgilash (takrorlanishni oldini olish)."""
    async with async_session() as session:
        stmt = select(ScheduledMessage).where(ScheduledMessage.id == schedule_id)
        res = await session.execute(stmt)
        sched = res.scalar_one_or_none()
        if sched:
            sched.last_sent_date = date_str
            await session.commit()


