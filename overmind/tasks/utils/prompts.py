"""
All task prompt constants and vendor-specific meta-prompt variants.

Prompt constants are grouped by purpose:
  - Correctness evaluation (standard, agentic, tool-calling)
  - Criteria generation
  - Prompt improvement (suggestion generation + improvement, standard + tool-calling)
  - Agent description generation
  - Display name generation

Vendor-specific improvement prompts follow the base section.
Different model providers respond best to different prompt structures:
  - Anthropic (Claude): XML tags, content-before-instructions ordering,
    output-stub prefilling ("Return the improved prompt:")
  - OpenAI (GPT):       Markdown ### headers, numbered step lists,
    explicit output anchor ("### Improved Prompt\n")
  - Gemini:             Markdown ## headers, numbered instructions,
    explicit output anchor ("## Improved Prompt\n")

Use get_prompt_for_provider(prompt_dict, provider) to select the right
variant at call time.  Falls back to "anthropic" if the provider is unknown.
"""


# ============================================================================
# CORRECTNESS EVALUATION PROMPTS
# ============================================================================

CORRECTNESS_SYSTEM_PROMPT = """Return ONLY valid JSON with a single key 'correctness' as a number from 0 to 1. 0 means completely incorrect, 1 means fully correct, and partial correctness should be a decimal."""

CORRECTNESS_PROMPT_TEMPLATE = """You are an expert data labeler evaluating model outputs for correctness. Your task is to assign a score based on the following context and rubric:

{project_context}

{agent_context}

<Rubric>
  A correct answer:
  - Provides accurate and complete information
  - Contains no factual errors
  - Addresses all parts of the question
  - Is logically consistent
  - Uses precise and accurate terminology

  When scoring, you should penalize:
  - Factual errors or inaccuracies
  - Incomplete or partial answers
  - Misleading or ambiguous statements
  - Incorrect terminology
  - Logical inconsistencies
  - Missing key information
</Rubric>

<Instructions>
  - Carefully read the input and output
  - Check for factual accuracy and completeness
  - Focus on correctness of information rather than style or verbosity
  - Consider the project and agent context when evaluating
</Instructions>

<Reminder>
  The goal is to evaluate factual correctness and completeness of the response in the context of the project and agent purpose.
</Reminder>

<Criteria>
{criteria}
</Criteria>

<input>
{inputs}
</input>

<output>
{outputs}
</output>
"""


# ============================================================================
# AGENTIC/MULTI-STEP EVALUATION PROMPTS
# ============================================================================

AGENTIC_CORRECTNESS_SYSTEM_PROMPT = """Return ONLY valid JSON with a single key 'correctness' as a number from 0 to 1. 0 means completely incorrect, 1 means fully correct, and partial correctness should be a decimal."""

AGENTIC_CORRECTNESS_PROMPT_TEMPLATE = """You are an expert data labeler evaluating agentic LLM outputs for correctness.

IMPORTANT: This is a multi-step agentic interaction with tool calls.

<EvaluationScope>
You are evaluating the FINAL OUTPUT shown to the user, NOT the intermediate tool calls.

Tool calls are INTERMEDIARY STEPS used by the agent to gather information or perform actions.
Your job is to assess whether the final answer is correct based on:
1. The information retrieved/computed by the tool calls
2. How well the agent synthesized that information
3. The accuracy and completeness of the final response

DO NOT penalize the agent for tool calls themselves - they are means to an end.
DO penalize if the agent misinterprets tool results or provides an incorrect final answer.
</EvaluationScope>

<OriginalQuery>
{original_query}
</OriginalQuery>

<ConversationFlow>
{conversation_flow}
</ConversationFlow>

<IntermediateSteps>
The agent made the following tool calls to gather information:

{intermediate_steps}

These are intermediary steps. Focus on whether the agent correctly used the information from these tools in the final output.
</IntermediateSteps>

<FinalOutput>
This is what you should evaluate for correctness:

{final_output}
</FinalOutput>

<Rubric>
A correct agentic response:
- Uses tool calls appropriately to gather necessary information
- Correctly interprets and synthesizes tool call results
- Provides accurate final answer based on retrieved/computed data
- Addresses all parts of the original user query
- Shows proper reasoning from tool results to final answer
- Does not make unsupported claims beyond tool-provided data

Penalize for:
- Misinterpreting tool call results
- Ignoring relevant tool outputs
- Making claims unsupported by tool data
- Factual errors in synthesizing information
- Incomplete use of available tool results
- Logical inconsistencies between tool results and final answer
</Rubric>

<Criteria>
Additional specific criteria for this task:

{criteria}
</Criteria>

<Instructions>
1. Review the original query to understand what the user asked
2. Examine the intermediate steps to see what information the agent gathered
3. Evaluate whether the final output correctly uses and synthesizes that information
4. Assign a score from 0 to 1 based on correctness of the final output
</Instructions>

<Reminder>
Evaluate the FINAL OUTPUT's correctness, not the tool calls themselves.
Tool calls are acceptable intermediary steps - focus on the end result.
</Reminder>
"""


