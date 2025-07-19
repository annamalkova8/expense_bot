# bot.py
from telegram import Update, InputFile
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from database import init_db, add_expense, get_statistics, delete_expense, export_csv
from dotenv import load_dotenv
from datetime import datetime
import os
from aiohttp import web

init_db()
load_dotenv()

TOKEN = os.getenv("TG_TOKEN")

def parse_expense(text):
    lines = text.strip().split('\n')
    if len(lines) != 6:
        return None
    return {
        'category': lines[1].lower(),
        'amount': float(lines[2]),
        'currency': lines[3].lower(),
        'buyer': lines[4].lower(),
        'payer': lines[5].lower()
    }


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Отправь данные о расходах в формате:\nрасходы\nкатегория\nсумма\nвалюта\nкто купил\nкто оплатил")

async def handle_expense(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = parse_expense(update.message.text)
    if not data:
        await update.message.reply_text("❌ Неверный формат. Пример:\nрасходы\nкатегория\nсумма\nвалюта\nкто купил\nкто оплатил")
        return
    timestamp = update.message.date.isoformat()
    add_expense(**data, timestamp=timestamp)
    await update.message.reply_text("✅ Расход добавлен!")



async def statistics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = update.message.text.strip().split("\n")
    if len(lines) != 4 or lines[0].lower() != "статистика":
        await update.message.reply_text("❌ Неверный формат. Пример:\nстатистика\n2024-07-01\n2024-07-31\nруб")
        return

    start_date, end_date, currency = lines[1], lines[2], lines[3].lower()
    stats_text = get_statistics(start_date, end_date, currency)
    await update.message.reply_text(stats_text)

async def delete_expenses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = update.message.text.strip().split('\n')
    if len(lines) not in [6, 7]:
        await update.message.reply_text(
            "❌ Неверный формат. Пример:\nудалить расходы\nкатегория\nсумма\nвалюта\nкто купил\nкто оплатил\n[дата в формате дд-мм-гггг (опционально)]"
        )
        return
    try:
        data = {
            'category': lines[1].lower(),
            'amount': float(lines[2]),
            'currency': lines[3].lower(),
            'buyer': lines[4].lower(),
            'payer': lines[5].lower(),
        }
        # Use provided date or fallback to message date
        if len(lines) == 7:
            try:
                # Parse dd-mm-yyyy to yyyy-mm-dd for DB
                date_obj = datetime.strptime(lines[6], "%d-%m-%Y")
                data['date'] = date_obj.date().isoformat()
            except ValueError:
                await update.message.reply_text("❌ Неверный формат даты. Используйте: дд-мм-гггг")
                return
        else:
            # Use date from Telegram message timestamp
            data['date'] = update.message.date.date().isoformat()

    except Exception:
        await update.message.reply_text("❌ Ошибка при чтении суммы или других данных. Проверьте формат.")
        return
    success = delete_expense(**data)
    if success:
        await update.message.reply_text("🗑️ Расход успешно удалён!")
    else:
        await update.message.reply_text("❌ Расход не найден.")

async def export_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file_path = export_csv()
    await update.message.reply_document(InputFile(file_path), filename="expenses.csv")

async def router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().lower()
    if text.startswith("расходы"):
        await handle_expense(update, context)
    elif text.startswith("удалить расходы"):
        await delete_expenses(update, context)
    elif text.startswith("статистика"):
        await statistics(update, context)
    elif text.startswith("все расходы"):
        await export_data(update, context)
    else:
        await update.message.reply_text("❔ Неизвестная команда. Используйте:\n- 'расходы'\n- 'удалить расходы'\n- 'статистика'\n- 'все расходы'")

if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, router))

    print("🚀 Bot running with polling...")
    app.run_polling()

