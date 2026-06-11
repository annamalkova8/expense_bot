import os
import re
from telegram import Update, InputFile
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from database import (
    init_db, add_expense, get_statistics, delete_expense,
    delete_expense_by_id, get_recent_expenses, export_csv
)
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()
init_db()

TOKEN = os.getenv("TG_TOKEN")

# ─── People ───────────────────────────────────────────────────────────────────
NAME_MAP = {
    "аня": "аня", "аней": "аня", "аню": "аня", "ане": "аня",
    "anna": "аня", "anya": "аня",
    "ваня": "ваня", "ване": "ваня", "ваню": "ваня", "ваней": "ваня",
    "vanya": "ваня", "ivan": "ваня",
    "я": "я",
}

# ─── Categories ───────────────────────────────────────────────────────────────
CATEGORY_MAP = {
    "еда": "еда", "продукты": "еда", "магазин": "еда", "супермаркет": "еда",
    "grocery": "еда", "groceries": "еда", "food": "еда", "supermarket": "еда",
    "пятёрочка": "еда", "пятерочка": "еда", "перекрёсток": "еда", "вкусвилл": "еда",
    "кафе": "кафе/рестораны", "ресторан": "кафе/рестораны", "бар": "кафе/рестораны",
    "кофе": "кафе/рестораны", "cafe": "кафе/рестораны", "restaurant": "кафе/рестораны",
    "coffee": "кафе/рестораны", "bar": "кафе/рестораны",
    "обед": "кафе/рестораны", "ужин": "кафе/рестораны", "завтрак": "кафе/рестораны",
    "перекус": "кафе/рестораны", "пицца": "кафе/рестораны", "суши": "кафе/рестораны",
    "транспорт": "транспорт", "такси": "транспорт", "метро": "транспорт",
    "автобус": "транспорт", "поезд": "транспорт", "трамвай": "транспорт",
    "taxi": "транспорт", "uber": "транспорт", "убер": "транспорт",
    "болт": "транспорт", "bolt": "транспорт", "каршеринг": "транспорт",
    "жильё": "жильё", "жилье": "жильё", "отель": "жильё", "хостел": "жильё",
    "airbnb": "жильё", "hotel": "жильё", "hostel": "жильё",
    "аренда": "аренда",
    "развлечения": "развлечения", "кино": "развлечения", "музей": "развлечения",
    "концерт": "развлечения", "театр": "развлечения", "выставка": "развлечения",
    "шопинг": "шопинг", "одежда": "шопинг", "shopping": "шопинг",
    "обувь": "шопинг", "косметика": "шопинг",
    "здоровье": "здоровье", "аптека": "здоровье", "pharmacy": "здоровье",
    "врач": "здоровье", "лекарства": "здоровье", "анализы": "здоровье",
    "коммуналка": "коммуналка", "интернет": "коммуналка", "связь": "коммуналка",
    "подписка": "подписка", "spotify": "подписка", "netflix": "подписка",
}

# ─── Currencies ───────────────────────────────────────────────────────────────
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


# ─── Parsers ──────────────────────────────────────────────────────────────────

def parse_transfer(text: str) -> dict | None:
    """
    Parses: 'Аня перевод Ване 100000 руб'
    Returns: {payer (sender), buyer (receiver), amount, currency} or None
    """
    t = text.lower().strip()
    if "перевод" not in t and "перевела" not in t and "перевёл" not in t and "перевел" not in t:
        return None

    amount_m = re.search(r'\b(\d+(?:[.,]\d+)?)\b', t)
    if not amount_m:
        return None
    amount = float(amount_m.group(1).replace(',', '.'))

    currency = "rub"
    for word, code in CURRENCY_MAP.items():
        if re.search(r'\b' + re.escape(word) + r'\b', t):
            currency = code
            break

    # Find sender and receiver around the transfer keyword
    TRANSFER_WORDS = r'(?:перевод|перевела|перевёл|перевел)'
    m = re.search(r'(\w+)\s+' + TRANSFER_WORDS + r'\s+(\w+)', t)
    if m:
        sender = normalize_name(m.group(1))
        receiver = normalize_name(m.group(2))
    else:
        return None

    return {
        "payer": sender,    # who sent
        "buyer": receiver,  # who received
        "category": "перевод",
        "amount": amount,
        "currency": currency,
        "split": 1,
    }


