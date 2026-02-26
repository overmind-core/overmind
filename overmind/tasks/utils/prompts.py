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
    scope/verification constraints, direct output instructions
  - OpenAI (GPT):       Markdown ### headers, numbered step lists,
    explicit output anchor ("### Improved Prompt\n"),
    XML-style blocks for scope_constraints, output_verbosity, long_context_handling,
    uncertainty_and_ambiguity, and tool_calling_best_practices
  - Gemini:             XML tags (<role>, <context>, <task>, <constraints>,
    <output_format>, <final_instruction>), context-first ordering,
    explicit planning/validation steps, self-critique before output

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

<AvailableTools readonly="true">
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

<long_context_handling>
If the examples above are lengthy or numerous:
- First produce a short internal outline of the key failure patterns (tool selection vs. argument quality vs. answer synthesis) before generating suggestions.
- Anchor each suggestion to specific observed failures rather than speaking generically.
</long_context_handling>

<Instructions>
- Identify patterns: are failures mostly in tool selection, argument quality, or answer synthesis?
- Generate 3-5 specific, actionable suggestions to improve the prompt template
- Focus ONLY on improving the system/user prompt text — tool definitions are API contracts and must stay unchanged
- If tool selection is poor, suggest clearer instructions about WHEN to use each tool
- If arguments are poor, suggest adding constraints or examples for HOW to call tools correctly
- If final answers are poor despite good tool results, suggest formatting and synthesis instructions
- Be concrete and specific, not generic
- If examples include user feedback, use it to refine the suggestions
</Instructions>

<tool_calling_best_practices>
When suggesting tool-usage improvements, consider:
- Whether the prompt should encourage parallelizing independent tool calls to reduce latency
- Whether verification steps should follow high-impact tool operations
- Whether tool descriptions in the prompt are specific enough (1–2 sentences each for what they do and when to use them)
- Whether the prompt instructs the model to prefer tools over internal knowledge for fresh or user-specific data
</tool_calling_best_practices>

<output_verbosity>
Each suggestion: 1–2 sentences — state the problem briefly, then the fix.
Prefer compact, actionable bullets over long narrative paragraphs.
</output_verbosity>

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

<AvailableTools readonly="true">
{tool_definitions}
</AvailableTools>

<ImprovementSuggestions>
{suggestions}
</ImprovementSuggestions>

<GoodPerformingToolCallExamples score=">=0.8">
{good_tool_call_examples}
</GoodPerformingToolCallExamples>

<GoodPerformingTextExamples score=">=0.8">
{good_text_examples}
</GoodPerformingTextExamples>

<PoorPerformingToolCallExamples score="<0.5">
{poor_tool_call_examples}
</PoorPerformingToolCallExamples>

<PoorPerformingTextExamples score="<0.5">
{poor_text_examples}
</PoorPerformingTextExamples>

<Instructions>
- Improve ONLY the system and user prompt text
- Tool definitions are shown for context only — they must remain unchanged
- If the current prompt has template variables (e.g., {{variable_name}}), preserve them exactly
- Use good examples to understand what works well and preserve those strengths
- Address issues evident in poor tool-call examples (tool selection and argument problems)
- Address issues evident in poor text examples (answer synthesis and faithfulness problems)
- If tool selection is a problem, add clearer guidance about when to use each tool
- If answer synthesis is a problem, add instructions about how to present tool results faithfully
- Keep the prompt generalizable — do not overfit to specific examples
</Instructions>

<scope_constraints>
Implement EXACTLY and ONLY the changes supported by the improvement suggestions.
Do not add extra sections, features, or tool-usage instructions beyond what the suggestions call for.
If a suggestion is ambiguous, choose the simplest valid interpretation.
Preserve the original prompt's structure unless a suggestion explicitly calls for restructuring.
</scope_constraints>

<tool_calling_best_practices>
When improving tool-usage instructions, consider adding:
- Guidance to parallelize independent tool calls when possible to reduce latency
- A requirement to briefly summarize what changed and any validation performed after write/update tool calls
- Specific 1–2 sentence descriptions for when to use each tool, if the current prompt lacks them
- Instructions to prefer tools over internal knowledge for fresh or user-specific data
</tool_calling_best_practices>

