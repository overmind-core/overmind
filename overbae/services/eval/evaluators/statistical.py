"""Statistical (dataset-level) evaluator family.

Computed once per ``(variant, evaluator)`` over ALL predictions in the run: the
task layer collects the per-sample predictions ``judge.emit_prediction``
produced and calls :func:`aggregate` for one dataset-scope draft.

``config["metric"]`` names a key of :data:`METRICS`;
``config["output_normalize"]`` a mode of :func:`_apply_output_normalize`, which
canonicalises a verbose answer like "The answer is: positive" down to its label.
"""

from __future__ import annotations

import logging
import random
import re
from collections import Counter, defaultdict
from typing import Any

from overbae.services.eval.evaluators.base import OUTCOME_ABSTAINED, ScoreDraft

logger = logging.getLogger(__name__)


def _apply_output_normalize(s: str, mode: str) -> str:
    """Applied to model predictions only, never to reference labels, so
    reference formatting expectations never have to change."""
    if not mode or mode == "strip":
        return s.strip()
    if mode == "first_word":
        parts = s.strip().split()
        return parts[0] if parts else s
    if mode == "last_word":
        parts = s.strip().split()
        return parts[-1] if parts else s
    if mode == "first_line":
        for line in s.splitlines():
            stripped = line.strip()
            if stripped:
                return stripped
        return s.strip()
    if mode == "last_line":
        for line in reversed(s.splitlines()):
            stripped = line.strip()
            if stripped:
                return stripped
        return s.strip()
    if mode.startswith("regex:"):
        pattern = mode[len("regex:") :]
        try:
            m = re.search(pattern, s, re.IGNORECASE)
            return m.group(0) if m else s.strip()
        except re.error as exc:
            logger.warning("output_normalize regex error (%s): %s", pattern, exc)
            return s.strip()
    logger.warning("Unknown output_normalize mode %r, falling back to strip", mode)
    return s.strip()


def aggregate(
    predictions: list[str],
    references: list[str],
    evaluator,
) -> ScoreDraft:
    config = evaluator.config or {}
    metric = config.get("metric", "accuracy")
    if metric in _REFERENCE_FREE_METRICS:
        preds = [p for p in predictions if p is not None]
        refs: list[str] = []
        empty_reason = "No predictions available."
    else:
        pairs = [(p, r) for p, r in zip(predictions, references, strict=False) if r is not None]
        preds = [p for p, _ in pairs]
        refs = [r for _, r in pairs]
        empty_reason = "No (prediction, reference) pairs available."
    if not preds:
        return ScoreDraft(
            name=evaluator.name,
            data_type="numeric",
            value=None,
            outcome=OUTCOME_ABSTAINED,
            reasoning=empty_reason,
            scope="dataset",
        )

    # A dataset metric over a handful of samples is noise, so an unmet
    # ``min_n`` abstains rather than reporting a misleadingly precise number.
    min_n = int(config.get("min_n") or 0)
    if min_n and len(preds) < min_n:
        return ScoreDraft(
            name=evaluator.name,
            data_type="numeric",
            value=None,
            outcome=OUTCOME_ABSTAINED,
            reasoning=(
                f"Insufficient sample size for a stable {metric}: {len(preds)} "
                f"prediction(s) < min_n={min_n}. Abstaining (low confidence)."
            ),
            scope="dataset",
            sub_scores=[
                {"metric": metric, "n": len(preds), "min_n": min_n, "low_confidence": True}
            ],
        )

    fn = METRICS.get(metric)
    if fn is None:
        return ScoreDraft(
            name=evaluator.name, value=None, reasoning=f"Unknown metric '{metric}'", scope="dataset"
        )
    # A metric may return a third element of extra sub_scores, mirroring the
    # deterministic family's contract.
    result = fn(preds, refs, config)
    value, detail = result[0], result[1]
    extra = list(result[2]) if len(result) > 2 else []
    return ScoreDraft(
        name=evaluator.name,
        data_type="numeric",
        value=value,
        # A dataset metric is a graded numeric signal; no threshold may convert
        # it into a boolean pass/fail.
        passed=None,
        reasoning=detail,
        scope="dataset",
        sub_scores=[{"metric": metric, "n": len(preds)}, *extra],
    )


