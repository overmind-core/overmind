"""
Unit tests for the template extraction pipeline.

Validates the full production path: span input format → _get_span_input_text_merged
→ extract_templates, ensuring structurally different agents are separated and
variables are correctly identified.

Uses Faker to generate realistic variable values for 10 diverse template types
across three span input formats (OpenAI, Gemini, single-user-message).
"""

import json
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from faker import Faker

from overmind.core.template_extractor import ExtractionConfig, extract_templates
from overmind.tasks.agent_discovery import (
    _get_span_input_text_merged,
    _get_system_prompt_text,
    _group_by_system_prompt,
    _unwrap_content_parts,
)

fake = Faker()
Faker.seed(42)

PRODUCTION_CONFIG = ExtractionConfig(min_group_size=2)

SAMPLES_PER_TEMPLATE = 30


# ---------------------------------------------------------------------------
# Helpers — mirror the three span input shapes seen in production
# ---------------------------------------------------------------------------


def _make_openai_span(system: str, user: str) -> SimpleNamespace:
    """OpenAI/Anthropic style: separate system + user messages."""
    return SimpleNamespace(
        input=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
    )


def _make_gemini_span(system: str, user: str) -> SimpleNamespace:
    """Gemini style: system + user concatenated inside a JSON parts envelope."""
    merged = f"{system}\n\nUser: {user}"
    content_parts = json.dumps([{"type": "text", "text": merged}])
    return SimpleNamespace(input=[{"role": "user", "content": content_parts}])


def _make_single_user_span(text: str) -> SimpleNamespace:
    """Single user message (no system prompt)."""
    return SimpleNamespace(input=[{"role": "user", "content": text}])


# ---------------------------------------------------------------------------
# Template catalogue — 10 structurally different templates
# ---------------------------------------------------------------------------


@dataclass
class TemplateDef:
    name: str
    system: str  # fixed portion (empty string if single-user format)
    user_fn: callable  # () -> str, generates a new user message
    format: str  # "openai" | "gemini" | "single"


