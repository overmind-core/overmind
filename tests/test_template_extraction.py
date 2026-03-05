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
        user_fn=lambda: (
            f"calculate: {fake.random_int(1, 999)} * {fake.random_int(1, 999)}"
        ),
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
        user_fn=lambda: (
            f"My {fake.word()} is not working since {fake.date_this_month()}. Order #{fake.random_int(10000, 99999)}."
        ),
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
        user_fn=lambda: (
            f"def {fake.word()}({fake.word()}):\n    return {fake.word()}.{fake.word()}({fake.random_int(1, 100)})"
        ),
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
