import asyncio
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

from src.bot import setup_bot
from src.core.ai_module import AIModule
from src.core.data_collector import DataCollector
from src.core.elo_calculator import EloCalculator
from src.core.interaction import InteractionModule
from src.utils.config import Config, SettingsManager, load_config
from src.utils.logging_config import setup_logging

logger = logging.getLogger(__name__)


async def main():
    try:
        config: Config = load_config()

        setup_logging(log_level=logging.INFO)
        logger.info("Starting Digital Persona application...")
        logger.info("Configuration loaded successfully.")

        settings_manager = SettingsManager(config.paths.settings_file)
        await settings_manager.load_settings()
        logger.info(f"Settings loaded from {config.paths.settings_file}")
    except (ValueError, FileNotFoundError) as e:
        logger.critical(
            f"CRITICAL ERROR: Could not load configuration/settings. {e}", exc_info=True
        )
        print(
            f"CRITICAL ERROR: Could not load configuration/settings. Please check .env, config.yaml and permissions. Details: {e}"
        )
        return

    logger.info("Initializing components...")

    bot_properties = DefaultBotProperties(parse_mode=ParseMode.HTML)
    bot = Bot(token=config.bot.token, default=bot_properties)
    dp = Dispatcher()
    logger.debug("Aiogram Bot and Dispatcher initialized.")

    client = TelegramClient(
        str(config.paths.base_dir / config.telethon.session_name),
        config.telethon.api_id,
        config.telethon.api_hash,
    )
    logger.debug("Telethon client instance created.")

    try:
        ai_module = AIModule(
            api_key=config.gemini.api_key, settings_manager=settings_manager
        )
        logger.debug("AI Module initialized.")
    except Exception as e:
        logger.critical(f"Failed to initialize AI Module: {e}", exc_info=True)
        print(
            f"CRITICAL ERROR: Failed to initialize AI Module. Check API key and dependencies. Details: {e}"
        )
        return

    data_dir = Path(config.paths.data_dir)

    elo_calculator = EloCalculator(data_dir=data_dir, settings_manager=settings_manager)
    logger.debug("Elo Calculator initialized.")

    data_collector = DataCollector(
        client=client, settings_manager=settings_manager, config=config
    )
    logger.debug("Data Collector initialized.")

    interaction_module = InteractionModule(
        client=client,
        bot_instance=bot,
        ai_module=ai_module,
        settings_manager=settings_manager,
        elo_calculator=elo_calculator,
        config=config,
        data_dir=data_dir,
    )
    logger.debug("Interaction Module initialized.")

    logger.info("Setting up Aiogram bot handlers and Telethon event listeners...")
    await setup_bot(
        bot=bot,
        dp=dp,
        config=config,
        settings_manager=settings_manager,
        interaction_module=interaction_module,
        data_collector=data_collector,
    )
    await interaction_module.add_event_handlers()

    logger.info("Starting components...")
    try:
        logger.info("Connecting Telethon client...")
        async with client:
            if not await client.is_user_authorized():
                logger.info("Telethon client needs authorization.")
                print("Telethon client needs authorization.")
                phone = config.telethon.phone_number
                if not phone:
                    phone = input(
                        "Please enter your phone number (e.g., +1234567890): "
                    )
                try:
                    await client.send_code_request(phone)
                    code = input("Please enter the code you received: ")
                    await client.sign_in(phone, code)
                except SessionPasswordNeededError:
                    password = input("Two-step verification password needed: ")
                    await client.sign_in(password=password)
                except Exception as auth_err:
                    logger.critical(
                        f"Telethon authorization failed: {auth_err}", exc_info=True
                    )
                    print(f"ERROR: Telethon authorization failed: {auth_err}")
                    return

            logger.info("Telethon client connected and authorized successfully.")
            user_info = await client.get_me()
            logger.info(
                f"Running as Telethon user: {user_info.first_name} (ID: {user_info.id})"
            )
            await interaction_module.set_client_ready(user_info.id)

            logger.info("Starting Aiogram bot polling...")
            polling_task = asyncio.create_task(
                dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
            )

            await polling_task
    except KeyboardInterrupt:
        logger.warning("Application stopped manually via KeyboardInterrupt.")
    except Exception as e:
        logger.critical(
            f"An unhandled exception occurred in main execution: {e}", exc_info=True
        )
    finally:
        logger.info("Shutting down...")
        if client.is_connected():
            logger.info("Disconnecting Telethon client...")
            await client.disconnect()
            logger.info("Telethon client disconnected.")
        logger.info("Shutdown complete.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("\nApplication stopped.")
    except Exception as e:
        logging.critical(
            f"Critical error during initial startup or final shutdown: {e}",
            exc_info=True,
        )
        print(f"\nCRITICAL ERROR during startup/shutdown: {e}")
