import argparse
import json

from overbae.services.decision_benchmark_scoring import compare_suite, score_suite

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", required=True)
    parser.add_argument("--predictions", required=True)
    parser.add_argument("--baseline-predictions")
    parser.add_argument("--output", required=True)
    parser.add_argument("--bootstrap-samples", type=int, default=1000)
    args = parser.parse_args()
    if args.baseline_predictions:
        result = compare_suite(
            args.suite,
            args.baseline_predictions,
            args.predictions,
            args.output,
            bootstrap_samples=args.bootstrap_samples,
        )
    else:
        result = score_suite(
            args.suite, args.predictions, args.output, bootstrap_samples=args.bootstrap_samples
        )
    print(json.dumps({"output": args.output, "benchmarks": list(result["benchmarks"])}))
