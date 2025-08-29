import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict

try:
    from .data_collector import STATS_KEYS
except ImportError:
    STATS_KEYS = ["text", "photo", "video", "voice", "other_media"]

logger = logging.getLogger(__name__)
_file_locks: Dict[Path, asyncio.Lock] = {}
_lock_lock = asyncio.Lock()


async def _get_file_lock(filepath: Path) -> asyncio.Lock:
    async with _lock_lock:
        if filepath not in _file_locks:
            _file_locks[filepath] = asyncio.Lock()
        return _file_locks[filepath]


def _get_chat_dir(data_dir: Path, chat_id: int) -> Path:
    return data_dir / str(chat_id)


async def load_chat_history(chat_id: int, data_dir: Path) -> Dict[str, Any]:
    chat_dir = _get_chat_dir(data_dir, chat_id)
    filepath = chat_dir / f"{chat_id}.json"
    lock = await _get_file_lock(filepath)

    logger.debug(f"[load_chat_history {chat_id}] Requesting file lock for {filepath}")

    default_structure = {
        "chat_id": chat_id,
        "chat_title": f"Unknown Chat {chat_id}",
        "aggregated_stats": {key: 0 for key in STATS_KEYS},
        "messages": [],
    }

    async with lock:
        logger.info(
            f"[load_chat_history {chat_id}] File lock acquired. Checking if file exists: {filepath}"
        )
        if not filepath.exists():
            logger.warning(
                f"[load_chat_history {chat_id}] History file not found at {filepath}. Returning default structure. Releasing lock."
            )

            return default_structure
        try:
            logger.info(
                f"[load_chat_history {chat_id}] File exists. Attempting to open and read..."
            )

            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            logger.info(
                f"[load_chat_history {chat_id}] File read successfully. Validating structure..."
            )

            if "aggregated_stats" not in data or not isinstance(
                data.get("aggregated_stats"), dict
            ):
                logger.warning(
                    f"[load_chat_history {chat_id}] Reinitializing 'aggregated_stats'"
                )
                data["aggregated_stats"] = {key: 0 for key in STATS_KEYS}
            for key in STATS_KEYS:
                if key not in data["aggregated_stats"]:
                    data["aggregated_stats"][key] = 0
            if "messages" not in data or not isinstance(data.get("messages"), list):
                logger.warning(
                    f"[load_chat_history {chat_id}] Reinitializing 'messages' list"
                )
                data["messages"] = []
            if "chat_id" not in data:
                data["chat_id"] = chat_id
            if "chat_title" not in data:
                data["chat_title"] = chat_dir.name or f"Unknown Chat {chat_id}"

            logger.info(
                f"[load_chat_history {chat_id}] Validation complete. Returning data. Releasing lock."
            )

            return data
        except json.JSONDecodeError as e_json:
            logger.error(
                f"[load_chat_history {chat_id}] Failed to parse history file {filepath}: {e_json}. Returning default structure. Releasing lock."
            )
            default_structure["chat_title"] = f"Error Loading Chat {chat_id}"

            return default_structure
        except Exception as e:
            logger.error(
                f"[load_chat_history {chat_id}] Unexpected error loading file {filepath}: {e}. Returning default structure. Releasing lock.",
                exc_info=True,
            )
            default_structure["chat_title"] = f"Error Loading Chat {chat_id}"

            return default_structure

    logger.debug(f"[load_chat_history {chat_id}] Exited lock block.")


async def save_chat_history(chat_id: int, data_dir: Path, history_data: Dict[str, Any]):
    chat_dir = _get_chat_dir(data_dir, chat_id)
    filepath = chat_dir / f"{chat_id}.json"
    lock = await _get_file_lock(filepath)

    async with lock:
        try:
            chat_dir.mkdir(parents=True, exist_ok=True)
            logger.debug(
                f"[save_chat_history {chat_id}] Attempting to save history to {filepath}"
            )
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(history_data, f, ensure_ascii=False, indent=2)
            logger.debug(
                f"[save_chat_history {chat_id}] Successfully saved history to {filepath}"
            )
        except Exception as e:
            logger.error(
                f"[save_chat_history {chat_id}] Failed to save history to {filepath}: {e}",
                exc_info=True,
            )


async def update_chat_title(chat_id: int, data_dir: Path, new_title: str):
    chat_dir = _get_chat_dir(data_dir, chat_id)
    filepath = chat_dir / f"{chat_id}.json"
    logger.info(
        f"[update_chat_title {chat_id}] Attempting to get file lock for {filepath}"
    )
    lock = await _get_file_lock(filepath)

    async with lock:
        logger.info(f"[update_chat_title {chat_id}] Acquired file lock.")
        try:
            history_data = {}

            if filepath.exists():
                logger.info(
                    f"[update_chat_title {chat_id}] File exists, reading existing data to merge title..."
                )
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        history_data = json.load(f)
                except json.JSONDecodeError:
                    logger.warning(
                        f"[update_chat_title {chat_id}] Existing file {filepath} is corrupted. Re-initializing."
                    )

                    history_data = {
                        "chat_id": chat_id,
                        "aggregated_stats": {key: 0 for key in STATS_KEYS},
                        "messages": [],
                    }
            else:
                logger.info(
                    f"[update_chat_title {chat_id}] File {filepath} does not exist. Creating new structure."
                )

                history_data = {
                    "chat_id": chat_id,
                    "aggregated_stats": {key: 0 for key in STATS_KEYS},
                    "messages": [],
                }

            if history_data.get("chat_title") != new_title:
                history_data["chat_title"] = new_title
                logger.info(
                    f"[update_chat_title {chat_id}] Title updated to '{new_title}'. Attempting to save..."
                )

                chat_dir.mkdir(parents=True, exist_ok=True)
                with open(filepath, "w", encoding="utf-8") as f:
                    json.dump(history_data, f, ensure_ascii=False, indent=2)
                logger.info(
                    f"[update_chat_title {chat_id}] File saved successfully after title update. Releasing lock."
                )
            else:
                logger.info(
                    f"[update_chat_title {chat_id}] Title is already '{new_title}'. No save needed. Releasing lock."
                )
        except Exception as e:
            logger.error(
                f"[update_chat_title {chat_id}] Failed during title update process: {e}",
                exc_info=True,
            )


