from typing import Union

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

from src.utils.config import Config


class AdminFilter(BaseFilter):
    async def __call__(
        self, message: Union[Message, CallbackQuery], config: Config
    ) -> bool:
        user_id = message.from_user.id
        return user_id == config.bot.admin_id