<output_verbosity>
Keep the improved prompt concise — prefer compact bullets and short sections over long narrative paragraphs.
If adding constraints, use crisp 1–2 sentence rules.
</output_verbosity>

<verification>
Before returning the improved prompt, verify that:
- All template variables from the original prompt are preserved
- Tool definitions and schemas remain unchanged
- The improvements address the provided suggestions without adding unsolicited changes
</verification>

Respond with ONLY the improved prompt text, with no additional commentary."""


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

SUGGESTION_GENERATION_SYSTEM_PROMPT_ANTHROPIC = """You are an expert prompt engineer analyzing LLM performance. Your task is to identify patterns in poor-performing outputs and generate specific, actionable suggestions for improving the prompt template. Precise, well-targeted suggestions lead to measurable quality gains in downstream prompts, so focus on high-impact issues.

<output_constraints>
Return ONLY valid JSON — no explanation, no markdown fencing.
Each suggestion should be 1–2 sentences: state the problem briefly, then the fix.
Provide 3–5 suggestions; prefer fewer, higher-impact suggestions over many small ones.
</output_constraints>"""

SUGGESTION_GENERATION_PROMPT_ANTHROPIC = """Analyze the following poor-performing LLM interactions (correctness score < 0.5) and identify common issues.

<ProjectContext>
{project_description}
</ProjectContext>

<AgentContext>
{agent_description}
</AgentContext>

<CurrentPromptTemplate>
{current_prompt}
</CurrentPromptTemplate>

<PoorPerformingExamples>
{poor_examples}
</PoorPerformingExamples>

{tool_usage_analysis}

<long_context_handling>
If the examples above are lengthy or numerous:
- First produce a short internal outline of the key failure patterns before generating suggestions.
- Re-state the agent's core purpose and constraints before analyzing.
- Anchor each suggestion to specific observed failures rather than speaking generically.
</long_context_handling>

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

<uncertainty_and_ambiguity>
If failure patterns are ambiguous or could stem from multiple root causes, explicitly state the most likely interpretation and label your assumption.
Anchor claims in the provided examples — use language like "Based on the provided examples…" rather than absolute claims.
</uncertainty_and_ambiguity>

<output_verbosity>
Each suggestion: 1–2 sentences — state the problem briefly, then the fix.
Prefer compact, actionable bullets over long narrative paragraphs.
</output_verbosity>

Return JSON in this exact format:
{{
  "suggestions": [
    "Suggestion 1: Specific improvement to address issue X",
    "Suggestion 2: Another specific improvement",
    ...
  ]
}}"""

PROMPT_IMPROVEMENT_SYSTEM_PROMPT_ANTHROPIC = """You are an expert prompt engineer. Your task is to improve a prompt template based on suggestions and example performance data. A well-improved prompt directly addresses observed failure patterns while preserving what already works, leading to measurable quality gains.

<output_constraints>
Return ONLY the improved prompt text — no commentary, no markdown code fences, no preamble.
Respond directly with the prompt content without introductory phrases like "Here is…" or "Based on…".
</output_constraints>

<scope_constraints>
Implement EXACTLY and ONLY the improvements supported by the suggestions and examples.
Do not add unsolicited features, sections, or instructions beyond what the suggestions call for.
If a suggestion is ambiguous, choose the simplest valid interpretation.
Preserve the original prompt's tone, structure, and template variables unless a suggestion explicitly requires changing them.
</scope_constraints>"""

PROMPT_IMPROVEMENT_PROMPT_ANTHROPIC = """Improve the following prompt template based on the provided context, suggestions, and example spans.

{project_context}

{agent_context}

<CurrentPromptTemplate>
{current_prompt}
</CurrentPromptTemplate>

<ImprovementSuggestions>
{suggestions}
</ImprovementSuggestions>

<GoodPerformingExamples score=">=0.8">
{good_examples}
</GoodPerformingExamples>

<PoorPerformingExamples score="<0.5">
{poor_examples}
</PoorPerformingExamples>