TEMPLATE_DEFS: list[TemplateDef] = [
    # 1. Simple QA — short system prompt, 1 variable (question)
    TemplateDef(
        name="simple_qa",
        system="You are a helpful assistant. Answer in one sentence.",
        user_fn=lambda: fake.sentence(nb_words=8),
        format="openai",
    ),
    # 2. Tool executor — structured system prompt (Gemini format)
    TemplateDef(
        name="tool_executor",
        system=(
            "<<FUNCTION_EXECUTOR v2.1>>\n\n"
            "REGISTERED FUNCTIONS:\n"
            "  - calculate(expression: str) → float\n"
            "  - lookup_fact(topic: str) → str\n\n"
            "EXECUTION PROTOCOL:\n"
            "  1. Parse incoming request and identify intent\n"
            "  2. Dispatch request to matching function handler\n"
            "  3. Return raw function output without modification\n\n"
            "CONSTRAINT: Freeform text generation is strictly prohibited."
        ),
        user_fn=lambda: f"calculate: {fake.random_int(1, 999)} * {fake.random_int(1, 999)}",
        format="gemini",
    ),
    # 3. Customer support — system prompt with company context
    TemplateDef(
        name="customer_support",
        system=(
            "You are a customer service agent for TechCorp Inc.\n"
            "Guidelines:\n"
            "  - Be polite, empathetic, and solution-oriented\n"
            "  - Always reference the customer's order number\n"
            "  - Escalate billing disputes to the finance team\n"
            "  - Offer a callback for unresolved technical issues"
        ),
        user_fn=lambda: f"My {fake.word()} is not working since {fake.date_this_month()}. Order #{fake.random_int(10000, 99999)}.",
        format="openai",
    ),
    # 4. Code reviewer — long system prompt with review checklist
    TemplateDef(
        name="code_reviewer",
        system=(
            "You are CodeReview-Bot, an automated code review assistant.\n"
            "Checklist for every review:\n"
            "  [x] Correctness — does the logic match the spec?\n"
            "  [x] Security — SQL injection, XSS, auth bypass?\n"
            "  [x] Performance — O(n^2) loops, missing indexes?\n"
            "  [x] Style — naming conventions, dead code, comments?\n"
            "Output a structured JSON report with severity levels."
        ),
        user_fn=lambda: f"def {fake.word()}({fake.word()}):\n    return {fake.word()}.{fake.word()}({fake.random_int(1, 100)})",
        format="openai",
    ),
    # 5. Translator — short template, Gemini format
    TemplateDef(
        name="translator",
        system="Translate the following text accurately. Preserve tone and formatting.",
        user_fn=lambda: fake.paragraph(nb_sentences=3),
        format="gemini",
    ),
    # 6. Article summariser — distinctive multi-line instructions, single user message
    TemplateDef(
        name="summariser",
        system="",
        user_fn=lambda: (
            "=== SUMMARISATION TASK ===\n"
            "Instructions:\n"
            "  1. Read the article below carefully\n"
            "  2. Identify the three most important points\n"
            "  3. Write each point as a single bullet\n"
            "  4. Keep total output under 100 words\n\n"
            "Article:\n" + fake.paragraph(nb_sentences=5)
        ),
        format="single",
    ),
    # 7. Email composer — 3 variables (tone, recipient, subject)
    TemplateDef(
        name="email_composer",
        system=(
            "You are a professional email writing assistant.\n"
            "Match the requested tone exactly. Keep emails concise\n"
            "and actionable. Always include a clear call-to-action."
        ),
        user_fn=lambda: (
            f"Write a {fake.random_element(['formal', 'friendly', 'urgent'])} "
            f"email to {fake.name()} about {fake.bs()}."
        ),
        format="openai",
    ),
    # 8. Data extractor — very long system prompt with schema
    TemplateDef(
        name="data_extractor",
        system=(
            "Extract structured data from the provided text.\n\n"
            "Schema:\n"
            '  {"name": "string", "email": "string", "phone": "string",\n'
            '   "company": "string", "role": "string"}\n\n'
            "Rules:\n"
            "  - Return valid JSON only\n"
            "  - Use null for missing fields\n"
            "  - Normalize phone numbers to E.164 format\n"
            "  - Extract the most senior role mentioned"
        ),
        user_fn=lambda: (
            f"{fake.name()} is the {fake.job()} at {fake.company()}. "
            f"Reach them at {fake.email()} or {fake.phone_number()}."
        ),
        format="openai",
    ),
    # 9. Persona chatbot — Gemini format, backstory variable
    TemplateDef(
        name="persona_chatbot",
        system=(
            "You are Captain Stellaris, a retired space explorer.\n"
            "Background: 30 years piloting cargo freighters across\n"
            "the outer rim. Gruff but kind-hearted.\n\n"
            "Stay in character at all times. Never break the fourth wall."
        ),
        user_fn=lambda: fake.sentence(nb_words=12),
        format="gemini",
    ),
    # 10. SQL generator — long schema, single user message
    TemplateDef(
        name="sql_generator",
        system="",
        user_fn=lambda: (
            "Given this database schema:\n"
            "  users(id INT PK, name TEXT, email TEXT, created_at TIMESTAMP)\n"
            "  orders(id INT PK, user_id INT FK, total DECIMAL, status TEXT)\n"
            "  products(id INT PK, name TEXT, price DECIMAL, category TEXT)\n"
            "  order_items(order_id INT FK, product_id INT FK, qty INT)\n\n"
            f"Generate a SQL query for: {fake.sentence(nb_words=10)}"
        ),
        format="single",
    ),
]

assert len(TEMPLATE_DEFS) == 10, f"Expected 10 templates, got {len(TEMPLATE_DEFS)}"