# Default criteria for agentic spans
DEFAULT_AGENTIC_CRITERIA = """- Uses tools appropriately to gather necessary information
- Correctly interprets tool outputs and incorporates them into the response
- Provides accurate synthesis of information from multiple tool calls
- Shows proper reasoning chain from tool results to final answer
- Does not make unsupported claims beyond tool-provided data"""


# ============================================================================
# TOOL-CALLING EVALUATION PROMPTS
# Two separate judge prompts: one for tool selection quality,
# one for answer synthesis/faithfulness after tool results are returned.
# ============================================================================

DEFAULT_TOOL_CALL_CRITERIA = """- Selects the appropriate tool(s) for the user's request
- Calls all tools needed to fully answer the query without omissions
- Provides well-formed arguments matching the tool's parameter schema
- Uses correct argument values grounded in the user's query (no hallucinated values)
- Does not make redundant or unnecessary tool calls"""

DEFAULT_TOOL_ANSWER_CRITERIA = """- Accurately reflects the data returned by the tools without misquoting values
- Addresses all parts of the original user query
- Does not fabricate information beyond what the tools provided
- Synthesizes information from multiple tool results coherently
- Presents information clearly and completely with correct values and appropriate formatting"""

TOOL_CALL_CORRECTNESS_SYSTEM_PROMPT = """Return ONLY valid JSON with a single key 'correctness' as a number from 0 to 1. 0 means completely wrong tool selection or arguments, 1 means perfect tool selection and well-formed arguments."""

TOOL_CALL_CORRECTNESS_PROMPT_TEMPLATE = """You are an expert evaluator assessing whether an LLM correctly selected tools and provided well-formed arguments for a user query.

<UserQuery>
{user_query}
</UserQuery>

<ConversationHistory>
Full conversation history leading up to this tool call (includes any prior tool interactions and context):

{conversation_history}
</ConversationHistory>

<AvailableTools>
The following tools were available to the model:

{available_tools}
</AvailableTools>

<ModelToolCalls>
The model made the following tool calls:

{tool_calls}
</ModelToolCalls>

<Rubric>
A correct tool-calling response:
- Selects the appropriate tool(s) for the user's request
- Does not call unnecessary or irrelevant tools
- Calls all tools needed to fully answer the query (no missing calls)
- Provides valid, well-formed arguments matching the tool's parameter schema
- Uses correct argument values grounded in the user's query
  (e.g., correct entity names, correct enum values, no hallucinated values)
- Does not omit required parameters
- Covers all items from the user's request — the correct number and
  structure of calls depends on the tool's schema (some tools accept
  batch inputs in a single call; others require one call per item)

Penalize for:
- Calling the wrong tool
- Missing a tool call that was needed to fully answer the query
- Malformed or invalid arguments (wrong types, missing required fields)
- Hallucinated argument values not grounded in the user's query
- Redundant or duplicate tool calls
- Incorrect number of tool calls relative to the query
</Rubric>

<Criteria>
{criteria}
</Criteria>

<Instructions>
1. Read the user query to understand what information is needed
2. Review the available tools to understand what is possible
3. Check if the model selected the right tool(s) for this query
4. Verify that arguments are well-formed and match the user's intent
5. Check completeness — did the model make all necessary calls?
6. Score from 0 to 1 based on tool selection and argument quality
</Instructions>
"""

TOOL_ANSWER_CORRECTNESS_SYSTEM_PROMPT = """Return ONLY valid JSON with a single key 'correctness' as a number from 0 to 1. 0 means completely incorrect, 1 means fully correct and faithful to the tool results."""

