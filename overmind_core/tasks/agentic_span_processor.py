"""
Agentic span detection and preprocessing for multi-input/tool-calling LLM interactions.

This module handles:
- Detection of agentic spans (multi-turn with tool calls)
- Extraction of conversation flow, tool calls, and final outputs
- Structured representation for evaluation
"""

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def detect_agentic_span(
    input_data: Any, output_data: Any, metadata: Dict[str, Any]
) -> bool:
    """
    Detect if a span represents an agentic/multi-step interaction with tool calls.

    Args:
        input_data: The input payload (can be dict or list of messages)
        output_data: The output payload
        metadata: Span metadata attributes

    Returns:
        True if span is agentic, False otherwise
    """
    # Check if input is a list of messages (conversational format)
    if isinstance(input_data, list) and len(input_data) > 0:
        # Look for tool-related roles or tool_calls in messages
        for msg in input_data:
            if not isinstance(msg, dict):
                continue

            # Check for tool role (indicates tool result message)
            if msg.get("role") == "tool":
                return True

            # Check for tool_calls field (indicates agent made tool calls)
            if msg.get("tool_calls"):
                return True

            # Check for function_call (legacy OpenAI format)
            if msg.get("function_call"):
                return True

    # Check output for similar patterns
    if isinstance(output_data, list) and len(output_data) > 0:
        for msg in output_data:
            if not isinstance(msg, dict):
                continue
            if msg.get("tool_calls") or msg.get("function_call"):
                return True

    # Check metadata for tool-related attributes
    if metadata:
        # Check for tool.name or similar attributes
        if any(key.startswith("tool.") for key in metadata.keys()):
            return True

        # Check if metadata explicitly marks this as tool usage
        if metadata.get("has_tool_calls") or metadata.get("tool_count"):
            return True

    return False


def _find_tool_result(messages: List[Dict], tool_call_id: str) -> Optional[str]:
    """Find the tool result message matching a tool call ID."""
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") == "tool" and msg.get("tool_call_id") == tool_call_id:
            return msg.get("content", msg.get("name", ""))
    return None


def _extract_tool_calls_from_messages(messages: List[Dict]) -> List[Dict[str, Any]]:
    """
    Extract all tool calls and their results from a message list.

    Returns list of dicts with structure:
    {
        "tool_name": str,
        "tool_arguments": str or dict,
        "tool_result": str,
        "tool_call_id": str,
        "position": int  # position in conversation
    }
    """
    tool_calls = []

    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            continue

        # Handle modern tool_calls format
        if msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                if not isinstance(tc, dict):
                    continue

                tool_call_id = tc.get("id", "")
                tool_name = ""
                tool_args = ""

                # Extract function details
                if tc.get("function"):
                    tool_name = tc["function"].get("name", "")
                    tool_args = tc["function"].get("arguments", "")
                elif tc.get("name"):
                    tool_name = tc.get("name", "")
                    tool_args = tc.get("arguments", "")

                # Find the corresponding result
                tool_result = _find_tool_result(messages[i + 1 :], tool_call_id)

                tool_calls.append(
                    {
                        "tool_name": tool_name,
                        "tool_arguments": tool_args,
                        "tool_result": tool_result or "No result found",
                        "tool_call_id": tool_call_id,
                        "position": i,
                    }
                )

        # Handle legacy function_call format
        elif msg.get("function_call"):
            fc = msg["function_call"]
            tool_calls.append(
                {
                    "tool_name": fc.get("name", ""),
                    "tool_arguments": fc.get("arguments", ""),
                    "tool_result": "Legacy function call format",
                    "tool_call_id": f"legacy_{i}",
                    "position": i,
                }
            )

    return tool_calls


def _extract_final_output(input_data: Any, output_data: Any) -> Dict[str, Any]:
    """
    Extract the final output that should be evaluated.

    For agentic spans, this is typically the last assistant message
    after all tool calls are complete.
    """
    # If output is a list, find the last assistant message
    if isinstance(output_data, list) and len(output_data) > 0:
        # Reverse search for last assistant message
        for msg in reversed(output_data):
            if isinstance(msg, dict) and msg.get("role") == "assistant":
                # Skip if this message only contains tool calls (intermediary)
                if msg.get("tool_calls") and not msg.get("content"):
                    continue
                return msg

        # If no assistant message found, return last message
        return (
            output_data[-1]
            if isinstance(output_data[-1], dict)
            else {"content": str(output_data[-1])}
        )

    # If output is not a list, return as-is
    if isinstance(output_data, dict):
        return output_data

    # Fallback: wrap primitive types
    return {"content": str(output_data) if output_data else ""}


