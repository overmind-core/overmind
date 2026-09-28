def uses_generative_judge(evaluator) -> bool:
    return evaluator.kind in {"llm_judge", "agentic"} or (
        evaluator.kind == "trajectory" and (evaluator.config or {}).get("mode", "match") == "judge"
    )
