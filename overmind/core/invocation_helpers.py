import asyncio
import json
from fastapi import HTTPException
from openai import OpenAI
from opentelemetry import trace
from sqlalchemy.ext.asyncio import AsyncSession
from cachetools import LRUCache
from overmind.core.policies import (
    BUILT_IN_POLICIES,
    BUILT_IN_POLICY_IDS,
    POLICY_TEMPLATES,
    PolicyTemplate,
)
from toolz import assoc_in

CLIENT_OUTPUT_TEXT_EXTRACTORS = {
    "openai": {
        "default": lambda x: x.choices[0].message.content,
        "embeddings.create": lambda x: x.data[0].embedding,
        "responses.create": lambda x: str(x.output_text),
    },
}

CLIENTS = {
    "openai": OpenAI,
}

CLIENT_INPUT_TEXT_EXTRACTORS = {
    "openai": {
        "default": lambda x: x["messages"],
        "responses.create": lambda x: x["input"],
        "embeddings.create": lambda x: x["input"],
    },
}

CLIENT_INPUT_TEXT_REPLACERS = {
    "openai": {
        "responses.create": lambda original_client_params, new_input: assoc_in(
            original_client_params, ["input"], new_input
        ),
        "embeddings.create": lambda original_client_params, new_input: assoc_in(
            original_client_params, ["input"], new_input
        ),
        "default": lambda original_client_params, new_input: assoc_in(
            original_client_params, ["messages"], new_input
        ),
    }
}


class AsyncClientLRUCache(LRUCache):
    """
    An LRUCache that awaits the aclose() method of an evicted client.
    """

    def popitem(self):
        # This method is called by the cache itself when it needs to evict an item.
        key, val = super().popitem()
        # This works for OpenAI client but for others we may need a func to find suitable close method
        asyncio.create_task(val.close())
        return key, val


class ClientCacheManager:
    def __init__(self, maxsize: int):
        # Use our new custom cache class!
        self._cache = AsyncClientLRUCache(maxsize=maxsize)
        self._lock = asyncio.Lock()

    async def get_client(self, client_name: str, client_init_params: dict):
        cache_key = client_name + json.dumps(client_init_params, sort_keys=True)

        # Fast path check (no lock needed for reads)
        if cache_key in self._cache:
            return self._cache[cache_key]

        async with self._lock:
            # Double-check after acquiring the lock
            if cache_key in self._cache:
                return self._cache[cache_key]

            new_client = CLIENTS.get(client_name)(**client_init_params)
            self._cache[cache_key] = new_client
            return new_client

    async def close_all(self):
        async with self._lock:
            # Use asyncio.gather to close all clients concurrently
            close_tasks = [client.aclose() for client in self._cache.values()]
            await asyncio.gather(*close_tasks)
            self._cache.clear()