def _extract_original_query(input_data: Any) -> str:
    """Extract the original user query from input."""
    if isinstance(input_data, list) and len(input_data) > 0:
        # Find first user message
        for msg in input_data:
            if isinstance(msg, dict) and msg.get("role") == "user":
                content = msg.get("content", "")
                return str(content) if content else ""

    # If input is a dict with content
    if isinstance(input_data, dict):
        return str(input_data.get("content", input_data.get("query", "")))

    # Fallback: stringify the input
    return str(input_data) if input_data else ""


def _format_conversation_turns(messages: List[Any]) -> List[Dict[str, Any]]:
    """Format conversation turns for cleaner representation."""
    formatted = []

    for msg in messages:
        if not isinstance(msg, dict):
            continue

        role = msg.get("role", "unknown")
        content = msg.get("content", "")

        turn = {
            "role": role,
            "content": str(content) if content else "",
        }

        # Add tool call info if present
        if msg.get("tool_calls"):
            turn["has_tool_calls"] = True
            turn["tool_calls_count"] = len(msg["tool_calls"])

        formatted.append(turn)

    return formatted


def preprocess_span_for_evaluation(
    input_data: Any, output_data: Any, metadata: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Preprocess a span and extract structured information for evaluation.

    This function analyzes the span and returns a structured representation
    that includes:
    - Whether the span is agentic
    - Original user query
    - Tool calls and results
    - Conversation flow
    - Final output to be evaluated

    Args:
        input_data: Span input payload
        output_data: Span output payload
        metadata: Span metadata attributes

    Returns:
        Dict with structured span information:
        {
            "is_agentic": bool,
            "original_query": str,
            "tool_calls": List[Dict],
            "conversation_turns": List[Dict],
            "final_output": Dict,
            "metadata": Dict
        }
    """
    # Detect if this is an agentic span
    is_agentic = detect_agentic_span(input_data, output_data, metadata)

    # Extract components
    original_query = _extract_original_query(input_data)

    tool_calls = []
    conversation_turns = []

    if is_agentic and isinstance(input_data, list):
        tool_calls = _extract_tool_calls_from_messages(input_data)
        conversation_turns = _format_conversation_turns(input_data)

    final_output = _extract_final_output(input_data, output_data)

    result = {
        "is_agentic": is_agentic,
        "original_query": original_query,
        "tool_calls": tool_calls,
        "conversation_turns": conversation_turns,
        "final_output": final_output,
        "metadata": {
            "tool_calls_count": len(tool_calls),
            "conversation_length": len(conversation_turns) if conversation_turns else 0,
            "has_multiple_turns": len(conversation_turns) > 2
            if conversation_turns
            else False,
        },
    }

    logger.debug(
        f"Preprocessed span: is_agentic={is_agentic}, "
        f"tool_calls={len(tool_calls)}, "
        f"conversation_length={result['metadata']['conversation_length']}"
    )

    return result


def format_conversation_flow(conversation_turns: List[Dict[str, Any]]) -> str:
    """
    Format conversation turns into a readable string for the judge prompt.

    Returns formatted conversation like:
    Turn 1 [user]: What is the weather?
    Turn 2 [assistant]: [Made 1 tool call(s)]
    Turn 3 [tool]: Tool result: 72°F
    Turn 4 [assistant]: The weather is 72°F
    """
    if not conversation_turns:
        return "Single-turn interaction (no conversation history)"

    lines = []
    for i, turn in enumerate(conversation_turns, 1):
        role = turn.get("role", "unknown")
        content = turn.get("content", "")

        # Special handling for tool call messages
        if turn.get("has_tool_calls"):
            tool_count = turn.get("tool_calls_count", 0)
            lines.append(f"Turn {i} [{role}]: [Made {tool_count} tool call(s)]")
        elif role == "tool":
            # Truncate long tool results
            if len(content) > 200:
                content = content[:200] + "... (truncated)"
            lines.append(f"Turn {i} [{role}]: {content}")
        else:
            # Regular messages
            if len(content) > 500:
                content = content[:500] + "... (truncated)"
            lines.append(f"Turn {i} [{role}]: {content}")

    return "\n".join(lines)


def format_intermediate_steps(tool_calls: List[Dict[str, Any]]) -> str:
    """
    Format tool calls and results into a readable string for the judge prompt.

    Returns formatted steps like:
    Step 1: Called tool 'search' with arguments: {"query": "weather"}
           Result: 72°F sunny
    Step 2: Called tool 'convert_temp' with arguments: {"temp": 72, "unit": "F"}
           Result: 22°C
    """
    if not tool_calls:
        return "No tool calls made (direct response)"

    lines = []
    for i, tc in enumerate(tool_calls, 1):
        tool_name = tc.get("tool_name", "unknown")
        tool_args = tc.get("tool_arguments", "")
        tool_result = tc.get("tool_result", "")

        # Format arguments (truncate if too long)
        args_str = str(tool_args)
        if len(args_str) > 200:
            args_str = args_str[:200] + "... (truncated)"

        # Format result (truncate if too long)
        result_str = str(tool_result)
        if len(result_str) > 300:
            result_str = result_str[:300] + "... (truncated)"

        lines.append(f"Step {i}: Called tool '{tool_name}' with arguments: {args_str}")
        lines.append(f"       Result: {result_str}")
        lines.append("")  # Empty line for readability

    return "\n".join(lines)


def format_final_output(final_output: Dict[str, Any]) -> str:
    """
    Format the final output for the judge prompt.

    Extracts the content from the final message.
    """
    if isinstance(final_output, dict):
        content = final_output.get("content", "")
        if not content:
            # Fallback to stringifying the whole dict
            return str(final_output)
        return str(content)

    return str(final_output)


# ---------------------------------------------------------------------------
# New-style tool-calling span extraction
# ---------------------------------------------------------------------------


def _safe_parse_json(data: Any) -> Any:
    """Parse data as JSON if it is a string, otherwise return as-is."""
    if isinstance(data, str):
        try:
            return json.loads(data)
        except (json.JSONDecodeError, ValueError):
            return data
    return data


def _get_tools_from_metadata_attributes(metadata_attributes: Dict) -> List[Dict]:
    """
    Reconstruct the OpenAI-format tools array from the flat
    ``llm.request.functions.N.*`` keys stored in metadata_attributes by the
    LiteLLM / OpenTelemetry instrumentation.

    Example keys:
        llm.request.functions.0.name        -> "get_current_weather"
        llm.request.functions.0.description -> "Get the current weather …"
        llm.request.functions.0.parameters  -> '{"type":"object", …}'
    """
    if not metadata_attributes or not isinstance(metadata_attributes, dict):
        return []

    tools: List[Dict] = []
    i = 0
    while True:
        prefix = f"llm.request.functions.{i}"
        name = metadata_attributes.get(f"{prefix}.name")
        if not name:
            break
        function_def: Dict[str, Any] = {"name": name}
        description = metadata_attributes.get(f"{prefix}.description")
        if description:
            function_def["description"] = description
        parameters_raw = metadata_attributes.get(f"{prefix}.parameters")
        if parameters_raw:
            try:
                function_def["parameters"] = (
                    json.loads(parameters_raw)
                    if isinstance(parameters_raw, str)
                    else parameters_raw
                )
            except (json.JSONDecodeError, ValueError):
                function_def["parameters"] = parameters_raw
        tools.append({"type": "function", "function": function_def})
        i += 1

    return tools


def _format_message_history(messages: List[Dict]) -> str:
    """
    Format a list of messages into a human-readable conversation history for the judge.

    Renders each message with its role, content, tool calls made, and tool results
    received so the judge has full context of the interaction.
    """
    if not messages:
        return "No conversation history"

    lines = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue

        role = msg.get("role", "unknown")
        content = msg.get("content", "")

        if role == "assistant" and msg.get("tool_calls"):
            # Show each tool call being invoked
            call_parts = []
            for tc in msg["tool_calls"]:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function", {})
                name = fn.get("name") or tc.get("name", "unknown")
                args = fn.get("arguments") or tc.get("arguments", "")
                args_str = str(args)
                if len(args_str) > 200:
                    args_str = args_str[:200] + "... (truncated)"
                call_parts.append(f"{name}({args_str})")
            call_summary = ", ".join(call_parts) if call_parts else "unknown"
            lines.append(f"[assistant]: [Tool calls: {call_summary}]")
            # Also show any accompanying text content
            if content:
                content_str = str(content)
                if len(content_str) > 500:
                    content_str = content_str[:500] + "... (truncated)"
                lines.append(f"  content: {content_str}")

        elif role in ("tool", "function"):
            tool_call_id = msg.get("tool_call_id") or msg.get("name") or ""
            content_str = str(content)
            if len(content_str) > 500:
                content_str = content_str[:500] + "... (truncated)"
            label = f"[tool result{' (' + tool_call_id + ')' if tool_call_id else ''}]"
            lines.append(f"{label}: {content_str}")

        else:
            content_str = str(content)
            if len(content_str) > 1000:
                content_str = content_str[:1000] + "... (truncated)"
            if content_str:
                lines.append(f"[{role}]: {content_str}")

    return "\n".join(lines) if lines else "No conversation history"


def extract_tool_call_span_for_evaluation(
    input_data: Any,
    output_data: Any,
    metadata_attributes: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Extract structured components from a tool-call span for evaluation.

    Used when metadata_attributes.response_type == "tool_calls".

    Tool definitions are resolved in priority order:
      1. metadata_attributes["available_tools"] — pre-reconstructed at ingestion time
      2. metadata_attributes flat keys          — llm.request.functions.N.*

    Returns:
        {
            "user_query": str,
            "available_tools": list[dict],
            "tool_calls": list[dict],
            "conversation_history": str,  # full message history leading up to the call
        }
    """
    input_data = _safe_parse_json(input_data)
    output_data = _safe_parse_json(output_data)

    user_query = _extract_original_query(input_data)

    meta = metadata_attributes or {}
    available_tools = meta.get("available_tools") or []

    conversation_history = _format_message_history(
        input_data if isinstance(input_data, list) else []
    )

    tool_calls: List[Dict] = []
    if isinstance(output_data, dict):
        if output_data.get("tool_calls"):
            tool_calls = output_data["tool_calls"]
        elif output_data.get("function_call"):
            # Legacy OpenAI single-function format — normalise to a list
            tool_calls = [output_data["function_call"]]
    elif isinstance(output_data, list):
        for msg in output_data:
            if not isinstance(msg, dict):
                continue
            if msg.get("tool_calls"):
                tool_calls = msg["tool_calls"]
                break
            if msg.get("function_call"):
                tool_calls = [msg["function_call"]]
                break

    return {
        "user_query": user_query,
        "available_tools": available_tools,
        "tool_calls": tool_calls,
        "conversation_history": conversation_history,
    }


def extract_tool_answer_span_for_evaluation(
    input_data: Any,
    output_data: Any,
) -> Dict[str, Any]:
    """
    Extract structured components from a tool-answer span for evaluation.

    Used when metadata_attributes.response_type == "text" and is_agentic == True.
    The input contains the full message history including tool result messages.

    Returns:
        {
            "user_query": str,
            "tool_results": list[{"tool_call_id": str, "content": str}],
            "final_answer": str,
            "conversation_flow": str,  # full message history including tool calls and results
        }
    """
    input_data = _safe_parse_json(input_data)
    output_data = _safe_parse_json(output_data)

    user_query = _extract_original_query(input_data)
    conversation_flow = _format_message_history(
        input_data if isinstance(input_data, list) else []
    )

    tool_results: List[Dict[str, str]] = []
    if isinstance(input_data, list):
        for msg in input_data:
            if not isinstance(msg, dict):
                continue
            # "tool" is the current OpenAI role; "function" is the legacy role
            if msg.get("role") in ("tool", "function"):
                tool_results.append(
                    {
                        "tool_call_id": str(
                            msg.get("tool_call_id") or msg.get("name") or ""
                        ),
                        "content": str(msg.get("content", "")),
                    }
                )

    final_answer = ""
    if isinstance(output_data, dict):
        final_answer = str(output_data.get("content") or "")
    elif isinstance(output_data, list):
        for msg in reversed(output_data):
            if isinstance(msg, dict) and msg.get("role") == "assistant":
                content = msg.get("content")
                if content:
                    final_answer = str(content)
                    break
    elif isinstance(output_data, str):
        final_answer = output_data

    return {
        "user_query": user_query,
        "tool_results": tool_results,
        "final_answer": final_answer,
        "conversation_flow": conversation_flow,
    }
