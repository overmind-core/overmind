"""Report generative evaluator defects across the whole library.

Run before an experiment: every check here is a defect class that was found once
on one agent's suite and would otherwise go unnoticed on every other agent's.
"""

from django.core.management.base import BaseCommand

from overbae.services.eval.audit import audit_evaluators


class Command(BaseCommand):
    help = "Audit every evaluator for defects that silently corrupt a measurement."

    def add_arguments(self, parser):
        parser.add_argument("--project-id", type=str, default="", help="Limit to one project UUID.")
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Exit non-zero when anything is found, for use as a pre-run gate.",
        )

    def handle(self, *args, **options):
        report = audit_evaluators(options["project_id"])
        problems = 0

        for finding in report["missing_checklist"]:
            problems += 1
            self.stdout.write(
                self.style.ERROR(
                    f"no checklist: {finding['evaluator']} v{finding['version']} — "
                    "an LLM judge scores the fraction of items that pass, so it cannot score"
                )
            )
        for finding in report["unbound_variables"]:
            problems += 1
            self.stdout.write(
                self.style.ERROR(
                    f"unbound variable(s) {finding['variables']}: {finding['evaluator']} "
                    f"v{finding['version']} — the judge is asked about evidence it never gets"
                )
            )
        for finding in report["confidence_comparisons"]:
            problems += 1
            self.stdout.write(
                self.style.WARNING(
                    f"confidence compared to a reference: {finding['evaluator']} "
                    f"v{finding['version']} items={finding['items']} — a stated confidence has no "
                    "correct value; score calibration over the run instead"
                )
            )
        for finding in report["rubric_scoring_machinery"]:
            problems += 1
            self.stdout.write(
                self.style.WARNING(
                    f"rubric argues with the runtime: {finding['evaluator']} "
                    f"v{finding['version']} {finding['contradictions']} — the rubric reaches the "
                    "judge verbatim, but a generative judge only answers checklist items and the "
                    "score is computed from its verdicts"
                )
            )
        for finding in report["never_scored"]:
            problems += 1
            self.stdout.write(
                self.style.WARNING(
                    f"never scored: {finding['evaluator']} — enabled, and its agent has been "
                    "evaluated, yet it produced no score, so nothing about it has been observed"
                )
            )
        # Not counted as a problem: nothing is wrong with the evaluator, nobody
        # has run an eval for that agent. Reporting it as a defect is how a
        # pre-run gate earns a reputation for crying wolf.
        for finding in report["never_evaluated"]:
            self.stdout.write(
                f"not yet exercised: {finding['evaluator']} — enabled, but its agent has "
                "never had an eval run"
            )
        for finding in report["co_failure"]:
            if finding["suspect"]:
                problems += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"items fail together ({finding['ratio']:.2f} over {finding['rows']} "
                        f"rows, {finding['patterns']} distinct patterns): {finding['evaluator']} "
                        "— rephrasings of one concern, so a single defect costs the whole score"
                    )
                )
            elif finding["degenerate"]:
                problems += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"items never vary independently ({finding['patterns']} distinct "
                        f"pattern(s) over {finding['rows']} rows): {finding['evaluator']} — every "
                        "item moves together, which is also what a fabricated score looks like"
                    )
                )

        style = self.style.SUCCESS if not problems else self.style.WARNING
        self.stdout.write(style(f"{problems} finding(s)."))
        if problems and options["strict"]:
            raise SystemExit(1)
