import sqlite3
import requests
from functools import lru_cache
from collections import defaultdict
import csv
import os
from datetime import datetime

DB_NAME = os.getenv("DB_PATH", "expenses.db")

# ─── Who's in the household ───────────────────────────────────────────────────
MEMBER_1 = "аня"
MEMBER_2 = "ваня"

# Custom split for "аренда" category (must sum to 1.0)
ARENDA_SPLIT = {MEMBER_1: 0.4, MEMBER_2: 0.6}


@lru_cache(maxsize=500)
def get_exchange_rate(base_currency: str, target_currency: str) -> float:
    api_key = os.getenv("CUR_KEY")
    if base_currency.lower() == target_currency.lower():
        return 1.0
    if not api_key:
        print("Warning: CUR_KEY not set, no conversion applied")
        return 1.0
    url = f"https://v6.exchangerate-api.com/v6/{api_key}/latest/{base_currency.upper()}"
    try:
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        data = response.json()
        if data.get("result") != "success":
            print(f"API error: {data.get('error-type', 'unknown')}")
            return 1.0
        return data.get("conversion_rates", {}).get(target_currency.upper(), 1.0)
    except requests.RequestException as e:
        print(f"Exchange rate request failed: {e}")
        return 1.0


def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS expenses (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                category  TEXT,
                amount    REAL,
                currency  TEXT,
                buyer     TEXT,
                payer     TEXT,
                split     INTEGER DEFAULT 2,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        try:
            c.execute("ALTER TABLE expenses ADD COLUMN split INTEGER DEFAULT 2")
        except sqlite3.OperationalError:
            pass
        conn.commit()


def add_expense(category, amount, currency, buyer, payer, timestamp, split=2):
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("""
            INSERT INTO expenses (category, amount, currency, buyer, payer, split, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (category, amount, currency, buyer, payer, split, timestamp))
        conn.commit()


def get_recent_expenses(limit: int = 10) -> list:
    """Returns last N expenses as list of dicts for display."""
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("""
            SELECT id, category, amount, currency, buyer, payer, timestamp
            FROM expenses
            ORDER BY timestamp DESC
            LIMIT ?
        """, (limit,))
        rows = c.fetchall()
    return [
        {"id": r[0], "category": r[1], "amount": r[2], "currency": r[3],
         "buyer": r[4], "payer": r[5], "timestamp": r[6]}
        for r in rows
    ]


def delete_expense_by_id(expense_id: int) -> bool:
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
        conn.commit()
        return c.rowcount > 0


def delete_expense(category, amount, currency, buyer, payer, date):
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("""
            DELETE FROM expenses
            WHERE id = (
                SELECT id FROM expenses
                WHERE category = ? AND amount = ? AND currency = ?
                  AND buyer = ? AND payer = ?
                  AND DATE(timestamp) = DATE(?)
                ORDER BY timestamp DESC
                LIMIT 1
            )
        """, (category, amount, currency, buyer, payer, date))
        conn.commit()
        return c.rowcount > 0


def _distribute_expense(category: str, amount: float, buyer: str, split: int) -> dict:
    members = [MEMBER_1, MEMBER_2]

    # Transfers don't affect spending — they only affect balance via paid/received
    if category == "перевод":
        return {}

    if buyer == "оба":
        if category.lower() == "аренда":
            return {m: amount * frac for m, frac in ARENDA_SPLIT.items()}
        else:
            per = amount / len(members)
            return {m: per for m in members}

    if buyer in members:
        return {buyer: amount}

    per = amount / max(split, 1)
    return {m: per for m in members}


def get_statistics(start_date: str, end_date: str, output_currency: str) -> str:
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("""
            SELECT timestamp, category, amount, currency, buyer, payer, COALESCE(split, 2)
            FROM expenses
            WHERE DATE(timestamp) BETWEEN ? AND ?
            ORDER BY timestamp ASC
        """, (start_date, end_date))
        rows = c.fetchall()

    if not rows:
        return "Нет данных за указанный период."

    spent = defaultdict(float)
    paid = defaultdict(float)
    received = defaultdict(float)  # for tracking transfers
    category_totals = defaultdict(float)

    for timestamp, category, amount, exp_currency, buyer, payer, split in rows:
        rate = get_exchange_rate(exp_currency, output_currency)
        converted = amount * rate

        if category == "перевод":
            # buyer = who received the money, payer = who sent it
            # This reduces what payer owes (or increases what buyer owes)
            if payer and payer not in ("неизвестно",):
                received[payer] += converted   # payer sent money → credit
            if buyer and buyer not in ("неизвестно",):
                received[buyer] -= converted   # buyer received money → debit
            continue

        category_totals[category] += converted
        if payer and payer not in ("оба", "общее", "неизвестно"):
            paid[payer] += converted

        distribution = _distribute_expense(category, converted, buyer, split)
        for member, share in distribution.items():
            spent[member] += share

    members = sorted(set(list(spent.keys()) + list(paid.keys()) + list(received.keys())))
    balances = {
        m: round(paid.get(m, 0) - spent.get(m, 0) + received.get(m, 0), 2)
        for m in members
    }

    lines = [f"📅 *Статистика {start_date} – {end_date}* ({output_currency.upper()})\n"]

    lines.append("💸 *Потрачено:*")
    for m in members:
        lines.append(f"  {m}: {spent[m]:.2f}")

    lines.append("\n💰 *Баланс* (+ = должны тебе, − = ты должен):")
    for m in members:
        bal = balances[m]
        sign = "+" if bal >= 0 else ""
        lines.append(f"  {m}: {sign}{bal:.2f}")

    creditors = {m: b for m, b in balances.items() if b > 0}
    debtors = {m: -b for m, b in balances.items() if b < 0}
    if creditors and debtors:
        lines.append("\n🔁 *Перевести:*")
        for debtor, debt in debtors.items():
            for creditor, credit in creditors.items():
                transfer = min(debt, credit)
                if transfer > 0.01:
                    lines.append(f"  {debtor} → {creditor}: {transfer:.2f} {output_currency.upper()}")

    lines.append("\n📊 *По категориям:*")
    for cat, total in sorted(category_totals.items(), key=lambda x: -x[1]):
        lines.append(f"  {cat}: {total:.2f}")

    return "\n".join(lines)


def export_csv():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM expenses ORDER BY timestamp ASC")
        rows = c.fetchall()

    filename = f"expenses_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    filepath = os.path.join(os.getcwd(), filename)
    with open(filepath, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "category", "amount", "currency", "buyer", "payer", "split", "timestamp"])
        writer.writerows(rows)
    return filepath