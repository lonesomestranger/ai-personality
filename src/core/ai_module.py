import asyncio
import logging
import random
import time
from typing import Any, Dict, List, Optional, Union

import google.generativeai as genai
from google.api_core.exceptions import (
    GoogleAPIError,
    InternalServerError,
    ResourceExhausted,
)
from google.generativeai.types import (
    ContentDict,
    GenerationConfig,
    HarmBlockThreshold,
    HarmCategory,
    HarmProbability,
)

from src.utils.config import SettingsManager

logger = logging.getLogger(__name__)

ALL_HARM_CATEGORIES = [
    HarmCategory.HARM_CATEGORY_HARASSMENT,
    HarmCategory.HARM_CATEGORY_HATE_SPEECH,
    HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
    HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
]

DEFAULT_SAFETY_SETTINGS = {
    category: HarmBlockThreshold.BLOCK_NONE for category in ALL_HARM_CATEGORIES
}

RELAXED_SAFETY_SETTINGS = {
    category: HarmBlockThreshold.BLOCK_NONE for category in ALL_HARM_CATEGORIES
}

DEFAULT_GENERATION_CONFIG = GenerationConfig(
    temperature=1,
    top_p=0.95,
    top_k=40,
    max_output_tokens=1024,
)

HARMFUL_PROBABILITIES = (HarmProbability.MEDIUM, HarmProbability.HIGH)
POTENTIALLY_HARMFUL_PROBABILITIES = (
    HarmProbability.LOW,
    HarmProbability.MEDIUM,
    HarmProbability.HIGH,
)


class AIContentSafetyError(Exception):
    pass


class AIGenerationError(Exception):
    pass


