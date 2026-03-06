"""
Q&A mock agent with a deliberately suboptimal prompt.

The prompt is verbose, unstructured, and provides no formatting guidance.
The model (gpt-5-mini) is overkill for these trivial factual questions.

This lets us form clear expectations:
- Prompt tuning should easily improve the prompt structure.
- Backtesting should recommend a cheaper model for simple Q&A.
"""

from mock_agents.base import BaseMockAgent

SYSTEM_PROMPT = "You are a helpful assistant. You should answer user query in 1 sentence, concisely and clearly."

QUERIES = [
    "What is the capital of France?",
    "What is 2+2?",
    "Who wrote Romeo and Juliet?",
    "What is the boiling point of water in Celsius?",
    "How many continents are there?",
    "What is the largest planet in our solar system?",
    "Who painted the Mona Lisa?",
    "What year did World War II end?",
    "What is the chemical symbol for gold?",
    "How many sides does a hexagon have?",
    "What is the speed of light in km/s approximately?",
    "Who discovered penicillin?",
    "What is the smallest prime number?",
    "What language is spoken in Brazil?",
    "What is the tallest mountain in the world?",
    "How many bones are in the adult human body?",
    "What is the currency of Japan?",
    "Who invented the telephone?",
    "What is the largest ocean on Earth?",
    "What is the square root of 64?",
    "What is the capital of Australia?",
    "Who wrote '1984'?",
    "What is the atomic number of carbon?",
    "How many players are on a soccer team?",
    "What is the largest desert in the world?",
    "What is the freezing point of water in Fahrenheit?",
    "Who was the first person to walk on the moon?",
    "What is the longest river in the world?",
    "How many teeth does an adult human have?",
    "What is the chemical formula for water?",
]

assert len(QUERIES) == 30, f"Expected 30 queries, got {len(QUERIES)}"


# Responses for these models are cached under tests/e2e/cache/<provider>/;
# running without the cache files will make real API calls.
PROVIDER_MODELS = {
    "openai": "gpt-5-mini",
    "anthropic": "claude-haiku-4-5",
    "gemini": "gemini-3.1-flash-lite-preview",
}


class QAAgent(BaseMockAgent):
    SYSTEM_PROMPT = SYSTEM_PROMPT
    QUERIES = QUERIES

    def __init__(self, provider: str, **kwargs):
        self.MODEL = PROVIDER_MODELS[provider]
        super().__init__(provider=provider, **kwargs)