async def invoke_client(
    client_name: str,
    method_path: list[str],
    client_call_params: dict,
    client_init_params: dict,
    client_manager: ClientCacheManager,
    approved_mcp_label_tools_map: dict,
    tracer: trace.Tracer,
    current_user=None,
):
    target_client = await client_manager.get_client(client_name, client_init_params)

    nodes = ["__start__"]
    edges = []

    # Traverse the client object to get to the final method
    try:
        current_obj = target_client
        for part in method_path:
            current_obj = getattr(current_obj, part)

        # The final object should be a callable method
        if not callable(current_obj):
            raise AttributeError("Final path segment is not a callable method.")

        result = current_obj(**client_call_params)

        nodes.append("call_llm_client")
        edges.append([nodes[-2], nodes[-1]])

        if client_name == "openai" and approved_mcp_label_tools_map:
            while True:
                last_output = result.output[-1]
                if last_output.type == "mcp_approval_request":
                    nodes.append("mcp_approval_request")
                    edges.append([nodes[-2], nodes[-1]])

                    with tracer.start_as_current_span(
                        "mcp_approval_request"
                    ) as approval_span:
                        approval_span.set_attribute("project_id", current_user.username)
                        approval_span.set_attribute(
                            "business_id", current_user.business_id
                        )
                        approval_span.set_attribute("user_id", current_user.username)
                        approval_span.set_attribute(
                            "approval_mcp_server_label", last_output.server_label
                        )
                        approval_span.set_attribute(
                            "approval_tool_name", last_output.name
                        )
                        approval_span.set_attribute(
                            "approval_request_full_client_response",
                            json.dumps(result.model_dump()),
                        )
                        approved_tools_for_this_server_label = (
                            approved_mcp_label_tools_map.get(last_output.server_label)
                        )
                        if (
                            last_output.name in approved_tools_for_this_server_label
                            or approved_tools_for_this_server_label == ["*"]
                        ):
                            # this relies on previous response id however if that isn't stored e.g. due to data retention then we can just append the approval to initial input + output
                            client_call_params["previous_response_id"] = result.id
                            approval_response = {
                                "type": "mcp_approval_response",
                                "approve": True,
                                "approval_request_id": last_output.id,
                            }
                            client_call_params["input"] = [approval_response]

                            approval_span.set_attribute(
                                "approval_response", json.dumps(approval_response)
                            )
                            approval_span.set_attribute("policy_outcome", "approved")
                            approval_span.set_attribute(
                                "inputs",
                                f"Requesting tool {last_output.name} from server {last_output.server_label}",
                            )
                            approval_span.set_attribute(
                                "outputs",
                                f"Approved tool {last_output.name} from server {last_output.server_label}",
                            )
                            result = current_obj(**client_call_params)
                        else:
                            approval_response = {
                                "type": "mcp_approval_response",
                                "approve": False,
                                "approval_request_id": last_output.id,
                            }
                            approval_span.set_attribute(
                                "approval_response", json.dumps(approval_response)
                            )
                            approval_span.set_attribute("policy_outcome", "rejected")
                            approval_span.set_attribute(
                                "inputs",
                                f"Requesting tool {last_output.name} from server {last_output.server_label}",
                            )
                            approval_span.set_attribute(
                                "outputs",
                                f"Rejected tool {last_output.name} from server {last_output.server_label}",
                            )
                            raise HTTPException(
                                status_code=404,
                                detail=f"Attempted to call unauthorized MCP tool: {last_output.server_label}",
                            )
                else:
                    break

        output_text = CLIENT_OUTPUT_TEXT_EXTRACTORS[client_name].get(
            ".".join(method_path),
            CLIENT_OUTPUT_TEXT_EXTRACTORS[client_name]["default"],
        )(result)

        nodes.append("__end__")
        edges.append([nodes[-2], nodes[-1]])

        return result, output_text, nodes, edges

    except AttributeError:
        raise HTTPException(
            status_code=404,
            detail=f"Method path '{'.'.join(method_path)}' not found on client '{client_name}'.",
        )


def instantiate_policy(policy_ref: str | dict, db: AsyncSession) -> PolicyTemplate:
    if isinstance(policy_ref, str):
        # Retrieve the policy from database by its ID - filter by business_id
        if policy_ref in BUILT_IN_POLICY_IDS:
            policy_data = [
                policy
                for policy in BUILT_IN_POLICIES
                if policy["policy_id"] == policy_ref
            ][0]
        # todo: we don't really need to have permanent custom policies just yet
        # else:
        #     policy_data = (
        #         db.query(Policy)
        #         .filter(
        #             Policy.policy_id == policy_ref,
        #             Policy.business_id == current_user.business_id,
        #         )
        #         .first()
        #         .__dict__
        #     )

    elif isinstance(policy_ref, dict):
        # otherwise if user uses a template we will need to get the template, engine and params
        policy_data = policy_ref

    policy_instance = POLICY_TEMPLATES[policy_data["policy_template"]](
        **policy_data["parameters"]
    )
    return policy_instance