<Instructions>
- Consider the project context and agent purpose when improving the prompt
- Improve the prompt based on the suggestions
- Use good examples to understand what works well and preserve those strengths
- Address issues evident in poor examples
- Make the prompt more clear, specific, and actionable
- Ensure improvements align with the project domain and agent purpose
- Avoid overfitting to the specific examples — keep the prompt generalizable
- If the current prompt has template variables (e.g., {{variable_name}}), preserve them exactly
- If the prompt includes tool definitions or tool schemas, preserve them exactly as they are
- If improving instructions around tool usage, be specific about when and how to use each tool
</Instructions>

<scope_constraints>
Implement EXACTLY and ONLY the changes supported by the improvement suggestions.
Do not add extra sections, features, or instructions beyond what the suggestions call for.
If a suggestion is ambiguous, choose the simplest valid interpretation.
Preserve the original prompt's structure unless a suggestion explicitly calls for restructuring.
</scope_constraints>

<output_verbosity>
Keep the improved prompt concise — prefer compact bullets and short sections over long narrative paragraphs.
If adding constraints or instructions, use crisp 1–2 sentence rules.
</output_verbosity>

<verification>
Before returning the improved prompt, verify that:
- All template variables from the original prompt are preserved
- Tool definitions and schemas remain unchanged
- The improvements address the provided suggestions without adding unsolicited changes
</verification>

Respond with ONLY the improved prompt text, with no additional commentary."""


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

SUGGESTION_GENERATION_SYSTEM_PROMPT_OPENAI = """You are an expert prompt engineer analyzing LLM performance.

Your task:
1. Diagnose failure patterns in poor-performing outputs — classify each as: missing context, vague instructions, formatting gap, tool-guidance gap, or scope drift
2. Generate specific, actionable suggestions for improving the prompt template

<output_constraints>
- Return ONLY valid JSON — no explanation, no markdown fencing.
- Each suggestion: 1–2 sentences — state the problem briefly, then the fix.
- Do not restate the problem context — go straight to actionable improvements.
- 3–5 suggestions maximum; prefer fewer, higher-impact suggestions over many small ones.
</output_constraints>"""

SUGGESTION_GENERATION_SYSTEM_PROMPT_GEMINI = """<role>
You are an expert prompt engineer analyzing LLM performance. You are precise, analytical, and focused on identifying high-impact failure patterns.
</role>

<instructions>
1. **Analyze**: Identify patterns in poor-performing outputs by comparing them against the prompt template and agent purpose.
2. **Diagnose**: Determine root causes — distinguish between prompt ambiguity, missing constraints, and unclear instructions.
3. **Suggest**: Generate specific, actionable suggestions that directly address the diagnosed issues.
4. **Validate**: Before returning, verify each suggestion is grounded in the provided examples and not generic advice.
</instructions>

<constraints>
- Return ONLY valid JSON — no explanation, no markdown fencing.
- Each suggestion: 1–2 sentences — state the problem briefly, then the fix.
- Provide 3–5 suggestions; prefer fewer, higher-impact suggestions over many small ones.
- Anchor claims in the provided examples — use language like "Based on the provided examples…" rather than absolute claims.
</constraints>

<output_format>
Valid JSON with a single key "suggestions" containing an array of strings. No other keys or text.
</output_format>"""

SUGGESTION_GENERATION_SYSTEM_PROMPTS: dict[str, str] = {
    "anthropic": SUGGESTION_GENERATION_SYSTEM_PROMPT_ANTHROPIC,
    "openai": SUGGESTION_GENERATION_SYSTEM_PROMPT_OPENAI,
    "gemini": SUGGESTION_GENERATION_SYSTEM_PROMPT_GEMINI,
}

# ----------------------------------------------------------------------------
# Prompt-improvement system prompts
# ----------------------------------------------------------------------------

PROMPT_IMPROVEMENT_SYSTEM_PROMPT_OPENAI = """You are an expert prompt engineer.

Your task:
- Improve the given prompt template using the provided suggestions and performance data
- Return ONLY the improved prompt text — no commentary, no markdown code fences, no preamble

