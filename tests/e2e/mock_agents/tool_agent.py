"""
Tool-using mock agent for testing agentic span detection.

Uses a structured system prompt that is DELIBERATELY different from the
QA agent's natural-language prompt — different vocabulary, different structure,
different length.  This ensures the template extractor identifies them as
two separate prompt templates.

Tests:
- Agentic span detection (is_agentic flag)
- Tool call response_type classification
- Tool call evaluation path in the LLM judge
- Template extractor correctly separating tool vs QA agents
"""

from mock_agents.base import BaseMockAgent

SYSTEM_PROMPT = (
    "<<FUNCTION_EXECUTOR v2.1>>\n\n"
    "REGISTERED FUNCTIONS:\n"
    "  - calculate(expression: str) → float\n"
    "  - lookup_fact(topic: str) → str\n\n"
    "EXECUTION PROTOCOL:\n"
    "  1. Parse incoming request and identify intent\n"
    "  2. Dispatch request to matching function handler\n"
    "  3. Return raw function output without modification\n\n"
    "CONSTRAINT: Freeform text generation is strictly prohibited. "
    "Every response must originate from a function invocation."
)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "calculate",
            "description": "Evaluate a mathematical expression and return the result.",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "The math expression to evaluate, e.g. '15 * 0.15'",
                    }
                },
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_fact",
            "description": "Look up a factual piece of information.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "The topic to look up, e.g. 'population of Tokyo'",
                    }
                },
                "required": ["topic"],
            },
        },
    },
]

QUERIES = [
    "calculate: 15% of 230",
    "calculate: square root of 144",
    "calculate: 1024 divided by 16",
    "calculate: 7 factorial",
    "lookup_fact: population of Tokyo",
    "calculate: 2 raised to power 10",
    "calculate: area of circle, radius=5",
    "lookup_fact: height of Mount Everest in meters",
    "calculate: 99 multiplied by 101",
    "calculate: 15% tip on $85.50 bill",
    "lookup_fact: speed of light in vacuum",
    "calculate: 1000 minus 372",
    "calculate: hypotenuse, sides 3 and 4",
    "lookup_fact: boiling point of ethanol",
    "calculate: 25 cubed",
    "calculate: 365 times 24",
    "lookup_fact: distance from Earth to Mars",
    "calculate: 20% discount on $149.99",
    "calculate: sum of first 50 natural numbers",
    "calculate: convert 72 Fahrenheit to Celsius",
    "lookup_fact: atomic number of gold",
    "calculate: compound interest on $1000 at 5% for 3 years",
    "calculate: perimeter of rectangle, length=12, width=7",
    "lookup_fact: year the Berlin Wall fell",
    "calculate: 256 divided by 0.25",
    "calculate: volume of sphere, radius=3",
    "lookup_fact: chemical formula of table salt",
    "calculate: 13 squared plus 14 squared",
    "calculate: 10% sales tax on $67.30",
    "lookup_fact: wavelength of red light in nanometers",
]

assert len(QUERIES) == 30, f"Expected 30 queries, got {len(QUERIES)}"

# Responses for these models are cached under tests/e2e/cache/<provider>/;
# running without the cache files will make real API calls.
PROVIDER_MODELS = {
    "openai": "gpt-5-mini",
    "anthropic": "claude-haiku-4-5",
    "gemini": "gemini-3.1-flash-lite-preview",
}


class ToolAgent(BaseMockAgent):
    SYSTEM_PROMPT = SYSTEM_PROMPT
    QUERIES = QUERIES
    TOOLS = TOOLS

    def __init__(self, provider: str, **kwargs):
        self.MODEL = PROVIDER_MODELS[provider]
        super().__init__(provider=provider, **kwargs)