TOOL_ANSWER_CORRECTNESS_PROMPT_TEMPLATE = """You are an expert evaluator assessing whether an LLM correctly synthesized tool results into a final answer.

IMPORTANT: Your job is to evaluate whether the final answer faithfully and completely uses the data returned by the tools shown in the conversation flow below.

<OriginalUserQuery>
{user_query}
</OriginalUserQuery>

<ConversationFlow>
Full conversation history showing all tool calls made, tool responses received, and surrounding context:

{conversation_flow}
</ConversationFlow>

<FinalAnswer>
{final_answer}
</FinalAnswer>

<Rubric>
A correct final answer:
- Accurately reflects the data returned by the tools
- Does not misquote, misinterpret, or alter tool result values
- Addresses ALL parts of the original user query
- Synthesizes information from multiple tool results coherently
- Does not make claims unsupported by the tool data
- Presents information clearly and completely

Penalize for:
- Misquoting tool result values (e.g., reporting a different number than what the tool returned)
- Omitting results for items the user asked about when tool data was available
- Adding information not present in tool results
- Contradicting tool results
- Partial answers when complete data was available
</Rubric>

<Criteria>
{criteria}
</Criteria>

<Instructions>
1. Read the original query to understand what the user wanted
2. Review the conversation flow to identify all tool results that were available
3. Compare the final answer against those tool results for accuracy
4. Check that ALL queried items are addressed in the answer
5. Verify no information was fabricated beyond what tools provided
6. Score from 0 to 1 based on faithfulness and completeness
</Instructions>
"""


# ============================================================================
# TOOL-CALLING PROMPT IMPROVEMENT PROMPTS
# Used when poor-performing spans carry response_type metadata.
# Keeps tool definitions as read-only context; only the system/user
# prompt text is improved.
# ============================================================================

TOOL_SUGGESTION_GENERATION_PROMPT_ANTHROPIC = """Analyze the following poor-performing LLM interactions and identify common issues. This is a tool-calling agent with two types of interactions: tool selection calls and final answer synthesis.

<CurrentPromptTemplate>
{current_prompt}
</CurrentPromptTemplate>

<AvailableTools (read-only — do NOT suggest changes to tool definitions)>
{tool_definitions}
</AvailableTools>

<PoorPerformingToolCallSpans>
These spans show cases where the model selected the WRONG tools or passed BAD arguments (scored on tool selection + argument quality):

{poor_tool_call_examples}
</PoorPerformingToolCallSpans>

<PoorPerformingTextSpans>
These spans show cases where the model received correct tool results but gave a BAD final answer (scored on answer faithfulness + completeness):

{poor_text_examples}
</PoorPerformingTextSpans>

<Instructions>
- Identify patterns: are failures mostly in tool selection, argument quality, or answer synthesis?
- Generate 3-5 specific, actionable suggestions to improve the prompt template
- Focus ONLY on improving the system/user prompt text
- Do NOT suggest changes to tool definitions (they are API contracts)
- If tool selection is poor, suggest clearer instructions about WHEN to use each tool
- If arguments are poor, suggest adding constraints or examples for HOW to call tools correctly
- If final answers are poor despite good tool results, suggest formatting and synthesis instructions
- Be concrete and specific, not generic
- If examples include user feedback, use it to refine the suggestions
</Instructions>

Return JSON in this exact format:
{{
  "suggestions": [
    "Suggestion 1: Specific improvement to address issue X",
    "Suggestion 2: Another specific improvement",
    ...
  ]
}}"""

TOOL_PROMPT_IMPROVEMENT_PROMPT_ANTHROPIC = """Improve the following prompt template based on the provided suggestions and example spans. This prompt is used by a tool-calling agent.

<CurrentPromptTemplate>
{current_prompt}
</CurrentPromptTemplate>

<AvailableTools (read-only context — do NOT modify tool definitions)>
{tool_definitions}
</AvailableTools>

<ImprovementSuggestions>
{suggestions}
</ImprovementSuggestions>

<GoodPerformingToolCallExamples (score >= 0.8)>
{good_tool_call_examples}
</GoodPerformingToolCallExamples>

<GoodPerformingTextExamples (score >= 0.8)>
{good_text_examples}
</GoodPerformingTextExamples>

<PoorPerformingToolCallExamples (score < 0.5)>
{poor_tool_call_examples}
</PoorPerformingToolCallExamples>

<PoorPerformingTextExamples (score < 0.5)>
{poor_text_examples}
</PoorPerformingTextExamples>

<Instructions>
- Improve ONLY the system and user prompt text
- Do NOT modify tool definitions — they are shown for context only
- If the current prompt has template variables (e.g., {{variable_name}}), preserve them exactly
- Use good examples to understand what works well and preserve those strengths
- Address issues evident in poor tool-call examples (tool selection and argument problems)
- Address issues evident in poor text examples (answer synthesis and faithfulness problems)
- If tool selection is a problem, add clearer guidance about when to use each tool
- If answer synthesis is a problem, add instructions about how to present tool results faithfully
- Keep the prompt generalizable — do not overfit to specific examples
- Return ONLY the improved prompt text, with no additional commentary
</Instructions>

Return the improved prompt:"""