def _norm(s: str, config: dict) -> str:
    """Normalise a reference string (case folding only, no output_normalize)."""
    s = (s or "").strip()
    return s.lower() if config.get("case_insensitive", True) else s


def _norm_pred(s: str, config: dict) -> str:
    """Normalise a prediction string: output_normalize first, then case fold."""
    mode = config.get("output_normalize", "strip")
    s = _apply_output_normalize(s or "", mode)
    return s.lower() if config.get("case_insensitive", True) else s


def _accuracy(preds, refs, config):
    correct = sum(
        1 for p, r in zip(preds, refs, strict=False) if _norm_pred(p, config) == _norm(r, config)
    )
    val = correct / len(preds)
    return val, f"accuracy={val:.4f} ({correct}/{len(preds)})"


def _exact_match(preds, refs, config):
    # exact_match respects output_normalize just like accuracy.
    return _accuracy(preds, refs, config)


def _prf(preds, refs, config, which: str):
    average = config.get("average", "macro")
    y_pred = [_norm_pred(p, config) for p in preds]
    y_true = [_norm(r, config) for r in refs]
    try:
        from sklearn.metrics import f1_score, precision_score, recall_score  # noqa: PLC0415

        labels = sorted(set(y_true) | set(y_pred))
        kwargs = {"average": average, "labels": labels, "zero_division": 0}
        if which == "precision":
            val = float(precision_score(y_true, y_pred, **kwargs))
        elif which == "recall":
            val = float(recall_score(y_true, y_pred, **kwargs))
        else:
            val = float(f1_score(y_true, y_pred, **kwargs))
        return val, f"{which}({average})={val:.4f}"
    except Exception as exc:  # noqa: BLE001
        logger.warning("sklearn %s failed: %s", which, exc)
        return None, f"{which} computation failed: {exc}"


def _precision(preds, refs, config):
    return _prf(preds, refs, config, "precision")


def _recall(preds, refs, config):
    return _prf(preds, refs, config, "recall")


def _f1(preds, refs, config):
    return _prf(preds, refs, config, "f1")


def _tokenize(s: str) -> list[str]:
    return (s or "").lower().split()


def _bleu(preds, refs, config):
    # Corpus-level BLEU-4 with brevity penalty (lightweight, dependency-free).
    import math

    weights = [0.25, 0.25, 0.25, 0.25]
    p_numer = [0] * 4
    p_denom = [0] * 4
    ref_len = 0
    pred_len = 0
    for pred, ref in zip(preds, refs, strict=False):
        pt = _tokenize(pred)
        rt = _tokenize(ref)
        pred_len += len(pt)
        ref_len += len(rt)
        for n in range(1, 5):
            p_ngrams = Counter(_ngrams(pt, n))
            r_ngrams = Counter(_ngrams(rt, n))
            overlap = sum(min(c, r_ngrams[g]) for g, c in p_ngrams.items())
            p_numer[n - 1] += overlap
            p_denom[n - 1] += max(sum(p_ngrams.values()), 0)
    precisions = []
    for num, den in zip(p_numer, p_denom, strict=False):
        precisions.append((num / den) if den else 0.0)
    if min(precisions) <= 0:
        geo = 0.0
    else:
        geo = math.exp(sum(w * math.log(p) for w, p in zip(weights, precisions, strict=False)))
    bp = 1.0 if pred_len > ref_len else math.exp(1 - ref_len / pred_len) if pred_len else 0.0
    val = bp * geo
    return val, f"BLEU-4={val:.4f}"


def _ngrams(tokens: list[str], n: int) -> list[tuple]:
    return [tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)]