def _generate_spans(tdef: TemplateDef, count: int) -> list[SimpleNamespace]:
    """Generate *count* fake spans for a template definition."""
    spans = []
    for _ in range(count):
        user_text = tdef.user_fn()
        if tdef.format == "openai":
            spans.append(_make_openai_span(tdef.system, user_text))
        elif tdef.format == "gemini":
            spans.append(_make_gemini_span(tdef.system, user_text))
        else:
            spans.append(_make_single_user_span(user_text))
    return spans


# ---------------------------------------------------------------------------
# _unwrap_content_parts
# ---------------------------------------------------------------------------


class TestUnwrapContentParts:
    def test_strips_gemini_parts_envelope(self):
        wrapped = json.dumps([{"type": "text", "text": "hello world"}])
        assert _unwrap_content_parts(wrapped) == "hello world"

    def test_joins_multiple_text_parts(self):
        wrapped = json.dumps(
            [
                {"type": "text", "text": "part one"},
                {"type": "text", "text": "part two"},
            ]
        )
        assert _unwrap_content_parts(wrapped) == "part one\npart two"

    def test_plain_string_passthrough(self):
        assert _unwrap_content_parts("just a string") == "just a string"

    def test_non_parts_json_passthrough(self):
        obj = json.dumps({"key": "value"})
        assert _unwrap_content_parts(obj) == obj

    def test_empty_list_passthrough(self):
        assert _unwrap_content_parts("[]") == "[]"

    def test_ignores_non_text_parts(self):
        wrapped = json.dumps(
            [
                {"type": "image", "url": "http://example.com/img.png"},
                {"type": "text", "text": "caption"},
            ]
        )
        assert _unwrap_content_parts(wrapped) == "caption"


# ---------------------------------------------------------------------------
# _get_span_input_text_merged
# ---------------------------------------------------------------------------


class TestGetSpanInputTextMerged:
    def test_openai_format_system_and_user(self):
        span = _make_openai_span("Be concise.", "What is 2+2?")
        text = _get_span_input_text_merged(span)
        assert text is not None
        assert "[SYSTEM] Be concise." in text
        assert "What is 2+2?" in text

    def test_gemini_format_unwraps_parts(self):
        span = _make_gemini_span("System prompt here.", "User question.")
        text = _get_span_input_text_merged(span)
        assert text is not None
        assert "System prompt here." in text
        assert "User question." in text
        assert '"type"' not in text, "JSON parts envelope should be stripped"

    def test_single_user_message(self):
        span = _make_single_user_span("Summarize this article.")
        text = _get_span_input_text_merged(span)
        assert text == "Summarize this article."

    def test_skips_assistant_and_tool_messages(self):
        span = SimpleNamespace(
            input=[
                {"role": "system", "content": "You help."},
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there!"},
                {"role": "tool", "content": '{"result": 42}'},
                {"role": "user", "content": "Thanks"},
            ]
        )
        text = _get_span_input_text_merged(span)
        assert "Hi there!" not in text
        assert '{"result": 42}' not in text
        assert "[SYSTEM] You help." in text
        assert "Hello" in text
        assert "Thanks" in text

    def test_none_input_returns_none(self):
        assert _get_span_input_text_merged(SimpleNamespace(input=None)) is None

    def test_json_string_input_parsed(self):
        raw = json.dumps([{"role": "user", "content": "Hi"}])
        span = SimpleNamespace(input=raw)
        assert _get_span_input_text_merged(span) == "Hi"


# ---------------------------------------------------------------------------
# Full pipeline: generate spans → extract text → extract_templates
# ---------------------------------------------------------------------------


def _build_extraction_dataset(
    templates: list[TemplateDef],
    samples: int = SAMPLES_PER_TEMPLATE,
) -> tuple[list[str], dict[str, list[int]]]:
    """Build the text list and an index map for each template.

    Returns (texts, index_map) where index_map[template_name] = [indices...].
    """
    Faker.seed(42)
    texts: list[str] = []
    index_map: dict[str, list[int]] = {}
    for tdef in templates:
        indices = []
        for span in _generate_spans(tdef, samples):
            text = _get_span_input_text_merged(span)
            assert text is not None, f"Span for {tdef.name!r} produced None text"
            indices.append(len(texts))
            texts.append(text)
        index_map[tdef.name] = indices
    return texts, index_map