<scope_constraints>
- Implement EXACTLY and ONLY the improvements supported by the suggestions and examples.
- Do not add unsolicited features, sections, or instructions beyond what the suggestions call for.
- If a suggestion is ambiguous, choose the simplest valid interpretation.
- Preserve the original prompt's tone, structure, and template variables unless a suggestion explicitly requires changing them.
</scope_constraints>"""

PROMPT_IMPROVEMENT_SYSTEM_PROMPT_GEMINI = """<role>
You are an expert prompt engineer. You are precise and systematic, improving prompts by directly addressing observed failure patterns while preserving what already works.
</role>

<instructions>
1. **Plan**: Review the suggestions and identify which parts of the prompt each suggestion targets.
2. **Execute**: Apply each suggestion to the prompt, making minimal targeted changes.
3. **Validate**: Verify that all template variables are preserved and no unsolicited changes were introduced.
4. **Format**: Return only the improved prompt text.
</instructions>

<constraints>
- Return ONLY the improved prompt text — no explanation, preamble, headers, or markdown formatting around the prompt.
- Implement EXACTLY and ONLY the improvements supported by the suggestions and examples.
- Do not add unsolicited features, sections, or instructions beyond what the suggestions call for.
- If a suggestion is ambiguous, choose the simplest valid interpretation.
- Preserve the original prompt's tone, structure, and template variables unless a suggestion explicitly requires changing them.
</constraints>

<output_format>
Return the improved prompt text directly with no surrounding commentary.
</output_format>"""

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

<long_context_handling>
If the examples above are lengthy or numerous:
- First, produce a short internal outline of the key failure patterns before generating suggestions.
- Re-state the agent's core purpose and constraints before analyzing.
- Anchor each suggestion to specific observed failures rather than speaking generically.
</long_context_handling>

### Instructions
1. Review the project context and agent purpose first — understand the domain before analyzing failures.
2. Classify each failure into one of: missing context, vague instructions, formatting gap, scope drift, tool-guidance gap, or hallucination/grounding issue.
3. Identify the 3–5 most common failure patterns across examples — group similar failures together.
4. For each pattern, generate one specific, actionable suggestion that directly addresses that root cause.
5. Be concrete and domain-specific — name the exact instruction or section in the prompt that needs changing.
6. Do not suggest changes that are not evidenced by at least one example; do not expand problem scope beyond what the data shows.
7. If examples show tool usage, assess whether failures stem from poor tool selection, bad arguments, or weak answer synthesis — address each separately.
8. If tool definitions exist in the prompt, preserve them; only suggest changes to surrounding instructions.

<uncertainty_and_ambiguity>
- If failure patterns are ambiguous or could stem from multiple root causes, explicitly state the most likely interpretation and label your assumption.
- Never fabricate specific scores, line numbers, or references not grounded in the provided examples.
- Prefer language like "Based on the provided examples…" over absolute claims.
</uncertainty_and_ambiguity>

<output_verbosity>
- Each suggestion: 1–2 sentences — state the problem briefly, then the fix.
- Do not rephrase the user's request or restate the full context.
- Prefer compact, actionable bullets over long narrative paragraphs.
</output_verbosity>

Return JSON in this exact format:
{{
  "suggestions": [
    "Suggestion 1: Specific improvement to address issue X",
    "Suggestion 2: Another specific improvement",
    ...
  ]
}}"""

