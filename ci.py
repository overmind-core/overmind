import os
from datetime import datetime

from openrouter import OpenRouter
from opentelemetry.semconv_ai import SpanAttributes
from pydantic import BaseModel

from overmind import attrs


class Dataset(BaseModel):
    name: str
    description: str
    url: str
    image: str
    tags: list[str]
    created_at: datetime
    updated_at: datetime


class DataRow(BaseModel):
    dataset_id: str
    data: dict
    created_at: datetime
    updated_at: datetime


# our schema is openai schema
# for any other provider, we convert it back to openai schema, and then we use openrouter to try out multiple models


def anthropic_to_openai_schema(row: DataRow) -> str:
    return f"""
    {row.data}
    """


def gemini_to_openai_schema(row: DataRow) -> str:
    return f"""
    {row.data}
    """


def transform(data: dict) -> str:
    llm_provider = data[attrs.LLM_PROVIDER]

    if llm_provider == "openai":
        return data
    elif llm_provider == "anthropic":
        return anthropic_to_openai_schema(data)
    elif llm_provider == "gemini":
        return gemini_to_openai_schema(data)
    else:
        raise ValueError(f"Invalid provider: {llm_provider}")


def span_to_openai_schema(span: dict) -> str:
    def get_attr(*attrs):
        for attr in attrs:
            if attr in span:
                return span[attr]

    messages = []
    for message in span[SpanAttributes.LLM_MESSAGES]:
        messages.append(
            {
                "role": message[attrs.LLM_MESSAGE_ROLE],
                "content": message[attrs.LLM_MESSAGE_CONTENT],
            }
        )
    return {
        "stream": get_attr(SpanAttributes.GEN_AI_IS_STREAMING),
        "temperature": get_attr(SpanAttributes.LLM_REQUEST_TEMPERATURE),
        "top_p": get_attr(SpanAttributes.LLM_REQUEST_TOP_P),
        "top_k": get_attr(SpanAttributes.LLM_TOP_K),
        "frequency_penalty": get_attr(SpanAttributes.GEN_AI_FREQUENCY_PENALTY),
        "presence_penalty": get_attr(SpanAttributes.GEN_AI_PRESENCE_PENALTY),
        "max_tokens": get_attr(SpanAttributes.GEN_AI_MAX_TOKENS),
        "stop": get_attr(SpanAttributes.GEN_AI_STOP),
        "n": get_attr(SpanAttributes.GEN_AI_N),
        "model": get_attr(attrs.LLM_MODEL),
        "messages": messages,
    }


class Backtest:
    provider = OpenRouter(api_key=os.getenv("OPENROUTER_API_KEY", ""))

    # now expecting a dataset, we need to run backtest and/or finetuning on the dataset
    def run(self, row: DataRow, new_model: str) -> None:
        data = transform(span_to_openai_schema(row.data))
        response = self.provider.chat.send(**data, model=new_model)
        return response
