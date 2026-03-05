import { ArrowRight, Trophy, Zap, DollarSign } from "lucide-react";
import { Link } from "@tanstack/react-router";

import { Button } from "@/components/ui/button";
import { RawResultAccordion } from "./RawResultAccordion";

interface ModelRec {
  model: string;
  avg_eval_score?: number;
  performance_delta_pct?: number;
  performance_delta_pp?: number;
  avg_latency_ms?: number;
  avg_cost_per_request?: number;
  reason?: string;
}

interface BaselineRec extends ModelRec {
  scored_span_count?: number;
}

interface Recommendations {
  summary?: string;
  verdict?: string;
  baseline?: BaselineRec;
  top_performer?: ModelRec;
  fastest?: ModelRec;
  cheapest?: ModelRec;
  best_overall?: ModelRec;
}

interface BacktestingData {
  current_model?: string;
  models_tested?: number;
  spans_tested?: number;
  suggestion_id?: string;
  recommendations?: Recommendations;
}

function fmt(n: number | undefined, digits = 2) {
  if (n === undefined || n === null) return "—";
  return n.toFixed(digits);
}

function fmtPct(n: number | undefined) {
  if (n === undefined || n === null) return null;
  const sign = n >= 0 ? "+" : "";
  return `${sign}${n.toFixed(1)}%`;
}

function truncateModel(model: string) {
  return model.length > 20 ? `${model.slice(0, 18)}…` : model;
}

interface RecommendationCardProps {
  icon: React.ReactNode;
  title: string;
  rec: ModelRec;
  highlight?: string | null;
}

function RecommendationCard({ icon, title, rec, highlight }: RecommendationCardProps) {
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-border bg-muted/20 p-4">
      <div className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground">
        {icon}
        {title}
      </div>
      <div className="text-sm font-semibold" title={rec.model}>
        {truncateModel(rec.model)}
      </div>
      <div className="space-y-1 text-xs text-muted-foreground">
        {rec.avg_eval_score !== undefined && (
          <div>Score: {fmt(rec.avg_eval_score, 3)}</div>
        )}
        {rec.avg_latency_ms !== undefined && (
          <div>Latency: {fmt(rec.avg_latency_ms, 0)} ms</div>
        )}
        {rec.avg_cost_per_request !== undefined && (
          <div>Cost/req: ${rec.avg_cost_per_request.toFixed(6)}</div>
        )}
      </div>
      {highlight && (
        <span className="mt-1 self-start rounded bg-amber-500/10 px-2 py-0.5 text-xs font-semibold text-amber-600">
          {highlight}
        </span>
      )}
    </div>
  );
}

interface BacktestingResultProps {
  result: Record<string, unknown>;
  promptSlug?: string | null;
}

export function BacktestingResult({ result, promptSlug }: BacktestingResultProps) {
  const data = result as BacktestingData;
  const recs = data.recommendations;
  const baseline = recs?.baseline;

  return (
    <div className="space-y-5">
      {/* View Suggestion button */}
      {data.suggestion_id && promptSlug && (
        <div className="flex justify-end">
          <Button asChild size="sm" variant="outline">
            <Link
              params={{ slug: promptSlug }}
              search={{ tab: "suggestions" }}
              to="/agents/$slug"
            >
              View Suggestion
              <ArrowRight className="ml-1.5 size-3.5" />
            </Link>
          </Button>
        </div>
      )}

      {/* Baseline summary row */}
      {baseline && (
        <div className="rounded-lg border border-border bg-muted/20 px-4 py-3 text-sm">
          <span className="font-medium">Baseline:</span>{" "}
          <span className="font-mono">{baseline.model}</span>
          {" · "}Score {fmt(baseline.avg_eval_score, 3)}
          {baseline.avg_latency_ms !== undefined && (
            <> · {fmt(baseline.avg_latency_ms, 0)} ms</>
          )}
          {baseline.avg_cost_per_request !== undefined && (
            <> · ${baseline.avg_cost_per_request.toFixed(6)}/req</>
          )}
          {data.spans_tested !== undefined && (
            <span className="ml-2 text-xs text-muted-foreground">
              · {data.spans_tested} spans tested
            </span>
          )}
        </div>
      )}

      {/* Recommendation cards */}
      {recs && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {recs.top_performer && (
            <RecommendationCard
              highlight={recs.top_performer.performance_delta_pct != null ? fmtPct(recs.top_performer.performance_delta_pct) + " vs baseline" : null}
              icon={<Trophy className="size-3.5 text-amber-500" />}
              rec={recs.top_performer}
              title="Top Performer"
            />
          )}
          {recs.fastest && (
            <RecommendationCard
              highlight={recs.fastest.performance_delta_pp != null ? `${fmtPct(recs.fastest.performance_delta_pp)} score` : null}
              icon={<Zap className="size-3.5 text-blue-500" />}
              rec={recs.fastest}
              title="Fastest"
            />
          )}
          {recs.cheapest && (
            <RecommendationCard
              highlight={recs.cheapest.performance_delta_pp != null ? `${fmtPct(recs.cheapest.performance_delta_pp)} score` : null}
              icon={<DollarSign className="size-3.5 text-green-600" />}
              rec={recs.cheapest}
              title="Cheapest"
            />
          )}
          {recs.best_overall && !recs.top_performer && (
            <RecommendationCard
              highlight="Best Overall"
              icon={<Trophy className="size-3.5 text-amber-500" />}
              rec={recs.best_overall}
              title="Best Overall"
            />
          )}
        </div>
      )}

      {/* Summary text */}
      {recs?.summary && (
        <p className="text-sm text-muted-foreground">{recs.summary}</p>
      )}

      <RawResultAccordion result={result} />
    </div>
  );
}
