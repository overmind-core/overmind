import math

from modal_shared.decisions import decision_reference


def finite_numbers(values, count):
    if (
        not isinstance(values, list)
        or len(values) != count
        or any(type(x) not in {int, float} or not math.isfinite(x) for x in values)
    ):
        raise ValueError("Decision vectors require one finite number per option")
    return values


def probability_distribution(values, count):
    finite_numbers(values, count)
    if any(not 0 <= x <= 1 for x in values) or not math.isclose(
        math.fsum(values), 1, abs_tol=1e-6, rel_tol=0
    ):
        raise ValueError("Decision probabilities must be normalized")
    return values


def score_decision(request, reference, prediction):
    decision_reference(request, reference)
    count = len(request["options"])
    p = probability_distribution(prediction["probabilities"], count)
    logp = finite_numbers(prediction["log_probabilities"], count)
    if any(
        lp > 1e-6 or not math.isclose(math.exp(lp), probability, abs_tol=1e-6, rel_tol=1e-5)
        for probability, lp in zip(p, logp, strict=True)
    ):
        raise ValueError("Decision probabilities and log-probabilities disagree")
    if not isinstance(reference, dict):
        raise ValueError("A decision reference must be an object")
    q = None
    if set(reference) == {"probabilities"}:
        q = probability_distribution(reference["probabilities"], count)
    elif set(reference) != {"mean", "values"} or request["kind"] != "score":
        raise ValueError("Use a probability distribution or an explicit ordinal mean reference")

    predicted = max(range(count), key=p.__getitem__)
    result = {"confidence": p[predicted], "predicted_option": predicted}
    if q is not None:
        result.update(
            cross_entropy=-math.fsum(a * b for a, b in zip(q, logp, strict=True)),
            brier=math.fsum((a - b) ** 2 for a, b in zip(p, q, strict=True)),
            top1_reference_mass=q[predicted],
            mode_agreement=int(q[predicted] == max(q)),
        )
        if max(q) == 1:
            result["accuracy"] = int(q[predicted] == 1)

    if request["kind"] == "score":
        if q is not None:
            values = [i / (count - 1) for i in range(count)]
            target_mean = math.fsum(v * a for v, a in zip(values, q, strict=True))
            cumulative = 0.0
            squared = []
            for a, b in zip(p[:-1], q[:-1], strict=True):
                cumulative += a - b
                squared.append(cumulative**2)
            result["rps"] = math.fsum(squared) / (count - 1)
        else:
            values = finite_numbers(reference["values"], count)
            target_mean = reference["mean"]
            if (
                any(a >= b for a, b in zip(values[:-1], values[1:], strict=True))
                or type(target_mean) not in {int, float}
                or not math.isfinite(target_mean)
                or not values[0] <= target_mean <= values[-1]
            ):
                raise ValueError("An ordinal mean requires increasing values and an in-range mean")
        estimate = math.fsum(v * a for v, a in zip(values, p, strict=True))
        result.update(
            expected_score=estimate,
            expected_score_mae=abs(estimate - target_mean),
            expected_score_squared_error=(estimate - target_mean) ** 2,
        )
    return result
