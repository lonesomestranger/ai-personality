import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(log_level=logging.INFO, console_log_level=logging.DEBUG):
    try:
        from .config import load_config

        config = load_config()
        log_file_path = Path(config.paths.log_file)
    except Exception as e:
        print(
            f"Warning: Could not load config to determine log file path: {e}. Using default './logs/errors.log'"
        )
        log_file_path = Path("./logs/errors.log")
        log_file_path.parent.mkdir(parents=True, exist_ok=True)

    log_formatter = logging.Formatter(
        fmt="%(levelname)-8s - %(name)-25s - %(message)s",
    )

    file_formatter = logging.Formatter(
        fmt="%(levelname)-8s - %(name)-25s - %(filename)s:%(lineno)d - %(message)s",
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(console_log_level)
    console_handler.setFormatter(log_formatter)
    root_logger.addHandler(console_handler)

    file_handler = RotatingFileHandler(
        log_file_path,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(file_formatter)
    root_logger.addHandler(file_handler)

    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("telethon.network").setLevel(logging.WARNING)
    logging.getLogger("telethon.client").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)

    logger = logging.getLogger(__name__)
    logger.info(
        f"Logging setup complete. Log level: {logging.getLevelName(log_level)}. Console level: {logging.getLevelName(console_log_level)}. Log file: {log_file_path}"
    )