def _rouge_l(preds, refs, config):
    scores = []
    for pred, ref in zip(preds, refs, strict=False):
        pt, rt = _tokenize(pred), _tokenize(ref)
        lcs = _lcs_len(pt, rt)
        if not pt or not rt:
            scores.append(0.0)
            continue
        prec = lcs / len(pt)
        rec = lcs / len(rt)
        f = (2 * prec * rec / (prec + rec)) if (prec + rec) else 0.0
        scores.append(f)
    val = sum(scores) / len(scores)
    return val, f"ROUGE-L(F)={val:.4f}"


def _lcs_len(a: list[str], b: list[str]) -> int:
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        for j in range(1, len(b) + 1):
            cur[j] = prev[j - 1] + 1 if a[i - 1] == b[j - 1] else max(prev[j], cur[j - 1])
        prev = cur
    return prev[len(b)]


def _embedding_cosine(preds, refs, config):
    from overbae.core.llms import EmbeddingUnavailableError, get_embedding  # noqa: PLC0415

    sims = []
    for pred, ref in zip(preds, refs, strict=False):
        try:
            ep = get_embedding(pred[:8000])
            er = get_embedding(ref[:8000])
            sims.append(_cosine(ep, er))
        except EmbeddingUnavailableError as exc:
            # Permanent (no OpenAI key) — skip the whole metric, don't grind
            # through every pair.
            logger.warning("embedding_cosine skipped: %s", exc)
            return None, str(exc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("embedding failed: %s", exc)
    if not sims:
        return None, "embedding computation failed"
    val = sum(sims) / len(sims)
    return val, f"mean cosine={val:.4f}"


def _cosine(a: list[float], b: list[float]) -> float:
    import math

    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return (dot / (na * nb)) if na and nb else 0.0


def _chrf(preds, refs, config):
    """chrF character n-gram F-score (Popovic 2015/2017). The default beta=2
    weights recall over precision."""
    from collections import Counter  # noqa: PLC0415

    beta = float(config.get("chrf_beta", 2.0))
    char_n = int(config.get("chrf_char_n", 6))
    word_n = int(config.get("chrf_word_n", 2))  # 0 → pure chrF, 2 → chrF++

    def _char_ngrams(s: str, n: int) -> Counter:
        return Counter(s[i : i + n] for i in range(len(s) - n + 1)) if len(s) >= n else Counter()

    def _word_ngrams(s: str, n: int) -> Counter:
        tokens = s.split()
        return (
            Counter(tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1))
            if len(tokens) >= n
            else Counter()
        )

    def _prf_ngrams(pred: str, ref: str) -> tuple[float, float]:
        prec_vals, rec_vals = [], []
        for n in range(1, char_n + 1):
            pg = _char_ngrams(pred, n)
            rg = _char_ngrams(ref, n)
            matched = sum(min(pg[g], rg[g]) for g in pg)
            prec_vals.append(matched / sum(pg.values()) if pg else 0.0)
            rec_vals.append(matched / sum(rg.values()) if rg else 0.0)
        for n in range(1, word_n + 1):
            pg = _word_ngrams(pred, n)
            rg = _word_ngrams(ref, n)
            matched = sum(min(pg[g], rg[g]) for g in pg)
            prec_vals.append(matched / sum(pg.values()) if pg else 0.0)
            rec_vals.append(matched / sum(rg.values()) if rg else 0.0)
        avg_p = sum(prec_vals) / len(prec_vals) if prec_vals else 0.0
        avg_r = sum(rec_vals) / len(rec_vals) if rec_vals else 0.0
        return avg_p, avg_r

    scores = []
    for pred, ref in zip(preds, refs, strict=False):
        p, r = _prf_ngrams(pred.strip(), ref.strip())
        beta2 = beta**2
        f = (1 + beta2) * p * r / (beta2 * p + r) if (p + r) > 0 else 0.0
        scores.append(f)
    val = sum(scores) / len(scores) if scores else 0.0
    return val, f"chrF++={val:.4f} (beta={beta}, char_n={char_n}, word_n={word_n})"


