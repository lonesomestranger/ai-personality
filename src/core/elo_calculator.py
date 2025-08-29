import asyncio
import logging
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from src.core import data_storage
from src.utils.config import SettingsManager

logger = logging.getLogger(__name__)

HISTORY_SCORE_WEIGHTS = {
    "text": 0.5,
    "photo": 1.0,
    "video": 1.2,
    "voice": 1.5,
    "other_media": 0.2,
}
USE_LOGARITHMIC_HISTORY_SCORE = True

INTEREST_BONUS_PER_MATCH = 5.0
MAX_INTEREST_BONUS = 100.0

MANUAL_BOOST_VALUE = 500.0


class EloCalculator:
    def __init__(self, data_dir: Path, settings_manager: SettingsManager):
        self.data_dir = data_dir
        self.settings_manager = settings_manager
        self._persona_interests: Optional[Set[str]] = None
        self._elo_cache: Dict[int, float] = {}
        self._cache_lock = asyncio.Lock()

    def _extract_interests_from_instructions(self, base_instructions: str) -> Set[str]:
        match = re.search(
            r"Ключевые интересы:\s*(.*)", base_instructions, re.IGNORECASE | re.DOTALL
        )
        if not match:
            logger.warning(
                "Could not find 'Ключевые интересы:' marker in base instructions. Interest bonus will be 0."
            )
            return set()

        interests_str = match.group(1).strip()
        interests = {
            interest.strip().lower()
            for interest in interests_str.split(",")
            if interest.strip()
        }
        logger.debug(f"Extracted persona interests: {interests}")
        return interests

    async def _get_persona_interests(
        self, base_instructions: Optional[str]
    ) -> Set[str]:
        if self._persona_interests is None:
            if not base_instructions:
                logger.warning(
                    "Base instructions not provided, cannot determine interests."
                )
                self._persona_interests = set()
            else:
                # TODO: Где взять base_instructions?
                # Вариант 1: Передавать в каждый вызов calculate_elo (неудобно)
                # Вариант 2: Сохранить в settings.json или config.yaml
                # Вариант 3: Прочитать из специального файла (e.g., persona_profile.txt)
                # Пока реализуем извлечение, но вопрос источника base_instructions остается.
                # Предположим, что они как-то доступны.
                self._persona_interests = self._extract_interests_from_instructions(
                    base_instructions
                )
        return self._persona_interests

    async def _calculate_history_score(
        self, chat_id: int, history_data: Dict[str, Any]
    ) -> float:
        stats = history_data.get("aggregated_stats")
        if not stats:
            logger.warning(
                f"No 'aggregated_stats' found in history for chat {chat_id}. History Score will be 0."
            )
            return 0.0

        score = 0.0
        for key, weight in HISTORY_SCORE_WEIGHTS.items():
            count = stats.get(key, 0)
            if count > 0:
                if USE_LOGARITHMIC_HISTORY_SCORE:
                    term_score = weight * math.log(count + 1)
                else:
                    term_score = weight * count
                score += term_score

        logger.debug(f"Calculated History Score for chat {chat_id}: {score:.2f}")
        return score

    async def _calculate_interest_bonus(
        self, chat_id: int, history_data: Dict[str, Any], persona_interests: Set[str]
    ) -> float:
        if not persona_interests:
            return 0.0

        bonus = 0.0
        messages = history_data.get("messages", [])
        if not messages:
            return 0.0

        contact_messages_text = []
        for msg in messages:
            sender = msg.get("sender", "")
            if sender != "You" and msg.get("text"):
                contact_messages_text.append(msg["text"].lower())

        if not contact_messages_text:
            return 0.0

        match_count = 0
        full_text = " ".join(contact_messages_text)
        for interest in persona_interests:
            pattern = r"\b" + re.escape(interest) + r"\b"
            found = re.findall(pattern, full_text)
            match_count += len(found)

        bonus = match_count * INTEREST_BONUS_PER_MATCH
        bonus = min(bonus, MAX_INTEREST_BONUS)

        logger.debug(
            f"Calculated Interest Bonus for chat {chat_id}: {bonus:.2f} (based on {match_count} matches)"
        )
        return bonus

    def _get_manual_boost(self, chat_id: int) -> float:
        priority_chats = self.settings_manager.get_priority_initiation_chats()
        if chat_id in priority_chats:
            logger.debug(
                f"Applying Manual Boost ({MANUAL_BOOST_VALUE}) for priority chat {chat_id}"
            )
            return MANUAL_BOOST_VALUE
        return 0.0

    async def calculate_elo(
        self, chat_id: int, base_instructions: Optional[str] = None
    ) -> float:
        async with self._cache_lock:
            if chat_id in self._elo_cache:
                logger.debug(
                    f"Returning cached Elo for chat {chat_id}: {self._elo_cache[chat_id]:.2f}"
                )
                return self._elo_cache[chat_id]

        logger.debug(f"Calculating Elo for chat {chat_id}...")
        try:
            history_data = await data_storage.load_chat_history(chat_id, self.data_dir)
            if not history_data["messages"]:
                logger.debug(
                    f"No message history found for chat {chat_id}. Calculating only manual boost."
                )
                manual_boost = self._get_manual_boost(chat_id)
                final_elo = manual_boost
            else:
                history_score = await self._calculate_history_score(
                    chat_id, history_data
                )

                persona_interests = await self._get_persona_interests(base_instructions)
                interest_bonus = await self._calculate_interest_bonus(
                    chat_id, history_data, persona_interests
                )

                manual_boost = self._get_manual_boost(chat_id)

                final_elo = history_score + interest_bonus + manual_boost

            logger.info(f"Calculated Elo for chat {chat_id}: {final_elo:.2f}")

            async with self._cache_lock:
                self._elo_cache[chat_id] = final_elo
            return final_elo

        except FileNotFoundError:
            logger.warning(
                f"History file not found for chat {chat_id} during Elo calculation. Elo = 0."
            )
            manual_boost = self._get_manual_boost(chat_id)
            return manual_boost
        except Exception as e:
            logger.error(
                f"Failed to calculate Elo for chat {chat_id}: {e}", exc_info=True
            )
            return 0.0

    async def get_top_chat_for_initiation(
        self, eligible_chat_ids: List[int], base_instructions: Optional[str] = None
    ) -> Optional[int]:
        if not eligible_chat_ids:
            logger.info("No eligible chats provided for initiation check.")
            return None

        logger.info(
            f"Finding top chat for initiation among {len(eligible_chat_ids)} candidates..."
        )

        elo_scores: Dict[int, float] = {}
        tasks = [
            self.calculate_elo(chat_id, base_instructions)
            for chat_id in eligible_chat_ids
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for chat_id, result in zip(eligible_chat_ids, results):
            if isinstance(result, Exception):
                logger.error(
                    f"Error calculating Elo for chat {chat_id} in get_top_chat: {result}"
                )
                elo_scores[chat_id] = 0.0
            else:
                elo_scores[chat_id] = result

        if not elo_scores:
            logger.warning("No Elo scores could be calculated.")
            return None

        top_chat_id = max(elo_scores, key=elo_scores.get)
        max_elo = elo_scores[top_chat_id]

        if max_elo <= 0:
            logger.info(
                "Maximum Elo score is zero or negative. No chat selected for initiation."
            )
            return None

        logger.info(
            f"Top chat for initiation selected: {top_chat_id} with Elo score: {max_elo:.2f}"
        )
        return top_chat_id

    async def clear_cache(self):
        async with self._cache_lock:
            self._elo_cache.clear()
            self._persona_interests = None
        logger.info("Elo cache cleared.")
