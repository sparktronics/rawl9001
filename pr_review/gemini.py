"""Vertex AI / Gemini integration."""

import logging
import time

from google import genai

from pr_review.prompt import load_system_prompt
from pr_review.utils import timed_operation

logger = logging.getLogger("pr_review")


def call_gemini(config: dict, prompt: str, debug: bool = False, pr_id: int = None) -> str:
    """Send prompt to Gemini via Vertex AI and return response.

    Args:
        config: Configuration dictionary
        prompt: User prompt to send to Gemini
        debug: If True, save prompt inputs to GCS
        pr_id: Pull request ID (required when debug=True)

    Returns:
        Response text from Gemini
    """

    model_name = config["GEMINI_MODEL"]
    project = config["VERTEX_PROJECT"]
    location = config["VERTEX_LOCATION"]

    # Load system prompt from GCS or fallback to hardcoded
    system_prompt = load_system_prompt(config["GCS_BUCKET"])

    # Save prompt inputs if debug mode is enabled
    if debug and pr_id:
        try:
            from pr_review.storage import save_prompt_input
            save_prompt_input(config["GCS_BUCKET"], pr_id, system_prompt, prompt)
            logger.info(f"[GEMINI] Debug mode: Prompt input saved to GCS for PR #{pr_id}")
        except Exception as e:
            logger.warning(f"[GEMINI] Failed to save prompt input (non-blocking): {e}")

    logger.info(f"[GEMINI] Calling Vertex AI | Model: {model_name} | Project: {project} | Location: {location}")
    logger.info(f"[GEMINI] Prompt size: {len(prompt)} chars | System prompt: {len(system_prompt)} chars")
    logger.debug(f"[GEMINI] Config: max_output_tokens=8192, temperature=0.2")

    with timed_operation() as elapsed:
        try:
            # Initialize the GenAI client for Vertex AI
            client = genai.Client(
                vertexai=True,
                project=project,
                location=location,
            )

            logger.debug(f"[GEMINI] Client initialized in {elapsed():.0f}ms")

            # Generate content with system instruction
            generate_start = time.time()
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config={
                    "system_instruction": system_prompt,
                    "max_output_tokens": 40036,  # Allow for very long responses due to java code
                    "temperature": 0.2,  # Lower for more focused analysis
                },
            )

            generate_time = (time.time() - generate_start) * 1000
            response_size = len(response.text) if response.text else 0

            logger.info(f"[GEMINI] Response received | {response_size} chars | Generate: {generate_time:.0f}ms | Total: {elapsed():.0f}ms")

            # Log usage metadata if available
            if hasattr(response, 'usage_metadata') and response.usage_metadata:
                usage = response.usage_metadata
                logger.info(f"[GEMINI] Tokens - Input: {getattr(usage, 'prompt_token_count', 'N/A')} | Output: {getattr(usage, 'candidates_token_count', 'N/A')}")

            return response.text

        except Exception as e:
            logger.error(f"[GEMINI] API call FAILED | {elapsed():.0f}ms | Error type: {type(e).__name__}")
            logger.error(f"[GEMINI] Error details: {str(e)}")
            raise
