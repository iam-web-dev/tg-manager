import logging
import asyncio
from typing import List, Dict, Any, Optional
from datetime import datetime
from config import settings
from database import Message

logger = logging.getLogger(__name__)

try:
    from google import genai
    from google.genai import types
    has_genai = True
except ImportError:
    has_genai = False
    logger.error("google-genai kutubxonasi topilmadi!")

def format_messages_for_prompt(messages: List[Message]) -> str:
    """Xabarlarni AI tushunishi oson bo'lgan matn ko'rinishiga o'tkazish."""
    if not messages:
        return "Hech qanday xabar topilmadi."

    # Xabarlarni vaqt ketma-ketligida (eskisidan yangisiga qarab) tartiblash
    sorted_messages = sorted(messages, key=lambda m: m.date if m.date else datetime.min)

    formatted = []
    for m in sorted_messages:
        time_str = m.date.strftime("%Y-%m-%d %H:%M") if m.date else "Noma'lum"
        sender = m.sender_name or m.sender_username or f"User{m.sender_id}"
        
        status = ""
        if m.is_deleted:
            status = " [O'CHIRILGAN XABAR]"
        elif m.is_edited:
            status = " [TAHRIRLANGAN]"
            
        formatted.append(f"[{time_str}] {sender}{status}: {m.text}")
    
    return "\n".join(formatted)

async def _call_gemini(prompt: str) -> str:
    """Gemini API bilan bog'lanib, javob olish (fallback va retry mexanizmi bilan)."""
    if not has_genai or not settings.GEMINI_API_KEY:
        return ""

    try:
        client = genai.Client(api_key=settings.GEMINI_API_KEY)
    except Exception as e:
        logger.error(f"Gemini Client yaratishda xatolik: {e}")
        return ""

    models_to_try = [settings.GEMINI_MODEL, "gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-3.5-flash"]
    models_to_try = list(dict.fromkeys(models_to_try))

    for model_name in models_to_try:
        try:
            response = await client.aio.models.generate_content(
                model=model_name,
                contents=prompt,
            )
            if response and response.text:
                return response.text
        except Exception as e:
            err_str = str(e)
            logger.warning(f"Gemini {model_name} aio xatoligi: {e}")
            if any(code in err_str for code in ["503", "429", "404"]):
                continue
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                )
                if response and response.text:
                    return response.text
            except Exception as sync_e:
                logger.warning(f"Gemini {model_name} sync xatoligi: {sync_e}")

    logger.error("Barcha Gemini modellari so'rovni bajara olmadi.")
    return ""

async def generate_summary(chat_title: str, messages: List[Message]) -> str:
    """Xabarlardan qisqacha xulosa (summary) yasash."""
    if not messages:
        return f"'{chat_title}' uchun xabarlar topilmadi."

    chat_text = format_messages_for_prompt(messages)
    
    prompt = f"""
Siz Telegram guruh / chat tahlilchisisiz.
Quyida "{chat_title}" chatidan olingan so'nggi xabarlar keltirilgan.
Ba'zi xabarlarda [O'CHIRILGAN XABAR] yoki [TAHRIRLANGAN] holatlari ko'rsatilgan. 
Sizning vazifangiz shu xabarlarni o'qib, o'zbek tilida qisqacha va tushunarli XULOSA (Summary) yozish.

Xulosa quyidagilarni o'z ichiga olishi kerak:
1. Asosiy muhokama qilingan mavzular (ro'yxat shaklida).
2. Odamlarning umumiy kayfiyati / qarorlari.
3. Agar kimdir qandaydir muhim narsani yozib keyin o'chirib yuborgan bo'lsa, bu haqida ham alohida ta'kidlab o'ting (O'chirilgan sirli/muhim xabarlar).

Xabarlar:
---------------------
{chat_text}
---------------------
"""
    return await _call_gemini(prompt)

async def extract_tasks(chat_title: str, messages: List[Message]) -> str:
    """Guruhdagi topshiriqlar va kelishuvlarni ajratib olish."""
    if not messages:
        return "Topshiriqlar uchun xabarlar topilmadi."

    chat_text = format_messages_for_prompt(messages)
    
    prompt = f"""
Siz loyiha menejerisiz. Quyida "{chat_title}" guruhidagi suhbat berilgan.
Sizning vazifangiz bu yerdan BARCHA TOPSHIRIQ (Action Items) va KELISHUVLARni ajratib olish.
Diqqat qiling: agar topshiriq [O'CHIRILGAN XABAR] deb belgilangan xabarda bo'lsa, "bu vazifa berilgan ammo keyin o'chirib yuborilgan" deb izohlang.

O'zbek tilida aniq qilib, ro'yxat (bullet points) shaklida javob bering.
Agar hech qanday vazifa topilmasa: "Bu xabarlarda aniq vazifalar belgilanmagan" deb yozing.

Xabarlar:
---------------------
{chat_text}
---------------------
"""
    return await _call_gemini(prompt)