# ============================================================================
# CRITERIA GENERATION PROMPTS
# ============================================================================

CRITERIA_GENERATION_SYSTEM_PROMPT = """You are an expert in evaluating LLM outputs. Your task is to generate evaluation criteria based on example inputs and outputs. Return ONLY valid JSON."""

CRITERIA_GENERATION_PROMPT = """Based on the following context and example LLM interactions, generate evaluation criteria rules for assessing the "correctness" of future outputs.

<Project Context>
{project_description}
</Project Context>

<Examples>
{examples}
</Examples>

{judge_feedback_note}

{agentic_note}

<Instructions>
- Generate at max 5 specific rules that define correctness for this type of task
- Each rule should be clear, specific, and actionable
- Focus on the most important patterns you observe in the examples
- Consider: accuracy, completeness, relevance, logical consistency, domain-specific requirements
- Rules should be applicable to similar future interactions
- Use the project context to understand domain-specific nuances
- Include rules that cover edge cases and boundary conditions observed in the examples
- When examples include "User feedback on Judge", use that feedback to refine and adjust the criteria accordingly - users are indicating where the Judge's scoring was wrong
- If examples show tool usage/agentic behavior, include criteria about proper tool usage and synthesis
</Instructions>

Return JSON in this exact format:
{{
  "correctness": [
    "Description of what makes output correct",
    "Another specific rule",
    ...
  ]
}}
"""


AGENTIC_NOTE_FOR_CRITERIA = """
<AgenticBehaviorNote>
IMPORTANT: These examples include agentic/tool-using behavior where the agent makes tool calls to gather information.

When generating criteria, consider:
- How well the agent uses tools to gather necessary information
- Whether the agent correctly interprets tool results
- How effectively the agent synthesizes information from multiple tool calls
- Whether the final output properly incorporates tool-provided data

Remember: Tool calls are intermediary steps. Evaluate the final output's correctness based on how well it uses the gathered information.
</AgenticBehaviorNote>
"""


# ============================================================================
# PROMPT IMPROVEMENT PROMPTS
# ============================================================================

SUGGESTION_GENERATION_SYSTEM_PROMPT_ANTHROPIC = """You are an expert prompt engineer analyzing LLM performance. Your task is to identify patterns in poor-performing outputs and generate specific, actionable suggestions for improving the prompt template. Return ONLY valid JSON."""

SUGGESTION_GENERATION_PROMPT_ANTHROPIC = """Analyze the following poor-performing LLM interactions (correctness score < 0.5) and identify common issues.

<Project Context>
{project_description}
</Project Context>

<Agent Context>
{agent_description}
</Agent Context>

<Current Prompt Template>
{current_prompt}
</Current Prompt Template>

<Poor Performing Examples>
{poor_examples}
</Poor Performing Examples>

{tool_usage_analysis}

<Instructions>
- Consider the project context and agent purpose when analyzing issues
- Identify common patterns in the poor-performing outputs
- Focus on what the prompt is missing or unclear about
- Generate 3-5 specific, actionable suggestions to improve the prompt
- Each suggestion should address a distinct issue
- Be concrete and specific, not generic
- Ensure suggestions align with the project domain and agent purpose
- If examples show tool usage, consider whether the prompt provides adequate guidance for tool selection and usage
- If tool definitions exist in the prompt, preserve them while improving instructions around them
</Instructions>

Return JSON in this exact format:
{{
  "suggestions": [
    "Suggestion 1: Specific improvement to address issue X",
    "Suggestion 2: Another specific improvement",
    ...
  ]
}}"""

PROMPT_IMPROVEMENT_SYSTEM_PROMPT_ANTHROPIC = """You are an expert prompt engineer. Your task is to improve a prompt template based on suggestions and example performance data. Return ONLY the improved prompt text, nothing else."""

PROMPT_IMPROVEMENT_PROMPT_ANTHROPIC = """Improve the following prompt template based on the provided context, suggestions, and example spans.

{project_context}

{agent_context}

<Current Prompt Template>
{current_prompt}
</Current Prompt Template>

<Improvement Suggestions>
{suggestions}
</Improvement Suggestions>

<Good Performing Examples (score >= 0.8)>
{good_examples}
</Good Performing Examples>

<Poor Performing Examples (score < 0.5)>
{poor_examples}
</Poor Performing Examples>

<Instructions>
- Consider the project context and agent purpose when improving the prompt
- Improve the prompt based on the suggestions
- Use good examples to understand what works well and preserve those strengths
- Address issues evident in poor examples
- Make the prompt more clear, specific, and actionable
- Ensure improvements align with the project domain and agent purpose
- Avoid overfitting to the specific examples - keep the prompt generalizable
- If the current prompt has template variables (e.g., {{variable_name}}), preserve them
- CRITICAL: If the prompt includes tool definitions or tool schemas, preserve them exactly as they are
- If improving instructions around tool usage, be specific about when and how to use each tool
- Return ONLY the improved prompt text, with no additional commentary
</Instructions>

Return the improved prompt:"""


