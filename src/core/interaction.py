import asyncio
import logging
import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from aiogram import Bot
from telethon import TelegramClient, events
from telethon.errors import (
    ChatWriteForbiddenError,
    FloodWaitError,
    UserIsBlockedError,
)
from telethon.errors.rpcerrorlist import ChatAdminRequiredError
from telethon.tl.types import (
    Channel,
    Chat,
    Message,
    PeerChannel,
    PeerChat,
    PeerUser,
    User,
)

from src.core import data_storage
from src.core.ai_module import AIModule
from src.core.elo_calculator import EloCalculator
from src.utils.config import Config, SettingsManager

logger = logging.getLogger(__name__)

CONTEXT_MESSAGE_COUNT = 10
GROUP_CONTEXT_MESSAGE_COUNT = 30
INITIATION_CHECK_INTERVAL_SECONDS = 60 * 60
MIN_TIME_BEFORE_INITIATION_HOURS = 12
MIN_INTERVAL_BETWEEN_AI_INITIATION = timedelta(days=1)
MAX_CONCURRENT_RESPONSES = 5
AI_DETECTION_KEYWORDS = [
    "ты бот",
    "ты ии",
    "ты ai",
    "автоответчик",
]
AI_DETECTION_ATTEMPTS = 3


class InteractionModule:
    def __init__(
        self,
        client: TelegramClient,
        bot_instance: Bot,
        ai_module: AIModule,
        settings_manager: SettingsManager,
        elo_calculator: EloCalculator,
        config: Config,
        data_dir: Path,
    ):
        self.client = client
        self.bot = bot_instance
        self.ai_module = ai_module
        self.settings_manager = settings_manager
        self.elo_calculator = elo_calculator
        self.config = config
        self.data_dir = data_dir

        self._me_id: Optional[int] = None
        self._admin_id: int = config.bot.admin_id

        self._is_running: bool = False
        self._initiation_task: Optional[asyncio.Task] = None
        self._response_semaphore = asyncio.Semaphore(MAX_CONCURRENT_RESPONSES)

        self._group_message_counters: Dict[int, int] = {}
        self._ai_detection_counters: Dict[int, int] = {}
        self._cooldown_until: Dict[int, datetime] = {}
        self._last_initiated_time: Dict[int, datetime] = {}

    async def set_client_ready(self, user_id: int):
        self._me_id = user_id
        await self.settings_manager.set("telethon_user_id", user_id)
        logger.info(f"InteractionModule ready. Operating as user ID: {self._me_id}")
        if self.settings_manager.is_persona_active():
            await self.start_persona()

    def is_active(self) -> bool:
        return self.settings_manager.is_persona_active()

    async def start_persona(self):
        if self._is_running:
            logger.warning("Persona is already running.")
            return
        if not self._me_id:
            logger.error(
                "Cannot start persona: Telethon client not ready (me_id is None)."
            )
            await self.settings_manager.set("persona_active", False)
            return

        await self.settings_manager.set("persona_active", True)
        self._is_running = True
        self._initiation_task = asyncio.create_task(self._initiation_loop())
        logger.info(
            "Digital persona activated. Listening for messages and checking for initiation opportunities."
        )

    async def stop_persona(self):
        if not self._is_running and not self.settings_manager.is_persona_active():
            return

        await self.settings_manager.set("persona_active", False)
        self._is_running = False

        if self._initiation_task:
            self._initiation_task.cancel()
            try:
                await self._initiation_task
            except asyncio.CancelledError:
                logger.info("Initiation task cancelled.")
            self._initiation_task = None

        self._group_message_counters.clear()
        self._ai_detection_counters.clear()
        self._cooldown_until.clear()
        self._last_initiated_time.clear()
        logger.info("Digital persona deactivated.")

    async def add_event_handlers(self):
        if not self.client:
            logger.error(
                "Cannot add event handlers: Telethon client is not initialized."
            )
            return
        self.client.add_event_handler(
            self._handle_new_message, events.NewMessage(incoming=True)
        )
        logger.info("Added NewMessage event handler.")

    async def _handle_new_message(self, event: events.NewMessage.Event):
        log_prefix = f"[_handle_new_message {event.chat_id}]"
        logger.debug(
            f"{log_prefix} Received event. MsgID: {event.message.id}, SenderID: {event.message.sender_id}"
        )

        message: Message = event.message
        chat_id = event.chat_id
        sender_id = message.sender_id

        if not self._is_running or not self.is_active():
            logger.debug(f"{log_prefix} Returning: Persona not running/active.")
            return

        if not self._me_id:
            logger.warning(
                f"{log_prefix} Returning: Self ID (_me_id) is not set. Cannot process message."
            )
            return
        if not sender_id or sender_id == self._me_id:
            logger.debug(
                f"{log_prefix} Returning: No sender_id or message from self ({sender_id})."
            )
            return

        if chat_id in self.settings_manager.get_excluded_chats():
            logger.debug(f"{log_prefix} Returning: Chat is in excluded list.")
            return

        now_utc = datetime.now(timezone.utc)
        cooldown_active = False
        if chat_id in self._cooldown_until:
            if now_utc < self._cooldown_until[chat_id]:
                logger.debug(
                    f"{log_prefix} Returning: On AI detection cooldown until {self._cooldown_until[chat_id]}."
                )
                cooldown_active = True
                return
            else:
                logger.info(f"{log_prefix} AI detection cooldown expired.")
                self._cooldown_until.pop(chat_id, None)
                self._ai_detection_counters.pop(chat_id, None)

        is_group = isinstance(event.chat, (Chat, Channel))
        if message.text and not cooldown_active:
            try:
                ai_detected_and_reacted = await self._check_ai_detection(
                    chat_id, message
                )
                if ai_detected_and_reacted:
                    logger.debug(
                        f"{log_prefix} Returning: AI detection triggered response/cooldown."
                    )
                    return
            except Exception as e_ai_check:
                logger.error(
                    f"{log_prefix} Error during _check_ai_detection: {e_ai_check}",
                    exc_info=True,
                )

        logger.debug(f"{log_prefix} Passed initial checks. Is group: {is_group}")

        acquired_semaphore = False
        try:
            logger.debug(f"{log_prefix} Attempting to acquire response semaphore...")
            async with asyncio.timeout(60):
                async with self._response_semaphore:
                    acquired_semaphore = True
                    logger.info(f"{log_prefix} Response semaphore acquired.")

                    if is_group:
                        self._group_message_counters[chat_id] = (
                            self._group_message_counters.get(chat_id, 0) + 1
                        )
                        limit = self.settings_manager.get_group_reply_frequency()
                        should_reply_group = (
                            self._group_message_counters.get(chat_id, 0) >= limit
                        )
                        logger.debug(
                            f"{log_prefix} Group check: Counter={self._group_message_counters.get(chat_id, 0)}, Limit={limit}, ShouldReply={should_reply_group}"
                        )

                        if should_reply_group:
                            logger.info(
                                f"{log_prefix} Group reply condition met. Calling response generation."
                            )
                            await self._generate_and_send_response(
                                chat_id, message, is_group=True
                            )
                            self._group_message_counters[chat_id] = 0
                        else:
                            logger.debug(
                                f"{log_prefix} Group counter below limit. No action."
                            )
                            try:
                                logger.debug(
                                    f"{log_prefix} Saving non-reply group message to history."
                                )
                                sender_type, _ = await self._get_sender_info(message)
                                formatted_data, _ = (
                                    self.data_collector._format_message_data(
                                        message, sender_type
                                    )
                                )
                                if formatted_data:
                                    asyncio.create_task(
                                        data_storage.append_message_to_history(
                                            chat_id, self.data_dir, formatted_data
                                        )
                                    )
                                else:
                                    logger.debug(
                                        f"{log_prefix} Message not suitable for formatting/storage (MsgID: {message.id})."
                                    )

                            except Exception as e_save_group:
                                logger.error(
                                    f"{log_prefix} Failed to save non-reply group message to history: {e_save_group}",
                                    exc_info=True,
                                )

                    else:
                        logger.debug(f"{log_prefix} Processing as private chat.")
                        if message.text or message.photo:
                            logger.info(
                                f"{log_prefix} Private chat message is text/photo. Calling _generate_and_send_response..."
                            )
                            await self._generate_and_send_response(
                                chat_id, message, is_group=False
                            )
                            logger.info(
                                f"{log_prefix} Returned from _generate_and_send_response call."
                            )
                        else:
                            logger.debug(
                                f"{log_prefix} Private message type not supported for reply (MsgID: {message.id}). Saving to history."
                            )
                            try:
                                sender_type, _ = await self._get_sender_info(message)
                                formatted_data, _ = (
                                    self.data_collector._format_message_data(
                                        message, sender_type
                                    )
                                )
                                if formatted_data:
                                    asyncio.create_task(
                                        data_storage.append_message_to_history(
                                            chat_id, self.data_dir, formatted_data
                                        )
                                    )
                                else:
                                    logger.debug(
                                        f"{log_prefix} Private message not suitable for formatting/storage (MsgID: {message.id})."
                                    )
                            except Exception as e_save_priv:
                                logger.error(
                                    f"{log_prefix} Failed to save non-reply private message to history: {e_save_priv}",
                                    exc_info=True,
                                )

                    logger.info(
                        f"{log_prefix} Reached end of semaphore block (will release)."
                    )

        except asyncio.TimeoutError:
            logger.error(
                f"{log_prefix} Timeout acquiring response semaphore. Skipping message processing."
            )
            acquired_semaphore = False
        except Exception as e_sema:
            logger.error(
                f"{log_prefix} Exception occurred within semaphore block or during acquire/timeout: {e_sema}",
                exc_info=True,
            )
        finally:
            logger.debug(
                f"{log_prefix} FINALLY block reached. Semaphore was acquired: {acquired_semaphore}"
            )

    async def _get_sender_info(self, message: Message) -> tuple[str, int | None]:
        if not self._me_id:
            logger.error("Cannot get sender info: self._me_id is not set.")
            return "Unknown", None

        sender_id = message.sender_id
        sender_type = "Unknown"

        if sender_id:
            if sender_id == self._me_id:
                sender_type = "You"
            elif isinstance(message.peer_id, User) or isinstance(
                message.peer_id, PeerUser
            ):
                sender_type = "Contact"
            elif isinstance(message.peer_id, (Chat, Channel, PeerChat, PeerChannel)):
                if isinstance(message.sender, User):
                    sender_type = f"User_{sender_id}"
                elif isinstance(message.sender, Channel):
                    sender_type = f"Channel_{sender_id}"
                else:
                    sender_type = f"Peer_{sender_id}"
            else:
                sender_type = f"Sender_{sender_id}"
        elif isinstance(message.sender, (Chat, Channel)):
            sender_id = message.sender.id
            sender_type = f"Peer_{sender_id}"

        return sender_type, sender_id

    async def _check_ai_detection(self, chat_id: int, message: Message) -> bool:
        text_lower = message.text.lower()
        detected = any(keyword in text_lower for keyword in AI_DETECTION_KEYWORDS)

        if detected:
            detection_count = self._ai_detection_counters.get(chat_id, 0) + 1
            self._ai_detection_counters[chat_id] = detection_count
            detected_keywords = [k for k in AI_DETECTION_KEYWORDS if k in text_lower]
            logger.warning(
                f"Potential AI detection in chat {chat_id} (Attempt {detection_count}). Keywords: {detected_keywords}"
            )

            if detection_count <= AI_DETECTION_ATTEMPTS:
                logger.info(
                    f"Generating denial/joke response for AI detection attempt {detection_count} in chat {chat_id}"
                )
                denial_prompt = (
                    "Тебя только что обвинили в том, что ты бот или ИИ (использовали слова типа: "
                    f"{detected_keywords}). "
                    "Твоя задача - максимально естественно и в твоем обычном стиле отшутиться, "
                    "перевести тему или сделать вид, что не понял(а) намека. НЕ признавайся. "
                    "Придумай короткий ответ."
                )
                base_instructions = await self._get_base_instructions()
                if base_instructions:
                    full_denial_prompt = f"{base_instructions}\n\n{denial_prompt}"
                    sent_ok = False
                    response_text = await self.ai_module.generate_response(
                        chat_id=chat_id,
                        base_instructions=full_denial_prompt,
                        dialog_context=[],
                        incoming_message_text=message.text,
                    )
                    if response_text:
                        sent_ok = await self._send_message_with_delay(
                            chat_id, response_text
                        )
                    else:
                        generic_reply = random.choice(
                            self.settings_manager.get_generic_error_replies()
                        )
                        sent_ok = await self._send_message_with_delay(
                            chat_id, generic_reply
                        )

                    if not sent_ok:
                        logger.warning(
                            f"Could not send AI denial/generic reply to chat {chat_id} (likely due to permissions or other send error)."
                        )
                else:
                    logger.error(
                        "Cannot generate denial response: failed to load base instructions."
                    )

                    generic_reply = random.choice(
                        self.settings_manager.get_generic_error_replies()
                    )

                    sent_ok = await self._send_message_with_delay(
                        chat_id, generic_reply
                    )
                    if not sent_ok:
                        logger.warning(
                            f"Could not send generic reply (no base instructions) to chat {chat_id} (likely due to permissions or other send error)."
                        )
            else:
                logger.warning(
                    f"AI detection suspicion threshold exceeded ({AI_DETECTION_ATTEMPTS} attempts) for chat {chat_id}. Initiating cooldown."
                )
                cooldown_config = (
                    self.settings_manager.get_ai_detection_cooldown_hours()
                )
                cooldown_hours = random.uniform(
                    cooldown_config.get("min", 2), cooldown_config.get("max", 24)
                )
                cooldown_until = datetime.now(timezone.utc) + timedelta(
                    hours=cooldown_hours
                )
                self._cooldown_until[chat_id] = cooldown_until

                chat_info = f"{chat_id}"
                sender_info = f"{message.sender_id}"
                try:
                    chat_entity = await self.client.get_entity(chat_id)
                    if isinstance(chat_entity, User):
                        chat_info = (
                            f"{getattr(chat_entity, 'first_name', '')} {getattr(chat_entity, 'last_name', '')}".strip()
                            or chat_info
                        )
                    else:
                        chat_info = f"{getattr(chat_entity, 'title', None) or getattr(chat_entity, 'username', None) or chat_id}"

                    if message.sender:
                        sender_entity = message.sender
                        sender_display = f"{getattr(sender_entity, 'first_name', '')} {getattr(sender_entity, 'last_name', '')}".strip()
                        if not sender_display:
                            sender_display = (
                                f"@{getattr(sender_entity, 'username', None)}"
                            )
                        if sender_display == "@None":
                            sender_display = None
                        sender_info = sender_display or f"{message.sender_id}"

                except Exception as e_get_entity:
                    logger.warning(
                        f"Could not get entity details for notification in chat {chat_id}: {e_get_entity}"
                    )

                notification_text = (
                    f"⚠️ Обнаружено подозрение на раскрытие ИИ!\n"
                    f"Чат: {chat_info} (ID: {chat_id})\n"
                    f"Пользователь: {sender_info} (ID: {message.sender_id})\n"
                    f"Сообщение: '{message.text}'\n"
                    f"Личность переведена в режим молчания в этом чате до: {cooldown_until.strftime('%Y-%m-%d %H:%M:%S UTC')}"
                )
                try:
                    await self.bot.send_message(self._admin_id, notification_text)
                    logger.info(
                        f"Sent AI detection notification to admin ({self._admin_id}) for chat {chat_id}."
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to send AI detection notification to admin ({self._admin_id}): {e}"
                    )

                logger.info(
                    f"AI detection cooldown started for chat {chat_id} until {cooldown_until}."
                )

            return True

        return False

    async def _generate_and_send_response(
        self, chat_id: int, incoming_message: Message, is_group: bool
    ):
        start_time = time.monotonic()
        logger.info(
            f"[_generate_and_send_response {chat_id}] Starting processing. Is group: {is_group}"
        )
        processed_successfully = False
        try:
            context_limit = (
                GROUP_CONTEXT_MESSAGE_COUNT if is_group else CONTEXT_MESSAGE_COUNT
            )
            logger.debug(
                f"[_generate_and_send_response {chat_id}] Attempting to load chat history..."
            )
            history_data = await data_storage.load_chat_history(chat_id, self.data_dir)
            logger.debug(
                f"[_generate_and_send_response {chat_id}] History loaded. {len(history_data.get('messages', []))} messages."
            )

            dialog_context = history_data["messages"][-context_limit:]
            logger.debug(
                f"[_generate_and_send_response {chat_id}] Using last {len(dialog_context)} messages for context."
            )

            logger.debug(
                f"[_generate_and_send_response {chat_id}] Getting base instructions..."
            )
            base_instructions = await self._get_base_instructions()
            if not base_instructions:
                logger.error(
                    f"[_generate_and_send_response {chat_id}] Cannot generate response: base instructions are missing."
                )
                return
            logger.debug(
                f"[_generate_and_send_response {chat_id}] Base instructions obtained."
            )

            image_bytes: Optional[bytes] = None
            mime_type: Optional[str] = None
            if incoming_message.photo:
                logger.debug(
                    f"[_generate_and_send_response {chat_id}] Incoming message has photo. Attempting download..."
                )
                try:
                    image_bytes = await self.client.download_media(
                        incoming_message.photo, file=bytes
                    )
                    mime_type = "image/jpeg"
                    logger.debug(
                        f"[_generate_and_send_response {chat_id}] Downloaded image ({len(image_bytes)} bytes, type: {mime_type}) for AI input."
                    )
                except Exception as e:
                    logger.error(
                        f"[_generate_and_send_response {chat_id}] Failed to download photo for AI input: {e}"
                    )
                    return
            logger.debug(
                f"[_generate_and_send_response {chat_id}] Calling AI module generate_response..."
            )
            response_text = await self.ai_module.generate_response(
                chat_id=chat_id,
                base_instructions=base_instructions,
                dialog_context=dialog_context,
                incoming_message_text=incoming_message.text,
                incoming_image_bytes=image_bytes,
                image_mime_type=mime_type if mime_type else "image/jpeg",
            )
            logger.debug(
                f"[_generate_and_send_response {chat_id}] AI module returned response (is None: {response_text is None})."
            )

            if response_text:
                logger.debug(
                    f"[_generate_and_send_response {chat_id}] Attempting to send generated response via _send_message_with_delay..."
                )
                sent_ok = await self._send_message_with_delay(chat_id, response_text)
                processed_successfully = sent_ok
                if sent_ok:
                    logger.debug(
                        f"[_generate_and_send_response {chat_id}] _send_message_with_delay indicates success."
                    )
                else:
                    logger.error(
                        f"[_generate_and_send_response {chat_id}] _send_message_with_delay indicates failure."
                    )

            else:
                logger.warning(
                    f"[_generate_and_send_response {chat_id}] AI failed to generate response. Sending generic reply."
                )
                logger.debug(
                    f"[_generate_and_send_response {chat_id}] Attempting to send generic reply via _send_message_with_delay..."
                )
                sent_ok = await self._send_message_with_delay(
                    chat_id,
                    random.choice(self.settings_manager.get_generic_error_replies()),
                )
                processed_successfully = sent_ok
                if sent_ok:
                    logger.debug(
                        f"[_generate_and_send_response {chat_id}] Generic reply sent successfully."
                    )
                else:
                    logger.error(
                        f"[_generate_and_send_response {chat_id}] Failed to send generic reply."
                    )

        except FileNotFoundError:
            logger.warning(
                f"[_generate_and_send_response {chat_id}] History file not found when generating response. Cannot provide context."
            )
            processed_successfully = False
        except Exception as e:
            logger.error(
                f"[_generate_and_send_response {chat_id}] Unhandled exception in try block: {e}",
                exc_info=True,
            )
            processed_successfully = False
        finally:
            end_time = time.monotonic()
            logger.info(
                f"[_generate_and_send_response {chat_id}] FINALLY block reached. Processed successfully: {processed_successfully}. Time: {end_time - start_time:.2f}s."
            )

    async def _resolve_entity(self, chat_id: int):
        try:
            entity = await self.client.get_entity(chat_id)
            logger.debug(f"Successfully resolved/found entity for {chat_id}")
            return True
        except ValueError:
            logger.error(
                f"Could not resolve entity for {chat_id} even with get_entity(). User might be inaccessible."
            )
            return False
        except Exception as e:
            logger.error(f"Error resolving entity for {chat_id}: {e}", exc_info=True)
            return False

    async def _save_sent_message_task(self, chat_id: int, message_data: Dict[str, Any]):
        try:
            await data_storage.append_message_to_history(
                chat_id, self.data_dir, message_data
            )
        except Exception as e:
            msg_id = message_data.get("message_id", "N/A")
            logger.error(
                f"[_save_sent_message_task {chat_id}-{msg_id}] Error saving sent message in background task: {e}",
                exc_info=True,
            )

    async def _send_message_with_delay(self, chat_id: int, text: str) -> bool:
        if not text:
            logger.warning(f"Attempted to send empty message to chat {chat_id}.")
            return False

        is_known_entity = await self._resolve_entity(chat_id)
        if not is_known_entity:
            logger.error(
                f"[_send_message_with_delay {chat_id}] Could not resolve entity. Aborting send."
            )
            return False

        sent_message = None
        delay = random.uniform(0.5, 2.5)
        send_successful = False

        try:
            typing_context = None
            try:
                typing_context = self.client.action(chat_id, "typing")
                async with typing_context:
                    logger.debug(
                        f"Set typing status for chat {chat_id}, sleeping for {delay:.2f}s"
                    )
                    await asyncio.sleep(delay)
            except ChatAdminRequiredError:
                logger.warning(
                    f"No permission to set typing status in chat {chat_id}. Skipping typing status."
                )
                await asyncio.sleep(delay)
            except Exception as e_typing:
                logger.error(
                    f"Error setting typing status for chat {chat_id}: {e_typing}. Proceeding without typing status."
                )
                await asyncio.sleep(delay)

            sent_message = await self.client.send_message(chat_id, text)
            logger.info(f"Sent message to chat {chat_id}: '{text[:50]}...'")
            send_successful = True
        except FloodWaitError as e:
            logger.warning(
                f"Flood wait ({e.seconds}s) triggered when sending message to {chat_id}. Sleeping..."
            )
            await asyncio.sleep(e.seconds + 1)
        except (
            UserIsBlockedError,
            ChatWriteForbiddenError,
            ChatAdminRequiredError,
        ) as e:
            logger.warning(f"Cannot send message to {chat_id}: {type(e).__name__}.")
        except ValueError as e:
            logger.error(f"ValueError sending message to {chat_id}: {e}", exc_info=True)
        except Exception as e:
            logger.error(
                f"Failed to send message to {chat_id}: {type(e).__name__} - {e}",
                exc_info=True,
            )

        if sent_message:
            message_data = {
                "message_id": sent_message.id,
                "sender": "You",
                "timestamp": sent_message.date.astimezone(timezone.utc).isoformat(),
                "text": text,
                "is_forward": False,
                "forward_source": None,
                "reply_to_message_id": None,
                "media_attached": False,
                "photo_attached": False,
            }

            logger.debug(
                f"[_send_message_with_delay {chat_id}] Creating background task to save sent message {sent_message.id}..."
            )
            asyncio.create_task(self._save_sent_message_task(chat_id, message_data))
            return True
        else:
            logger.warning(
                f"[_send_message_with_delay {chat_id}] Message was not sent successfully."
            )
            return False

    async def _initiation_loop(self):
        logger.info("Initiation loop started.")
        interval_config = self.settings_manager.get_initiation_interval_hours()
        interval_seconds = random.uniform(
            interval_config.get("min", 1) * 3600, interval_config.get("max", 4) * 3600
        )

        while self._is_running:
            try:
                await asyncio.sleep(interval_seconds)

                if not self._is_running or not self.is_active():
                    continue

                logger.info("Checking for conversation initiation opportunities...")

                eligible_chats = []
                try:
                    async for dialog in self.client.iter_dialogs():
                        if (
                            dialog.id != self._me_id
                            and dialog.is_user
                            and dialog.entity
                            and not dialog.entity.bot
                        ):
                            if (
                                dialog.id
                                not in self.settings_manager.get_excluded_chats()
                            ):
                                now_utc = datetime.now(timezone.utc)
                                if (
                                    dialog.id not in self._cooldown_until
                                    or now_utc >= self._cooldown_until[dialog.id]
                                ):
                                    eligible_chats.append(dialog.id)
                                else:
                                    logger.debug(
                                        f"Skipping chat {dialog.id} for initiation (on cooldown)."
                                    )
                except Exception as e_dialogs:
                    logger.error(
                        f"Error iterating dialogs in initiation loop: {e_dialogs}",
                        exc_info=True,
                    )
                    continue

                if not eligible_chats:
                    logger.info(
                        "No eligible personal chats found for initiation check."
                    )
                    interval_seconds = random.uniform(
                        interval_config.get("min", 1) * 3600,
                        interval_config.get("max", 4) * 3600,
                    )
                    continue

                base_instructions = await self._get_base_instructions()
                if not base_instructions:
                    logger.error(
                        "Cannot perform initiation check: base instructions missing."
                    )
                    interval_seconds = random.uniform(
                        interval_config.get("min", 1) * 3600,
                        interval_config.get("max", 4) * 3600,
                    )
                    continue

                top_chat_id = await self.elo_calculator.get_top_chat_for_initiation(
                    eligible_chats, base_instructions
                )

                if top_chat_id:
                    logger.info(
                        f"Potential initiation target found: chat {top_chat_id}"
                    )
                    await self._try_initiate_conversation(
                        top_chat_id, base_instructions
                    )
                else:
                    logger.info(
                        "No suitable chat found for initiation based on current Elo scores."
                    )

                interval_seconds = random.uniform(
                    interval_config.get("min", 1) * 3600,
                    interval_config.get("max", 4) * 3600,
                )
                await self.elo_calculator.clear_cache()

            except asyncio.CancelledError:
                logger.info("Initiation loop cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in initiation loop: {e}", exc_info=True)
                await asyncio.sleep(60 * 5)

    async def _try_initiate_conversation(self, chat_id: int, base_instructions: str):
        try:
            history_data = await data_storage.load_chat_history(chat_id, self.data_dir)
            messages = history_data.get("messages", [])

            last_msg_time = datetime.min.replace(tzinfo=timezone.utc)
            if messages:
                try:
                    if messages[-1].get("timestamp"):
                        last_msg_time = datetime.fromisoformat(
                            messages[-1]["timestamp"]
                        )
                    else:
                        logger.warning(
                            f"Last message in chat {chat_id} missing timestamp. Using default min time."
                        )
                except (ValueError, KeyError, IndexError):
                    logger.warning(
                        f"Could not parse timestamp of last message for chat {chat_id}. Using default min time."
                    )

            time_since_last = datetime.now(timezone.utc) - last_msg_time
            min_time_delta = timedelta(hours=MIN_TIME_BEFORE_INITIATION_HOURS)

            if messages and time_since_last < min_time_delta:
                logger.info(
                    f"Skipping initiation for chat {chat_id}: last message was too recent ({time_since_last})."
                )
                return

            if chat_id in self._last_initiated_time:
                time_since_last_ai_initiation = (
                    datetime.now(timezone.utc) - self._last_initiated_time[chat_id]
                )
                if time_since_last_ai_initiation < MIN_INTERVAL_BETWEEN_AI_INITIATION:
                    logger.info(
                        f"Skipping initiation for chat {chat_id}: AI initiated recently ({time_since_last_ai_initiation})."
                    )
                    return

            logger.info(
                f"Conditions met for initiating conversation in chat {chat_id}. Generating message..."
            )

            initiation_prompt = (
                "Ты должен(на) инициировать разговор с этим человеком, так как вы давно не общались. "
                "Придумай короткое, естественное и ненавязчивое сообщение в твоем стиле, "
                "чтобы начать диалог (например, 'Привет! Как дела?', 'Давно не общались, как ты?', "
                "'Вспомнил(а) тут про [общий интерес, если знаешь]...'). "
                "Учти контекст вашего предыдущего общения (если он есть в истории ниже)."
            )
            context_limit = 5
            dialog_context = messages[-context_limit:]
            full_initiation_prompt = f"{base_instructions}\n\n{initiation_prompt}"

            response_text = await self.ai_module.generate_response(
                chat_id=chat_id,
                base_instructions=full_initiation_prompt,
                dialog_context=dialog_context,
                incoming_message_text=None,
            )

            if response_text:
                await self._send_message_with_delay(chat_id, response_text)
                self._last_initiated_time[chat_id] = datetime.now(timezone.utc)
                logger.info(f"Successfully initiated conversation in chat {chat_id}.")
            else:
                logger.error(
                    f"Failed to generate initiation message for chat {chat_id}."
                )

        except FileNotFoundError:
            is_priority = (
                chat_id in self.settings_manager.get_priority_initiation_chats()
            )
            if is_priority:
                logger.warning(
                    f"History file not found for PRIORITY chat {chat_id} during initiation check. Attempting initiation without context..."
                )
                initiation_prompt = (
                    "Ты должен(на) инициировать разговор с этим человеком (он в списке приоритетных). "
                    "Истории общения нет. Придумай максимально общее, короткое, естественное и ненавязчивое "
                    "сообщение в твоем стиле, чтобы начать диалог (например, 'Привет! Как дела?', 'Как настроение?')."
                )

                if not base_instructions:
                    logger.error(
                        f"Cannot initiate PRIORITY chat {chat_id} without history: base instructions missing."
                    )
                    return

                full_initiation_prompt = f"{base_instructions}\n\n{initiation_prompt}"
                response_text = await self.ai_module.generate_response(
                    chat_id=chat_id,
                    base_instructions=full_initiation_prompt,
                    dialog_context=[],
                    incoming_message_text=None,
                )
                if response_text:
                    await self._send_message_with_delay(chat_id, response_text)
                    self._last_initiated_time[chat_id] = datetime.now(timezone.utc)
                    logger.info(
                        f"Successfully initiated conversation in PRIORITY chat {chat_id} (no history)."
                    )
                else:
                    logger.error(
                        f"Failed to generate initiation message for PRIORITY chat {chat_id} (no history)."
                    )
            else:
                logger.warning(
                    f"History file not found for chat {chat_id} during initiation check. Skipping initiation."
                )

        except Exception as e:
            logger.error(
                f"Error during initiation attempt for chat {chat_id}: {e}",
                exc_info=True,
            )

    async def _get_base_instructions(self) -> Optional[str]:
        instructions = self.settings_manager.get_persona_base_instructions()
        if not instructions:
            logger.error(
                "Base instructions ('persona_base_instructions') not found in settings."
            )
            return None
        return str(instructions)