def parse_natural_expense(text: str) -> dict | None:
    t = text.lower().strip()

    amount_m = re.search(r'\b(\d+(?:[.,]\d+)?)\b', t)
    if not amount_m:
        return None
    amount = float(amount_m.group(1).replace(',', '.'))

    currency = "eur"
    for word, code in CURRENCY_MAP.items():
        if re.search(r'\b' + re.escape(word) + r'\b', t):
            currency = code
            break

    category = "другое"
    for word in sorted(CATEGORY_MAP, key=len, reverse=True):
        if word in t:
            category = CATEGORY_MAP[word]
            break

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

    return {
        "payer": payer,
        "buyer": payer,
        "category": category,
        "amount": amount,
        "currency": currency,
        "split": 2,
    }


def format_expense_confirmation(data: dict) -> str:
    if data["category"] == "перевод":
        return (
            f"💸 *Перевод записан*\n\n"
            f"От: *{data['payer']}*\n"
            f"Кому: *{data['buyer']}*\n"
            f"Сумма: {data['amount']:.2f} {data['currency'].upper()}"
        )
    per_person = data["amount"] / data["split"]
    return (
        f"✅ *Расход добавлен*\n\n"
        f"👤 Оплатил(а): *{data['payer']}*\n"
        f"🛍 Категория: {data['category']}\n"
        f"💰 Сумма: {data['amount']:.2f} {data['currency'].upper()}\n"
        f"➗ На каждого: {per_person:.2f} {data['currency'].upper()}"
    )


def format_expense_list(expenses: list) -> str:
    if not expenses:
        return "Нет расходов."
    lines = ["Последние расходы — напиши номер для удаления:\n"]
    for i, e in enumerate(expenses, 1):
        date = e["timestamp"][:10]
        lines.append(
            f"{i}. {date} | {e['category']} | "
            f"{e['amount']:.2f} {e['currency'].upper()} | "
            f"{e['payer']}"
        )
    return "\n".join(lines)


# ─── Handlers ─────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! 👋 Я трекер расходов.\n\n"
        "Просто пиши в свободной форме:\n"
        "• _Аня оплатила еду 40 евро_\n"
        "• _Ваня купил продукты 1200 рублей_\n"
        "• _мы потратили на кафе 80 евро_\n"
        "• _Аня перевод Ване 10000 руб_\n\n"
        "Команды:\n"
        "• *удалить* — показать список и удалить по номеру\n"
        "• *статистика* — итоги за период\n"
        "• *все расходы* — выгрузить CSV",
        parse_mode="Markdown"
    )


async def handle_expense_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    # First try transfer
    data = parse_transfer(text)
    if not data:
        data = parse_natural_expense(text)
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
    await update.message.reply_text(format_expense_confirmation(data), parse_mode="Markdown")


async def show_delete_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show last 10 expenses numbered for deletion."""
    expenses = get_recent_expenses(10)
    context.user_data["delete_list"] = expenses  # store for next message
    context.user_data["awaiting_delete"] = True
    await update.message.reply_text(format_expense_list(expenses))


async def statistics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = update.message.text.strip().split("\n")
    if len(lines) != 4:
        await update.message.reply_text("❌ Формат:\nстатистика\n2024-07-01\n2024-07-31\neur")
        return
    _, start_date, end_date, currency_raw = lines
    stats_text = get_statistics(start_date, end_date, normalize_currency(currency_raw))
    await update.message.reply_text(stats_text, parse_mode="Markdown")


async def export_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file_path = export_csv()
    await update.message.reply_document(InputFile(file_path), filename="expenses.csv")


async def router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    lower = text.lower()

    # Handle pending delete selection
    if context.user_data.get("awaiting_delete"):
        context.user_data["awaiting_delete"] = False
        expenses = context.user_data.get("delete_list", [])
        try:
            idx = int(text.strip()) - 1
            if 0 <= idx < len(expenses):
                expense_id = expenses[idx]["id"]
                if delete_expense_by_id(expense_id):
                    e = expenses[idx]
                    await update.message.reply_text(
                        f"🗑️ Удалено: {e['category']} {e['amount']:.2f} {e['currency'].upper()} ({e['timestamp'][:10]})"
                    )
                else:
                    await update.message.reply_text("❌ Не найдено.")
            else:
                await update.message.reply_text("❌ Неверный номер.")
        except ValueError:
            await update.message.reply_text("❌ Напиши число из списка.")
        return

    if lower.startswith("удалить"):
        await show_delete_list(update, context)
    elif lower.startswith("статистика"):
        await statistics(update, context)
    elif lower.startswith("все расходы"):
        await export_data(update, context)
    else:
        await handle_expense_input(update, context)


if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, router))
    print("🚀 Bot running...")
    app.run_polling()