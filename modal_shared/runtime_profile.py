import random


def representative_order(lengths, *, samples, batch_size, seed):
    ordered = sorted(range(len(lengths)), key=lambda i: (lengths[i], i))
    count = min(samples, len(ordered))
    selected = [ordered[round(i * (len(ordered) - 1) / max(1, count - 1))] for i in range(count)]
    blocks = [selected[i : i + batch_size] for i in range(0, count, batch_size)]
    random.Random(seed).shuffle(blocks)
    prefix = [i for block in blocks for i in block]
    retained = set(prefix)
    return prefix + [i for i in ordered if i not in retained], {
        "sampling": "systematic_length_quantiles_with_shuffled_length_batches",
        "source_rows": len(lengths),
        "selected_rows": count,
        "source_length_range": [min(lengths), max(lengths)] if lengths else [],
        "selected_length_range": [min(lengths[i] for i in prefix), max(lengths[i] for i in prefix)]
        if prefix
        else [],
        "seed": seed,
        "quality_evidence": False,
        "throughput_scope": "bounded training window including optimizer warmup; not steady state",
    }