async def ensure_media_dir_exists(data_dir: Path, chat_id: int) -> Path:
    chat_dir = _get_chat_dir(data_dir, chat_id)
    media_dir = chat_dir / "media"
    try:
        media_dir.mkdir(parents=True, exist_ok=True)
        logger.debug(
            f"[ensure_media_dir_exists {chat_id}] Ensured media directory exists: {media_dir}"
        )
    except Exception as e:
        logger.error(
            f"[ensure_media_dir_exists {chat_id}] Failed to create media directory {media_dir}: {e}"
        )

    return media_dir


async def append_message_to_history(
    chat_id: int, data_dir: Path, message_data: Dict[str, Any]
):
    if not message_data:
        logger.warning(
            f"[append_message {chat_id}] Attempted to append empty message data."
        )
        return

    chat_dir = _get_chat_dir(data_dir, chat_id)
    filepath = chat_dir / f"{chat_id}.json"
    msg_id_for_log = message_data.get("message_id", "N/A")

    logger.debug(
        f"[append_message {chat_id}-{msg_id_for_log}] Preparing to append. Will request lock later."
    )

    history_data = None
    try:
        logger.debug(
            f"[append_message {chat_id}-{msg_id_for_log}] Attempting to load history before acquiring main lock..."
        )

        load_lock = await _get_file_lock(filepath)
        async with load_lock:
            if filepath.exists():
                logger.info(
                    f"[append_message {chat_id}-{msg_id_for_log}] File exists. Reading inside temporary read lock..."
                )
                with open(filepath, "r", encoding="utf-8") as f:
                    history_data = json.load(f)
                logger.info(
                    f"[append_message {chat_id}-{msg_id_for_log}] File read successful inside read lock."
                )
            else:
                logger.info(
                    f"[append_message {chat_id}-{msg_id_for_log}] File does not exist. Initializing structure."
                )

                history_data = {
                    "chat_id": chat_id,
                    "chat_title": f"Unknown Chat {chat_id}",
                    "aggregated_stats": {key: 0 for key in STATS_KEYS},
                    "messages": [],
                }
    except json.JSONDecodeError:
        logger.error(
            f"[append_message {chat_id}-{msg_id_for_log}] Failed to decode existing JSON. Re-initializing structure.",
            exc_info=True,
        )
        history_data = {
            "chat_id": chat_id,
            "chat_title": f"Corrupted Chat {chat_id}",
            "aggregated_stats": {key: 0 for key in STATS_KEYS},
            "messages": [],
        }
    except Exception as e_load:
        logger.error(
            f"[append_message {chat_id}-{msg_id_for_log}] Error loading history before lock: {e_load}. Aborting append.",
            exc_info=True,
        )
        return

    logger.debug(
        f"[append_message {chat_id}-{msg_id_for_log}] History loaded/initialized. Modifying in memory..."
    )
    if history_data is None:
        logger.error(
            f"[append_message {chat_id}-{msg_id_for_log}] history_data is None after load attempt. Aborting."
        )
        return

    msg_id = message_data.get("message_id")
    if msg_id and msg_id > 0:
        if any(m.get("message_id") == msg_id for m in history_data.get("messages", [])):
            logger.warning(
                f"[append_message {chat_id}-{msg_id_for_log}] Message ID {msg_id} already exists in loaded data. Skipping append."
            )
            return

    history_data.setdefault("messages", []).append(message_data)
    stats = history_data.setdefault("aggregated_stats", {key: 0 for key in STATS_KEYS})
    if message_data.get("text"):
        stats["text"] = stats.get("text", 0) + 1
    if message_data.get("photo_attached"):
        stats["photo"] = stats.get("photo", 0) + 1

    history_data["aggregated_stats"] = stats
    logger.debug(
        f"[append_message {chat_id}-{msg_id_for_log}] Data modified in memory. Requesting main file lock for writing..."
    )

    lock = await _get_file_lock(filepath)
    async with lock:
        logger.info(
            f"[append_message {chat_id}-{msg_id_for_log}] Write lock acquired. Attempting to save to disk..."
        )
        try:
            chat_dir.mkdir(parents=True, exist_ok=True)

            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(history_data, f, ensure_ascii=False, indent=2)

            logger.info(
                f"[append_message {chat_id}-{msg_id_for_log}] Successfully saved appended message to {filepath}. Releasing write lock."
            )
        except Exception as e_write:
            logger.error(
                f"[append_message {chat_id}-{msg_id_for_log}] Failed to save history {filepath} after appending message: {e_write}. Releasing write lock.",
                exc_info=True,
            )

    logger.debug(
        f"[append_message {chat_id}-{msg_id_for_log}] Exited write lock block."
    )