class AIModule:
    def __init__(self, api_key: str, settings_manager: SettingsManager):
        try:
            genai.configure(api_key=api_key)
            logger.info("Google Generative AI configured successfully.")
        except Exception as e:
            logger.critical(
                f"Failed to configure Google Generative AI: {e}", exc_info=True
            )
            raise ConnectionError(
                f"Failed to configure Google Generative AI: {e}"
            ) from e

        self.settings_manager = settings_manager
        self.max_retries = 3
        self.retry_delay_base = 5

        self._text_model = None
        self._vision_model = None

    def _get_text_model(
        self, safety_settings: Dict[HarmCategory, HarmBlockThreshold]
    ) -> genai.GenerativeModel:
        self._text_model = genai.GenerativeModel(
            "gemini-2.0-flash",
            generation_config=DEFAULT_GENERATION_CONFIG,
            safety_settings=safety_settings,
        )
        logger.debug(
            f"Initialized gemini-2.0-flash model with safety settings: {safety_settings}"
        )
        return self._text_model

    def _get_vision_model(
        self, safety_settings: Dict[HarmCategory, HarmBlockThreshold]
    ) -> genai.GenerativeModel:
        self._vision_model = genai.GenerativeModel(
            "gemini-2.0-flash",
            generation_config=DEFAULT_GENERATION_CONFIG,
            safety_settings=safety_settings,
        )
        logger.debug(
            f"Initialized gemini-2.0-flash (vision capable) model with safety settings: {safety_settings}"
        )
        return self._vision_model

    def _determine_safety_settings(
        self, chat_id: int
    ) -> Dict[HarmCategory, HarmBlockThreshold]:
        restricted_removed = (
            self.settings_manager.get_content_restriction_removed_chats()
        )
        if chat_id in restricted_removed:
            logger.debug(f"Using RELAXED safety settings for chat_id {chat_id}")
            return RELAXED_SAFETY_SETTINGS
        else:
            logger.debug(f"Using DEFAULT safety settings for chat_id {chat_id}")
            return DEFAULT_SAFETY_SETTINGS

    def _format_context_for_prompt(self, dialog_context: List[Dict[str, Any]]) -> str:
        if not dialog_context:
            return "Никакой предыдущей истории сообщений нет."

        formatted_lines = ["Вот последние сообщения в этом диалоге:"]
        for msg in dialog_context:
            sender = msg.get("sender", "Неизвестно")
            text = msg.get("text", "[Сообщение без текста]")
            prefix = "Ты:" if sender == "You" else f"{sender}:"

            is_reply = msg.get("reply_to_message_id")
            is_forward = msg.get("is_forward")
            media = msg.get("media_attached")
            meta = []
            if is_reply:
                meta.append(f"(ответ на {is_reply})")
            if is_forward:
                meta.append(f"(переслано из {msg.get('forward_source', '?')})")
            if media:
                meta.append("(с медиа)")

            meta_str = " ".join(meta) if meta else ""
            formatted_lines.append(f"{prefix} {text} {meta_str}".strip())

        return "\n".join(formatted_lines)

    async def generate_response(
        self,
        chat_id: int,
        base_instructions: str,
        dialog_context: List[Dict[str, Any]],
        incoming_message_text: Optional[str] = None,
        incoming_image_bytes: Optional[bytes] = None,
        image_mime_type: str = "image/jpeg",
    ) -> Optional[str]:
        safety_settings = self._determine_safety_settings(chat_id)
        model: genai.GenerativeModel
        prompt_parts: List[Union[str, ContentDict]] = []

        formatted_context = self._format_context_for_prompt(dialog_context)

        full_prompt_text = f"{base_instructions}\n\n{formatted_context}\n\n"
        sender_name = "Собеседник"
        if incoming_message_text and incoming_image_bytes:
            full_prompt_text += f"Новое сообщение от {sender_name} (с изображением): {incoming_message_text}\n\nОпиши твою реакцию на текст и изображение.\n\nТвой ответ:"
            model = self._get_vision_model(safety_settings)
            prompt_parts.append(full_prompt_text)
            prompt_parts.append(
                {"mime_type": image_mime_type, "data": incoming_image_bytes}
            )
            logger.debug(f"Using vision model for chat {chat_id} (text+image)")
        elif incoming_image_bytes:
            full_prompt_text += "Собеседник прислал изображение. Опиши твою реакцию на него в соответствии с инструкциями.\n\nТвой ответ:"
            model = self._get_vision_model(safety_settings)
            prompt_parts.append(full_prompt_text)
            prompt_parts.append(
                {"mime_type": image_mime_type, "data": incoming_image_bytes}
            )
            logger.debug(f"Using vision model for chat {chat_id} (image only)")
        elif incoming_message_text:
            full_prompt_text += f"Новое сообщение от {sender_name}: {incoming_message_text}\n\nТвой ответ:"
            model = self._get_text_model(safety_settings)
            prompt_parts.append(full_prompt_text)
            logger.debug(f"Using text model for chat {chat_id} (text only)")
        else:
            logger.debug(
                f"generate_response called for chat {chat_id} without incoming text or image (likely initiation)."
            )

            full_prompt_text = base_instructions
            model = self._get_text_model(safety_settings)
            prompt_parts.append(full_prompt_text)
            logger.debug(f"Using text model for chat {chat_id} (initiation)")

        for attempt in range(self.max_retries):
            try:
                logger.info(
                    f"Generating response for chat {chat_id} (Attempt {attempt + 1}/{self.max_retries})..."
                )
                start_time = time.monotonic()

                response = await model.generate_content_async(
                    contents=prompt_parts,
                )

                end_time = time.monotonic()
                logger.info(
                    f"Gemini response received for chat {chat_id} in {end_time - start_time:.2f}s."
                )

                if response.prompt_feedback.block_reason:
                    logger.warning(
                        f"Prompt blocked for chat {chat_id}. Reason: {response.prompt_feedback.block_reason}. "
                        f"Safety ratings: {response.prompt_feedback.safety_ratings}"
                    )
                    raise AIContentSafetyError(
                        f"Prompt blocked: {response.prompt_feedback.block_reason}"
                    )

                if not response.candidates:
                    logger.warning(
                        f"No candidates generated for chat {chat_id}. Prompt feedback: {response.prompt_feedback}"
                    )

                    prompt_potentially_harmful = any(
                        rating.probability in POTENTIALLY_HARMFUL_PROBABILITIES
                        for rating in response.prompt_feedback.safety_ratings
                    )
                    if prompt_potentially_harmful:
                        raise AIContentSafetyError(
                            "Response likely blocked by safety filters (no candidates, prompt ratings were LOW or higher)."
                        )
                    else:
                        raise AIGenerationError(
                            "No candidates generated, reason unclear (prompt ratings were NEGLIGIBLE)."
                        )

                candidate = response.candidates[0]
                finish_reason = getattr(candidate, "finish_reason", "UNKNOWN")
                safety_ratings = getattr(candidate, "safety_ratings", [])

                if finish_reason not in (
                    1,
                    "STOP",
                    2,
                    "MAX_TOKENS",
                ):
                    logger.warning(
                        f"Generation stopped unexpectedly for chat {chat_id}. Reason: {finish_reason}. "
                        f"Safety ratings: {safety_ratings}"
                    )

                    if finish_reason == 3 or finish_reason == "SAFETY":
                        raise AIContentSafetyError(
                            f"Candidate blocked by safety filters. Finish Reason: {finish_reason}"
                        )
                    elif finish_reason == 4 or finish_reason == "RECITATION":
                        raise AIGenerationError(
                            f"Generation stopped due to recitation filter: {finish_reason}"
                        )
                    else:
                        raise AIGenerationError(
                            f"Generation stopped due to unexpected reason: {finish_reason}"
                        )

                harmful_ratings_found = any(
                    rating.probability in HARMFUL_PROBABILITIES
                    for rating in safety_ratings
                )
                if harmful_ratings_found:
                    logger.warning(
                        f"Harmful probability (MEDIUM or HIGH) detected in ratings for chat {chat_id} "
                        f"despite finish reason '{finish_reason}'. Ratings: {safety_ratings}"
                    )

                if not candidate.content or not candidate.content.parts:
                    logger.warning(
                        f"Empty content or parts in candidate for chat {chat_id}. Finish Reason: {finish_reason}. Candidate: {candidate}"
                    )

                    potentially_harmful_ratings = any(
                        rating.probability in POTENTIALLY_HARMFUL_PROBABILITIES
                        for rating in safety_ratings
                    )
                    if potentially_harmful_ratings:
                        raise AIContentSafetyError(
                            "Generated candidate has empty content, potentially due to safety filters."
                        )
                    else:
                        raise AIGenerationError(
                            "Generated candidate has empty content or parts."
                        )

                response_text = "".join(
                    part.text
                    for part in candidate.content.parts
                    if hasattr(part, "text")
                ).strip()

                if not response_text:
                    logger.warning(
                        f"Extracted empty text from candidate parts for chat {chat_id}. Finish Reason: {finish_reason}."
                    )

                    potentially_harmful_ratings = any(
                        rating.probability in POTENTIALLY_HARMFUL_PROBABILITIES
                        for rating in safety_ratings
                    )
                    if potentially_harmful_ratings:
                        raise AIContentSafetyError(
                            "Extracted empty text, potentially due to safety filters."
                        )
                    else:
                        raise AIGenerationError(
                            "Extracted empty text from candidate parts."
                        )

                logger.info(f"Successfully generated response for chat {chat_id}.")
                response_text = response_text.replace("*", "")
                return response_text
            except AIContentSafetyError as e:
                logger.error(f"Content safety error for chat {chat_id}: {e}")
                return None
            except (InternalServerError, ResourceExhausted, GoogleAPIError) as e:
                logger.warning(
                    f"API Error on attempt {attempt + 1} for chat {chat_id}: {type(e).__name__} - {e}"
                )
                if attempt + 1 == self.max_retries:
                    logger.error(
                        f"API Error persisted after {self.max_retries} attempts for chat {chat_id}. Giving up."
                    )
                    return None

                delay = self.retry_delay_base * (2**attempt) + random.uniform(0, 1)
                logger.info(f"Retrying in {delay:.2f} seconds...")
                await asyncio.sleep(delay)
            except AIGenerationError as e:
                logger.error(f"Generation error for chat {chat_id}: {e}")
                return None
            except Exception as e:
                logger.exception(
                    f"Unexpected error during AI response generation for chat {chat_id}: {e}"
                )
                return None

        logger.error(
            f"Exited generation loop unexpectedly after {self.max_retries} attempts for chat {chat_id}."
        )
        return None

    async def analyze_persona(
        self, full_history: List[Dict[str, Any]]
    ) -> Optional[str]:
        logger.info("Attempting to analyze persona from history...")
        if not full_history:
            logger.warning("Cannot analyze persona from empty history.")
            return None

        safety_settings = DEFAULT_SAFETY_SETTINGS
        model = self._get_text_model(safety_settings)

        # TODO: Calculate token count for better precision if needed
        formatted_history = self._format_context_for_prompt(full_history[-1000:])
        analysis_prompt = (
            "Проанализируй предоставленную историю сообщений пользователя ('Ты:'). "
            "Основываясь ИСКЛЮЧИТЕЛЬНО на тексте сообщений пользователя 'Ты:', его стиле ответов, лексике, смайлах (если есть), "
            "типичной длине сообщений, упомянутых темах и интересах, сформулируй ОЧЕНЬ КРАТКОЕ описание его цифровой личности. "
            "Опиши ТОЛЬКО наблюдаемые факты из текста. Вывод должен быть в формате:\n"
            "Манера речи: [описание]\n"
            "Типичная длина сообщений: [описание]\n"
            "Характер: [описание, если можно судить]\n"
            "Ключевые интересы: [перечисление через запятую]\n\n"
            f"История для анализа:\n{formatted_history}\n\n"
            "Твой анализ:"
        )

        try:
            analysis_text = await self.generate_response(
                chat_id=0,
                base_instructions=analysis_prompt,
                dialog_context=[],
            )
            if analysis_text:
                logger.info("Persona analysis successful.")
                return analysis_text.strip()
            else:
                logger.error("Persona analysis failed to generate text.")
                return None
        except Exception as e:
            logger.exception(f"Error during persona analysis: {e}")
            return None