# ============================================================================
# AGENT DESCRIPTION GENERATION PROMPTS
# ============================================================================

AGENT_DESCRIPTION_SYSTEM_PROMPT = """You are an expert in analyzing AI agent behavior. Your task is to generate a concise description of what an agent does based on example interactions. Return ONLY valid JSON."""

AGENT_DESCRIPTION_GENERATION_PROMPT = """Based on the following context and example interactions, generate a concise 1-2 sentence description of what this agent does.

<Project Context>
{project_description}
</Project Context>

<Example Interactions>
{examples}
</Example Interactions>

{feedback_note}

<Instructions>
- Generate a clear, concise description (3-4 sentences) of what this agent does
- Focus on the agent's purpose and functionality based on the examples
- Use the project context to understand the domain
- If feedback is provided, incorporate insights about what users expect from the agent
- Be specific about what the agent does, not generic
- Include a sentence about notable edge cases or boundary conditions the agent must handle correctly (e.g. empty inputs, ambiguous queries, out-of-scope requests, unusual formats)
</Instructions>

Return JSON in this exact format:
{{
  "description": "Clear description of what this agent does"
}}
"""

AGENT_DESCRIPTION_UPDATE_FROM_FEEDBACK_PROMPT = """Based on the following context, examples, and user feedback, update the agent description to serve as EXPLICIT SCORING GUIDANCE for a judge evaluating this agent's responses.

<Project Context>
{project_description}
</Project Context>

<Current Agent Description>
{current_description}
</Current Agent Description>

<Example Interactions with User Feedback>
{examples_with_feedback}
</Example Interactions with User Feedback>

<Instructions>
- The updated description will be given directly to a scoring judge — write it as scoring instructions, not just a purpose statement
- Start with 1 sentence on what this agent does
- For EVERY thumbs-down (negative) feedback entry: extract the specific failure pattern the user identified and add an explicit "MUST SCORE LOW (0.0 or near 0.0)" rule — be concrete and specific, not vague. Do not soften or generalise the user's intent.
- For thumbs-up (positive) feedback: briefly reinforce what a correct response looks like
- If negative feedback says something like "this should fail" or "score is wrong" or "too lenient", treat that as a hard FAIL condition and state it forcefully in the description
- Total length: 4-6 sentences maximum
- Do NOT include general platitudes like "responses should be helpful" — only specific, feedback-derived rules
</Instructions>

Return JSON in this exact format:
{{
  "description": "Agent purpose sentence. CORRECT responses look like: [from positive feedback]. MUST SCORE LOW (0.0): [specific failure pattern from negative feedback]. MUST SCORE LOW (0.0): [additional failure pattern if any]."
}}
"""


# ============================================================================
# DISPLAY NAME GENERATION PROMPTS
# ============================================================================

DISPLAY_NAME_USER_PROMPT = """Generate a concise display name (3-4 words, title case) for this prompt template:

{prompt_template}

Display name:"""


# ----------------------------------------------------------------------------
# Suggestion-generation system prompts
# ----------------------------------------------------------------------------

SUGGESTION_GENERATION_SYSTEM_PROMPT_OPENAI = (
    "You are an expert prompt engineer analyzing LLM performance.\n\n"
    "Your task:\n"
    "1. Identify patterns in poor-performing outputs\n"
    "2. Generate specific, actionable suggestions for improving the prompt template\n\n"
    "Return ONLY valid JSON — no explanation, no markdown fencing."
)

SUGGESTION_GENERATION_SYSTEM_PROMPT_GEMINI = (
    "## Role\n"
    "You are an expert prompt engineer analyzing LLM performance.\n\n"
    "## Task\n"
    "Identify patterns in poor-performing outputs and generate specific, "
    "actionable suggestions for improving the prompt template.\n\n"
    "## Output Format\n"
    "Return ONLY valid JSON with no additional text or markdown formatting."
)

SUGGESTION_GENERATION_SYSTEM_PROMPTS: dict[str, str] = {
    "anthropic": SUGGESTION_GENERATION_SYSTEM_PROMPT_ANTHROPIC,
    "openai": SUGGESTION_GENERATION_SYSTEM_PROMPT_OPENAI,
    "gemini": SUGGESTION_GENERATION_SYSTEM_PROMPT_GEMINI,
}