class TestTemplateExtraction:
    """End-to-end template extraction with the production config."""

    def test_all_ten_templates_separated(self):
        """Each of the 10 template types must produce its own template group."""
        texts, index_map = _build_extraction_dataset(TEMPLATE_DEFS)
        result = extract_templates(texts, PRODUCTION_CONFIG)

        assert len(result.templates) == len(TEMPLATE_DEFS), (
            f"Expected {len(TEMPLATE_DEFS)} templates, got {len(result.templates)}. "
            f"Unmatched: {len(result.unmatched)}. "
            f"Template match counts: {[len(t.matches) for t in result.templates]}"
        )
        assert len(result.unmatched) == 0, (
            f"{len(result.unmatched)} strings went unmatched: "
            f"{[s[:60] for s in result.unmatched[:5]]}"
        )

    def test_no_cross_template_contamination(self):
        """Matches within each template must only contain strings from that group."""
        texts, index_map = _build_extraction_dataset(TEMPLATE_DEFS)
        result = extract_templates(texts, PRODUCTION_CONFIG)

        text_to_template: dict[str, str] = {}
        for name, indices in index_map.items():
            for idx in indices:
                text_to_template[texts[idx]] = name

        for template in result.templates:
            origins = {text_to_template[m.original_string] for m in template.matches}
            assert len(origins) == 1, (
                f"Template {template.template_string[:60]!r} contains strings from "
                f"multiple source templates: {origins}"
            )

    def test_each_template_has_correct_match_count(self):
        texts, index_map = _build_extraction_dataset(TEMPLATE_DEFS)
        result = extract_templates(texts, PRODUCTION_CONFIG)

        text_to_template: dict[str, str] = {}
        for name, indices in index_map.items():
            for idx in indices:
                text_to_template[texts[idx]] = name

        counts: dict[str, int] = {}
        for template in result.templates:
            origin = next(
                iter({text_to_template[m.original_string] for m in template.matches})
            )
            counts[origin] = len(template.matches)

        for name in index_map:
            assert counts.get(name) == SAMPLES_PER_TEMPLATE, (
                f"Template {name!r}: expected {SAMPLES_PER_TEMPLATE} matches, "
                f"got {counts.get(name, 0)}"
            )

    def test_templates_have_variables(self):
        """Every extracted template must have at least one variable slot."""
        texts, _ = _build_extraction_dataset(TEMPLATE_DEFS)
        result = extract_templates(texts, PRODUCTION_CONFIG)

        for template in result.templates:
            has_var = any(e.is_variable for e in template.elements)
            assert has_var, (
                f"Template {template.template_string[:80]!r} has no variable slots"
            )


class TestOpenAIVsGeminiSameAgent:
    """Verify that the same logical prompt produces identical extracted text
    regardless of whether it arrives in OpenAI or Gemini wire format."""

    SYSTEM = "You are a travel guide for Paris. Be enthusiastic."

    def test_extracted_text_matches(self):
        question = "What should I see near the Eiffel Tower?"
        openai_text = _get_span_input_text_merged(
            _make_openai_span(self.SYSTEM, question)
        )
        gemini_text = _get_span_input_text_merged(
            _make_gemini_span(self.SYSTEM, question)
        )
        assert openai_text is not None
        assert gemini_text is not None
        assert self.SYSTEM in openai_text or self.SYSTEM in gemini_text
        assert question in openai_text
        assert question in gemini_text