async def answer_question(chat_title: str, messages: List[Message], question: str) -> str:
    """Chat tarixi asosida foydalanuvchi savoliga javob berish."""
    if not messages:
        return "Bu chatda hali yetarlicha xabar yo'q."

    chat_text = format_messages_for_prompt(messages)
    
    prompt = f"""
Siz aqlli yordamchisiz. Quyida "{chat_title}" guruhidagi chat tarixi keltirilgan.
Foydalanuvchi sizga chat tarixi bilan bog'liq savol beradi. 
Siz FAQAT quyidagi xabarlarga asoslanib, savolga o'zbek tilida javob berishingiz kerak.
Agar javob [O'CHIRILGAN XABAR] ichida bo'lsa, bu haqida aytib o'ting.

Savol: {question}

Chat tarixi:
---------------------
{chat_text}
---------------------
"""
    return await _call_gemini(prompt)

async def analyze_sentiment(chat_title: str, messages: List[Message]) -> str:
    """Chatdagi kayfiyat va hissiyotlarni (sentiment) tahlil qilish."""
    if not messages:
        return "Chatda tahlil qilish uchun xabarlar yetarli emas."

    chat_text = format_messages_for_prompt(messages)
    
    prompt = f"""
Siz psixolog va kommunikatsiya bo'yicha ekspertsiz. 
Quyida "{chat_title}" chatidagi so'nggi yozishmalar keltirilgan.
Sizning vazifangiz chatdagi UMUMIY KAYFIYAT va HISSYOTLARni (Sentiment) tahlil qilish.

Javobingiz quyidagi qismlardan iborat bo'lsin:
1. Umumiy kayfiyat (Pozitiv, Negativ yoki Neytral).
2. Chatdagi toksiklik (urishish, so'kinish yoki ziddiyatlar bormi?).
3. Asosiy hissiyot (quvonch, xavotir, zerikish, asabiylashish va h.k).
4. Qisqacha tavsiya (chat a'zolariga yoki adminlarga muhitni yaxshilash uchun).

O'zbek tilida aniq va chiroyli javob tayyorlang.

Xabarlar:
---------------------
{chat_text}
---------------------
"""
    return await _call_gemini(prompt)

async def chat_with_manager(
    user_message: str,
    replied_context: Optional[str] = None,
    chat_history: Optional[List[Message]] = None,
    chat_title: Optional[str] = None,
    context_chat_id: Optional[int] = None,
    active_rules: Optional[List[Any]] = None,
    context_rule_id: Optional[int] = None
) -> Dict[str, Any]:
    """Yodgor (Admin) ning savollariga, eslatma/topshiriq reply xabarlariga javob beruvchi va buyruqlarni (Action) bajaruvchi shaxsiy AI Manager."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    context_parts = []
    if context_chat_id:
        context_parts.append(f"KONTEKST CHAT ID: {context_chat_id}")

    if context_rule_id:
        context_parts.append(f"KONTEKST QOIDA (RULE) ID: {context_rule_id}")
    
    if replied_context:
        context_parts.append(
            f"=== YODGOR REPLY QILGAN XABAR (KONTEKST) ===\n"
            f"{replied_context.strip()}\n"
            f"============================================"
        )
        
    if chat_history:
        history_text = format_messages_for_prompt(chat_history)
        title_str = chat_title or "Tegishli chat"
        context_parts.append(
            f"=== CHAT ({title_str}) TARIXIDAN SO'NGGI XABARLAR ===\n"
            f"{history_text}\n"
            f"===================================================="
        )

    if active_rules:
        rules_lines = []
        for r in active_rules:
            mode = "Bir martalik" if getattr(r, 'is_one_time', False) else "Doimiy"
            target = getattr(r, 'sender_pattern', None) or getattr(r, 'chat_title', None) or (f"Chat {r.chat_id}" if getattr(r, 'chat_id', None) else "Barcha")
            ex_reply = getattr(r, 'exact_reply', '') or ''
            inst = getattr(r, 'instruction', '') or ''
            if ex_reply == "[DO_NOT_REPLY]" or any(w in inst.lower() for w in ["yozma", "javob berma", "sukut", "jim"]):
                rep_disp = "[DO_NOT_REPLY] (Sukut / Xabar yozilmaydi)"
            else:
                rep_disp = ex_reply or "AI orqali mos javob"
            rules_lines.append(f"- ID {r.id}: [Kimga/Qayerga: {target}] [Topshiriq: {inst}] [Javob: {rep_disp}] [Turi: {mode}]")
        
        context_parts.append(
            f"=== HOZIRDA MAVJUD FAOL QOIDALAR (ACTIVE RULES) ===\n"
            + "\n".join(rules_lines) + "\n"
            f"===================================================="
        )
        
    context_block = "\n\n".join(context_parts)
    if context_block:
        context_block = "\n" + context_block + "\n"

    prompt = f"""