# ----------------------------------------------------------------------------
# Prompt-improvement system prompts
# ----------------------------------------------------------------------------

PROMPT_IMPROVEMENT_SYSTEM_PROMPT_OPENAI = (
    "You are an expert prompt engineer.\n\n"
    "Your task:\n"
    "- Improve the given prompt template using the provided suggestions and performance data\n"
    "- Return ONLY the improved prompt text — no commentary, no markdown code fences, no preamble"
)

PROMPT_IMPROVEMENT_SYSTEM_PROMPT_GEMINI = (
    "## Role\n"
    "You are an expert prompt engineer.\n\n"
    "## Task\n"
    "Improve the provided prompt template based on suggestions and example performance data.\n\n"
    "## Output\n"
    "Return ONLY the improved prompt text. "
    "Do not include any explanation, headers, or markdown formatting around the prompt."
)

PROMPT_IMPROVEMENT_SYSTEM_PROMPTS: dict[str, str] = {
    "anthropic": PROMPT_IMPROVEMENT_SYSTEM_PROMPT_ANTHROPIC,
    "openai": PROMPT_IMPROVEMENT_SYSTEM_PROMPT_OPENAI,
    "gemini": PROMPT_IMPROVEMENT_SYSTEM_PROMPT_GEMINI,
}

# ----------------------------------------------------------------------------
# Suggestion-generation user prompts  (standard / legacy path)
# ----------------------------------------------------------------------------

SUGGESTION_GENERATION_PROMPT_OPENAI = """Analyze the following poor-performing LLM interactions (correctness score < 0.5) and identify common issues.

### Project Context
{project_description}

### Agent Context
{agent_description}

### Current Prompt Template
{current_prompt}

### Poor Performing Examples
{poor_examples}

{tool_usage_analysis}

### Instructions
- Consider the project context and agent purpose when analyzing issues
- Identify common patterns in the poor-performing outputs
- Focus on what the prompt is missing or unclear about
- Generate 3-5 specific, actionable suggestions to improve the prompt
- Each suggestion should address a distinct issue
- Be concrete and specific, not generic
- Ensure suggestions align with the project domain and agent purpose
- If examples show tool usage, consider whether the prompt provides adequate guidance for tool selection and usage
- If tool definitions exist in the prompt, preserve them while improving instructions around them

Return JSON in this exact format:
{{
  "suggestions": [
    "Suggestion 1: Specific improvement to address issue X",
    "Suggestion 2: Another specific improvement",
    ...
  ]
}}"""

SUGGESTION_GENERATION_PROMPT_GEMINI = """Analyze the following poor-performing LLM interactions (correctness score < 0.5) and identify common issues.

## Project Context
{project_description}

## Agent Context
{agent_description}

## Current Prompt Template
{current_prompt}

## Poor Performing Examples
{poor_examples}

{tool_usage_analysis}

## Instructions
1. Consider the project context and agent purpose when analyzing issues
2. Identify common patterns in the poor-performing outputs
3. Focus on what the prompt is missing or unclear about
4. Generate 3-5 specific, actionable suggestions to improve the prompt
5. Each suggestion should address a distinct issue
6. Be concrete and specific, not generic
7. Ensure suggestions align with the project domain and agent purpose
8. If examples show tool usage, consider whether the prompt provides adequate guidance for tool selection and usage
9. If tool definitions exist in the prompt, preserve them while improving instructions around them

Return JSON in this exact format:
{{
  "suggestions": [
    "Suggestion 1: Specific improvement to address issue X",
    "Suggestion 2: Another specific improvement",
    ...
  ]
}}"""

SUGGESTION_GENERATION_PROMPTS: dict[str, str] = {
    "anthropic": SUGGESTION_GENERATION_PROMPT_ANTHROPIC,
    "openai": SUGGESTION_GENERATION_PROMPT_OPENAI,
    "gemini": SUGGESTION_GENERATION_PROMPT_GEMINI,
}

# ----------------------------------------------------------------------------
# Prompt-improvement user prompts  (standard / legacy path)
# ----------------------------------------------------------------------------

