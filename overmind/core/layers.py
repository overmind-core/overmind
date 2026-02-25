import json

from fastapi import HTTPException

from overmind.core.invocation_helpers import instantiate_policy


def run_overmind_layer(input_data: str, policies, current_user, db, tracer, **kwargs):
    """
    input_data is str since we will rarely use openAI models, we would run all sorts of other models or DLP algos
    to do things - so input from user will just be a string. Any structure will be added by policies as appropriate
    """
    if not policies:
        raise ValueError("At least one policy must be provided to run_overmind_layer")

    nodes = ["__start__"]
    edges = []

    with tracer.start_as_current_span("execute_layer") as parent_span:
        parent_span.set_attribute("project_id", str(current_user.token.project_id))
        parent_span.set_attribute(
            "business_id", str(current_user.token.organisation_id)
        )
        parent_span.set_attribute("user_id", str(current_user.token.user_id))
        all_policy_results = {}

        # Execute each policy in sequence
        model_name = kwargs.get("model_name")
        for policy_ref in policies:
            policy_instance = instantiate_policy(
                policy_ref,
                db,
            )
            nodes.append(policy_instance.id)
            edges.append([nodes[-2], nodes[-1]])

            with tracer.start_as_current_span(
                f"execute_policy_{policy_instance.id}"
            ) as span:
                span.set_attribute("project_id", str(current_user.token.project_id))
                span.set_attribute(
                    "business_id", str(current_user.token.organisation_id)
                )
                span.set_attribute("user_id", str(current_user.token.user_id))
                # todo: somewhat hacky, should be a better way to do this
                if (
                    policy_instance.id == "reject_irrelevant_answer"
                    or policy_instance.id
                    == "reject_llm_judge_with_criteria_and_question"
                ):
                    if not kwargs.get("question", None):
                        raise HTTPException(
                            status_code=400,
                            detail="question is required in body.kwargs",
                        )
                    formatted_input_data = policy_instance.format_target_text(
                        answer=input_data, question=kwargs["question"]
                    )
                    result, processed_data = policy_instance.run(
                        input_text=formatted_input_data,
                        model_name=model_name,
                    )
                else:
                    result, processed_data = policy_instance.run(
                        input_text=input_data,
                        model_name=model_name,
                    )

                span.set_attribute("inputs", input_data)
                if processed_data:
                    span.set_attribute("outputs", processed_data)
                span.set_attribute("policy_outcome", result["result"])
                span.set_attribute("policy_results", json.dumps(result))

            all_policy_results[policy_instance.id] = result

        overall_policy_outcome = {r["result"] for r in all_policy_results.values()}
        if "rejected" in overall_policy_outcome:
            overall_policy_outcome = "rejected"
        elif "altered" in overall_policy_outcome:
            overall_policy_outcome = "altered"
        else:
            overall_policy_outcome = "passed"

        parent_span.set_attribute("inputs", input_data)
        if processed_data:
            parent_span.set_attribute("outputs", processed_data)
        parent_span.set_attribute("policy_outcome", overall_policy_outcome)
        parent_span.set_attribute("policy_results", json.dumps(all_policy_results))

        nodes.append("__end__")
        edges.append([nodes[-2], nodes[-1]])

        metadata = {
            "graph": {
                "nodes": nodes,
                "edges": edges,
            }
        }
        parent_span.set_attribute("metadata", json.dumps(metadata))

    span_context = {
        "trace_id": format(parent_span.get_span_context().trace_id, "032x"),
        "span_id": format(parent_span.get_span_context().span_id, "016x"),
    }

    return {
        "policy_results": all_policy_results,
        "overall_policy_outcome": overall_policy_outcome,
        "processed_data": processed_data,
        "span_context": span_context,
    }, metadata
