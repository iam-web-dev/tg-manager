# Telegram Manager & AI Analyzer 🤖

Telegram chatlar, guruhlar va kanallarni kuzatish, boshqarish hamda **Google Gemini AI** yordamida tahlil qilish va xulosalar (summary) chiqarish uchun mo'ljallangan Telegram bot va userbot tizimi.

---

## 🚀 Asosiy imkoniyatlar

- **Telethon Userbot (Collector):** Shaxsiy akkauntingiz orqali chatlar, guruhlar va kanallardagi yangi xabarlarni avtomatik yig'ib boradi.
- **Aiogram 3 Boshqaruv Boti:** Admin uchun qulay tugmali interfeys orqali botni to'liq boshqarish imkoniyati.
- **Google Gemini AI Tahlili:** Xabarlarni umumlashtirish (summary), muhim voqealarni ajratib ko'rsatish va savollarga javob berish.
- **Rejali Xabarlar (Scheduler):** Belgilangan vaqtda avtomatik xabarlar yoki eslatmalar yuborish.
- **Asinxron Ma'lumotlar Bazasi:** SQLAlchemy 2.0 va aiosqlite (SQLite) orqali ma'lumotlarni xavfsiz va tez saqlash.

---

## 📁 Loyiha tuzilishi

```text
├── ai_service.py       # Google Gemini AI integratsiyasi
├── bot.py              # Aiogram 3 asosidagi admin bot interfeysi
├── collector.py        # Telethon orqali xabarlarni yig'uvchi modul
├── config.py           # Sozlamalar va konfiguratsiya (Pydantic Settings)
├── database.py         # SQLAlchemy modellari va DB operatsiyalari
├── main.py             # Asosiy ishga tushirish fayli
├── scheduler.py        # Rejalashtirilgan vazifalar va xabarlar
├── telegram_login.py   # Telethon sessiyasini birinchi marta faollashtirish
├── requirements.txt    # Kerakli Python kutubxonalari
├── .env.example        # Muhit o'zgaruvchilari namunasi
└── .gitignore          # Maxfiy fayllarni Git'dan chetlatish
```

---

## 🛠 O'rnatish va Ishga tushirish

### 1. Repozitoriyani klonlash
```bash
git clone https://github.com/iam-web-dev/tg-manager.git
cd tg-manager
```

### 2. Virtual muhitni yaratish va faollashtirish
```bash
python3 -m venv .venv
source .venv/bin/activate  # Linux / macOS
# yoki
.venv\Scripts\activate     # Windows
```

### 3. Kutubxonalarni o'rnatish
```bash
pip install -r requirements.txt
```

### 4. Konfiguratsiyani sozlash (.env)
`.env.example` faylidan nusxa olib, `.env` faylini yarating:
```bash
cp .env.example .env
```

`.env` faylini ochib, kerakli ma'lumotlarni kiriting:
- `BOT_TOKEN` — [@BotFather](https://t.me/BotFather) orqali olingan Telegram Bot tokeni.
- `API_ID` va `API_HASH` — [my.telegram.org](https://my.telegram.org) saytidan olingan Telegram API ma'lumotlari.
- `ADMIN_ID` — Sizning shaxsiy Telegram ID raqamingiz ([@userinfobot](https://t.me/userinfobot) orqali bilish mumkin).
- `GEMINI_API_KEY` — [Google AI Studio](https://aistudio.google.com/) dan olingan Gemini API kaliti.

### 5. Telegram akkauntga kirish (Sessiya yaratish)
Telethon sessiyasini yaratish uchun bir marta quyidagi buyruqni bajaring va telefon raqamingizga kelgan kodni kiriting:
```bash
python3 telegram_login.py
```
Bu buyruq natijasida `.session` fayli yaratiladi. *(Eslatma: session fayli va .env hech qachon Git'ga yuklanmasligi kerak!)*

### 6. Botni ishga tushirish
```bash
python3 main.py
```

Fon rejimida ishga tushirish uchun:
```bash
nohup python3 main.py >> bot.log 2>&1 &
```

---

## 🔒 Xavfsizlik

- `.env`, `*.session`, `*.db`, `bot.log` kabi shaxsiy va maxfiy ma'lumotlar `.gitignore` ga kiritilgan.
- Hech qachon o'zingizning `.session` faylingiz yoki `.env` faylingizni ommaviy joylarga tarqatmang!

---

## 📄 Litsenziya
Ushbu loyiha MIT litsenziyasi ostida taqdim etiladi.