PROMPT_IMPROVEMENT_PROMPT_OPENAI = """Improve the following prompt template based on the provided context, suggestions, and example spans.

{project_context}

{agent_context}

### Current Prompt Template
{current_prompt}

### Improvement Suggestions
{suggestions}

### Good Performing Examples (score >= 0.8)
{good_examples}

### Poor Performing Examples (score < 0.5)
{poor_examples}

### Instructions
- Consider the project context and agent purpose when improving the prompt
- Improve the prompt based on the suggestions
- Use good examples to understand what works well and preserve those strengths
- Address issues evident in poor examples
- Make the prompt more clear, specific, and actionable
- Ensure improvements align with the project domain and agent purpose
- Avoid overfitting to the specific examples — keep the prompt generalizable
- If the current prompt has template variables (e.g., {{variable_name}}), preserve them exactly
- CRITICAL: If the prompt includes tool definitions or tool schemas, preserve them exactly as they are
- If improving instructions around tool usage, be specific about when and how to use each tool
- Return ONLY the improved prompt text, with no additional commentary

### Improved Prompt
"""

PROMPT_IMPROVEMENT_PROMPT_GEMINI = """Improve the following prompt template based on the provided context, suggestions, and example spans.

{project_context}

{agent_context}

## Current Prompt Template
{current_prompt}

## Improvement Suggestions
{suggestions}

## Good Performing Examples (score >= 0.8)
{good_examples}

## Poor Performing Examples (score < 0.5)
{poor_examples}

## Instructions
1. Consider the project context and agent purpose when improving the prompt
2. Use good examples to understand what works well and preserve those strengths
3. Address issues evident in poor examples
4. Make the prompt more clear, specific, and actionable
5. Ensure improvements align with the project domain and agent purpose
6. Avoid overfitting to specific examples — keep the prompt generalizable
7. Preserve all template variables exactly (e.g., {{variable_name}})
8. CRITICAL: If the prompt includes tool definitions or tool schemas, preserve them exactly
9. If improving tool usage instructions, be specific about when and how to use each tool
10. Return ONLY the improved prompt text with no additional commentary

## Improved Prompt
"""

PROMPT_IMPROVEMENT_PROMPTS: dict[str, str] = {
    "anthropic": PROMPT_IMPROVEMENT_PROMPT_ANTHROPIC,
    "openai": PROMPT_IMPROVEMENT_PROMPT_OPENAI,
    "gemini": PROMPT_IMPROVEMENT_PROMPT_GEMINI,
}

# ----------------------------------------------------------------------------
# Tool-calling suggestion-generation user prompts
# ----------------------------------------------------------------------------

TOOL_SUGGESTION_GENERATION_PROMPT_OPENAI = """Analyze the following poor-performing LLM interactions and identify common issues. This is a tool-calling agent with two types of interactions: tool selection calls and final answer synthesis.

### Current Prompt Template
{current_prompt}

### Available Tools (read-only — do NOT suggest changes to tool definitions)
{tool_definitions}

### Poor Performing Tool-Call Spans
These spans show cases where the model selected the WRONG tools or passed BAD arguments (scored on tool selection + argument quality):

{poor_tool_call_examples}

### Poor Performing Text Spans
These spans show cases where the model received correct tool results but gave a BAD final answer (scored on answer faithfulness + completeness):

{poor_text_examples}

### Instructions
- Identify patterns: are failures mostly in tool selection, argument quality, or answer synthesis?
- Generate 3-5 specific, actionable suggestions to improve the prompt template
- Focus ONLY on improving the system/user prompt text
- Do NOT suggest changes to tool definitions (they are API contracts)
- If tool selection is poor, suggest clearer instructions about WHEN to use each tool
- If arguments are poor, suggest adding constraints or examples for HOW to call tools correctly
- If final answers are poor despite good tool results, suggest formatting and synthesis instructions
- Be concrete and specific, not generic
- If examples include user feedback, use it to refine the suggestions

Return JSON in this exact format:
{{
  "suggestions": [
    "Suggestion 1: Specific improvement to address issue X",
    "Suggestion 2: Another specific improvement",
    ...
  ]
}}"""

TOOL_SUGGESTION_GENERATION_PROMPT_GEMINI = """Analyze the following poor-performing LLM interactions and identify common issues. This is a tool-calling agent with two types of interactions: tool selection calls and final answer synthesis.

## Current Prompt Template
{current_prompt}

## Available Tools (read-only — do NOT suggest changes to tool definitions)
{tool_definitions}

## Poor Performing Tool-Call Spans
These spans show cases where the model selected the WRONG tools or passed BAD arguments:

{poor_tool_call_examples}

## Poor Performing Text Spans
These spans show cases where the model received correct tool results but gave a BAD final answer:

{poor_text_examples}

## Instructions
1. Identify patterns: are failures mostly in tool selection, argument quality, or answer synthesis?
2. Generate 3-5 specific, actionable suggestions to improve the prompt template
3. Focus ONLY on improving the system/user prompt text
4. Do NOT suggest changes to tool definitions (they are API contracts)
5. If tool selection is poor, suggest clearer instructions about WHEN to use each tool
6. If arguments are poor, suggest adding constraints or examples for HOW to call tools correctly
7. If final answers are poor despite good tool results, suggest formatting and synthesis instructions
8. Be concrete and specific, not generic
9. If examples include user feedback, use it to refine the suggestions

Return JSON in this exact format:
{{
  "suggestions": [
    "Suggestion 1: Specific improvement to address issue X",
    "Suggestion 2: Another specific improvement",
    ...
  ]
}}"""