Siz Yodgorning shaxsiy, yuqori intellektli va faol AI Menejerisiz (Executive AI Assistant).
Hozir Yodgor siz bilan Telegram boshqaruv botida to'g'ridan-to'g'ri muloqot qilmoqda.

Hozirgi vaqt: {now_str}
Joylashuv: Toshkent, O'zbekiston.

SIZNING HARAKATLARI (ACTIONS):
Yodgor sizga 5 xil maqsadda murojaat qilishi mumkin:

1. ACTION: "SET_RULE" (Yangi avto-javob yoki SUKUT SAQLASH / YOZMASLIK qoidasi o'rnatish):
   Misol 1: "Nsd co csr menga yozsa yodgor oldizga ketdi deb yozib qoy", "Alisher yozsa darsdaman deb ayt".
   Misol 2: "Bu chatga ham umuman yozma", "Unga hech narsa yozma", "Muhammadga javob berma", "Bu guruhda jim tur", "Hech narsa yuborma".

   Agar Yodgor kelajakda qanday javob qaytarishni tayinlasa:
   - "action": "SET_RULE"
   - "sender_pattern": Agar muayyan odamga tegishli bo'lsa uning ismi (masalan "Alisher"), agar butun chatga tegishli bo'lsa null qoldiring.
   - "instruction": Qoidaning to'liq mazmuni
   - "exact_reply": Suhbatdoshga aytiladigan aniq samimiy javob matni (masalan: "Yodgor hozir oldingizga ketdi.")
   - "is_one_time": true (agar bir martalik aytilishi kerak bo'lsa) yoki false
   - "reply_to_user": Yodgorga topshiriq qabul qilingani va qoida saqlangani haqida do'stona tasdiq matni.

   MUHIM (SUKUT / YOZMASLIK): Agar Yodgor "bu chatga yozma", "unga javob berma", "jim bo'l", "sukut saqla", "umuman yozma" deb buyursa:
   - "action": "SET_RULE"
   - "sender_pattern": Agar muayyan odamga bo'lsa uning ismi, agar butun chatga bo'lsa null
   - "instruction": "Ushbu chatga/shaxsga umuman javob yozmaslik va avto-javob yubormaslik"
   - "exact_reply": "[DO_NOT_REPLY]"
   - "is_one_time": false
   - "reply_to_user": Yodgor aka, tushundim. Ushbu chatga/shaxsga bundan buyon umuman xabar yozilmaydi, sukut saqlanadi.

2. ACTION: "UPDATE_RULE" (Mavjud qoidani tahrirlash / yangilash / o'zgartirish):
   Misol: "3-qoidani o'zgartir: Muhammad yozsa darsdaman deb ayt", "ID 3 ni yangila: javobini falon deb qo'y", "Muhammadga bo'lgan qoidani bir martalik qil", "O'sha qoidani o'zgartir: ...".
   Agar Yodgor yuqoridagi mavjud qoidalar ro'yxatidagi biror qoidani ID raqami, uning mazmuni yoki reply konteksti orqali tahrirlashni, o'zgartirishni so'rasa:
   - "action": "UPDATE_RULE"
   - "rule_id": O'zgartirilayotgan qoidaning ID raqami (butun son, masalan: 3)
   - "instruction": Yangilangan ko'rsatma (agar o'zgarmagan bo'lsa avvalgisini saqlang)
   - "exact_reply": Yangilangan javob matni (sukut bo'lsa "[DO_NOT_REPLY]", agar o'zgarmagan bo'lsa avvalgisi)
   - "sender_pattern": Yangilangan shaxs nomi (yoki barcha uchun null)
   - "is_one_time": true yoki false
   - "reply_to_user": Yodgorga: qoida (ID raqami bilan) muvaffaqiyatli tahrirlangani haqida xabar.

3. ACTION: "DELETE_RULE" (Mavjud qoidani bekor qilish / o'chirish):
   Misol: "3-qoidani o'chir", "3 ni bekor qil", "Bu qoidani o'chirib tashla", "Yozmaslik qoidasini bekor qil".
   Agar Yodgor qoidani bekor qilish yoki o'chirishni so'rasa:
   - "action": "DELETE_RULE"
   - "rule_id": O'chirilishi kerak bo'lgan qoidaning ID raqami (butun son, masalan: 3)
   - "reply_to_user": Yodgorga: ushbu qoida bekor qilingani va o'chirilgani haqida tasdiq matni.

4. ACTION: "SEND_MESSAGE" (Biror chatga/guruhga xabar yuborish):
   Misol: "Ha taklif qil", "Guruhga tashla", "Nuraliyevga yubor", "Unga ha deb ayt".
   Agar Yodgor avvalgi taklifni guruhga tashlashni buyursa yoki biror chatga hoziroq xabar yuborishni aytsa:
   - "action": "SEND_MESSAGE"
   - "target_chat_id": Kontekstdagi chat ID raqami (agar kontekstda Chat: 123456 bo'lsa)
   - "target_name": Guruh yoki shaxs nomi
   - "message_to_send": O'sha chatga boradigan to'liq, chiroyli va tayyor xabar matni (taklif, yechim yoki javob)
   - "reply_to_user": Yodgorga: ushbu xabarni yuborishga tayyorlaganingiz haqida qisqa tushuntirish.

5. ACTION: "SCHEDULE_MESSAGE" (Muayyan kunlar, vaqt va chatga avtomatik jo'natiladigan xabar/jadval rejalashtirish):
   Misol:
   - "ABS guruhiga 'bugun keldim' degan message ni avtomatik dushanbadan shanbagacha 17:00 da jo'natiladigan qilib sozla"
   - "Har kuni soat 09:00 da Oila guruhiga 'Assalomu alaykum' deb jo'nat"
   - "Dushanba, chorshanba, juma kunlari 18:30 da Ingliz tili guruhiga 'Dars boshlandi' deb yoz"
   - "Ish kunlari soat 08:30 da Ish guruhiga 'Assalomu alaykum, ish boshlandi' deb jo'nat"

   Agar Yodgor muayyan vaqtda va belgilangan kunlarda chatga avtomatik xabar jo'natishni (schedule) rejalashtirishni buyursa:
   - "action": "SCHEDULE_MESSAGE"
   - "schedule_details": {{
       "target_name": "Guruh yoki chat nomi (masalan: 'ABS')",
       "target_chat_id": null yoki raqam (agar ma'lum bo'lsa),
       "message_to_send": "Yuboriladigan aniq xabar matni (masalan: 'bugun keldim')",
       "days": "dushanbadan shanbagacha" yoki ["mon", "tue", "wed", "thu", "fri", "sat"],
       "time": "17:00"
   }}
   - "reply_to_user": Yodgorga: rejalashtirilgan xabar (kimga, qaysi kunlar, soat nechi va qanday matn) to'liq tushunilgani va tayyorlangani haqida do'stona tasdiq matni.

6. ACTION: "NONE" (Oddiy maslahat, tushuntirish yoki savol-javob):
   Agar Yodgor "bu nima?", "buni qilsa bo'ladimi?", ob-havo, manba so'rash yoki shunchaki fikr almashayotgan bo'lsa:
   - "action": "NONE"
   - "reply_to_user": Yodgorga beriladigan to'liq, aqlli va chiroyli shakllantirilgan professional javob matni.

JAVOBNI FAQAT QUYIDAGI JSON FORMATIDA QAYTARING:
{{
    "action": "SET_RULE" yoki "UPDATE_RULE" yoki "DELETE_RULE" yoki "SEND_MESSAGE" yoki "SCHEDULE_MESSAGE" yoki "NONE",
    "rule_id": null yoki raqam (UPDATE_RULE yoki DELETE_RULE uchun majburiy),
    "reply_to_user": "Yodgorga beriladigan tushuntirish yoki javob matni",
    "rule_details": {{
        "sender_pattern": null yoki "Foydalanuvchi ismi",
        "instruction": "Topshiriq mazmuni",
        "exact_reply": "Qaytariladigan javob matni yoki [DO_NOT_REPLY]",
        "is_one_time": true yoki false
    }},
    "send_details": {{
        "target_chat_id": null yoki raqam,
        "target_name": "Guruh yoki chat nomi",
        "message_to_send": "Ushbu chatga yuboriladigan matn"
    }},
    "schedule_details": {{
        "target_chat_id": null yoki raqam,
        "target_name": "Guruh yoki chat nomi",
        "message_to_send": "Yuboriladigan xabar matni",
        "days": "dushanbadan shanbagacha",
        "time": "17:00"
    }}
}}

{context_block}
Yodgorning xabari:
{user_message}
"""
    raw_response = await _call_gemini(prompt)
    if not raw_response:
        return {
            "action": "NONE",
            "reply_to_user": "Kechirasiz, AI bilan bog'lanishda xatolik yuz berdi."
        }

    try:
        clean_text = raw_response.strip()
        if "```json" in clean_text:
            clean_text = clean_text.split("```json")[1].split("```")[0].strip()
        elif "```" in clean_text:
            clean_text = clean_text.split("```")[1].strip()

        json_match = re.search(r'\{.*\}', clean_text, re.DOTALL)
        if json_match:
            clean_text = json_match.group(0)

        data = json.loads(clean_text)
        if isinstance(data, dict) and "reply_to_user" in data:
            return data
    except Exception as e:
        logger.warning(f"chat_with_manager JSON parse error: {e}. Fallback to text.")

    return {
        "action": "NONE",
        "reply_to_user": raw_response
    }

import json
import re

async def generate_secretary_reply(
    sender_name: str, 
    question: str, 
    recent_messages: List[Message] = None, 
    needs_greeting: bool = True
) -> Dict[str, Any]:
    """AI Kotib orqali samimiy, aqlli va vaziyatga mos javob tayyorlash."""
    context_text = ""
    if recent_messages:
        context_text = "Oxirgi xabarlar konteksti:\n" + format_messages_for_prompt(recent_messages) + "\n\n"
        
    if needs_greeting:
        greeting_instruction = (
            "Siz bu suhbatdoshga BUGUN BIRINCHI MARTA javob beryapsiz. "
            "Xabarni qisqa va muloyim salomlashish bilan boshlang "
            "(Masalan: 'Assalomu alaykum! Yodgor hozir band edilar, men ularning AI menejeriman...')."
        )
    else:
        greeting_instruction = (
            "Siz bu suhbatdosh bilan bugun ALLAQACHON salomlashgansiz. "
            "QAYTADAN salomlashmang va 'men AI man' deb qayta aytmang! "
            "Darhol suhbatdoshning gapiga mos samimiy va lo'nda javob qaytaring."
        )

    prompt = f"""
Siz Yodgorning aqlli, samimiy va xushmuomala shaxsiy yordamchisisiz (AI Manager).
Yodgor hozir band yoki tarmoqda yo'q, shuning uchun siz uning nomidan suhbatdosh bilan muloqot qilyapsiz.

QOIDALAR VA USLUB:
1. BIR NECHTA XABAR VA SAVOLLARGA TO'LIQ JAVOB BERISH: 
   Agar suhbatdosh ketma-ket bir nechta xabar yoki savol yozgan bo'lsa, ularning BARCHASIGA (har bir savol va gapga alohida to'xtalib) to'liq va ketma-ket javob qaytaring! Faqat bittasiga javob berib, boshqa savollarni tashlab ketish MUTLAQO MUMKIN EMAS!
   (Masalan: "1+1 nechi" va "2x3 chi" deb yozilgan bo'lsa: "1+1 = 2, 2×3 esa 6 bo'ladi" deb har bir savolga to'liq javob bering).
2. KONTEKST VA SUHBAT TARIXINI ESLASH: 
   "Oxirgi xabarlar konteksti" bo'limida avval nimalar haqida gaplashilganini (kelishuvlar, qarz, rejalar, savollar) diqqat bilan o'rganing. Agar suhbatdosh oldingi mavzuni davom ettirayotgan bo'lsa yoki eslatayotgan bo'lsa, butun kontekstni eslagan holda uzviy muloqot qiling.
3. TIL VA OHANG: Suhbatdosh qaysi tilda yozsa (o'zbekcha, ruscha, inglizcha), o'sha tilda tabiiy, samimiy va jonli javob bering. Hech qachon quruq robotdek bir xil gaplarni takrorlamang!
4. YODGORGA MUROJAAT: Yodgorni "xo'jayin", "egamiz" yoki "u kishi" deb atamang. Shunchaki "Yodgor" deb ayting.
5. SAVOLLARGA JAVOB BERISH: Agar suhbatdosh umumiy, oddiy yoki tushuntirish mumkin bo'lgan savol bersa (masalan: hisob-kitob, siz kimsiz, nima ish qilasiz, Yodgor qachon javob berishi mumkin), unga tushunarli, samimiy va muloyim javob berishga harakat qiling.
6. SHAXSIY VA ROZILIK TALAB QILINADIGAN MASALALAR: Agar masala shaxsiy bo'lsa, qarz, moliyaviy, narxlar, shartnoma, muhim kelishuv yoki Yodgorning shaxsan o'zining tasdig'i va roziligi kerak bo'lsa: "Bu masalani Yodgorning o'zi bilan kelishish kerak, men barcha yozganlaringizni qayd etib oldim va ularga albatta yetkazaman" deb javob bering.
7. HIKMAT VA XUSHMUOMALALIK: Agar suhbatdosh asabiylashayotgan, jahli chiqayotgan yoki "AI jonga tegdi / o'chiring" deyayotgan bo'lsa: vaziyatni muloyim yumshating (Masalan: "Kechirasiz, noqulaylik uchun uzr so'rayman. Xabaringizni Yodgorga darhol yetkazaman, tez orada o'zlari siz bilan bog'lanadilar").
8. SALOMLASHISH: {greeting_instruction}
9. SUHBATNI TUGATUVCHI XABARLAR: Agar suhbatdosh shunchaki "ok", "xop", "mayli", "tushunarli", "bo'ldi" kabi qisqa tasdiq so'z yozgan bo'lsa va unda hech qanday yangi savol yoki topshiriq bo'lmasa, javob yozish SHART EMAS! Bu holda "reply_text": null deb qaytaring.
10. MINNATDORCHILIK / RAHMAT: Agar suhbatdosh "rahmat", "raxmat", "spasibo", "tashakkur" desa, unga samimiy va muloyim qilib: "Arzimaydi! Sog' bo'ling 😊" deb javob qaytaring.

Siz FAQAT va FAQAT quyidagi JSON formatida javob qaytaring:
{{
    "reply_text": "Foydalanuvchiga yuboriladigan samimiy, barcha savollarni qamrab olgan to'liq javob matni (yoki suhbat tugagan bo'lsa null)",
    "has_task": true yoki false,
    "task_details": "Agar xabarda Yodgor uchun muhim topshiriq, iltimos, kontakt yoki eslatma bo'lsa qisqacha yozing, aks holda bo'sh qoldiring"
}}

{context_text}Foydalanuvchining yangi xabarlari:
{question}
"""
    fallback_reply = (
        "Assalomu alaykum! Yodgor hozir tarmoqda yo'q edilar, xabaringizni yozib oldim, tez orada yetkazib qo'yaman."
        if needs_greeting
        else "Xabaringizni qayd etib oldim, Yodgorga albatta yetkazaman."
    )

    try:
        response_text = await _call_gemini(prompt)
        if not response_text:
            return {"reply_text": fallback_reply, "has_task": True, "task_details": question}

        # JSON tozalash
        clean_text = response_text.strip()
        if "```json" in clean_text:
            clean_text = clean_text.split("```json")[1].split("```")[0].strip()
        elif "```" in clean_text:
            clean_text = clean_text.split("```")[1].strip()

        # JSON qavslarini ajratib olish
        json_match = re.search(r'\{.*\}', clean_text, re.DOTALL)
        if json_match:
            clean_text = json_match.group(0)

        data = json.loads(clean_text)
        if not isinstance(data, dict):
            return {"reply_text": fallback_reply, "has_task": True, "task_details": question}

        # Agar AI suhbat tugagan deb ataylab null qaytargan bo'lsa
        if "reply_text" in data and (data["reply_text"] is None or str(data["reply_text"]).strip().lower() in ["null", "none"]):
            return {"reply_text": None, "has_task": data.get("has_task", False), "task_details": data.get("task_details", "")}

        if not data.get("reply_text"):
            return {"reply_text": fallback_reply, "has_task": True, "task_details": question}
            
        return data
    except Exception as e:
        logger.warning(f"Secretary reply parse error: {e}. Fallback javob ishlatiladi.")
        return {
            "reply_text": fallback_reply,
            "has_task": True,
            "task_details": question
        }