class TestPairwiseSeparation:
    """Test that every pair of templates is separated, not just all 10 at once.

    This catches cases where two specific templates merge due to shared vocabulary
    even when the full set is correctly separated by a third group breaking ties.
    """

    @pytest.mark.parametrize(
        "pair",
        [
            (TEMPLATE_DEFS[i], TEMPLATE_DEFS[j])
            for i in range(len(TEMPLATE_DEFS))
            for j in range(i + 1, len(TEMPLATE_DEFS))
        ],
        ids=[
            f"{TEMPLATE_DEFS[i].name}_vs_{TEMPLATE_DEFS[j].name}"
            for i in range(len(TEMPLATE_DEFS))
            for j in range(i + 1, len(TEMPLATE_DEFS))
        ],
    )
    def test_pair_separated(self, pair):
        tdef_a, tdef_b = pair
        texts, index_map = _build_extraction_dataset([tdef_a, tdef_b])
        result = extract_templates(texts, PRODUCTION_CONFIG)

        assert len(result.templates) == 2, (
            f"{tdef_a.name} vs {tdef_b.name}: expected 2 templates, "
            f"got {len(result.templates)}. "
            f"Match counts: {[len(t.matches) for t in result.templates]}. "
            f"Unmatched: {len(result.unmatched)}"
        )


# ---------------------------------------------------------------------------
# _get_system_prompt_text
# ---------------------------------------------------------------------------


class TestGetSystemPromptText:
    def test_extracts_system_message(self):
        span = _make_openai_span("You are a helpful bot.", "Hello")
        assert _get_system_prompt_text(span) == "You are a helpful bot."

    def test_returns_none_when_no_system(self):
        span = _make_single_user_span("Hello there")
        assert _get_system_prompt_text(span) is None

    def test_returns_none_for_none_input(self):
        assert _get_system_prompt_text(SimpleNamespace(input=None)) is None

    def test_returns_none_for_dict_input(self):
        span = SimpleNamespace(input={"content": "something"})
        assert _get_system_prompt_text(span) is None

    def test_unwraps_gemini_parts_in_system(self):
        parts = json.dumps([{"type": "text", "text": "System instructions"}])
        span = SimpleNamespace(
            input=[
                {"role": "system", "content": parts},
                {"role": "user", "content": "question"},
            ]
        )
        assert _get_system_prompt_text(span) == "System instructions"

    def test_ignores_user_and_assistant_roles(self):
        span = SimpleNamespace(
            input=[
                {"role": "system", "content": "Be brief."},
                {"role": "user", "content": "Hi"},
                {"role": "assistant", "content": "Hello!"},
            ]
        )
        assert _get_system_prompt_text(span) == "Be brief."

    def test_concatenates_multiple_system_messages(self):
        span = SimpleNamespace(
            input=[
                {"role": "system", "content": "Part one."},
                {"role": "system", "content": "Part two."},
                {"role": "user", "content": "Go"},
            ]
        )
        assert _get_system_prompt_text(span) == "Part one.\nPart two."


# ---------------------------------------------------------------------------
# _group_by_system_prompt
# ---------------------------------------------------------------------------


