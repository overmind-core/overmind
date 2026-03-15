"""
Celery task for automatic agent discovery.

This task runs periodically to discover agents by mapping spans to prompt templates using template extraction.
"""

import asyncio
import hashlib
import logging
import json
import uuid
from typing import Any
from uuid import UUID

from faker import Faker
from sqlalchemy import select, func, and_, cast
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from overmind.celery_app import celery_app
from overmind.core.template_extractor.extractor import _parse_template_string
from overmind.core.template_extractor.helpers import tokenize, token_values
from overmind.db.session import get_session_local
from overmind.models.traces import SpanModel, TraceModel
from overmind.models.prompts import (
    Prompt,
    PROMPT_STATUS_ACTIVE,
    PROMPT_STATUS_PENDING,
    PROMPT_STATUS_REJECTED,
    PROMPT_STATUS_SUPERSEDED,
)
from overmind.models.iam.projects import Project
from overmind.models.jobs import Job
from overmind.api.v1.endpoints.jobs import JobType, JobStatus
from overmind.core.template_extractor import (
    extract_templates,
    match_string_to_template,
    ExtractionConfig,
    Template,
)
from overmind.tasks.criteria_generator import generate_criteria_task
from overmind.tasks.agent_description_generator import (
    generate_initial_agent_description_task,
)
from overmind.models.suggestions import Suggestion as SuggestionModel
from overmind.tasks.utils.task_lock import with_task_lock
from overmind.tasks.prompt_display_name_generator import (
    generate_display_name_for_prompt,
)

logger = logging.getLogger(__name__)

# Minimum spans required before agent discovery is eligible
MIN_SPANS_FOR_AGENT_DISCOVERY = 30

# Maximum number of unmapped spans loaded per discovery run.
# Bounds ORM object memory (each span carries input/output/metadata JSONB).
# The task runs every 5 minutes so remaining spans are processed on the next cycle.
_MAX_UNMAPPED_SPANS_PER_RUN = 2000


async def validate_agent_discovery_eligibility(
    project_id: UUID, session
) -> tuple[bool, str | None, dict[str, Any] | None]:
    """
    Validate if a project is eligible for agent discovery.

    Used by both user-triggered (API) and system-triggered (Celery beat) paths
    before creating a job record, so that all eligibility logic lives in one place.

    Args:
        project_id: The project ID to validate
        session: Database session

    Returns:
        Tuple of (is_eligible, error_message, stats)
        - is_eligible: True if all checks pass
        - error_message: Reason if checks fail, None otherwise
        - stats: Dictionary with check results for debugging
    """
    stats = {}

    # Check 1: Project has at least 30 spans with usable LLM input.
    # The input column is JSONB with nullable=False and default={}, so every span
    # has a non-null input. Non-LLM spans (tool executions, framework spans, etc.)
    # default to input={}, which _get_span_input_text_merged() treats as unusable
    # (falsy check: `if not span.input: return None`). We explicitly exclude those
    # empty-JSONB-object and empty-JSONB-array inputs so the threshold is measured
    # against spans the extractor will actually process.
    empty_obj = cast("{}", JSONB)
    empty_arr = cast("[]", JSONB)
    empty_str = cast('""', JSONB)
    total_spans_stmt = (
        select(func.count(SpanModel.span_id))
        .select_from(SpanModel)
        .join(TraceModel, SpanModel.trace_id == TraceModel.trace_id)
        .where(
            and_(
                TraceModel.project_id == project_id,
                SpanModel.input != empty_obj,
                SpanModel.input != empty_arr,
                SpanModel.input != empty_str,
            )
        )
    )

    result = await session.execute(total_spans_stmt)
    total_count = result.scalar() or 0
    stats["total_spans"] = total_count

    if total_count < MIN_SPANS_FOR_AGENT_DISCOVERY:
        return (
            False,
            f"Agent discovery requires at least {MIN_SPANS_FOR_AGENT_DISCOVERY} spans with LLM input, but only {total_count} have been collected.",
            stats,
        )

    # Check 2: Project has unmapped spans
    unmapped_spans_stmt = (
        select(func.count(SpanModel.span_id))
        .select_from(SpanModel)
        .join(TraceModel, SpanModel.trace_id == TraceModel.trace_id)
        .where(and_(TraceModel.project_id == project_id, SpanModel.prompt_id.is_(None)))
    )

    result = await session.execute(unmapped_spans_stmt)
    unmapped_count = result.scalar() or 0
    stats["unmapped_spans"] = unmapped_count

    if unmapped_count == 0:
        return (
            False,
            "Everything is up to date — all requests have already been organised into templates.",
            stats,
        )

    # Check 3: At least one unmapped span has usable LLM input text.
    # Same empty-JSONB exclusion as Check 1 — input={} and input=[] are the default
    # for non-LLM spans and are discarded by _get_span_input_text_merged().
    unmapped_with_input_stmt = (
        select(func.count(SpanModel.span_id))
        .select_from(SpanModel)
        .join(TraceModel, SpanModel.trace_id == TraceModel.trace_id)
        .where(
            and_(
                TraceModel.project_id == project_id,
                SpanModel.prompt_id.is_(None),
                SpanModel.input != empty_obj,
                SpanModel.input != empty_arr,
                SpanModel.input != empty_str,
            )
        )
    )
    result = await session.execute(unmapped_with_input_stmt)
    unmapped_with_input_count = result.scalar() or 0
    stats["unmapped_spans_with_input"] = unmapped_with_input_count

    if unmapped_with_input_count == 0:
        return (
            False,
            f"Found {unmapped_count} unmapped span(s), but none contain usable LLM input content.",
            stats,
        )

    # Check 4: No existing PENDING/RUNNING agent discovery job
    existing_job_check = await session.execute(
        select(Job).where(
            and_(
                Job.project_id == project_id,
                Job.job_type == JobType.AGENT_DISCOVERY.value,
                Job.status.in_([JobStatus.PENDING.value, JobStatus.RUNNING.value]),
            )
        )
    )
    existing_job = existing_job_check.scalar_one_or_none()

    if existing_job:
        return (
            False,
            "A template extraction is already in progress. Please wait for it to finish.",
            stats,
        )

    # All checks passed!
    logger.info(
        f"Project {project_id} is eligible for agent discovery: {total_count} total spans, "
        f"{unmapped_count} unmapped, {unmapped_with_input_count} with input text"
    )
    return True, None, stats


