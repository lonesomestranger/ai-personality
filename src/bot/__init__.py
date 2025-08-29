import logging

from aiogram import Bot, Dispatcher, types

from src.core.data_collector import DataCollector
from src.core.interaction import InteractionModule
from src.utils.config import Config, SettingsManager

from .filters import AdminFilter
from .handlers.admin_handlers import admin_router

logger = logging.getLogger(__name__)


async def set_bot_commands(bot: Bot):
    commands = [
        types.BotCommand(
            command="/start", description="🚀 Запустить бота / Показать меню"
        ),
        types.BotCommand(command="/menu", description="📋 Показать главное меню"),
        types.BotCommand(
            command="/collect_history", description="📥 Запустить сбор истории"
        ),
    ]
    try:
        admin_id = bot.config.bot.admin_id
        await bot.set_my_commands(
            commands, scope=types.BotCommandScopeChat(chat_id=admin_id)
        )
        logger.info(f"Bot commands menu updated for admin {admin_id}.")
    except Exception as e:
        logger.error(f"Failed to set bot commands: {e}")


async def setup_bot(
    bot: Bot,
    dp: Dispatcher,
    config: Config,
    settings_manager: SettingsManager,
    interaction_module: InteractionModule,
    data_collector: DataCollector,
):
    logger.info("Configuring Aiogram bot...")

    dp["config"] = config
    dp["settings_manager"] = settings_manager
    dp["interaction_module"] = interaction_module
    dp["data_collector"] = data_collector
    dp["bot"] = bot

    admin_router.message.filter(AdminFilter())
    admin_router.callback_query.filter(AdminFilter())
    logger.info("Admin filter applied to admin router.")

    dp.include_router(admin_router)

    bot.config = config
    await set_bot_commands(bot)

    logger.info("Aiogram bot configured successfully.")
