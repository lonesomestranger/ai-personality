import logging
from typing import Optional

from aiogram.types import Message

logger = logging.getLogger(__name__)


async def parse_chat_id(message: Message) -> Optional[int]:
    if message.forward_from:
        logger.debug(f"Parsed chat ID {message.forward_from.id} from forward_from")
        return message.forward_from.id
    if message.forward_from_chat:
        logger.debug(
            f"Parsed chat ID {message.forward_from_chat.id} from forward_from_chat"
        )
        return message.forward_from_chat.id
    if message.forward_sender_name:
        logger.warning("Cannot get ID from forwarded message (sender hidden).")
        return None

    text = message.text.strip()
    if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
        try:
            chat_id = int(text)
            logger.debug(f"Parsed numeric chat ID {chat_id} from message text")
            return chat_id
        except ValueError:
            logger.warning(
                f"Could not convert '{text}' to int despite passing digit checks."
            )
            return None

    logger.debug(f"Could not parse chat ID from message: '{text}'")
    return None
