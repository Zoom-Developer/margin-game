from aiogram.filters import BaseFilter
from aiogram.types import Message

from src.config import ADMIN_IDS


class IsAdminFilter(BaseFilter):

    async def __call__(self, message: Message) -> bool:
        return message.from_user.id in ADMIN_IDS