SUGGESTION_GENERATION_PROMPT_GEMINI = """Analyze the following poor-performing LLM interactions (correctness score < 0.5) and identify common issues.

<context>
<project_context>
{project_description}
</project_context>

<agent_context>
{agent_description}
</agent_context>

<current_prompt_template>
{current_prompt}
</current_prompt_template>

<poor_performing_examples>
{poor_examples}
</poor_performing_examples>

{tool_usage_analysis}
</context>

<long_context_handling>
If the examples above are lengthy or numerous:
- First produce a short internal outline of the key failure patterns before generating suggestions.
- Re-state the agent's core purpose and constraints before analyzing.
- Anchor each suggestion to specific observed failures rather than speaking generically.
</long_context_handling>

<task>
Based on the context above, generate 3–5 specific, actionable suggestions to improve the prompt template.

Before generating suggestions:
1. Parse the agent's core purpose and constraints from the project and agent context.
2. Identify the most common failure patterns in the poor-performing examples.
3. Determine root causes — are failures due to prompt ambiguity, missing constraints, or unclear instructions?

Then generate suggestions following these rules:
- Consider the project context and agent purpose when analyzing issues.
- Focus on what the prompt is missing or unclear about.
- Each suggestion should address a distinct issue.
- Be concrete and specific, not generic.
- Ensure suggestions align with the project domain and agent purpose.
- If examples show tool usage, consider whether the prompt provides adequate guidance for tool selection and usage.
- If tool definitions exist in the prompt, preserve them while improving instructions around them.
</task>

<uncertainty_and_ambiguity>
If failure patterns are ambiguous or could stem from multiple root causes, explicitly state the most likely interpretation and label your assumption.
Anchor claims in the provided examples — use language like "Based on the provided examples…" rather than absolute claims.
</uncertainty_and_ambiguity>

<output_format>
Each suggestion: 1–2 sentences — state the problem briefly, then the fix.
Prefer compact, actionable bullets over long narrative paragraphs.

Return JSON in this exact format:
{{
  "suggestions": [
    "Suggestion 1: Specific improvement to address issue X",
    "Suggestion 2: Another specific improvement",
    ...
  ]
}}
</output_format>

<final_instruction>
Before returning, verify each suggestion is grounded in the provided examples and addresses a specific observed failure pattern.
</final_instruction>"""

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
1. Review the project and agent context to understand the domain before making any changes.
2. Study good examples to identify what the current prompt handles well — preserve those sections unchanged.
3. For each improvement suggestion, apply the minimum change needed — no scope expansion beyond what the suggestion requires.
4. Study poor examples to confirm each suggestion is still needed; skip any suggestion already addressed by the current prompt.
5. Make instructions clear, specific, and actionable for the target domain — prefer numbered steps over prose.
6. Implement EXACTLY what the suggestions ask — do not add unrequested features, structure, or embellishments.
7. Keep the prompt generalizable — do not hardcode details from specific examples.
8. Preserve all template variables exactly as they appear (e.g., {{variable_name}}).
9. CRITICAL: If the prompt includes tool definitions or tool schemas, preserve them exactly — only improve surrounding instructions.
10. If improving tool usage instructions, state explicitly: when to use each tool, what arguments to provide, and how to handle results.
11. If a suggestion is ambiguous, state your interpretation assumption explicitly rather than guessing — choose the simplest valid interpretation.

<scope_constraints>
- Implement EXACTLY and ONLY the changes supported by the improvement suggestions.
- Do not add extra sections, features, or instructions beyond what the suggestions call for.
- If a suggestion is ambiguous, choose the simplest valid interpretation.
- Do not restructure the prompt unless a suggestion explicitly calls for it.
</scope_constraints>

<output_verbosity>
- Keep the improved prompt concise — avoid long narrative paragraphs; prefer compact bullets and short sections.
- Do not add redundant restatements of the user's request.
- If adding constraints or instructions, use crisp 1–2 sentence rules.
</output_verbosity>

### Improved Prompt
"""

PROMPT_IMPROVEMENT_PROMPT_GEMINI = """Improve the following prompt template based on the provided context, suggestions, and example spans.

<context>
{project_context}

{agent_context}

<current_prompt_template>
{current_prompt}
</current_prompt_template>

<improvement_suggestions>
{suggestions}
</improvement_suggestions>

<good_performing_examples score=">=0.8">
{good_examples}
</good_performing_examples>

<poor_performing_examples score="<0.5">
{poor_examples}
</poor_performing_examples>
</context>

<task>
Based on the context above, improve the prompt template by applying the provided suggestions.

Before making changes:
1. Review the suggestions and identify which parts of the prompt each targets.
2. Study the good examples to understand what works well — preserve those strengths.
3. Study the poor examples to understand what fails — address those weaknesses.

