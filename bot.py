import os
import re
from telegram import Update, InputFile
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from database import init_db, add_expense, get_statistics, delete_expense, export_csv
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()
init_db()

TOKEN = os.getenv("TG_TOKEN")

# ─── People in your household ─────────────────────────────────────────────────
# Key = any word form that might appear in a message → Value = canonical name
# These MUST match what's in database.py MEMBERS list
NAME_MAP = {
    # Аня
    "аня": "аня", "аней": "аня", "аню": "аня", "ане": "аня",
    "anna": "аня", "anya": "аня",
    # Ваня
    "ваня": "ваня", "ване": "ваня", "ваню": "ваня", "ваней": "ваня",
    "ваня": "ваня", "vanya": "ваня", "ivan": "ваня", "ваня": "ваня",
    # Self-reference — will be resolved to sender's name later if needed
    "я": "я",
}

# ─── Category normalization ───────────────────────────────────────────────────
CATEGORY_MAP = {
    # Food & groceries
    "еда": "еда", "продукты": "еда", "магазин": "еда", "супермаркет": "еда",
    "grocery": "еда", "groceries": "еда", "food": "еда", "supermarket": "еда",
    "пятёрочка": "еда", "пятерочка": "еда", "перекрёсток": "еда", "вкусвилл": "еда",
    # Cafe & restaurants
    "кафе": "кафе/рестораны", "ресторан": "кафе/рестораны", "бар": "кафе/рестораны",
    "кофе": "кафе/рестораны", "cafe": "кафе/рестораны", "restaurant": "кафе/рестораны",
    "coffee": "кафе/рестораны", "bar": "кафе/рестораны",
    "обед": "кафе/рестораны", "ужин": "кафе/рестораны", "завтрак": "кафе/рестораны",
    "перекус": "кафе/рестораны", "пицца": "кафе/рестораны", "суши": "кафе/рестораны",
    # Transport
    "транспорт": "транспорт", "такси": "транспорт", "метро": "транспорт",
    "автобус": "транспорт", "поезд": "транспорт", "трамвай": "транспорт",
    "taxi": "транспорт", "transport": "транспорт",
    "uber": "транспорт", "убер": "транспорт", "болт": "транспорт", "bolt": "транспорт",
    "яндекс такси": "транспорт", "каршеринг": "транспорт",
    # Accommodation
    "жильё": "жильё", "жилье": "жильё", "отель": "жильё", "хостел": "жильё",
    "airbnb": "жильё", "hotel": "жильё", "hostel": "жильё",
    "аренда": "аренда",  # kept separate — may have custom split in DB
    # Entertainment
    "развлечения": "развлечения", "кино": "развлечения", "музей": "развлечения",
    "концерт": "развлечения", "cinema": "развлечения", "museum": "развлечения",
    "театр": "развлечения", "выставка": "развлечения", "парк": "развлечения",
    # Shopping
    "шопинг": "шопинг", "одежда": "шопинг", "shopping": "шопинг",
    "clothes": "шопинг", "обувь": "шопинг", "косметика": "шопинг",
    # Health
    "здоровье": "здоровье", "аптека": "здоровье", "pharmacy": "здоровье",
    "health": "здоровье", "doctor": "здоровье", "врач": "здоровье",
    "лекарства": "здоровье", "анализы": "здоровье",
    # Utilities / subscriptions
    "коммуналка": "коммуналка", "интернет": "коммуналка", "связь": "коммуналка",
    "подписка": "подписка", "spotify": "подписка", "netflix": "подписка",
}

# ─── Currency normalization ───────────────────────────────────────────────────
CURRENCY_MAP = {
    "евро": "eur", "euro": "eur", "euros": "eur", "€": "eur", "евров": "eur",
    "рублей": "rub", "рубль": "rub", "рубли": "rub", "руб": "rub", "₽": "rub",
    "долларов": "usd", "доллар": "usd", "dollars": "usd", "dollar": "usd", "$": "usd",
    "крон": "czk", "крона": "czk", "kč": "czk", "czk": "czk",
    "злотых": "pln", "злотый": "pln", "zl": "pln", "pln": "pln",
    "фунт": "gbp", "фунтов": "gbp", "pounds": "gbp", "£": "gbp", "gbp": "gbp",
    "форинт": "huf", "форинтов": "huf", "huf": "huf",
}


def normalize_name(raw: str) -> str:
    return NAME_MAP.get(raw.lower().strip(), raw.lower().strip())


def normalize_category(raw: str) -> str:
    return CATEGORY_MAP.get(raw.lower().strip(), raw.lower().strip())


def normalize_currency(raw: str) -> str:
    return CURRENCY_MAP.get(raw.lower().strip(), raw.lower().strip())


# ─── Natural language parser ──────────────────────────────────────────────────

