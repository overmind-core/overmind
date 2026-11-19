import type { ReactNode } from "react";

import { useQuery } from "@tanstack/react-query";
import { createFileRoute, Link } from "@tanstack/react-router";

import { Failure } from "~/components/failure";
import { type FeedbackDetail, overwatchGet, type TrailStep } from "~/lib/api";
import { formatAgo, humanize, SEVERITY_VARIANT } from "~/lib/format";

import { Badge } from "@/components/ui/badge";
import { Icon } from "@/components/ui/icons";
import { PageHeader } from "@/components/ui/page-header";
import { SectionCard } from "@/components/ui/section-card";
import { LoadingState } from "@/components/ui/spinner";

const RESULT: Record<TrailStep["result"], string> = {
  error: "✗",
  no_progress: "↻",
  ok: "✓",
  unexpected: "!",
};

export const Route = createFileRoute("/_app/feedback_/$feedbackId")({ component: FeedbackPage });

function FeedbackPage() {
  const { days } = Route.useSearch();
  const { feedbackId } = Route.useParams();
  const query = useQuery({
    queryFn: () => overwatchGet<FeedbackDetail>(`feedback/${feedbackId}`, {}),
    queryKey: ["feedback", feedbackId],
  });
  const report = query.data;

  return (
    <>
      <Link
        className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
        search={{ days, page: 1, page_size: 25 }}
        to="/feedback"
      >
        <Icon.back className="size-3.5" />
        Feedback
      </Link>
      <PageHeader
        className="flex-col sm:flex-row"
        description={
          report
            ? `${report.source === "agent" ? report.client || "Coding agent" : "Console"} · ${formatAgo(report.created_at)} · seen ${report.seen}× in 7 days`
            : undefined
        }
        title={report?.title ?? "Report"}
      />
      {query.isPending ? (
        <LoadingState fullPage label="Loading report" />
      ) : query.isError || !report ? (
        <Failure error={query.error ?? new Error("Not found")} onRetry={query.refetch} />
      ) : (
        <ReportBody report={report} />
      )}
    </>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="space-y-1">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="whitespace-pre-wrap break-words text-sm">{children}</dd>
    </div>
  );
}

function ReportBody({ report }: { report: FeedbackDetail }) {
  const { detail } = report;
  const reported = report.environment.reported ?? {};
  const observed = report.environment.observed ?? {};
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap gap-2">
        {report.severity ? (
          <Badge size="chip" variant={SEVERITY_VARIANT[report.severity]}>
            {humanize(report.severity)}
          </Badge>
        ) : null}
        <Badge size="chip" variant="info">
          {humanize(report.kind)}
        </Badge>
        <Badge size="chip" variant="neutral">
          {humanize(report.area)}
        </Badge>
        {report.outcome ? (
          <Badge size="chip" variant="neutral">
            {humanize(report.outcome)}
          </Badge>
        ) : null}
      </div>
      <SectionCard title="Report">
        <dl className="grid gap-4 md:grid-cols-2">
          {detail.intent ? <Field label="Trying to">{detail.intent}</Field> : null}
          <Field label={report.source === "agent" ? "What happened" : "Message"}>
            {report.message}
          </Field>
          {detail.expected ? <Field label="Expected">{detail.expected}</Field> : null}
          {detail.observed ? <Field label="Observed">{detail.observed}</Field> : null}
          {detail.suggestion ? <Field label="Suggested fix">{detail.suggestion}</Field> : null}
          {detail.resources?.length ? (
            <Field label="Resources">{detail.resources.join("\n")}</Field>
          ) : null}
        </dl>
      </SectionCard>
      {detail.trail?.length ? (
        <SectionCard title="Trail reported by the agent">
          <ol className="space-y-1 font-mono text-xs">
            {detail.trail.map((step, index) => (
              <li className="flex gap-3" key={`${index}-${step.action}`}>
                <span className={step.result === "ok" ? "text-success" : "text-destructive"}>
                  {RESULT[step.result]}
                </span>
                <span>{step.action}</span>
                <span className="text-muted-foreground">
                  {[
                    step.error_code,
                    (step.attempts ?? 1) > 1 ? `×${step.attempts}` : "",
                    step.note,
                  ]
                    .filter(Boolean)
                    .join(" ")}
                </span>
              </li>
            ))}
          </ol>
        </SectionCard>
      ) : null}
      {report.source === "agent" ? (
        <SectionCard title="Tool calls recorded by PostHog">
          {report.server_trail.available ? (
            report.server_trail.calls.length ? (
              <ol className="space-y-1 font-mono text-xs">
                {report.server_trail.calls.map((call) => (
                  <li className="flex gap-3" key={`${call.at}-${call.tool}`}>
                    <span className="text-muted-foreground">{call.at.slice(11, 19)}</span>
                    <span className={call.is_error ? "text-destructive" : "text-success"}>
                      {call.is_error ? "✗" : "✓"}
                    </span>
                    <span>{call.tool}</span>
                    <span className="text-muted-foreground">{call.error_code}</span>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="text-sm text-muted-foreground">
                No tool calls in the {report.server_trail.window_minutes} minutes before the
                report.
              </p>
            )
          ) : (
            <p className="text-sm text-muted-foreground">{report.server_trail.reason}</p>
          )}
        </SectionCard>
      ) : null}
      <SectionCard title="Environment">
        <dl className="grid gap-4 md:grid-cols-3">
          <Field label="User">
            {report.user_id ? (
              <Link
                className="hover:underline"
                params={{ accountId: String(report.user_id) }}
                search={{ days: 30 }}
                to="/accounts/$accountId"
              >
                {report.user_email ?? report.user_id}
              </Link>
            ) : (
              "—"
            )}
          </Field>
          <Field label="Project">{report.project_id ?? "—"}</Field>
          {Object.entries({ ...observed, ...reported }).map(([key, value]) => (
            <Field key={key} label={humanize(key)}>
              {value}
            </Field>
          ))}
        </dl>
      </SectionCard>
    </div>
  );
}