Apply improvements following these rules:
- Consider the project context and agent purpose when improving the prompt.
- Make the prompt more clear, specific, and actionable.
- Ensure improvements align with the project domain and agent purpose.
- Avoid overfitting to specific examples — keep the prompt generalizable.
- Preserve all template variables exactly (e.g., {{variable_name}}).
- CRITICAL: If the prompt includes tool definitions or tool schemas, preserve them exactly.
- If improving tool usage instructions, be specific about when and how to use each tool.
</task>

<scope_constraints>
- Implement EXACTLY and ONLY the changes supported by the improvement suggestions.
- Do not add extra sections, features, or instructions beyond what the suggestions call for.
- If a suggestion is ambiguous, choose the simplest valid interpretation.
- Preserve the original prompt's structure unless a suggestion explicitly calls for restructuring.
</scope_constraints>

<output_format>
Keep the improved prompt concise — prefer compact bullets and short sections over long narrative paragraphs.
If adding constraints or instructions, use crisp 1–2 sentence rules.
Return ONLY the improved prompt text with no additional commentary.
</output_format>

<final_instruction>
Before returning the improved prompt, verify that:
- All template variables from the original prompt are preserved.
- Tool definitions and schemas remain unchanged.
- The improvements address the provided suggestions without adding unsolicited changes.
</final_instruction>"""

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

<long_context_handling>
If the examples above are lengthy or numerous:
- First, produce a short internal outline of the key failure patterns (tool selection vs. argument quality vs. answer synthesis) before generating suggestions.
- Anchor each suggestion to specific observed failures rather than speaking generically.
</long_context_handling>

### Instructions
1. Diagnose the failure type first — classify each failure as: wrong tool selected, bad arguments, missing tool call, answer hallucination, answer incompleteness, or synthesis error.
2. Identify whether failures are concentrated in tool-call spans, text spans, or both — address each failure type separately.
3. Generate 3–5 specific, actionable suggestions — each addressing a distinct failure class with a concrete fix.
4. Scope suggestions to the system/user prompt text ONLY — do NOT suggest changes to tool definitions (they are API contracts).
5. Do not expand the problem surface area beyond what the examples demonstrate.
6. For tool selection failures: suggest crisp WHEN-to-use criteria (1–2 sentences per tool) that distinguish between similar tools with concrete conditions.
7. For argument failures: suggest explicit format constraints, value grounding rules, or schema reminders for the specific arguments that fail.
8. If the prompt lacks parallelism guidance, suggest adding an instruction to parallelize independent read operations to reduce latency.
9. For answer synthesis failures: suggest instructions to faithfully reflect tool results, cover all queried items, and require the model to briefly restate what changed and where after any write/update tool call.
10. If failure causes are ambiguous, state your interpretation as an explicit assumption within the suggestion rather than guessing silently.
11. If examples include user feedback, treat it as ground truth — prioritize those failure patterns above inferred ones.

<tool_calling_best_practices>
When suggesting tool-usage improvements, consider:
- Whether the prompt should encourage parallelizing independent tool calls to reduce latency.
- Whether verification steps should follow high-impact tool operations.
- Whether tool descriptions in the prompt are crisp enough (1–2 sentences each for what they do and when to use them).
- Whether the prompt instructs the model to prefer tools over internal knowledge for fresh or user-specific data.
</tool_calling_best_practices>

<output_verbosity>
- Each suggestion: 1–2 sentences — state the problem briefly, then the fix.
- Do not rephrase the full context. Prefer compact, actionable bullets.
</output_verbosity>

Return JSON in this exact format:
{{
  "suggestions": [
    "Suggestion 1: Specific improvement to address issue X",
    "Suggestion 2: Another specific improvement",
    ...
  ]
}}"""

