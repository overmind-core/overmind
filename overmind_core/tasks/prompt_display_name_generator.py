"""
Display name generation for prompts using LLM.
"""

import asyncio
import logging
from faker import Faker
from celery import shared_task
from overmind_core.overmind.llms import call_llm
from overmind_core.overmind.model_resolver import TaskType, resolve_model
from sqlalchemy import select, and_
from overmind_core.models.prompts import Prompt
from overmind_core.db.session import get_session_local
from overmind_core.tasks.prompts import DISPLAY_NAME_USER_PROMPT

logger = logging.getLogger(__name__)


async def generate_display_name_for_prompt(
    prompt_template: str,
    model: str = "",
) -> str:
    """
    Generate a display name for a prompt using LLM.

    Args:
        prompt_template: The prompt template text
        model: LLM model to use (resolved via model_resolver when empty)

    Returns:
        A generated display name string
    """
    try:
        # Get example spans if prompt_id is provided
        if not model:
            model = resolve_model(TaskType.DEFAULT)
        user_prompt = DISPLAY_NAME_USER_PROMPT.format(
            prompt_template=prompt_template[:1000],  # Limit template length
        )

        # Call LLM
        logger.info(f"Generating display name using {model}")
        display_name, _ = call_llm(
            input_text=user_prompt,
            system_prompt="",
            model=model,
        )

        # Clean up the response (remove quotes if present)
        display_name = display_name.strip().strip('"').strip("'")

        # Ensure it's not empty
        if not display_name or len(display_name) < 3:
            logger.warning("Generated display name too short, using fallback")
            return Faker().slug().replace("_", "-")

        logger.info(f"Generated display name: {display_name}")
        return display_name

    except Exception as e:
        logger.error(
            f"Failed to generate display name using LLM: {str(e)}", exc_info=True
        )
        # Fallback to random name
        fallback_name = Faker().slug().replace("_", "-")
        logger.info(f"Using fallback display name: {fallback_name}")
        return fallback_name


@shared_task(name="prompt_display_name_generator.generate_display_name_task")
def generate_display_name_task(prompt_id: str) -> dict[str, str]:
    """
    Celery task to generate a display name for a prompt in the background.

    Args:
        prompt_id: The prompt ID in format {project_id}_{version}_{slug}

    Returns:
        Dictionary with status and generated display_name or error message
    """

    async def _run_generation():
        from overmind_core.db.session import dispose_engine

        AsyncSessionLocal = get_session_local()
        async with AsyncSessionLocal() as session:
            try:
                # Parse prompt_id to get components
                project_id_str, version, slug = Prompt.parse_prompt_id(prompt_id)

                # Fetch the prompt
                stmt = select(Prompt).where(
                    and_(
                        Prompt.project_id == project_id_str,
                        Prompt.version == version,
                        Prompt.slug == slug,
                    )
                )
                result = await session.execute(stmt)
                prompt = result.scalar_one_or_none()

                if not prompt:
                    logger.error(f"Prompt not found: {prompt_id}")
                    return {
                        "status": "error",
                        "message": f"Prompt not found: {prompt_id}",
                    }

                # Generate display name
                logger.info(f"Generating display name for prompt {prompt_id}")
                display_name = await generate_display_name_for_prompt(
                    prompt_template=prompt.prompt,
                )

                # Update the prompt with the generated display name
                prompt.display_name = display_name
                await session.commit()

                logger.info(
                    f"Successfully updated display name for prompt {prompt_id}: {display_name}"
                )
                return {
                    "status": "success",
                    "prompt_id": prompt_id,
                    "display_name": display_name,
                }

            except Exception as e:
                logger.error(
                    f"Failed to generate display name for prompt {prompt_id}: {str(e)}",
                    exc_info=True,
                )
                return {"status": "error", "prompt_id": prompt_id, "message": str(e)}
            finally:
                # CRITICAL: Dispose of the engine to close all connections
                await dispose_engine()

    return asyncio.run(_run_generation())