def _sanitize_for_jsonb(obj):
    """
    Recursively strip null bytes (\x00) from strings in a dict/list structure.
    PostgreSQL JSONB cannot store \u0000 null characters.
    """
    if isinstance(obj, str):
        return obj.replace("\x00", "")
    elif isinstance(obj, dict):
        return {k: _sanitize_for_jsonb(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_sanitize_for_jsonb(item) for item in obj]
    return obj


def _unwrap_content_parts(content: str) -> str:
    """Unwrap Gemini-style content-parts JSON if present.

    Gemini traces store message content as a JSON string like:
        [{"type": "text", "text": "...actual prompt..."}]
    This wrapper adds shared JSON tokens that confuse the template extractor
    into merging structurally different agents. Strip it down to plain text.
    """
    try:
        inner = json.loads(content)
    except (json.JSONDecodeError, TypeError, ValueError):
        return content

    if isinstance(inner, list) and inner:
        text_parts = []
        for part in inner:
            if isinstance(part, dict) and part.get("type") == "text":
                text_parts.append(part.get("text", ""))
        if text_parts:
            return "\n".join(text_parts)

    return content


def _get_span_input_text_merged(span: SpanModel) -> str | None:
    """
    Extract the input text from a span for template matching.

    For agentic spans (with tool calls), extracts only the actual prompt:
    - System messages (if any)
    - User messages

    Excludes:
    - Assistant messages (tool calls and responses)
    - Tool result messages

    This ensures template extraction identifies the actual prompt template,
    not the tool interactions.

    Args:
        span: The span model instance

    Returns:
        The input text string, or None if not available
    """
    if not span.input:
        return None

    try:
        parsed = (
            span.input
            if isinstance(span.input, (list, dict))
            else json.loads(span.input)
        )
    except (json.JSONDecodeError, TypeError):
        return None

    if isinstance(parsed, list):
        # For list format (conversation), extract only prompt-relevant messages
        parts = []
        for item in parsed:
            if not isinstance(item, dict):
                # Non-dict items, just append as-is
                if isinstance(item, str):
                    parts.append(item)
                continue

            # Check message role
            role = item.get("role", "").lower()

            # Only extract user and system messages (the actual prompt)
            # Skip assistant and tool messages (responses/intermediary steps)
            if role in ("user", "system"):
                content = item.get("content")
                if content:
                    content = _unwrap_content_parts(str(content))
                    role_prefix = f"[{role.upper()}] " if role == "system" else ""
                    parts.append(role_prefix + content)
            # Skip assistant and tool roles - they're not part of the prompt template
            elif role in ("assistant", "tool"):
                continue
            # For items without role, check for content
            elif "content" in item:
                content = _unwrap_content_parts(str(item["content"]))
                parts.append(content)

        return "\n".join(parts) if parts else None
    elif isinstance(parsed, dict) and "content" in parsed:
        # Simple dict format
        return _unwrap_content_parts(str(parsed["content"]))

    return None


def _generate_prompt_hash(template_string: str) -> str:
    """
    Generate a hash for a prompt template.

    Args:
        template_string: The template string

    Returns:
        A hash string
    """
    return hashlib.sha256(template_string.encode()).hexdigest()


async def _create_prompt_from_template(
    db: AsyncSession,
    template: Template,
    project_id: UUID,
    user_id: UUID,
) -> Prompt:
    """
    Create a new Prompt record from a template.
    Does NOT trigger criteria generation - caller is responsible for that
    after span mappings have been committed.

    Args:
        db: Database session
        template: The extracted template
        project_id: Project ID
        user_id: User ID to associate with the prompt

    Returns:
        The created Prompt instance
    """
    prompt_hash = _generate_prompt_hash(template.template_string)

    # Check if a prompt with this hash already exists in the project (prevents duplicates on retry)
    hash_check_stmt = (
        select(Prompt)
        .where(and_(Prompt.hash == prompt_hash, Prompt.project_id == project_id))
        .order_by(Prompt.version.desc())
        .limit(1)
    )
    result = await db.execute(hash_check_stmt)
    existing_by_hash = result.scalar_one_or_none()

    if existing_by_hash:
        logger.info(
            f"Prompt with same template hash already exists: {existing_by_hash.prompt_id}, reusing"
        )
        return existing_by_hash

    # Generate a unique slug — regenerate until no collision exists so this
    # template is always stored as a completely independent prompt (version=1)
    # rather than accidentally being versioned under an existing prompt's slug.
    _faker = Faker()
    slug = _faker.slug().replace("_", "-")
    while True:
        slug_check_stmt = (
            select(Prompt)
            .where(and_(Prompt.slug == slug, Prompt.project_id == project_id))
            .limit(1)
        )
        slug_result = await db.execute(slug_check_stmt)
        if slug_result.scalar_one_or_none() is None:
            break
        slug = _faker.slug().replace("_", "-")

    # Generate display name for the prompt
    # Use the template examples if available
    display_name = await generate_display_name_for_prompt(
        prompt_template=template.template_string,
    )

    # Always create as a fresh prompt (version=1) — a newly detected template
    # that doesn't match any existing prompt is an independent prompt, not a
    # new version of something else.
    new_prompt = Prompt(
        slug=slug,
        hash=prompt_hash,
        prompt=template.template_string,
        display_name=display_name,
        user_id=user_id,
        project_id=project_id,
        version=1,
    )

    db.add(new_prompt)
    # Use flush (not commit) so callers control transaction boundaries.
    # All PK fields (slug, project_id, version) are set in Python so
    # prompt_id is available immediately without a round-trip refresh.
    await db.flush()

    return new_prompt


async def _get_existing_templates(
    db: AsyncSession,
    project_id: UUID,
) -> dict[str, tuple[Template, str]]:
    """
    Get existing templates from prompts in this project.

    All prompt versions are loaded so that spans can be matched to whichever
    version's template they actually use. Active versions are sorted first so
    they win tie-breaks when the matching loop iterates the dict.

    Args:
        db: Database session
        project_id: Project ID

    Returns:
        Dictionary mapping template strings to (Template, prompt_id) tuples
    """
    stmt = (
        select(Prompt)
        .where(Prompt.project_id == project_id)
        .order_by(Prompt.version.desc())
    )
    result = await db.execute(stmt)
    prompts = result.scalars().all()

    templates: dict[str, tuple[Template, str]] = {}

    for prompt in prompts:
        if prompt.prompt in templates:
            continue

        elements = _parse_template_string(prompt.prompt)

        anchor_tokens = [elem.value for elem in elements if not elem.is_variable]
        anchor_token_list = []
        for anchor_text in anchor_tokens:
            tokens = tokenize(anchor_text)
            anchor_token_list.extend(token_values(tokens))

        template = Template(
            template_string=prompt.prompt,
            elements=elements,
            anchor_tokens=anchor_token_list,
            matches=[],
        )
        templates[prompt.prompt] = (template, prompt.prompt_id)

    return templates


async def _map_spans_to_templates(
    db: AsyncSession,
    project_id: UUID,
    user_id: UUID,
) -> dict[str, int]:
    """
    Map unmapped spans to templates for a specific project.

    Args:
        db: Database session
        project_id: Project ID
        user_id: User ID for creating new prompts

    Returns:
        Dictionary with statistics: {'mapped': N, 'new_templates': M, 'unmapped': K}
    """
    # Get unmapped spans for traces in this project, capped to avoid loading
    # unbounded ORM objects into memory. Ordered oldest-first so earlier spans
    # are mapped before newer ones; remaining spans are picked up on the next run.
    unmapped_spans_stmt = (
        select(SpanModel)
        .join(TraceModel, SpanModel.trace_id == TraceModel.trace_id)
        .where(and_(TraceModel.project_id == project_id, SpanModel.prompt_id.is_(None)))
        .order_by(SpanModel.start_time_unix_nano.asc())
        .limit(_MAX_UNMAPPED_SPANS_PER_RUN)
    )

    result = await db.execute(unmapped_spans_stmt)
    unmapped_spans = result.scalars().all()

    # Extract input texts from unmapped spans, building a lookup for O(1) match resolution.
    # Multiple spans can share the same input text (e.g. identical prompts), so we store
    # a list per text and pop one span per match to avoid double-mapping.
    span_texts: list[tuple[SpanModel, str]] = []
    text_to_spans: dict[str, list[SpanModel]] = {}
    for span in unmapped_spans:
        text = _get_span_input_text_merged(span)
        if text:
            span_texts.append((span, text))
            text_to_spans.setdefault(text, []).append(span)

    # Check if any spans have been mapped before
    mapped_spans_count_stmt = (
        select(func.count(SpanModel.span_id))
        .select_from(SpanModel)
        .join(TraceModel, SpanModel.trace_id == TraceModel.trace_id)
        .where(
            and_(TraceModel.project_id == project_id, SpanModel.prompt_id.isnot(None))
        )
    )

    result = await db.execute(mapped_spans_count_stmt)
    mapped_count = result.scalar()

    stats = {"mapped": 0, "new_templates": 0, "unmapped": 0}

    # Track new prompts that need criteria generation (triggered AFTER commit)
    new_prompt_ids: list[str] = []

    if mapped_count == 0:
        # First run: enforce the minimum span threshold against the ACTUAL set of
        # usable spans (those that survived _get_span_input_text_merged filtering),
        # not just the raw DB count used in validate_agent_discovery_eligibility.
        # Non-LLM spans (framework spans, tool executions, etc.) may have non-empty
        # inputs in formats the extractor cannot use (e.g. dict without "content",
        # list with only assistant/tool messages, plain strings, etc.) and would
        # inflate the SQL count while being silently skipped here.
        if len(span_texts) < MIN_SPANS_FOR_AGENT_DISCOVERY:
            logger.info(
                f"Project {project_id}: Only {len(span_texts)} span(s) have usable "
                f"LLM input (need {MIN_SPANS_FOR_AGENT_DISCOVERY} for first-run "
                f"discovery), skipping template extraction."
            )
            stats["unmapped"] = len(unmapped_spans)
            return stats

        # No spans have been mapped yet - extract templates from all unmapped spans
        logger.info(
            f"Project {project_id}: No mapped spans found, extracting templates from {len(span_texts)} spans"
        )

        texts_only = [text for _, text in span_texts]
        config = ExtractionConfig(min_group_size=2)
        extraction_result = extract_templates(texts_only, config)

        # Create prompts and map spans
        for template in extraction_result.templates:
            prompt = await _create_prompt_from_template(
                db, template, project_id, user_id
            )
            stats["new_templates"] += 1
            new_prompt_ids.append(prompt.prompt_id)

            sanitized_vars = _sanitize_for_jsonb(
                {m.original_string: m.variables for m in template.matches}
            )
            for match in template.matches:
                candidates = text_to_spans.get(match.original_string)
                if candidates:
                    span = candidates.pop(0)
                    span.prompt_id = prompt.prompt_id
                    span.input_params = sanitized_vars[match.original_string]
                    stats["mapped"] += 1

        await db.commit()

        # Trigger criteria and agent description generation AFTER span mappings are committed
        for prompt_id in new_prompt_ids:
            logger.info(f"Triggering criteria generation for new prompt {prompt_id}")
            generate_criteria_task.delay(prompt_id=prompt_id)
            logger.info(
                f"Triggering agent description generation for new prompt {prompt_id}"
            )
            generate_initial_agent_description_task.delay(prompt_id=prompt_id)

        # Count unmapped
        stats["unmapped"] = len(span_texts) - stats["mapped"]

    else:
        # Some spans have been mapped - try to use existing templates first
        logger.info(
            f"Project {project_id}: {mapped_count} spans already mapped, using existing templates"
        )

        existing_templates = await _get_existing_templates(db, project_id)

        unmatched_span_texts: list[tuple[SpanModel, str]] = []

        # Try to match each unmapped span to existing templates
        for span, text in span_texts:
            matched = False
            for template, prompt_id in existing_templates.values():
                match = match_string_to_template(text, template)
                if match:
                    # Use the prompt_id directly from the existing templates
                    span.prompt_id = prompt_id
                    # Sanitize variables to strip null bytes before storing
                    span.input_params = _sanitize_for_jsonb(match.variables)
                    stats["mapped"] += 1
                    matched = True
                    break

            if not matched:
                unmatched_span_texts.append((span, text))

        await db.commit()

        # Try to extract new templates from unmatched spans
        if unmatched_span_texts:
            logger.info(
                f"Project {project_id}: {len(unmatched_span_texts)} spans didn't match existing templates, extracting new templates"
            )

            texts_only = [text for _, text in unmatched_span_texts]
            unmatched_text_to_spans: dict[str, list[SpanModel]] = {}
            for span, text in unmatched_span_texts:
                unmatched_text_to_spans.setdefault(text, []).append(span)

            config = ExtractionConfig(min_group_size=2)
            extraction_result = extract_templates(texts_only, config)

            if extraction_result.templates:
                # Create new prompts and map spans
                for template in extraction_result.templates:
                    prompt = await _create_prompt_from_template(
                        db, template, project_id, user_id
                    )
                    stats["new_templates"] += 1
                    new_prompt_ids.append(prompt.prompt_id)

                    sanitized_vars = _sanitize_for_jsonb(
                        {m.original_string: m.variables for m in template.matches}
                    )
                    for match in template.matches:
                        candidates = unmatched_text_to_spans.get(match.original_string)
                        if candidates:
                            span = candidates.pop(0)
                            span.prompt_id = prompt.prompt_id
                            span.input_params = sanitized_vars[match.original_string]
                            stats["mapped"] += 1

                await db.commit()

                # Trigger criteria and agent description generation AFTER span mappings are committed
                for prompt_id in new_prompt_ids:
                    logger.info(
                        f"Triggering criteria generation for new prompt {prompt_id}"
                    )
                    generate_criteria_task.delay(prompt_id=prompt_id)
                    logger.info(
                        f"Triggering agent description generation for new prompt {prompt_id}"
                    )
                    generate_initial_agent_description_task.delay(prompt_id=prompt_id)
            else:
                logger.info(
                    f"Project {project_id}: No new templates could be extracted from unmatched spans"
                )

        stats["unmapped"] = len(span_texts) - stats["mapped"]

    # After mapping, check if any pending version now has real production
    # spans and should be auto-accepted.
    await _auto_accept_pending_versions(db, project_id)

    logger.info(f"Project {project_id}: Mapping complete - {stats}")
    return stats


async def _auto_accept_pending_versions(db: AsyncSession, project_id: UUID) -> None:
    """
    For each prompt slug in the project, check if a pending (non-active) version
    has at least one real production span.  If so, flip ``is_active`` to that
    version and mark the associated suggestion as accepted.
    """
    # Get all distinct slugs that have multiple versions (i.e. a pending version exists)
    slugs_q = await db.execute(
        select(Prompt.slug)
        .where(Prompt.project_id == project_id)
        .group_by(Prompt.slug)
        .having(func.count(Prompt.version) > 1)
    )
    slugs_with_versions = [row[0] for row in slugs_q.all()]

    for slug in slugs_with_versions:
        versions_q = await db.execute(
            select(Prompt)
            .where(and_(Prompt.slug == slug, Prompt.project_id == project_id))
            .order_by(Prompt.version.desc())
        )
        all_versions = versions_q.scalars().all()
        active_prompt = next(
            (v for v in all_versions if v.status == PROMPT_STATUS_ACTIVE), None
        )
        max_prompt = all_versions[0]

        if not active_prompt or max_prompt.version == active_prompt.version:
            continue
        if max_prompt.status != PROMPT_STATUS_PENDING:
            continue

        # Check for real production spans on the pending (max) version
        real_span_check = await db.execute(
            select(func.count(SpanModel.span_id)).where(
                and_(
                    SpanModel.prompt_id == max_prompt.prompt_id,
                    SpanModel.exclude_system_spans(),
                )
            )
        )
        real_span_count = real_span_check.scalar() or 0

        if real_span_count >= 1:
            for v in all_versions:
                if (
                    v.version != max_prompt.version
                    and v.status != PROMPT_STATUS_REJECTED
                ):
                    v.status = PROMPT_STATUS_SUPERSEDED
            max_prompt.status = PROMPT_STATUS_ACTIVE

            pending_sugg_q = await db.execute(
                select(SuggestionModel).where(
                    and_(
                        SuggestionModel.prompt_slug == slug,
                        SuggestionModel.project_id == project_id,
                        SuggestionModel.new_prompt_version == max_prompt.version,
                        SuggestionModel.status == "pending",
                    )
                )
            )
            sugg = pending_sugg_q.scalar_one_or_none()
            if sugg:
                sugg.status = "accepted"

            await db.commit()
            logger.info(
                f"Auto-accepted version {max_prompt.version} for slug '{slug}' "
                f"(project {project_id}) — {real_span_count} production span(s) detected"
            )


async def _discover_agents(
    celery_task_id: str | None = None, job_id: str | None = None
) -> dict[str, Any]:
    """
    Async function to discover agents across all projects by mapping spans to templates.

    Args:
        celery_task_id: The Celery task ID for tracking
        job_id: Optional existing job ID (when dispatched by the reconciler).
                When provided, the task only processes the project that the job
                belongs to and updates that single job record.

    Returns:
        Dictionary with overall statistics
    """
    from overmind.db.session import dispose_engine

    logger.info("Starting agent discovery for all projects")

    overall_stats = {
        "projects_processed": 0,
        "total_mapped": 0,
        "total_new_templates": 0,
        "total_unmapped": 0,
        "errors": [],
    }

    try:
        AsyncSessionLocal = get_session_local()
        async with AsyncSessionLocal() as db:
            try:
                # If we have a job_id, scope to that job's project only
                existing_job = None
                target_project_id = None
                if job_id:
                    result = await db.execute(select(Job).where(Job.job_id == job_id))
                    existing_job = result.scalar_one_or_none()
                    if existing_job:
                        target_project_id = existing_job.project_id
                        logger.info(
                            f"Processing job {job_id} scoped to project {target_project_id}"
                        )
                    else:
                        logger.warning(
                            f"Job {job_id} not found, processing all projects"
                        )

                # Get projects to process
                stmt = (
                    select(Project)
                    .where(Project.is_active.is_(True))
                    .options(selectinload(Project.users))
                )
                if target_project_id:
                    stmt = stmt.where(Project.project_id == target_project_id)

                result = await db.execute(stmt)
                projects = result.scalars().all()

                logger.info(f"Found {len(projects)} active projects to process")

                for project in projects:
                    job = None
                    try:
                        # Get a user from the project to use for creating prompts
                        if not project.users:
                            logger.warning(
                                f"Project {project.project_id} has no users, skipping"
                            )
                            continue

                        user_id = project.users[0].user_id

                        # If we have the existing job and it matches this project, use it
                        if (
                            existing_job
                            and existing_job.project_id == project.project_id
                        ):
                            job = existing_job
                            logger.info(
                                f"Using existing job {job_id} for agent_discovery in project {project.project_id}"
                            )
                        else:
                            # Validate eligibility before creating a job
                            (
                                is_eligible,
                                error_message,
                                validation_stats,
                            ) = await validate_agent_discovery_eligibility(
                                project.project_id, db
                            )

                            if not is_eligible:
                                logger.info(
                                    f"Project {project.project_id} not eligible for agent discovery, skipping: {error_message}"
                                )
                                continue

                            # Create a new Job entry for this project
                            job = Job(
                                job_id=uuid.uuid4(),
                                job_type=JobType.AGENT_DISCOVERY.value,
                                project_id=project.project_id,
                                prompt_slug=None,  # Project-wide job
                                status=JobStatus.RUNNING.value,
                                celery_task_id=celery_task_id,
                                result={"validation_stats": validation_stats},
                                triggered_by_user_id=None,  # System-triggered
                            )
                            db.add(job)
                            await db.commit()
                            await db.refresh(job)
                            logger.info(
                                f"Created job entry (running) for agent_discovery in project {project.project_id}"
                            )

                        # Process this project
                        stats = await _map_spans_to_templates(
                            db, project.project_id, user_id
                        )

                        overall_stats["projects_processed"] += 1
                        overall_stats["total_mapped"] += stats.get("mapped", 0)
                        overall_stats["total_new_templates"] += stats.get(
                            "new_templates", 0
                        )
                        overall_stats["total_unmapped"] += stats.get("unmapped", 0)

                        # Update job to completed
                        job.status = JobStatus.COMPLETED.value
                        if stats.get("new_templates", 0) > 0:
                            job.result = stats
                        else:
                            job.result = {
                                "reason": "No new templates created",
                                "stats": stats,
                            }
                        await db.commit()
                        logger.info(
                            f"Updated job entry to completed for agent_discovery in project {project.project_id}"
                        )

                    except Exception as e:
                        error_msg = (
                            f"Error processing project {project.project_id}: {str(e)}"
                        )
                        logger.error(error_msg, exc_info=True)
                        overall_stats["errors"].append(error_msg)

                        # Rollback dirty session state before updating job status
                        try:
                            await db.rollback()
                        except Exception:
                            pass

                        # Update job to failed if it was created
                        if job:
                            try:
                                job.status = JobStatus.FAILED.value
                                job.result = {"error": str(e)}
                                await db.commit()
                                logger.info(
                                    f"Updated job entry to failed for project {project.project_id}"
                                )
                            except Exception:
                                logger.exception(
                                    "Failed to update job status to failed"
                                )

                logger.info(f"Completed agent discovery: {overall_stats}")

            except Exception as e:
                error_msg = f"Error in agent discovery: {str(e)}"
                logger.error(error_msg, exc_info=True)
                overall_stats["errors"].append(error_msg)
    finally:
        # CRITICAL: Dispose of the engine to close all connections
        # This prevents event loop errors when the same worker runs the task again
        await dispose_engine()

    return overall_stats


@celery_app.task(name="agent_discovery.discover_agents", bind=True)
@with_task_lock(lock_name="agent_discovery")
def discover_agents(self) -> dict[str, Any]:
    """
    Periodic Celery beat task to discover agents across all projects.

    Runs every 5 minutes, checks all active projects, creates RUNNING jobs
    for eligible ones and maps their spans to prompt templates.

    Uses distributed locking to prevent concurrent executions.
    If a previous instance is still running, new executions are cancelled.

    Returns:
        Dictionary with overall statistics
    """
    return asyncio.run(_discover_agents(celery_task_id=self.request.id))


@celery_app.task(name="agent_discovery.run_agent_discovery", bind=True)
def run_agent_discovery_task(self, job_id: str) -> dict[str, Any]:
    """
    Celery task to run agent discovery for a single job (dispatched by API or reconciler).

    Unlike the periodic discover_agents task, this is not locked and processes
    only the project that owns the given job.

    Args:
        job_id: The existing job ID to process

    Returns:
        Dictionary with overall statistics
    """
    return asyncio.run(_discover_agents(job_id=job_id))