TOOL_SUGGESTION_GENERATION_PROMPT_GEMINI = """Analyze the following poor-performing LLM interactions and identify common issues. This is a tool-calling agent with two types of interactions: tool selection calls and final answer synthesis.

<context>
<current_prompt_template>
{current_prompt}
</current_prompt_template>

<available_tools readonly="true">
{tool_definitions}
</available_tools>

<poor_performing_tool_call_spans>
These spans show cases where the model selected the WRONG tools or passed BAD arguments (scored on tool selection + argument quality):

{poor_tool_call_examples}
</poor_performing_tool_call_spans>

<poor_performing_text_spans>
These spans show cases where the model received correct tool results but gave a BAD final answer (scored on answer faithfulness + completeness):

{poor_text_examples}
</poor_performing_text_spans>
</context>

<long_context_handling>
If the examples above are lengthy or numerous:
- First produce a short internal outline of the key failure patterns (tool selection vs. argument quality vs. answer synthesis) before generating suggestions.
- Anchor each suggestion to specific observed failures rather than speaking generically.
</long_context_handling>

<task>
Based on the context above, generate 3–5 specific, actionable suggestions to improve the prompt template.

Before generating suggestions:
1. Classify the failure patterns: are they mostly in tool selection, argument quality, or answer synthesis?
2. Identify root causes for each failure category.
3. Prioritize suggestions by impact.

Then generate suggestions following these rules:
- Focus ONLY on improving the system/user prompt text — tool definitions are API contracts and must stay unchanged.
- If tool selection is poor, suggest clearer instructions about WHEN to use each tool.
- If arguments are poor, suggest adding constraints or examples for HOW to call tools correctly.
- If final answers are poor despite good tool results, suggest formatting and synthesis instructions.
- Be concrete and specific, not generic.
- If examples include user feedback, use it to refine the suggestions.
</task>

<tool_calling_best_practices>
When suggesting tool-usage improvements, consider:
- Whether the prompt should encourage parallelizing independent tool calls to reduce latency.
- Whether verification steps should follow high-impact tool operations.
- Whether tool descriptions in the prompt are specific enough (1–2 sentences each for what they do and when to use them).
- Whether the prompt instructs the model to prefer tools over internal knowledge for fresh or user-specific data.
</tool_calling_best_practices>

<output_format>
Each suggestion: 1–2 sentences — state the problem briefly, then the fix.
Prefer compact, actionable bullets over long narrative paragraphs.

Return JSON in this exact format:
{{
  "suggestions": [
    "Suggestion 1: Specific improvement to address issue X",
    "Suggestion 2: Another specific improvement",
    ...
  ]
}}
</output_format>

<final_instruction>
Before returning, verify each suggestion is grounded in the provided examples and addresses a specific observed failure pattern.
</final_instruction>"""

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
1. Improve ONLY the system and user prompt text — do NOT modify tool definitions (shown for context only).
2. Preserve all template variables exactly as they appear (e.g., {{variable_name}}).
3. Study good examples first — identify what the current prompt handles correctly and preserve those sections unchanged.
4. Apply each improvement suggestion using the minimum change needed; do not expand scope beyond what the suggestion requires.
5. For tool selection problems: add clear WHEN-to-use criteria for each tool, with concrete conditions that distinguish between similar tools.
6. For argument problems: add explicit grounding rules — values must come from the user query or prior tool results, never invented.
7. For answer synthesis problems: add explicit instructions to faithfully reflect tool results, cover all queried items, and avoid claims unsupported by tool data.
8. If a suggestion is ambiguous, state your interpretation assumption explicitly; choose the simplest valid interpretation rather than guessing silently.
9. Keep the prompt generalizable — do not hardcode values or details from specific examples.

<scope_constraints>
- Implement EXACTLY and ONLY the changes supported by the improvement suggestions.
- Do not add extra sections, features, or tool-usage instructions beyond what the suggestions call for.
- If a suggestion is ambiguous, choose the simplest valid interpretation.
- Do not restructure the prompt unless a suggestion explicitly calls for it.
</scope_constraints>

<tool_calling_best_practices>
When improving tool-usage instructions, consider adding:
- Guidance to parallelize independent tool calls (e.g., read_file, fetch_record, search_docs) when possible.
- A requirement to briefly restate what changed, where (ID or path), and any validation performed after write/update tool calls.
- Crisp 1–2 sentence descriptions for when to use each tool, if the current prompt lacks them.
- Instructions to prefer tools over internal knowledge for fresh or user-specific data.
</tool_calling_best_practices>