def parse_natural_expense(text: str) -> dict | None:
    """
    Parses messages like:
      'Аня оплатила еду 40 евро'
      'Ваня купил продукты на 1200 рублей'
      'мы потратили на кафе 80 евро'
      'Аня заплатила 35 евро за такси'
    Returns: {payer, buyer, category, amount, currency, split} or None
    """
    t = text.lower().strip()

    # 1. Amount — grab the first number
    amount_m = re.search(r'\b(\d+(?:[.,]\d+)?)\b', t)
    if not amount_m:
        return None
    amount = float(amount_m.group(1).replace(',', '.'))

    # 2. Currency — scan for known words/symbols
    currency = "eur"  # sensible default
    for word, code in CURRENCY_MAP.items():
        if re.search(r'\b' + re.escape(word) + r'\b', t):
            currency = code
            break

    # 3. Category — scan for known words
    category = "другое"
    # Sort by length descending to match longer phrases first
    for word in sorted(CATEGORY_MAP, key=len, reverse=True):
        if word in t:
            category = CATEGORY_MAP[word]
            break

    # 4. Who paid — verb-based patterns
    payer = None
    PAY_VERBS = r'(?:оплатил[аи]?|заплатил[аи]?|купил[аи]?|потратил[аи]?|взял[аи]?)'
    m = re.search(r'(\w+)\s+' + PAY_VERBS, t)
    if m:
        payer = normalize_name(m.group(1))

    if not payer:
        if re.search(r'\b(мы|вместе)\b', t):
            payer = "оба"
        else:
            payer = "неизвестно"

    buyer = payer  # by default payer = buyer (both used the thing)

    # 5. Default split by 2
    split = 2

    return {
        "payer": payer,
        "buyer": buyer,
        "category": category,
        "amount": amount,
        "currency": currency,
        "split": split,
    }


def format_confirmation(data: dict) -> str:
    per_person = data["amount"] / data["split"]
    return (
        f"✅ *Расход добавлен*\n\n"
        f"👤 Оплатил(а): *{data['payer']}*\n"
        f"🛍 Категория: {data['category']}\n"
        f"💰 Сумма: {data['amount']:.2f} {data['currency'].upper()}\n"
        f"➗ На каждого: {per_person:.2f} {data['currency'].upper()}"
    )


# ─── Handlers ─────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! 👋 Я трекер расходов.\n\n"
        "Просто пиши в свободной форме:\n"
        "• _Аня оплатила еду 40 евро_\n"
        "• _Ваня купил продукты 1200 рублей_\n"
        "• _мы потратили на кафе 80 евро_\n\n"
        "Или добавь вручную (6 строк):\n"
        "`расходы`\n`категория`\n`сумма`\n`валюта`\n`кто купил`\n`кто оплатил`\n\n"
        "Другие команды:\n"
        "• *статистика* — итоги за период\n"
        "• *все расходы* — выгрузить CSV\n"
        "• *удалить расходы* — удалить запись",
        parse_mode="Markdown"
    )


async def handle_natural_expense(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = parse_natural_expense(update.message.text.strip())
    if not data:
        await update.message.reply_text(
            "❌ Не могу разобрать. Попробуй: _Аня оплатила еду 40 евро_",
            parse_mode="Markdown"
        )
        return
    timestamp = update.message.date.isoformat()
    add_expense(
        category=data["category"],
        amount=data["amount"],
        currency=data["currency"],
        buyer=data["buyer"],
        payer=data["payer"],
        timestamp=timestamp,
        split=data["split"],
    )
    await update.message.reply_text(format_confirmation(data), parse_mode="Markdown")


async def handle_structured_expense(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = update.message.text.strip().split('\n')
    if len(lines) != 6:
        await update.message.reply_text(
            "❌ Нужно 6 строк:\nрасходы\nкатегория\nсумма\nвалюта\nкто купил\nкто оплатил"
        )
        return
    try:
        data = {
            "category": normalize_category(lines[1]),
            "amount": float(lines[2].replace(',', '.')),
            "currency": normalize_currency(lines[3]),
            "buyer": normalize_name(lines[4]),
            "payer": normalize_name(lines[5]),
            "split": 2,
        }
    except ValueError:
        await update.message.reply_text("❌ Ошибка: проверь сумму (должно быть число).")
        return
    timestamp = update.message.date.isoformat()
    add_expense(**data, timestamp=timestamp)
    await update.message.reply_text("✅ Расход добавлен!")


async def statistics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = update.message.text.strip().split("\n")
    if len(lines) != 4:
        await update.message.reply_text(
            "❌ Формат:\nстатистика\n2024-07-01\n2024-07-31\neur"
        )
        return
    _, start_date, end_date, currency_raw = lines
    stats_text = get_statistics(start_date, end_date, normalize_currency(currency_raw))
    await update.message.reply_text(stats_text)


async def delete_expenses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = update.message.text.strip().split('\n')
    if len(lines) not in [6, 7]:
        await update.message.reply_text(
            "❌ Формат:\nудалить расходы\nкатегория\nсумма\nвалюта\nкто купил\nкто оплатил\n[дд-мм-гггг — опционально]"
        )
        return
    try:
        data = {
            "category": normalize_category(lines[1]),
            "amount": float(lines[2].replace(',', '.')),
            "currency": normalize_currency(lines[3]),
            "buyer": normalize_name(lines[4]),
            "payer": normalize_name(lines[5]),
        }
        if len(lines) == 7:
            data["date"] = datetime.strptime(lines[6], "%d-%m-%Y").date().isoformat()
        else:
            data["date"] = update.message.date.date().isoformat()
    except Exception:
        await update.message.reply_text("❌ Ошибка при чтении данных. Проверь формат.")
        return
    if delete_expense(**data):
        await update.message.reply_text("🗑️ Расход удалён!")
    else:
        await update.message.reply_text("❌ Расход не найден.")


async def export_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file_path = export_csv()
    await update.message.reply_document(InputFile(file_path), filename="expenses.csv")


async def router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    lower = text.lower()

    if lower.startswith("расходы\n"):
        await handle_structured_expense(update, context)
    elif lower.startswith("удалить расходы"):
        await delete_expenses(update, context)
    elif lower.startswith("статистика"):
        await statistics(update, context)
    elif lower.startswith("все расходы"):
        await export_data(update, context)
    else:
        await handle_natural_expense(update, context)


if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, router))
    print("🚀 Bot running...")
    app.run_polling()