TOOL_SUGGESTION_GENERATION_PROMPTS: dict[str, str] = {
    "anthropic": TOOL_SUGGESTION_GENERATION_PROMPT_ANTHROPIC,
    "openai": TOOL_SUGGESTION_GENERATION_PROMPT_OPENAI,
    "gemini": TOOL_SUGGESTION_GENERATION_PROMPT_GEMINI,
}

# ----------------------------------------------------------------------------
# Tool-calling prompt-improvement user prompts
# ----------------------------------------------------------------------------

TOOL_PROMPT_IMPROVEMENT_PROMPT_OPENAI = """Improve the following prompt template based on the provided suggestions and example spans. This prompt is used by a tool-calling agent.

### Current Prompt Template
{current_prompt}

### Available Tools (read-only context — do NOT modify tool definitions)
{tool_definitions}

### Improvement Suggestions
{suggestions}

### Good Performing Tool-Call Examples (score >= 0.8)
{good_tool_call_examples}

### Good Performing Text Examples (score >= 0.8)
{good_text_examples}

### Poor Performing Tool-Call Examples (score < 0.5)
{poor_tool_call_examples}

### Poor Performing Text Examples (score < 0.5)
{poor_text_examples}

### Instructions
- Improve ONLY the system and user prompt text
- Do NOT modify tool definitions — they are shown for context only
- If the current prompt has template variables (e.g., {{variable_name}}), preserve them exactly
- Use good examples to understand what works well and preserve those strengths
- Address issues evident in poor tool-call examples (tool selection and argument problems)
- Address issues evident in poor text examples (answer synthesis and faithfulness problems)
- If tool selection is a problem, add clearer guidance about when to use each tool
- If answer synthesis is a problem, add instructions about how to present tool results faithfully
- Keep the prompt generalizable — do not overfit to specific examples
- Return ONLY the improved prompt text, with no additional commentary

### Improved Prompt
"""

TOOL_PROMPT_IMPROVEMENT_PROMPT_GEMINI = """Improve the following prompt template based on the provided suggestions and example spans. This prompt is used by a tool-calling agent.

## Current Prompt Template
{current_prompt}

## Available Tools (read-only context — do NOT modify tool definitions)
{tool_definitions}

## Improvement Suggestions
{suggestions}

## Good Performing Tool-Call Examples (score >= 0.8)
{good_tool_call_examples}

## Good Performing Text Examples (score >= 0.8)
{good_text_examples}

## Poor Performing Tool-Call Examples (score < 0.5)
{poor_tool_call_examples}

## Poor Performing Text Examples (score < 0.5)
{poor_text_examples}

## Instructions
1. Improve ONLY the system and user prompt text
2. Do NOT modify tool definitions — they are shown for context only
3. Preserve all template variables exactly (e.g., {{variable_name}})
4. Use good examples to understand what works well and preserve those strengths
5. Address tool-call issues: tool selection errors and malformed arguments
6. Address text issues: answer synthesis and faithfulness to tool results
7. If tool selection is a problem, add clearer guidance about when to use each tool
8. If answer synthesis is a problem, add instructions about presenting tool results faithfully
9. Keep the prompt generalizable — do not overfit to specific examples
10. Return ONLY the improved prompt text with no additional commentary

## Improved Prompt
"""

TOOL_PROMPT_IMPROVEMENT_PROMPTS: dict[str, str] = {
    "anthropic": TOOL_PROMPT_IMPROVEMENT_PROMPT_ANTHROPIC,
    "openai": TOOL_PROMPT_IMPROVEMENT_PROMPT_OPENAI,
    "gemini": TOOL_PROMPT_IMPROVEMENT_PROMPT_GEMINI,
}


def get_prompt_for_provider(prompt_dict: dict, provider: str) -> str:
    """Return the prompt variant for *provider*, defaulting to 'anthropic'."""
    return prompt_dict.get(provider, prompt_dict["anthropic"])