def _duplicate_rate(preds, refs, config):
    """Reference-free, so ``refs`` is unused. Lower is better: 0.0 means every
    output is unique."""
    normed = [_norm_pred(p, config) for p in preds]
    counts = Counter(normed)
    duplicated = sum(1 for n in normed if counts[n] > 1)
    val = duplicated / len(normed)
    return val, f"duplicate_rate={val:.4f} ({duplicated}/{len(normed)} samples share an output)"


def _confidence_outcome_pairs(preds, refs) -> list[tuple[float, float]]:
    """``(stated confidence, was the row actually correct)`` pairs.

    The reference is another evaluator's per-sample score, so it arrives as a
    fraction; anything short of a perfect row counts as incorrect, which is the
    event the confidence is a claim about.
    """
    pairs = []
    for pred, ref in zip(preds, refs, strict=False):
        try:
            confidence, correctness = float(pred), float(ref)
        except (TypeError, ValueError):
            continue
        if not 0.0 <= confidence <= 1.0:
            continue
        pairs.append((confidence, 1.0 if correctness >= 1.0 else 0.0))
    return pairs


_CALIBRATION_BINS = 10
_BOOTSTRAP_RESAMPLES = 1000


def _ece(pairs: list[tuple[float, float]]) -> float:
    bins: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for confidence, outcome in pairs:
        bins[min(int(confidence * _CALIBRATION_BINS), _CALIBRATION_BINS - 1)].append(
            (confidence, outcome)
        )
    return sum(
        len(rows)
        / len(pairs)
        * abs(sum(c for c, _ in rows) / len(rows) - sum(o for _, o in rows) / len(rows))
        for rows in bins.values()
    )


def _bootstrap_interval(pairs, statistic) -> tuple[float, float]:
    """95% interval on ``1 - statistic``, by resampling rows with replacement.

    A run-level metric emits one number per arm, so without this there is no way
    to tell a real gap from noise — and the gap measured between two models sat
    close to what a deliberately maximal defect produced, which is exactly the
    range where a point estimate misleads.
    """
    rng = random.Random(0)  # Fixed, so a re-aggregation reproduces the interval.
    n = len(pairs)
    draws = sorted(
        1.0 - statistic([pairs[rng.randrange(n)] for _ in range(n)])
        for _ in range(_BOOTSTRAP_RESAMPLES)
    )
    return draws[int(0.025 * _BOOTSTRAP_RESAMPLES)], draws[int(0.975 * _BOOTSTRAP_RESAMPLES) - 1]


def _calibration(preds, refs, config):  # noqa: ARG001
    """``1 - expected calibration error``, higher-is-better like every other metric.

    A model that states 0.97 and is right 70% of the time is overconfident, and
    no accuracy metric shows it: both models can extract equally well while one
    lies about how sure it is. Finetuning induces exactly this, since the student
    copies the teacher's confident tone without its accuracy.

    NOT Brier, which conflates calibration with accuracy. With confidences
    clustered in a narrow band, ``(c - o)**2`` is dominated by whether ``o`` is 0
    or 1, so the more accurate model wins on Brier however badly tuned its
    confidence is. Measured on one run: forcing 0.99 onto every incorrect row —
    the worst miscalibration the data allows — moved Brier by 0.013, while two
    models differing only in accuracy differed by 0.028. ECE bins by stated
    confidence and compares each bin against its own hit rate, so the accuracy
    level cancels out; binning also stops an overconfident bin silently
    offsetting an underconfident one, which a single mean gap would hide.
    """
    pairs = _confidence_outcome_pairs(preds, refs)
    if not pairs:
        return None, "no usable (confidence, outcome) pairs"
    ece = _ece(pairs)
    n_bins = len({min(int(c * _CALIBRATION_BINS), _CALIBRATION_BINS - 1) for c, _ in pairs})
    mean_confidence = sum(c for c, _ in pairs) / len(pairs)
    actual = sum(o for _, o in pairs) / len(pairs)
    brier = sum((c - o) ** 2 for c, o in pairs) / len(pairs)
    low, high = _bootstrap_interval(pairs, _ece)
    return (
        1.0 - ece,
        f"calibration={1.0 - ece:.4f} [{low:.4f}, {high:.4f}] "
        f"(ECE={ece:.4f} over {n_bins} bins, n={len(pairs)}); "
        f"states {mean_confidence:.3f} confidence, correct {actual:.3f} of the time "
        f"({'over' if mean_confidence > actual else 'under'}confident by "
        f"{abs(mean_confidence - actual):.3f}); Brier={brier:.4f}",
        [{"_interval": {"low": low, "high": high, "method": "bootstrap", "level": 0.95}}],
    )


