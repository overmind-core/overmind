"""
Tests for tasks/utils/prompts — vendor-specific meta-prompt variants.

Covers:
  - get_prompt_for_provider() helper
  - All 18 named prompt variables exist and are non-empty strings
  - All 6 lookup dicts have exactly the three expected provider keys
  - Dict values are identity-equal to the named variables (no copies)
  - ANTHROPIC named constants are the same objects stored in the lookup dicts
  - Each provider's prompts carry the expected structural markers
  - All format-string placeholders can be rendered without KeyError
"""

import pytest

from overmind.tasks.utils.prompts import (
    get_prompt_for_provider,
    # System prompt named variables
    SUGGESTION_GENERATION_SYSTEM_PROMPT_ANTHROPIC,
    SUGGESTION_GENERATION_SYSTEM_PROMPT_OPENAI,
    SUGGESTION_GENERATION_SYSTEM_PROMPT_GEMINI,
    PROMPT_IMPROVEMENT_SYSTEM_PROMPT_ANTHROPIC,
    PROMPT_IMPROVEMENT_SYSTEM_PROMPT_OPENAI,
    PROMPT_IMPROVEMENT_SYSTEM_PROMPT_GEMINI,
    # User prompt named variables
    SUGGESTION_GENERATION_PROMPT_ANTHROPIC,
    SUGGESTION_GENERATION_PROMPT_OPENAI,
    SUGGESTION_GENERATION_PROMPT_GEMINI,
    PROMPT_IMPROVEMENT_PROMPT_ANTHROPIC,
    PROMPT_IMPROVEMENT_PROMPT_OPENAI,
    PROMPT_IMPROVEMENT_PROMPT_GEMINI,
    TOOL_SUGGESTION_GENERATION_PROMPT_ANTHROPIC,
    TOOL_SUGGESTION_GENERATION_PROMPT_OPENAI,
    TOOL_SUGGESTION_GENERATION_PROMPT_GEMINI,
    TOOL_PROMPT_IMPROVEMENT_PROMPT_ANTHROPIC,
    TOOL_PROMPT_IMPROVEMENT_PROMPT_OPENAI,
    TOOL_PROMPT_IMPROVEMENT_PROMPT_GEMINI,
    # Lookup dicts
    SUGGESTION_GENERATION_SYSTEM_PROMPTS,
    SUGGESTION_GENERATION_PROMPTS,
    PROMPT_IMPROVEMENT_SYSTEM_PROMPTS,
    PROMPT_IMPROVEMENT_PROMPTS,
    TOOL_SUGGESTION_GENERATION_PROMPTS,
    TOOL_PROMPT_IMPROVEMENT_PROMPTS,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

ALL_DICTS = [
    ("SUGGESTION_GENERATION_SYSTEM_PROMPTS", SUGGESTION_GENERATION_SYSTEM_PROMPTS),
    ("SUGGESTION_GENERATION_PROMPTS", SUGGESTION_GENERATION_PROMPTS),
    ("PROMPT_IMPROVEMENT_SYSTEM_PROMPTS", PROMPT_IMPROVEMENT_SYSTEM_PROMPTS),
    ("PROMPT_IMPROVEMENT_PROMPTS", PROMPT_IMPROVEMENT_PROMPTS),
    ("TOOL_SUGGESTION_GENERATION_PROMPTS", TOOL_SUGGESTION_GENERATION_PROMPTS),
    ("TOOL_PROMPT_IMPROVEMENT_PROMPTS", TOOL_PROMPT_IMPROVEMENT_PROMPTS),
]

ALL_NAMED = [
    SUGGESTION_GENERATION_SYSTEM_PROMPT_ANTHROPIC,
    SUGGESTION_GENERATION_SYSTEM_PROMPT_OPENAI,
    SUGGESTION_GENERATION_SYSTEM_PROMPT_GEMINI,
    PROMPT_IMPROVEMENT_SYSTEM_PROMPT_ANTHROPIC,
    PROMPT_IMPROVEMENT_SYSTEM_PROMPT_OPENAI,
    PROMPT_IMPROVEMENT_SYSTEM_PROMPT_GEMINI,
    SUGGESTION_GENERATION_PROMPT_ANTHROPIC,
    SUGGESTION_GENERATION_PROMPT_OPENAI,
    SUGGESTION_GENERATION_PROMPT_GEMINI,
    PROMPT_IMPROVEMENT_PROMPT_ANTHROPIC,
    PROMPT_IMPROVEMENT_PROMPT_OPENAI,
    PROMPT_IMPROVEMENT_PROMPT_GEMINI,
    TOOL_SUGGESTION_GENERATION_PROMPT_ANTHROPIC,
    TOOL_SUGGESTION_GENERATION_PROMPT_OPENAI,
    TOOL_SUGGESTION_GENERATION_PROMPT_GEMINI,
    TOOL_PROMPT_IMPROVEMENT_PROMPT_ANTHROPIC,
    TOOL_PROMPT_IMPROVEMENT_PROMPT_OPENAI,
    TOOL_PROMPT_IMPROVEMENT_PROMPT_GEMINI,
]

# Format args needed to fully render each user prompt group
_SUGGESTION_KWARGS = dict(
    project_description="Test project",
    agent_description="Test agent",
    current_prompt="You are a helpful assistant.",
    poor_examples="Example 1 (score: 0.10): ...",
    tool_usage_analysis="",
)
_IMPROVEMENT_KWARGS = dict(
    project_context="<ProjectContext>Test</ProjectContext>",
    agent_context="<AgentContext>Test</AgentContext>",
    current_prompt="You are a helpful assistant.",
    suggestions="- Add more detail",
    good_examples="Example 1 (score: 0.90): ...",
    poor_examples="Example 1 (score: 0.10): ...",
)
_TOOL_SUGGESTION_KWARGS = dict(
    current_prompt="You are a helpful assistant.",
    tool_definitions='[{"name": "search", "description": "Searches the web"}]',
    poor_tool_call_examples="Example 1 (score: 0.10): ...",
    poor_text_examples="Example 2 (score: 0.20): ...",
)
_TOOL_IMPROVEMENT_KWARGS = dict(
    current_prompt="You are a helpful assistant.",
    tool_definitions='[{"name": "search", "description": "Searches the web"}]',
    suggestions="- Clarify tool usage",
    good_tool_call_examples="Example 1 (score: 0.90): ...",
    good_text_examples="Example 2 (score: 0.85): ...",
    poor_tool_call_examples="Example 3 (score: 0.10): ...",
    poor_text_examples="Example 4 (score: 0.15): ...",
)


# ---------------------------------------------------------------------------
# get_prompt_for_provider
# ---------------------------------------------------------------------------

class TestGetPromptForProvider:
    def test_returns_anthropic_variant(self):
        d = {"anthropic": "claude-prompt", "openai": "gpt-prompt", "gemini": "gemini-prompt"}
        assert get_prompt_for_provider(d, "anthropic") == "claude-prompt"

    def test_returns_openai_variant(self):
        d = {"anthropic": "claude-prompt", "openai": "gpt-prompt", "gemini": "gemini-prompt"}
        assert get_prompt_for_provider(d, "openai") == "gpt-prompt"

    def test_returns_gemini_variant(self):
        d = {"anthropic": "claude-prompt", "openai": "gpt-prompt", "gemini": "gemini-prompt"}
        assert get_prompt_for_provider(d, "gemini") == "gemini-prompt"

    def test_falls_back_to_anthropic_for_unknown_provider(self):
        d = {"anthropic": "claude-prompt", "openai": "gpt-prompt", "gemini": "gemini-prompt"}
        assert get_prompt_for_provider(d, "some_new_provider") == "claude-prompt"

    def test_falls_back_to_anthropic_for_empty_string(self):
        d = {"anthropic": "claude-prompt", "openai": "gpt-prompt", "gemini": "gemini-prompt"}
        assert get_prompt_for_provider(d, "") == "claude-prompt"

    def test_works_with_real_dicts(self):
        for _, d in ALL_DICTS:
            for provider in ("anthropic", "openai", "gemini"):
                result = get_prompt_for_provider(d, provider)
                assert isinstance(result, str) and len(result) > 0

    def test_unknown_provider_uses_real_dict(self):
        for _, d in ALL_DICTS:
            assert get_prompt_for_provider(d, "cohere") == d["anthropic"]


# ---------------------------------------------------------------------------
# Named variables existence and type
# ---------------------------------------------------------------------------

class TestNamedVariables:
    def test_all_18_are_non_empty_strings(self):
        for prompt in ALL_NAMED:
            assert isinstance(prompt, str)
            assert len(prompt.strip()) > 0

    def test_no_two_variants_in_same_group_are_identical(self):
        """Anthropic, OpenAI, Gemini variants must differ from each other."""
        groups = [
            (SUGGESTION_GENERATION_SYSTEM_PROMPT_ANTHROPIC,
             SUGGESTION_GENERATION_SYSTEM_PROMPT_OPENAI,
             SUGGESTION_GENERATION_SYSTEM_PROMPT_GEMINI),
            (SUGGESTION_GENERATION_PROMPT_ANTHROPIC,
             SUGGESTION_GENERATION_PROMPT_OPENAI,
             SUGGESTION_GENERATION_PROMPT_GEMINI),
            (PROMPT_IMPROVEMENT_PROMPT_ANTHROPIC,
             PROMPT_IMPROVEMENT_PROMPT_OPENAI,
             PROMPT_IMPROVEMENT_PROMPT_GEMINI),
            (TOOL_SUGGESTION_GENERATION_PROMPT_ANTHROPIC,
             TOOL_SUGGESTION_GENERATION_PROMPT_OPENAI,
             TOOL_SUGGESTION_GENERATION_PROMPT_GEMINI),
            (TOOL_PROMPT_IMPROVEMENT_PROMPT_ANTHROPIC,
             TOOL_PROMPT_IMPROVEMENT_PROMPT_OPENAI,
             TOOL_PROMPT_IMPROVEMENT_PROMPT_GEMINI),
        ]
        for anthropic, openai, gemini in groups:
            assert anthropic != openai
            assert anthropic != gemini
            assert openai != gemini

    def test_anthropic_named_constants_match_lookup_dicts(self):
        """ANTHROPIC named constants are the same objects stored in the lookup dicts."""
        assert SUGGESTION_GENERATION_SYSTEM_PROMPT_ANTHROPIC is SUGGESTION_GENERATION_SYSTEM_PROMPTS["anthropic"]
        assert SUGGESTION_GENERATION_PROMPT_ANTHROPIC is SUGGESTION_GENERATION_PROMPTS["anthropic"]
        assert PROMPT_IMPROVEMENT_SYSTEM_PROMPT_ANTHROPIC is PROMPT_IMPROVEMENT_SYSTEM_PROMPTS["anthropic"]
        assert PROMPT_IMPROVEMENT_PROMPT_ANTHROPIC is PROMPT_IMPROVEMENT_PROMPTS["anthropic"]
        assert TOOL_SUGGESTION_GENERATION_PROMPT_ANTHROPIC is TOOL_SUGGESTION_GENERATION_PROMPTS["anthropic"]
        assert TOOL_PROMPT_IMPROVEMENT_PROMPT_ANTHROPIC is TOOL_PROMPT_IMPROVEMENT_PROMPTS["anthropic"]


# ---------------------------------------------------------------------------
# Lookup dict structure
# ---------------------------------------------------------------------------

class TestLookupDicts:
    @pytest.mark.parametrize("name,d", ALL_DICTS)
    def test_has_exactly_three_providers(self, name, d):
        assert set(d.keys()) == {"anthropic", "openai", "gemini"}, (
            f"{name} has unexpected keys: {list(d.keys())}"
        )

    @pytest.mark.parametrize("name,d", ALL_DICTS)
    def test_all_values_are_non_empty_strings(self, name, d):
        for provider, prompt in d.items():
            assert isinstance(prompt, str) and len(prompt.strip()) > 0, (
                f"{name}[{provider!r}] is empty or not a string"
            )

    def test_suggestion_generation_system_prompts_references_named_vars(self):
        assert SUGGESTION_GENERATION_SYSTEM_PROMPTS["anthropic"] is SUGGESTION_GENERATION_SYSTEM_PROMPT_ANTHROPIC
        assert SUGGESTION_GENERATION_SYSTEM_PROMPTS["openai"] is SUGGESTION_GENERATION_SYSTEM_PROMPT_OPENAI
        assert SUGGESTION_GENERATION_SYSTEM_PROMPTS["gemini"] is SUGGESTION_GENERATION_SYSTEM_PROMPT_GEMINI

    def test_prompt_improvement_system_prompts_references_named_vars(self):
        assert PROMPT_IMPROVEMENT_SYSTEM_PROMPTS["anthropic"] is PROMPT_IMPROVEMENT_SYSTEM_PROMPT_ANTHROPIC
        assert PROMPT_IMPROVEMENT_SYSTEM_PROMPTS["openai"] is PROMPT_IMPROVEMENT_SYSTEM_PROMPT_OPENAI
        assert PROMPT_IMPROVEMENT_SYSTEM_PROMPTS["gemini"] is PROMPT_IMPROVEMENT_SYSTEM_PROMPT_GEMINI

    def test_suggestion_generation_prompts_references_named_vars(self):
        assert SUGGESTION_GENERATION_PROMPTS["anthropic"] is SUGGESTION_GENERATION_PROMPT_ANTHROPIC
        assert SUGGESTION_GENERATION_PROMPTS["openai"] is SUGGESTION_GENERATION_PROMPT_OPENAI
        assert SUGGESTION_GENERATION_PROMPTS["gemini"] is SUGGESTION_GENERATION_PROMPT_GEMINI

    def test_prompt_improvement_prompts_references_named_vars(self):
        assert PROMPT_IMPROVEMENT_PROMPTS["anthropic"] is PROMPT_IMPROVEMENT_PROMPT_ANTHROPIC
        assert PROMPT_IMPROVEMENT_PROMPTS["openai"] is PROMPT_IMPROVEMENT_PROMPT_OPENAI
        assert PROMPT_IMPROVEMENT_PROMPTS["gemini"] is PROMPT_IMPROVEMENT_PROMPT_GEMINI

    def test_tool_suggestion_generation_prompts_references_named_vars(self):
        assert TOOL_SUGGESTION_GENERATION_PROMPTS["anthropic"] is TOOL_SUGGESTION_GENERATION_PROMPT_ANTHROPIC
        assert TOOL_SUGGESTION_GENERATION_PROMPTS["openai"] is TOOL_SUGGESTION_GENERATION_PROMPT_OPENAI
        assert TOOL_SUGGESTION_GENERATION_PROMPTS["gemini"] is TOOL_SUGGESTION_GENERATION_PROMPT_GEMINI

    def test_tool_prompt_improvement_prompts_references_named_vars(self):
        assert TOOL_PROMPT_IMPROVEMENT_PROMPTS["anthropic"] is TOOL_PROMPT_IMPROVEMENT_PROMPT_ANTHROPIC
        assert TOOL_PROMPT_IMPROVEMENT_PROMPTS["openai"] is TOOL_PROMPT_IMPROVEMENT_PROMPT_OPENAI
        assert TOOL_PROMPT_IMPROVEMENT_PROMPTS["gemini"] is TOOL_PROMPT_IMPROVEMENT_PROMPT_GEMINI


# ---------------------------------------------------------------------------
# Provider-specific structural markers
# ---------------------------------------------------------------------------

class TestStructuralMarkers:
    """Each provider variant must use the expected structural formatting style."""

    # --- Anthropic: XML tags ---

    def test_anthropic_suggestion_prompt_uses_xml_tags(self):
        assert "<Instructions>" in SUGGESTION_GENERATION_PROMPT_ANTHROPIC
        assert "<Current Prompt Template>" in SUGGESTION_GENERATION_PROMPT_ANTHROPIC

    def test_anthropic_improvement_prompt_uses_xml_tags(self):
        assert "<Instructions>" in PROMPT_IMPROVEMENT_PROMPT_ANTHROPIC
        assert "<Current Prompt Template>" in PROMPT_IMPROVEMENT_PROMPT_ANTHROPIC

    def test_anthropic_tool_suggestion_prompt_uses_xml_tags(self):
        assert "<Instructions>" in TOOL_SUGGESTION_GENERATION_PROMPT_ANTHROPIC
        assert "<CurrentPromptTemplate>" in TOOL_SUGGESTION_GENERATION_PROMPT_ANTHROPIC

    def test_anthropic_tool_improvement_prompt_uses_xml_tags(self):
        assert "<Instructions>" in TOOL_PROMPT_IMPROVEMENT_PROMPT_ANTHROPIC
        assert "<CurrentPromptTemplate>" in TOOL_PROMPT_IMPROVEMENT_PROMPT_ANTHROPIC

    # --- OpenAI: ### markdown headers ---

    def test_openai_suggestion_prompt_uses_h3_headers(self):
        assert "### " in SUGGESTION_GENERATION_PROMPT_OPENAI
        assert "### Instructions" in SUGGESTION_GENERATION_PROMPT_OPENAI

    def test_openai_improvement_prompt_uses_h3_headers(self):
        assert "### " in PROMPT_IMPROVEMENT_PROMPT_OPENAI
        assert "### Improved Prompt" in PROMPT_IMPROVEMENT_PROMPT_OPENAI

    def test_openai_tool_suggestion_prompt_uses_h3_headers(self):
        assert "### " in TOOL_SUGGESTION_GENERATION_PROMPT_OPENAI
        assert "### Instructions" in TOOL_SUGGESTION_GENERATION_PROMPT_OPENAI

    def test_openai_tool_improvement_prompt_uses_h3_headers(self):
        assert "### " in TOOL_PROMPT_IMPROVEMENT_PROMPT_OPENAI
        assert "### Improved Prompt" in TOOL_PROMPT_IMPROVEMENT_PROMPT_OPENAI

    # --- Gemini: ## markdown headers ---

    def test_gemini_suggestion_prompt_uses_h2_headers(self):
        assert "## " in SUGGESTION_GENERATION_PROMPT_GEMINI
        assert "## Instructions" in SUGGESTION_GENERATION_PROMPT_GEMINI

    def test_gemini_improvement_prompt_uses_h2_headers(self):
        assert "## " in PROMPT_IMPROVEMENT_PROMPT_GEMINI
        assert "## Improved Prompt" in PROMPT_IMPROVEMENT_PROMPT_GEMINI

    def test_gemini_tool_suggestion_prompt_uses_h2_headers(self):
        assert "## " in TOOL_SUGGESTION_GENERATION_PROMPT_GEMINI
        assert "## Instructions" in TOOL_SUGGESTION_GENERATION_PROMPT_GEMINI

    def test_gemini_tool_improvement_prompt_uses_h2_headers(self):
        assert "## " in TOOL_PROMPT_IMPROVEMENT_PROMPT_GEMINI
        assert "## Improved Prompt" in TOOL_PROMPT_IMPROVEMENT_PROMPT_GEMINI

    def test_gemini_improvement_uses_numbered_instructions(self):
        assert "1. " in PROMPT_IMPROVEMENT_PROMPT_GEMINI
        assert "2. " in PROMPT_IMPROVEMENT_PROMPT_GEMINI

    def test_openai_uses_bullet_instructions(self):
        assert "- " in PROMPT_IMPROVEMENT_PROMPT_OPENAI

    # --- System prompts ---

    def test_openai_system_prompts_mention_numbered_tasks(self):
        assert "1." in SUGGESTION_GENERATION_SYSTEM_PROMPT_OPENAI

    def test_gemini_system_prompts_use_h2_role_header(self):
        assert "## Role" in SUGGESTION_GENERATION_SYSTEM_PROMPT_GEMINI
        assert "## Role" in PROMPT_IMPROVEMENT_SYSTEM_PROMPT_GEMINI


# ---------------------------------------------------------------------------
# Content integrity
# ---------------------------------------------------------------------------

class TestContentIntegrity:
    """Prompts contain critical content regardless of provider."""

    @pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini"])
    def test_suggestion_prompts_request_json_output(self, provider):
        prompt = SUGGESTION_GENERATION_PROMPTS[provider]
        assert '"suggestions"' in prompt

    @pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini"])
    def test_tool_suggestion_prompts_forbid_tool_definition_changes(self, provider):
        prompt = TOOL_SUGGESTION_GENERATION_PROMPTS[provider]
        assert "do NOT suggest changes to tool definitions" in prompt.lower() or \
               "do not suggest changes to tool definitions" in prompt.lower()

    @pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini"])
    def test_improvement_prompts_warn_about_overfitting(self, provider):
        prompt = PROMPT_IMPROVEMENT_PROMPTS[provider]
        assert "overfit" in prompt.lower()

    @pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini"])
    def test_improvement_prompts_preserve_template_variables(self, provider):
        prompt = PROMPT_IMPROVEMENT_PROMPTS[provider]
        assert "variable" in prompt.lower() or "{{variable_name}}" in prompt

    @pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini"])
    def test_tool_improvement_prompts_preserve_tool_definitions(self, provider):
        prompt = TOOL_PROMPT_IMPROVEMENT_PROMPTS[provider]
        assert "do not modify tool definitions" in prompt.lower() or \
               "do NOT modify tool definitions" in prompt

    @pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini"])
    def test_system_prompts_return_only_json(self, provider):
        prompt = SUGGESTION_GENERATION_SYSTEM_PROMPTS[provider]
        assert "json" in prompt.lower()

    @pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini"])
    def test_improvement_system_prompts_return_only_prompt_text(self, provider):
        prompt = PROMPT_IMPROVEMENT_SYSTEM_PROMPTS[provider]
        assert "improved prompt" in prompt.lower() or "return only" in prompt.lower()


# ---------------------------------------------------------------------------
# Format string rendering (smoke tests)
# ---------------------------------------------------------------------------

class TestFormatStringRendering:
    """All prompts must render without KeyError when given the expected placeholders."""

    @pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini"])
    def test_suggestion_generation_prompt_renders(self, provider):
        prompt = SUGGESTION_GENERATION_PROMPTS[provider]
        rendered = prompt.format(**_SUGGESTION_KWARGS)
        assert "Test project" in rendered
        assert "You are a helpful assistant." in rendered

    @pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini"])
    def test_prompt_improvement_prompt_renders(self, provider):
        prompt = PROMPT_IMPROVEMENT_PROMPTS[provider]
        rendered = prompt.format(**_IMPROVEMENT_KWARGS)
        assert "You are a helpful assistant." in rendered
        assert "- Add more detail" in rendered

    @pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini"])
    def test_tool_suggestion_generation_prompt_renders(self, provider):
        prompt = TOOL_SUGGESTION_GENERATION_PROMPTS[provider]
        rendered = prompt.format(**_TOOL_SUGGESTION_KWARGS)
        assert "You are a helpful assistant." in rendered

    @pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini"])
    def test_tool_prompt_improvement_prompt_renders(self, provider):
        prompt = TOOL_PROMPT_IMPROVEMENT_PROMPTS[provider]
        rendered = prompt.format(**_TOOL_IMPROVEMENT_KWARGS)
        assert "You are a helpful assistant." in rendered
        assert "- Clarify tool usage" in rendered

    @pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini"])
    def test_suggestion_prompt_embeds_examples(self, provider):
        prompt = SUGGESTION_GENERATION_PROMPTS[provider]
        rendered = prompt.format(**_SUGGESTION_KWARGS)
        assert "Example 1" in rendered

    def test_prompt_improvement_empty_project_context_renders(self):
        """project_context / agent_context can be empty strings."""
        for provider in ("anthropic", "openai", "gemini"):
            rendered = PROMPT_IMPROVEMENT_PROMPTS[provider].format(
                **{**_IMPROVEMENT_KWARGS, "project_context": "", "agent_context": ""}
            )
            assert len(rendered) > 0

    @pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini"])
    def test_suggestion_prompt_empty_tool_analysis_renders(self, provider):
        """tool_usage_analysis is optional and can be an empty string."""
        rendered = SUGGESTION_GENERATION_PROMPTS[provider].format(
            **{**_SUGGESTION_KWARGS, "tool_usage_analysis": ""}
        )
        assert len(rendered) > 0
