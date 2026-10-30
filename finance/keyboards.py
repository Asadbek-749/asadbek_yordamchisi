"""Inline keyboards and UI for Personal Finance Manager."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def pending_transaction_keyboard(transaction_id: int) -> InlineKeyboardMarkup:
    """Inline confirmation keyboard for a single pending transaction."""
    buttons = [
        [
            InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"fin:confirm:{transaction_id}"),
            InlineKeyboardButton(text="✏️ Tahrirlash", callback_data=f"fin:edit:{transaction_id}"),
            InlineKeyboardButton(text="❌ Bekor qilish", callback_data=f"fin:cancel:{transaction_id}"),
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def compound_pending_keyboard() -> InlineKeyboardMarkup:
    """Inline keyboard when multiple pending transactions are parsed simultaneously."""
    buttons = [
        [
            InlineKeyboardButton(text="✅ Hammasini tasdiqlash", callback_data="fin:confirm_all"),
            InlineKeyboardButton(text="❌ Bekor qilish", callback_data="fin:cancel_all"),
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def main_finance_keyboard() -> InlineKeyboardMarkup:
    """Main dashboard keyboard for /finance command."""
    buttons = [
        [
            InlineKeyboardButton(text="➕ Daromad", callback_data="fin:add_income"),
            InlineKeyboardButton(text="➖ Xarajat", callback_data="fin:add_expense"),
        ],
        [
            InlineKeyboardButton(text="📊 Bugun", callback_data="fin:today"),
            InlineKeyboardButton(text="📅 Hafta", callback_data="fin:week"),
            InlineKeyboardButton(text="📆 Oy", callback_data="fin:month"),
        ],
        [
            InlineKeyboardButton(text="🎯 Byudjet", callback_data="fin:budget"),
            InlineKeyboardButton(text="⏳ Pending", callback_data="fin:pending"),
        ],
        [
            InlineKeyboardButton(text="📈 Tahlil", callback_data="fin:analytics"),
            InlineKeyboardButton(text="⚙️ Sozlamalar", callback_data="fin:settings"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def back_to_finance_keyboard() -> InlineKeyboardMarkup:
    """Back button to return to the main finance menu."""
    buttons = [
        [
            InlineKeyboardButton(text="🔙 Moliya menyusi", callback_data="fin:main"),
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def edit_menu_keyboard(transaction_id: int) -> InlineKeyboardMarkup:
    """What to change on a pending transaction."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💰 Summa", callback_data=f"fin:editamt:{transaction_id}"),
                InlineKeyboardButton(text="📌 Kategoriya", callback_data=f"fin:editcat:{transaction_id}"),
            ],
            [InlineKeyboardButton(text="🔙 Orqaga", callback_data=f"fin:editback:{transaction_id}")],
        ]
    )


def category_choice_keyboard(transaction_id: int, categories: list) -> InlineKeyboardMarkup:
    """Category picker (two per row). callback_data stays far below Telegram's 64-byte limit."""
    rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(categories), 2):
        rows.append(
            [
                InlineKeyboardButton(text=c.display_name, callback_data=f"fin:setcat:{transaction_id}:{c.name}")
                for c in categories[i : i + 2]
            ]
        )
    rows.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data=f"fin:editback:{transaction_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def recurring_reminder_keyboard(recurring_id: int) -> InlineKeyboardMarkup:
    """One tap adds this month's recurring payment as an expense."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="➕ Xarajatga qo'shish", callback_data=f"fin:recadd:{recurring_id}"),
                InlineKeyboardButton(text="⏭ Keyinroq", callback_data=f"fin:recskip:{recurring_id}"),
            ]
        ]
    )
