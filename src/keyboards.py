from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.config import ROUNDS


def create_round_keyboard(round_id: int, asset_id: int, selected_pos: str | None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for position in ROUNDS[round_id - 1]:
        builder.add(InlineKeyboardButton(text=("✅ " if selected_pos == position.id else "") + position.name, callback_data=f"invest:{round_id}:{position.id}:{asset_id}"))
    return builder.adjust(2).as_markup()