<output_verbosity>
- Keep the improved prompt concise — prefer compact bullets and short sections over long narrative paragraphs.
- If adding constraints, use crisp 1–2 sentence rules.
</output_verbosity>

### Improved Prompt
"""

TOOL_PROMPT_IMPROVEMENT_PROMPT_GEMINI = """Improve the following prompt template based on the provided suggestions and example spans. This prompt is used by a tool-calling agent.

<context>
<current_prompt_template>
{current_prompt}
</current_prompt_template>

<available_tools readonly="true">
{tool_definitions}
</available_tools>

<improvement_suggestions>
{suggestions}
</improvement_suggestions>

<good_performing_tool_call_examples score=">=0.8">
{good_tool_call_examples}
</good_performing_tool_call_examples>

<good_performing_text_examples score=">=0.8">
{good_text_examples}
</good_performing_text_examples>

<poor_performing_tool_call_examples score="<0.5">
{poor_tool_call_examples}
</poor_performing_tool_call_examples>

<poor_performing_text_examples score="<0.5">
{poor_text_examples}
</poor_performing_text_examples>
</context>

<task>
Based on the context above, improve the prompt template by applying the provided suggestions.

Before making changes:
1. Review the suggestions and identify which parts of the prompt each targets.
2. Study the good examples to understand what works well — preserve those strengths.
3. Classify the poor examples into tool-call issues (selection/arguments) and text issues (synthesis/faithfulness).

Apply improvements following these rules:
- Improve ONLY the system and user prompt text.
- Tool definitions are shown for context only — they must remain unchanged.
- Preserve all template variables exactly (e.g., {{variable_name}}).
- Address issues evident in poor tool-call examples (tool selection and argument problems).
- Address issues evident in poor text examples (answer synthesis and faithfulness problems).
- If tool selection is a problem, add clearer guidance about when to use each tool.
- If answer synthesis is a problem, add instructions about how to present tool results faithfully.
- Keep the prompt generalizable — do not overfit to specific examples.
</task>

<scope_constraints>
- Implement EXACTLY and ONLY the changes supported by the improvement suggestions.
- Do not add extra sections, features, or tool-usage instructions beyond what the suggestions call for.
- If a suggestion is ambiguous, choose the simplest valid interpretation.
- Preserve the original prompt's structure unless a suggestion explicitly calls for restructuring.
</scope_constraints>

<tool_calling_best_practices>
When improving tool-usage instructions, consider adding:
- Guidance to parallelize independent tool calls when possible to reduce latency.
- A requirement to briefly summarize what changed and any validation performed after write/update tool calls.
- Specific 1–2 sentence descriptions for when to use each tool, if the current prompt lacks them.
- Instructions to prefer tools over internal knowledge for fresh or user-specific data.
</tool_calling_best_practices>

<output_format>
Keep the improved prompt concise — prefer compact bullets and short sections over long narrative paragraphs.
If adding constraints, use crisp 1–2 sentence rules.
Return ONLY the improved prompt text with no additional commentary.
</output_format>

<final_instruction>
Before returning the improved prompt, verify that:
- All template variables from the original prompt are preserved.
- Tool definitions and schemas remain unchanged.
- The improvements address the provided suggestions without adding unsolicited changes.
</final_instruction>"""

TOOL_PROMPT_IMPROVEMENT_PROMPTS: dict[str, str] = {
    "anthropic": TOOL_PROMPT_IMPROVEMENT_PROMPT_ANTHROPIC,
    "openai": TOOL_PROMPT_IMPROVEMENT_PROMPT_OPENAI,
    "gemini": TOOL_PROMPT_IMPROVEMENT_PROMPT_GEMINI,
}


def get_prompt_for_provider(prompt_dict: dict, provider: str) -> str:
    """Return the prompt variant for *provider*, defaulting to 'anthropic'."""
    return prompt_dict.get(provider, prompt_dict["anthropic"])