METRICS = {
    "calibration": _calibration,
    "accuracy": _accuracy,
    "exact_match": _exact_match,
    "precision": _precision,
    "recall": _recall,
    "f1": _f1,
    "bleu": _bleu,
    "chrf": _chrf,
    "rouge_l": _rouge_l,
    "embedding_cosine": _embedding_cosine,
    "duplicate_rate": _duplicate_rate,
}

# Metrics that do not need references — they compare predictions to each other.
_REFERENCE_FREE_METRICS = {"duplicate_rate"}


def per_class_metrics(
    predictions: list[str], references: list[str], config: dict | None = None
) -> dict[str, Any]:
    """``{}`` when the pairs are unusable or sklearn is unavailable — callers
    skip silently."""
    config = config or {}
    y_pred = [_norm_pred(p, config) for p in predictions]
    y_true = [_norm(r, config) for r in references if r is not None]
    if len(y_pred) != len(y_true) or not y_true:
        return {}
    try:
        from sklearn.metrics import (  # noqa: PLC0415
            accuracy_score,
            precision_recall_fscore_support,
        )

        labels = sorted(set(y_true) | set(y_pred))
        prec, rec, f1, support = precision_recall_fscore_support(
            y_true, y_pred, labels=labels, average=None, zero_division=0
        )
        classes = [
            {
                "label": label,
                "precision": round(float(p), 6),
                "recall": round(float(r), 6),
                "f1": round(float(f), 6),
                "support": int(s),
            }
            for label, p, r, f, s in zip(labels, prec, rec, f1, support, strict=True)
        ]
        aggregates: dict[str, Any] = {
            "accuracy": round(float(accuracy_score(y_true, y_pred)), 6),
            "n": len(y_true),
        }
        for average in ("macro", "micro", "weighted"):
            a_p, a_r, a_f, _ = precision_recall_fscore_support(
                y_true, y_pred, labels=labels, average=average, zero_division=0
            )
            aggregates[average] = {
                "precision": round(float(a_p), 6),
                "recall": round(float(a_r), 6),
                "f1": round(float(a_f), 6),
            }
        return {
            "classes": classes,
            "aggregates": aggregates,
            "confusion_matrix": confusion_matrix(predictions, references, config),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("per_class_metrics failed: %s", exc)
        return {}


def confusion_matrix(
    predictions: list[str], references: list[Any], config: dict | None = None
) -> dict[str, Any]:
    config = config or {}
    # A confusion matrix cross-tabulates labels. A metric whose references are
    # numeric — calibration's per-row correctness, for one — has no labels to
    # tabulate, and the label normalizer assumes a string.
    if any(not isinstance(r, str) for r in references if r is not None):
        return {}
    y_pred = [_norm_pred(p, config) for p in predictions]
    y_true = [_norm(r, config) for r in references if r is not None]
    if len(y_pred) != len(y_true) or not y_true:
        return {}
    try:
        from sklearn.metrics import confusion_matrix as sk_cm  # noqa: PLC0415

        labels = sorted(set(y_true) | set(y_pred))
        matrix = sk_cm(y_true, y_pred, labels=labels).tolist()
        return {"labels": labels, "matrix": matrix}
    except Exception:  # noqa: BLE001
        return {}
