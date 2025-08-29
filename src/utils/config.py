import asyncio
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from dotenv import load_dotenv

logger = logging.getLogger(__name__)


@dataclass
class TelethonConfig:
    api_id: int
    api_hash: str
    session_name: str
    phone_number: Optional[str] = None


@dataclass
class BotConfig:
    token: str
    admin_id: int


@dataclass
class GeminiConfig:
    api_key: str


@dataclass
class PathsConfig:
    data_dir: str = "./chats"
    log_file: str = "./logs/errors.log"
    settings_file: str = "./settings.json"
    base_dir: Path = Path(__file__).parent.parent.parent


@dataclass
class Config:
    telethon: TelethonConfig
    bot: BotConfig
    gemini: GeminiConfig
    paths: PathsConfig


def _get_env_var(
    var_name: str, required: bool = True, default: Any = None
) -> Optional[str]:
    value = os.getenv(var_name)
    if required and value is None:
        logger.error(f"Environment variable '{var_name}' not found.")
        raise ValueError(f"Required environment variable '{var_name}' is not set.")
    return value if value is not None else default


def load_config(env_path: str = ".env", config_path: str = "config.yaml") -> Config:
    load_dotenv(dotenv_path=env_path)
    logger.info(f"Loaded environment variables from: {env_path}")

    yaml_config = {}
    config_file = Path(config_path)
    if config_file.exists():
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                yaml_config = yaml.safe_load(f) or {}
            logger.info(f"Loaded base configuration from: {config_path}")
        except yaml.YAMLError as e:
            logger.warning(
                f"Could not parse {config_path}: {e}. Proceeding without it."
            )
        except Exception as e:
            logger.warning(f"Could not read {config_path}: {e}. Proceeding without it.")
    else:
        logger.warning(
            f"{config_path} not found. Using environment variables and defaults only."
        )

    try:
        telethon_cfg = TelethonConfig(
            api_id=int(_get_env_var("TELETHON_API_ID")),
            api_hash=_get_env_var("TELETHON_API_HASH"),
            session_name=_get_env_var(
                "SESSION_NAME", default="digital_persona_session"
            ),
            phone_number=_get_env_var("PHONE_NUMBER", required=False),
        )

        bot_cfg = BotConfig(
            token=_get_env_var("BOT_TOKEN"), admin_id=int(_get_env_var("ADMIN_ID"))
        )

        gemini_cfg = GeminiConfig(api_key=_get_env_var("GEMINI_API_KEY"))

        paths_cfg = PathsConfig(
            data_dir=_get_env_var(
                "DATA_DIR",
                required=False,
                default=yaml_config.get("paths", {}).get("data_dir", "./chats"),
            ),
            log_file=_get_env_var(
                "LOG_FILE",
                required=False,
                default=yaml_config.get("paths", {}).get(
                    "log_file", "./logs/errors.log"
                ),
            ),
            settings_file=_get_env_var(
                "SETTINGS_FILE",
                required=False,
                default=yaml_config.get("paths", {}).get(
                    "settings_file", "./settings.json"
                ),
            ),
        )

        Path(paths_cfg.data_dir).mkdir(parents=True, exist_ok=True)
        log_dir = Path(paths_cfg.log_file).parent
        log_dir.mkdir(parents=True, exist_ok=True)

        return Config(
            telethon=telethon_cfg, bot=bot_cfg, gemini=gemini_cfg, paths=paths_cfg
        )
    except (ValueError, TypeError) as e:
        logger.critical(f"Configuration error: {e}", exc_info=True)
        raise ValueError(
            f"Configuration loading failed. Check .env and config.yaml. Details: {e}"
        )


DEFAULT_SETTINGS = {
    "persona_active": False,
    "telethon_user_id": None,
    "download_limit_n": 1000,
    "group_reply_frequency_n": 50,
    "ai_detection_cooldown_hours": {"min": 2, "max": 24},
    "excluded_chats": [],
    "content_restriction_removed_chats": [],
    "priority_initiation_chats": [],
    "initiation_interval_hours": {
        "min": 1,
        "max": 4,
    },
    "generic_error_replies": [
        "ахахаха",
        "лол",
        "и что это",
        "))",
        ")))",
    ],
    "persona_base_instructions": """
    Здесь должен быть текст, который будет использоваться в качестве базовых инструкций для вашей личности.
""".strip(),
}


class SettingsManager:
    def __init__(self, settings_path: str):
        self.settings_path = Path(settings_path)
        self._settings: Dict[str, Any] = {}
        self._lock = asyncio.Lock()

    async def load_settings(self):
        async with self._lock:
            if self.settings_path.exists():
                try:
                    with open(self.settings_path, "r", encoding="utf-8") as f:
                        loaded_settings = json.load(f)

                    self._settings = DEFAULT_SETTINGS.copy()
                    self._settings.update(loaded_settings)
                    logger.info(f"Settings loaded from {self.settings_path}")
                    await self._save_settings_internal()
                except (json.JSONDecodeError, Exception) as e:
                    logger.error(
                        f"Failed to load settings from {self.settings_path}: {e}. Using default settings."
                    )
                    self._settings = DEFAULT_SETTINGS.copy()
                    await self._save_settings_internal()
            else:
                logger.warning(
                    f"Settings file {self.settings_path} not found. Creating with default settings."
                )
                self._settings = DEFAULT_SETTINGS.copy()
                await self._save_settings_internal()

    async def _save_settings_internal(self):
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.settings_path, "w", encoding="utf-8") as f:
                json.dump(self._settings, f, ensure_ascii=False, indent=4)
            logger.debug(f"Settings saved to {self.settings_path}")
        except Exception as e:
            logger.error(f"Failed to save settings to {self.settings_path}: {e}")

    async def save_settings(self):
        async with self._lock:
            await self._save_settings_internal()

    def get(self, key: str, default: Any = None) -> Any:
        return self._settings.get(key, default)

    async def set(self, key: str, value: Any):
        async with self._lock:
            self._settings[key] = value
            await self._save_settings_internal()

    @property
    def settings(self) -> Dict[str, Any]:
        return self._settings.copy()

    def is_persona_active(self) -> bool:
        return self.get("persona_active", False)

    def get_telethon_user_id(self) -> Optional[int]:
        return self.get("telethon_user_id")

    def get_download_limit(self) -> int:
        return self.get("download_limit_n", DEFAULT_SETTINGS["download_limit_n"])

    def get_group_reply_frequency(self) -> int:
        return self.get(
            "group_reply_frequency_n", DEFAULT_SETTINGS["group_reply_frequency_n"]
        )

    def get_ai_detection_cooldown_hours(self) -> dict:
        return self.get(
            "ai_detection_cooldown_hours",
            DEFAULT_SETTINGS["ai_detection_cooldown_hours"],
        )

    def get_excluded_chats(self) -> list[int]:
        return self.get("excluded_chats", [])

    def get_content_restriction_removed_chats(self) -> list[int]:
        return self.get("content_restriction_removed_chats", [])

    def get_priority_initiation_chats(self) -> list[int]:
        return self.get("priority_initiation_chats", [])

    def get_generic_error_replies(self) -> list[str]:
        return self.get(
            "generic_error_replies", DEFAULT_SETTINGS["generic_error_replies"]
        )

    def get_initiation_interval_hours(self) -> dict:
        return self.get(
            "initiation_interval_hours", DEFAULT_SETTINGS["initiation_interval_hours"]
        )

    def get_persona_base_instructions(self) -> Optional[str]:
        return self.get("persona_base_instructions")
