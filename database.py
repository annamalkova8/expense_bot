import sqlite3
import requests
from functools import lru_cache
from collections import defaultdict
import csv
import os
from datetime import datetime

DB_NAME = "expenses.db"

@lru_cache(maxsize=500)
def get_exchange_rate(base_currency: str, target_currency: str) -> float:
    api_key = os.getenv("CUR_KEY")  # Read API key here
    if base_currency.lower() == target_currency.lower():
        return 1.0
    if not api_key:
        print("Warning: CUR_KEY environment variable not set")
        return 1.0

    url = f'https://v6.exchangerate-api.com/v6/{api_key}/latest/{base_currency.upper()}'
    try:
        response = requests.get(url)
        response.raise_for_status()  # HTTP errors will raise here
        data = response.json()

        if data.get('result') != 'success':
            print(f"API error: {data.get('error-type', 'Unknown error')}")
            return 1.0

        rates = data.get('conversion_rates', {})
        return rates.get(target_currency.upper(), 1.0)

    except requests.RequestException as e:
        print(f"Request failed: {e}")
        return 1.0

def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT,
                amount REAL,
                currency TEXT,
                buyer TEXT,
                payer TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()


def add_expense(category, amount, currency, buyer, payer, timestamp):
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("""
            INSERT INTO expenses (category, amount, currency, buyer, payer, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (category, amount, currency, buyer, payer, timestamp))
        conn.commit()

def get_statistics(start_date, end_date, output_currency):
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("""
            SELECT timestamp, category, amount, currency, buyer, payer
            FROM expenses
            WHERE DATE(timestamp) BETWEEN ? AND ?
        """, (start_date, end_date))
        rows = c.fetchall()

    if not rows:
        return "Нет данных за указанный период."

    # Track raw spent amounts
    spent_raw = defaultdict(float)
    paid_by = defaultdict(float)
    category_totals = defaultdict(float)

    for timestamp, category, amount, expense_currency, buyer, payer in rows:
        rate = get_exchange_rate(expense_currency, output_currency)
        converted_amount = amount * rate

        spent_raw[buyer] += converted_amount
        paid_by[payer] += converted_amount
        category_totals[category] += converted_amount

    # Calculate how much each person spent including half of 'оба'
    both_amount = spent_raw.get('оба', 0)
    spent = defaultdict(float)

    for person in spent_raw:
        if person == 'оба':
            # 'оба' amount only counted for 'оба'
            spent['оба'] = both_amount
        elif person in ['аня', 'ваня']:
            # Add half of 'оба' amount to аня and ваня
            spent[person] = spent_raw[person] + both_amount / 2
        else:
            # Other buyers, if any
            spent[person] = spent_raw[person]

    # Include аня and ваня if they didn't have any spending but 'оба' is present
    if 'оба' in spent_raw:
        if 'аня' not in spent:
            spent['аня'] = both_amount / 2
        if 'ваня' not in spent:
            spent['ваня'] = both_amount / 2

    # Calculate balances = paid - spent
    members = set(spent.keys()).union(paid_by.keys())
    balances = {person: paid_by.get(person, 0) - spent.get(person, 0) for person in members}

    # Build the report string
    result = [f"📅 Статистика за {start_date} – {end_date} ({output_currency.upper()}):\n"]

    result.append("💸 Потрачено (с учётом половины от 'оба'):")
    for person in sorted(spent.keys()):
        result.append(f"{person}: {round(spent[person], 2)}")

    result.append("\n💰 Баланс (кто сколько должен/получил):")
    for person in sorted(balances.keys()):
        bal = balances[person]
        sign = "+" if bal >= 0 else ""
        result.append(f"{person}: {sign}{round(bal, 2)}")

    result.append("\n📊 Категории расходов:")
    for cat, total in category_totals.items():
        result.append(f"{cat}: {round(total, 2)}")

    return "\n".join(result)


def delete_expense(category, amount, currency, buyer, payer, date):
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("""
            DELETE FROM expenses
            WHERE id = (
                SELECT id FROM expenses
                WHERE category = ? AND amount = ? AND currency = ? AND buyer = ? AND payer = ?
                AND DATE(timestamp) = DATE(?)
                ORDER BY timestamp DESC
                LIMIT 1
            )
        """, (category, amount, currency, buyer, payer, date))
        conn.commit()
        return c.rowcount > 0


def export_csv():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM expenses ORDER BY timestamp ASC")
        rows = c.fetchall()

    filename = f"expenses_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    filepath = os.path.join(os.getcwd(), filename)
    
    with open(filepath, mode='w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        # Write header
        writer.writerow(['id', 'category', 'amount', 'currency', 'buyer', 'payer', 'timestamp'])
        writer.writerows(rows)
    
    return filepath
