import asyncio
import logging
import math
import random
import time
from datetime import timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from telethon import TelegramClient
from telethon.errors import (
    ChannelPrivateError,
    ChatAdminRequiredError,
    ChatWriteForbiddenError,
    FloodWaitError,
    RPCError,
    UserDeactivatedBanError,
    UserIdInvalidError,
)
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
from src.utils.config import Config, SettingsManager

logger = logging.getLogger(__name__)

SUPPORTED_MESSAGE_TYPES_FOR_STORAGE = (Message,)
STATS_KEYS = ["text", "photo", "video", "voice", "other_media"]
MESSAGE_ITERATION_TIMEOUT_SECONDS = 20


class DataCollector:
    def __init__(
        self, client: TelegramClient, settings_manager: SettingsManager, config: Config
    ):
        self.client = client
        self.settings_manager = settings_manager
        self.config = config
        self.data_dir = Path(self.config.paths.data_dir)
        self._me_id: Optional[int] = None

    async def _get_me_id(self) -> int:
        if not self.client or not self.client.is_connected():
            logger.error("Cannot collect history: Telethon client is not connected.")
            raise ConnectionError("Telethon client is not connected.")
        if self._me_id is None:
            me = await self.client.get_me()
            if not me:
                raise ConnectionError(
                    "Could not get self user information from Telethon."
                )
            self._me_id = me.id
            logger.info(f"Running DataCollector as user ID: {self._me_id}")
        return self._me_id

    async def _get_sender_info(self, message: Message) -> Tuple[str, Optional[int]]:
        me_id = await self._get_me_id()
        sender_id = None
        sender_type = "Unknown"

        if message.sender_id:
            sender_id = message.sender_id
            if message.sender_id == me_id:
                sender_type = "You"
            elif isinstance(message.peer_id, PeerUser):
                sender_type = "Contact"
            elif isinstance(message.peer_id, (PeerChat, PeerChannel)):
                if isinstance(message.sender, User):
                    sender_type = f"User_{message.sender_id}"
                elif isinstance(message.sender, Channel):
                    sender_type = f"Channel_{message.sender_id}"
                else:
                    sender_type = f"Peer_{message.sender_id}"
            else:
                sender_type = f"Sender_{message.sender_id}"
        elif isinstance(message.sender, (Chat, Channel)):
            sender_id = message.sender.id
            sender_type = f"Peer_{sender_id}"

        return sender_type, sender_id

    def _format_message_data(
        self, message: Message, sender_type: str
    ) -> Tuple[Optional[Dict[str, Any]], Dict[str, int]]:
        stats = {key: 0 for key in STATS_KEYS}
        message_data = None

        if message.text:
            stats["text"] = 1
        if message.photo:
            stats["photo"] = 1
        if message.video:
            stats["video"] = 1
        if message.voice:
            stats["voice"] = 1
        if message.media and not (message.photo or message.video or message.voice):
            if message.text or getattr(message.media, "ttl_seconds", None) is None:
                stats["other_media"] = 1

        is_text = bool(message.text)
        is_photo = bool(message.photo)
        is_forward = bool(message.forward)

        should_store = is_text or is_photo or is_forward

        if not should_store:
            logger.debug(
                f"Skipping storage for message {message.id} (type not supported). Stats counted: {stats}"
            )
            return None, stats

        message_data = {
            "message_id": message.id,
            "sender": sender_type,
            "timestamp": message.date.astimezone(timezone.utc).isoformat(),
            "text": message.text or "",
            "is_forward": is_forward,
            "forward_source": None,
            "reply_to_message_id": message.reply_to_msg_id,
            "media_attached": bool(message.media),
            "photo_attached": is_photo,
        }

        if is_forward and message.forward:
            fwd_from = message.forward.from_id
            fwd_chat = message.forward.chat
            fwd_from_name = message.forward.from_name
            saved_from_peer = message.forward.saved_from_peer

            source_info = "Unknown Forward Source"
            if fwd_from_name:
                source_info = fwd_from_name
            elif fwd_from:
                source_info = f"Peer_{fwd_from.user_id if isinstance(fwd_from, PeerUser) else fwd_from.channel_id if isinstance(fwd_from, PeerChannel) else fwd_from.chat_id}"
            elif fwd_chat:
                source_info = f"Peer_{fwd_chat.id}"
            elif saved_from_peer:
                source_info = f"Peer_{saved_from_peer.user_id if isinstance(saved_from_peer, PeerUser) else saved_from_peer.channel_id if isinstance(saved_from_peer, PeerChannel) else saved_from_peer.chat_id}"

            message_data["forward_source"] = source_info

        logger.debug(f"Formatted message {message.id} for storage. Stats: {stats}")
        return message_data, stats

    async def _download_photo(self, message: Message, chat_id: int) -> bool:
        media_dir = await data_storage.ensure_media_dir_exists(self.data_dir, chat_id)
        file_path = media_dir / f"{message.id}.jpg"
        if file_path.exists():
            logger.debug(f"Photo {file_path} already exists. Skipping download.")
            return True
        try:
            logger.debug(
                f"Downloading photo from message {message.id} to {file_path}..."
            )
            start_time = time.monotonic()
            downloaded_path = await self.client.download_media(
                message.photo, file=file_path
            )
            end_time = time.monotonic()
            if downloaded_path:
                logger.info(
                    f"Successfully downloaded photo to {downloaded_path} in {end_time - start_time:.2f}s."
                )
                return True
            else:
                logger.warning(
                    f"Photo download for message {message.id} returned None (possibly empty)."
                )

                if file_path.exists() and file_path.stat().st_size == 0:
                    file_path.unlink()
                return False

        except FloodWaitError as e:
            logger.warning(
                f"Flood wait ({e.seconds}s) triggered during photo download for message {message.id}. Sleeping..."
            )
            await asyncio.sleep(e.seconds + random.uniform(1, 3))
            logger.warning(
                f"Skipping photo download for message {message.id} after flood wait."
            )
            return False
        except Exception as e:
            logger.error(
                f"Failed to download photo from message {message.id} to {file_path}: {e}",
                exc_info=True,
            )

            if file_path.exists():
                try:
                    file_path.unlink()
                except OSError:
                    pass
            return False

    async def collect_history_for_chat(self, chat_entity_or_id, limit_n: int) -> bool:
        if not self.client or not self.client.is_connected():
            logger.error("Cannot collect history: Telethon client is not connected.")
            return False

        chat_id = None
        chat_title = "Unknown Chat"
        chat_entity = None

        try:
            if isinstance(chat_entity_or_id, (int, str)):
                logger.debug(f"Getting entity for ID/Username: {chat_entity_or_id}")
                chat_entity = await self.client.get_entity(chat_entity_or_id)
                chat_id = chat_entity.id
            elif hasattr(chat_entity_or_id, "id"):
                chat_entity = chat_entity_or_id
                chat_id = chat_entity.id
            else:
                logger.error(
                    f"Invalid chat_entity_or_id type: {type(chat_entity_or_id)}"
                )
                return False

            if isinstance(chat_entity, User):
                chat_title = (
                    f"{getattr(chat_entity, 'first_name', '')} {getattr(chat_entity, 'last_name', '')}".strip()
                    or getattr(chat_entity, "username")
                    or f"User_{chat_id}"
                )
            elif isinstance(chat_entity, (Chat, Channel)):
                chat_title = (
                    getattr(chat_entity, "title", None)
                    or getattr(chat_entity, "username", None)
                    or f"Peer_{chat_id}"
                )
            else:
                chat_title = (
                    getattr(chat_entity, "username", None) or f"Entity_{chat_id}"
                )

            logger.info(
                f"Starting history collection for chat: '{chat_title}' (ID: {chat_id}) with limit N={limit_n}"
            )

            logger.info(f"Attempting to load chat history for {chat_id}...")
            history_data = await data_storage.load_chat_history(chat_id, self.data_dir)
            logger.info(f"Finished loading chat history for {chat_id}.")

            existing_message_ids = {
                msg["message_id"]
                for msg in history_data["messages"]
                if msg.get("message_id")
            }
            last_known_message_id = (
                max(existing_message_ids) if existing_message_ids else 0
            )
            aggregated_stats = history_data.get(
                "aggregated_stats", {key: 0 for key in STATS_KEYS}
            )

            logger.info(
                f"Loaded existing history for {chat_id}. {len(history_data['messages'])} messages found. Last known ID: {last_known_message_id}"
            )

            if history_data.get("chat_title") != chat_title:
                await data_storage.update_chat_title(chat_id, self.data_dir, chat_title)
                history_data["chat_title"] = chat_title

            new_messages_data: List[Dict[str, Any]] = []
            all_downloaded_messages: List[Message] = []
            batch_stats = {key: 0 for key in STATS_KEYS}
            processed_count = 0
            iteration_start_time = time.monotonic()

            logger.info(f"Preparing to iterate messages for chat {chat_id}...")
            try:
                logger.debug(
                    f"Attempting to create iter_messages for chat {chat_id}..."
                )
                message_iterator = self.client.iter_messages(
                    chat_entity,
                    limit=limit_n,
                    min_id=last_known_message_id,
                    wait_time=1,
                )
                logger.debug(
                    f"iter_messages object created for chat {chat_id}. Starting iteration..."
                )

                async for message in message_iterator:
                    if processed_count == 0:
                        logger.info(
                            f"Received first message ({message.id}) from iterator for chat {chat_id}."
                        )

                    if not isinstance(message, Message):
                        logger.warning(
                            f"Skipping non-message item in chat {chat_id}: {type(message)}"
                        )
                        continue

                    if message.id in existing_message_ids:
                        logger.debug(
                            f"Skipping already existing message {message.id} in chat {chat_id}"
                        )
                        continue

                    processed_count += 1
                    all_downloaded_messages.append(message)

                    sender_type, _ = await self._get_sender_info(message)
                    formatted_data, message_stats = self._format_message_data(
                        message, sender_type
                    )

                    for key in STATS_KEYS:
                        batch_stats[key] += message_stats.get(key, 0)

                    if formatted_data:
                        new_messages_data.append(formatted_data)

                    if processed_count % 100 == 0:
                        logger.debug(
                            f"Processed {processed_count} messages for chat {chat_id}, pausing briefly..."
                        )
                        await asyncio.sleep(random.uniform(0.2, 0.7))
            except (
                ChannelPrivateError,
                ChatAdminRequiredError,
                UserDeactivatedBanError,
                UserIdInvalidError,
                ChatWriteForbiddenError,
            ) as e:
                logger.warning(
                    f"Access Error while iterating messages for chat {chat_id} ('{chat_title}'): {type(e).__name__} - {e}. Skipping."
                )
                return False
            except FloodWaitError as e:
                logger.warning(
                    f"Flood wait ({e.seconds}s) caught during message iteration for chat {chat_id}. Sleeping and skipping chat."
                )
                await asyncio.sleep(e.seconds + random.uniform(1, 3))
                return False
            except RPCError as e:
                error_code = getattr(e, "code", None)
                error_msg = str(e).lower()
                logger.error(
                    f"Telegram RPC Error (Code: {error_code}) during message iteration for chat {chat_id}: {type(e).__name__} - {e}",
                    exc_info=True,
                )
                return False
            except Exception as e:
                logger.error(
                    f"Unexpected error during message iteration for chat {chat_id}: {type(e).__name__} - {e}",
                    exc_info=True,
                )
                return False

            iteration_end_time = time.monotonic()
            logger.info(
                f"Finished iterating messages for chat {chat_id}. "
                f"Downloaded {processed_count} messages ({len(new_messages_data)} formatted) "
                f"in {iteration_end_time - iteration_start_time:.2f}s."
            )
            if batch_stats and any(v > 0 for v in batch_stats.values()):
                logger.info(f"Stats for this batch: {batch_stats}")

            photos_in_batch = [msg for msg in all_downloaded_messages if msg.photo]
            if photos_in_batch:
                percent_to_download = 0.01
                min_photos = 1
                max_photos = 10
                num_photos_percent = math.ceil(
                    len(photos_in_batch) * percent_to_download
                )
                num_photos_to_download = max(min_photos, num_photos_percent)
                num_photos_to_download = min(num_photos_to_download, max_photos)

                photos_in_batch.sort(key=lambda m: m.id, reverse=True)
                photos_to_download = photos_in_batch[:num_photos_to_download]
                logger.info(
                    f"Found {len(photos_in_batch)} photos in batch. Will attempt to download last {len(photos_to_download)} "
                    f"(target: {percent_to_download * 100}%, min: {min_photos}, max: {max_photos})."
                )

                logger.debug(f"Starting photo download tasks for chat {chat_id}...")
                download_tasks = [
                    self._download_photo(photo_msg, chat_id)
                    for photo_msg in reversed(photos_to_download)
                ]

                await asyncio.gather(*download_tasks)
                logger.debug(f"Finished photo download tasks for chat {chat_id}.")
                await asyncio.sleep(random.uniform(0.5, 1.0))

            if new_messages_data:
                history_data["messages"].extend(reversed(new_messages_data))
                history_data["messages"].sort(key=lambda x: x.get("message_id", 0))

                for key in STATS_KEYS:
                    aggregated_stats[key] = (
                        aggregated_stats.get(key, 0) + batch_stats[key]
                    )
                history_data["aggregated_stats"] = aggregated_stats

                logger.info(
                    f"Added {len(new_messages_data)} new messages. Total messages now: {len(history_data['messages'])}"
                )
                logger.info(
                    f"Updated aggregated stats for chat {chat_id}: {aggregated_stats}"
                )
                await data_storage.save_chat_history(
                    chat_id, self.data_dir, history_data
                )
            else:
                logger.info(f"No new messages found or formatted for chat {chat_id}.")
                await data_storage.save_chat_history(
                    chat_id, self.data_dir, history_data
                )

            return True
        except (
            ChannelPrivateError,
            ChatAdminRequiredError,
            UserDeactivatedBanError,
            UserIdInvalidError,
            ValueError,
        ) as e:
            logger.warning(
                f"Cannot access or find entity for {chat_entity_or_id}: {type(e).__name__} - {e}. Skipping chat."
            )
            return False
        except FloodWaitError as e:
            logger.warning(
                f"Flood wait ({e.seconds}s) triggered for chat {chat_id or chat_entity_or_id} during initial phase. Sleeping and skipping chat."
            )
            await asyncio.sleep(e.seconds + random.uniform(1, 3))
            return False
        except Exception as e:
            logger.error(
                f"Failed to collect history for chat {chat_id or chat_entity_or_id}: {type(e).__name__} - {e}",
                exc_info=True,
            )
            return False

    async def collect_all_chats_history(self, dialog_limit: Optional[int] = None):
        if not self.client or not self.client.is_connected():
            logger.error(
                "Cannot start history collection: Telethon client is not connected."
            )
            return

        logger.info("Starting collection of history for all chats...")
        try:
            await self._get_me_id()
        except ConnectionError as e:
            logger.critical(f"Failed to get own user ID: {e}. Aborting collection.")
            return

        limit_n = self.settings_manager.get_download_limit()
        processed_dialogs = 0
        successful_collections = 0
        failed_collections = 0
        start_time = time.monotonic()
        excluded_chats = self.settings_manager.get_excluded_chats()

        try:
            dialog_iterator = self.client.iter_dialogs(limit=dialog_limit)
            async for dialog in dialog_iterator:
                processed_dialogs += 1
                chat_entity = dialog.entity
                dialog_id = dialog.id
                dialog_name = dialog.name

                logger.debug(
                    f"Processing dialog {processed_dialogs}: '{dialog_name}' (ID: {dialog_id}, Type: {type(chat_entity).__name__})"
                )

                if dialog_id == self._me_id:
                    logger.info("Skipping 'Saved Messages'.")
                    continue

                if dialog_id in excluded_chats:
                    logger.info(
                        f"Skipping excluded chat '{dialog_name}' (ID: {dialog_id})."
                    )
                    continue

                if not chat_entity or getattr(chat_entity, "deactivated", False):
                    logger.warning(
                        f"Skipping dialog '{dialog_name}' (ID: {dialog_id}) because entity is missing or deactivated."
                    )
                    failed_collections += 1
                    continue

                success = await self.collect_history_for_chat(chat_entity, limit_n)
                if success:
                    successful_collections += 1
                else:
                    failed_collections += 1

                await asyncio.sleep(random.uniform(2, 5))

        except FloodWaitError as e:
            logger.error(
                f"Flood wait ({e.seconds}s) triggered during dialog iteration. Stopping collection."
            )
            await asyncio.sleep(e.seconds + 5)
        except Exception as e:
            logger.error(
                f"An error occurred during dialog iteration after processing {processed_dialogs} dialogs: {e}",
                exc_info=True,
            )
        finally:
            end_time = time.monotonic()
            logger.info("-" * 30)
            logger.info(f"History collection finished in {end_time - start_time:.2f}s.")
            logger.info(f"Dialogs processed: {processed_dialogs}")
            logger.info(f"Successful collections: {successful_collections}")
            logger.info(f"Failed/Skipped collections: {failed_collections}")
            logger.info("-" * 30)