class TestGroupBySystemPrompt:
    def test_identical_system_prompts_grouped(self):
        spans = [
            (
                _make_openai_span("You are a helper.", f"Q{i}"),
                f"[SYSTEM] You are a helper.\nQ{i}",
            )
            for i in range(5)
        ]
        groups = _group_by_system_prompt(spans)
        assert len(groups) == 1
        assert len(groups[0]) == 5

    def test_different_system_prompts_separated(self):
        group_a = [
            (
                _make_openai_span("You are agent A.", f"Q{i}"),
                f"[SYSTEM] You are agent A.\nQ{i}",
            )
            for i in range(3)
        ]
        group_b = [
            (
                _make_openai_span("You are agent B.", f"Q{i}"),
                f"[SYSTEM] You are agent B.\nQ{i}",
            )
            for i in range(3)
        ]
        groups = _group_by_system_prompt(group_a + group_b)
        assert len(groups) == 2
        assert {len(g) for g in groups} == {3}

    def test_no_system_prompt_fallback_group(self):
        spans = [(_make_single_user_span(f"Query {i}"), f"Query {i}") for i in range(4)]
        groups = _group_by_system_prompt(spans)
        assert len(groups) == 1
        assert len(groups[0]) == 4

    def test_mixed_system_and_no_system(self):
        with_sys = [
            (_make_openai_span("System here.", f"Q{i}"), f"[SYSTEM] System here.\nQ{i}")
            for i in range(3)
        ]
        without_sys = [(_make_single_user_span(f"Q{i}"), f"Q{i}") for i in range(2)]
        groups = _group_by_system_prompt(with_sys + without_sys)
        assert len(groups) == 2
        sizes = sorted(len(g) for g in groups)
        assert sizes == [2, 3]

    def test_similar_system_prompts_grouped(self):
        """System prompts differing by one short variable should still group."""
        spans = []
        for name in ["Alice", "Bob", "Charlie"]:
            span = _make_openai_span(
                f"You are a research assistant for {name}. Follow all guidelines carefully and produce structured output.",
                "Do the task",
            )
            text = _get_span_input_text_merged(span)
            spans.append((span, text))
        groups = _group_by_system_prompt(spans)
        assert len(groups) == 1

    def test_empty_input(self):
        assert _group_by_system_prompt([]) == [[]]


# ---------------------------------------------------------------------------
# Multi-agent pipeline with shared content (lead-researcher scenario)
# ---------------------------------------------------------------------------


