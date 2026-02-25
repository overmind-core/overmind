import json
from fastapi import APIRouter, Depends, Request
from opentelemetry import trace
from sqlalchemy.ext.asyncio import AsyncSession
from overmind.api.v1.helpers.authentication import (
    AuthenticatedUserOrToken,
    get_current_user,
)
from overmind.config import setup_opentelemetry
from overmind.db.session import get_db
from overmind.core.invocation_helpers import (
    CLIENT_INPUT_TEXT_EXTRACTORS,
    CLIENT_INPUT_TEXT_REPLACERS,
    invoke_client,
)
from overmind.core.layers import run_overmind_layer
from overmind.core.mcp_validation import validate_client_call_params
from overmind.api.v1.helpers.permissions import ProjectPermission

router = APIRouter()


@router.post("/run/{client_path:path}")
async def run_proxy(
    client_path: str,
    payload: dict,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: AuthenticatedUserOrToken = Depends(get_current_user),
):
    """
    Invoke an agent with the given payload
    """

    authorization_provider = request.app.state.authorization_provider
    organisation_id = current_user.get_organisation_id()
    project_id = current_user.token.project_id if current_user.token else None
    authorisation_context = await authorization_provider.check_permissions(
        user=current_user,
        db=db,
        required_permissions=[ProjectPermission.ADD_CONTENT.value],
        organisation_id=organisation_id,
        project_id=project_id,
    )

    path_parts = client_path.split(".")

    if not path_parts:
        client_name = "openai"
        method_path = ["responses", "create"]
    else:
        client_name = path_parts[0]
        method_path = path_parts[1:]

    client_call_params = payload.get("client_call_params", "{}")
    client_init_params = payload.get("client_init_params", {})

    client_call_params = json.loads(client_call_params)

    request_input_policies = payload.get("input_policies", [])
    request_output_policies = payload.get("output_policies", [])

    org_policy_provider = request.app.state.org_policy_provider

    # Get user permissions from auth context (enterprise) or empty set (core)
    user_permissions: set = (
        getattr(authorisation_context, "USER_PERMISSIONS", None) or set()
    )

    if ProjectPermission.MANAGE_TOKENS.value not in user_permissions:
        policies_from_org = await org_policy_provider.get_org_llm_policies(
            db=db, current_user=current_user
        )
        if policies_from_org and (
            policies_from_org.get("input") or policies_from_org.get("output")
        ):
            request_input_policies = policies_from_org.get(
                "input", request_input_policies
            )
            request_output_policies = policies_from_org.get(
                "output", request_output_policies
            )

    input_data = CLIENT_INPUT_TEXT_EXTRACTORS[client_name].get(
        ".".join(method_path),
        CLIENT_INPUT_TEXT_EXTRACTORS[client_name]["default"],
    )(client_call_params)

    # todo: have an internal endpoint in proxy so that we just provide user info here
    setup_opentelemetry()

    tracer = trace.get_tracer("overmind.proxy")

    nodes = ["__start__"]
    edges = []

    with tracer.start_as_current_span("proxy_run") as parent_span:
        parent_span.set_attribute("inputs", json.dumps(input_data))
        parent_span.set_attribute("client_name", client_name)
        parent_span.set_attribute("method_path", method_path)
        parent_span.set_attribute("client_call_params", json.dumps(client_call_params))
        parent_span.set_attribute("client_init_params", json.dumps(client_init_params))
        parent_span.set_attribute("request_input_policies", request_input_policies)
        parent_span.set_attribute("request_output_policies", request_output_policies)
        parent_span.set_attribute("project_id", str(project_id) if project_id else "")
        if organisation_id:
            parent_span.set_attribute("business_id", str(organisation_id))
        parent_span.set_attribute("user_id", str(current_user.user_id))

        mcp_policy = await org_policy_provider.get_org_mcp_policy(
            organisation_id=organisation_id, db=db
        )

        if mcp_policy is not None:
            mcp_url_policy = mcp_policy
        else:
            # No policy version or no MCP policy - use default whitelist
            mcp_url_policy = {"type": "whitelist", "servers": {"*": ["*"]}}

        client_call_params, approved_mcp_label_tools_map = validate_client_call_params(
            client_name=client_name,
            client_call_params=client_call_params,
            mcp_url_policy=mcp_url_policy,
        )

        if request_input_policies:
            input_layer_results, input_layer_metadata = run_overmind_layer(
                input_data=json.dumps(input_data),
                policies=request_input_policies,
                current_user=current_user,
                db=db,
                tracer=tracer,
            )

            # todo: add tests for this
            edges.append(
                [nodes[-1], input_layer_metadata["graph"]["nodes"][1]]
            )  # edge between proxy.__start__ and first policy node (skipping layer __start__)
            edges.extend(
                input_layer_metadata["graph"]["edges"][1:-1]
            )  # now add edges from layer, skipping edges between layer __start__ and __end__
            nodes.extend(
                input_layer_metadata["graph"]["nodes"][1:-1]
            )  # now add nodes from layer, skipping nodes for layer __start__ and __end__

            if input_layer_results["overall_policy_outcome"] == "rejected":
                return {
                    "llm_client_response": None,
                    "input_layer_results": input_layer_results,
                }
            elif input_layer_results.get("overall_policy_outcome") == "altered":
                processed_input_data = json.loads(input_layer_results["processed_data"])
                processed_client_call_params = CLIENT_INPUT_TEXT_REPLACERS[client_name][
                    ".".join(method_path)
                ](client_call_params, processed_input_data)
            else:
                # Handle case when policies exist but outcome is "passed" or other values
                processed_client_call_params = client_call_params
                processed_input_data = input_data
        else:
            input_layer_results = {}
            processed_client_call_params = client_call_params
            processed_input_data = input_data

        with tracer.start_as_current_span("call_llm_client") as call_llm_client_span:
            call_llm_client_span.set_attribute(
                "project_id", str(project_id) if project_id else ""
            )
            if organisation_id:
                call_llm_client_span.set_attribute("business_id", str(organisation_id))
            call_llm_client_span.set_attribute("user_id", str(current_user.user_id))
            # todo: some of the fields are duplicated from the parent span attributes, not sure if its worth it
            call_llm_client_span.set_attribute("client_name", client_name)
            call_llm_client_span.set_attribute("method_path", method_path)
            call_llm_client_span.set_attribute(
                "processed_client_call_params", json.dumps(processed_client_call_params)
            )
            call_llm_client_span.set_attribute(
                "client_init_params", json.dumps(client_init_params)
            )
            call_llm_client_span.set_attribute(
                "inputs", json.dumps(processed_input_data)
            )

            (
                client_response,
                output_data,
                llm_client_call_nodes,
                llm_client_call_edges,
            ) = await invoke_client(
                client_name=client_name,
                method_path=method_path,
                client_call_params=processed_client_call_params,
                client_init_params=client_init_params,
                client_manager=request.app.state.client_manager,
                approved_mcp_label_tools_map=approved_mcp_label_tools_map,
                tracer=tracer,
                current_user=current_user,
            )

            # todo: add tests for this
            edges.append([nodes[-1], llm_client_call_nodes[1]])
            edges.extend(llm_client_call_edges[1:-1])
            nodes.extend(llm_client_call_nodes[1:-1])

            call_llm_client_span.set_attribute("outputs", output_data)
            call_llm_client_span.set_attribute(
                "client_response", json.dumps(client_response.model_dump())
            )

        if request_output_policies:
            output_layer_results, output_layer_metadata = run_overmind_layer(
                input_data=output_data,
                policies=request_output_policies,
                current_user=current_user,
                db=db,
                tracer=tracer,
                question=processed_input_data,
            )

            edges.append([nodes[-1], output_layer_metadata["graph"]["nodes"][1]])
            edges.extend(output_layer_metadata["graph"]["edges"][1:-1])
            nodes.extend(output_layer_metadata["graph"]["nodes"][1:-1])
        else:
            output_layer_results = {}

        if output_layer_results.get("overall_policy_outcome") == "altered":
            final_text_result = output_layer_results["processed_data"]
        elif output_layer_results.get("overall_policy_outcome") == "rejected":
            final_text_result = ""
        else:
            final_text_result = output_data

        parent_span.set_attribute("outputs", final_text_result)
        all_overall_policy_outcomes = {
            input_layer_results.get("overall_policy_outcome"),
            output_layer_results.get("overall_policy_outcome"),
        }
        if "rejected" in all_overall_policy_outcomes:
            overall_policy_outcome = "rejected"
        elif "altered" in all_overall_policy_outcomes:
            overall_policy_outcome = "altered"
        elif "passed" in all_overall_policy_outcomes:
            overall_policy_outcome = "passed"
        else:
            overall_policy_outcome = None

        if overall_policy_outcome:
            parent_span.set_attribute("policy_outcome", overall_policy_outcome)

        span_context = {
            "trace_id": format(parent_span.get_span_context().trace_id, "032x"),
            "span_id": format(parent_span.get_span_context().span_id, "016x"),
        }

        nodes.append("__end__")
        edges.append([nodes[-2], nodes[-1]])

        metadata = {
            "graph": {
                "nodes": nodes,
                "edges": edges,
            }
        }

        parent_span.set_attribute("metadata", json.dumps(metadata))

    return {
        "llm_client_response": client_response.model_dump(),
        "input_layer_results": input_layer_results,
        "output_layer_results": output_layer_results,
        "processed_output": final_text_result,
        "processed_input": processed_input_data,
        "span_context": span_context,
    }