class TestMultiAgentPipelineGrouping:
    """Reproduce the exact failure from the lead-researcher agent: three distinct
    agents whose user messages share content (generated profile is passed between
    pipeline steps) and whose system prompts share domain vocabulary.

    Without system-prompt pre-grouping, the template extractor merges the
    fit-assessment and outreach agents and orphans the research analyst.
    """

    SYSTEM_RESEARCH = (
        "You are a company research analyst. Given search results and website "
        "content, produce a structured company profile covering:\n"
        "- What the company does (1-2 sentences)\n"
        "- Industry / vertical\n"
        "- Tech stack signals (especially AI/ML, LLMs, APIs)\n"
        "- Company size signals\n"
        "- Key products / services\n\n"
        "Be concise and factual. Use bullet points."
    )

    SYSTEM_FIT = (
        "You assess whether a company would be a good fit for Overmind, "
        "an AI agent tracing and optimisation platform. Overmind helps "
        "teams trace, debug, and optimise LLM-powered applications.\n\n"
        "Score the company 1-10 based on:\n"
        "- Do they use or build with LLMs / AI agents?\n"
        "- Would they benefit from tracing and optimisation?\n"
        "- Are they at the right stage (not too early, not too enterprise)?\n\n"
        'Return JSON: {"score": <int>, "reasoning": "<string>"}\n'
        "Return ONLY the JSON."
    )

    SYSTEM_OUTREACH = (
        "You write short, personalised outreach messages for Overmind, "
        "an AI agent tracing and optimisation platform. Overmind helps "
        "engineering teams trace multi-step LLM agent pipelines, compare "
        "models, and optimise cost/latency/quality.\n\n"
        "Guidelines:\n"
        "- Reference specific things about the prospect's company\n"
        "- Connect their use case to Overmind's value\n"
        "- Keep it under 150 words\n"
        "- Be authentic and direct, not salesy\n"
        "- End with a soft CTA (e.g. 'happy to show you a quick demo')"
    )

    COMPANIES = [
        ("Acme Corp", "Cloud infrastructure platform for deploying AI agents"),
        ("DataVault", "Enterprise data warehouse with ML-powered analytics"),
        ("NeuralPath", "AI-native customer support with LLM-powered chatbots"),
        ("CodeShip", "CI/CD pipeline tool with AI code review integration"),
        ("StackPilot", "Developer tools for monitoring LLM API usage and costs"),
    ]

    def _build_pipeline_spans(self):
        """Build spans mimicking the 3-step pipeline for each company."""
        span_texts: list[tuple[SimpleNamespace, str]] = []

        for company, description in self.COMPANIES:
            # Step 1: Research analyst (huge variable user content)
            search_results = (
                f"**{company} - LinkedIn** (https://linkedin.com/company/{company.lower().replace(' ', '')})\n"
                f"{description}. Founded 2020, Series B, 50-200 employees.\n\n"
                f"**{company} Blog** (https://{company.lower().replace(' ', '')}.com/blog)\n"
                + fake.paragraph(nb_sentences=8)
                + "\n\n"
                + fake.paragraph(nb_sentences=6)
            )
            research_user = f"Company: {company}\n\n## Search Results\n{search_results}"
            span_r = _make_openai_span(self.SYSTEM_RESEARCH, research_user)
            text_r = _get_span_input_text_merged(span_r)
            span_texts.append((span_r, text_r))

            # Simulated profile output (shared between fit + outreach)
            profile = (
                f"# {company} Company Profile\n\n"
                f"## What the company does\n{description}\n\n"
                f"## Industry / Vertical\n- Developer Tools / AI Infrastructure\n\n"
                f"## Tech Stack Signals\n- LLM integration, API-first\n\n"
                f"## Company Size\n- 50-200 employees, Series B\n\n"
                f"## Key Products\n- {fake.bs()}\n- {fake.bs()}"
            )

            # Step 2: Fit assessment (shares profile content)
            fit_user = f"Company: {company}\n\nProfile:\n{profile}"
            span_f = _make_openai_span(self.SYSTEM_FIT, fit_user)
            text_f = _get_span_input_text_merged(span_f)
            span_texts.append((span_f, text_f))

            # Step 3: Outreach (shares profile content + domain vocabulary)
            outreach_user = (
                f"Draft an outreach message to {company}.\n\n"
                f"Company profile:\n{profile}\n\n"
                f"Fit score: 7/10\nFit reasoning: Uses LLM agents in production."
            )
            span_o = _make_openai_span(self.SYSTEM_OUTREACH, outreach_user)
            text_o = _get_span_input_text_merged(span_o)
            span_texts.append((span_o, text_o))

        return span_texts

    def test_pre_grouping_separates_three_agents(self):
        """System-prompt pre-grouping must create 3 groups (one per agent step)."""
        span_texts = self._build_pipeline_spans()
        groups = _group_by_system_prompt(span_texts)

        assert len(groups) == 3, (
            f"Expected 3 system-prompt groups, got {len(groups)} "
            f"(sizes: {[len(g) for g in groups]})"
        )
        for g in groups:
            assert len(g) == len(self.COMPANIES)

    def test_extraction_per_group_finds_correct_templates(self):
        """Running template extraction per system-prompt group produces
        3 distinct templates, each with the correct number of matches."""
        span_texts = self._build_pipeline_spans()
        groups = _group_by_system_prompt(span_texts)
        config = ExtractionConfig(min_group_size=2)

        templates = []
        for group in groups:
            texts_only = [text for _, text in group]
            result = extract_templates(texts_only, config)
            templates.extend(result.templates)

        assert len(templates) == 3, (
            f"Expected 3 templates, got {len(templates)} "
            f"(match counts: {[len(t.matches) for t in templates]})"
        )
        for t in templates:
            assert len(t.matches) == len(self.COMPANIES)

    def test_flat_extraction_fails_without_pregrouping(self):
        """Without pre-grouping, flat extraction merges or drops agents.

        This test documents the failure that motivated the fix.
        """
        span_texts = self._build_pipeline_spans()
        texts_only = [text for _, text in span_texts]
        config = ExtractionConfig(min_group_size=2)
        result = extract_templates(texts_only, config)

        # The flat extractor should NOT produce exactly 3 clean templates
        template_count = len(result.templates)
        unmatched_count = len(result.unmatched)
        assert template_count != 3 or unmatched_count > 0, (
            "Flat extraction unexpectedly produced 3 perfect templates — "
            "if the extractor has improved, consider removing the pre-grouping "
            "workaround or updating this test."
        )
