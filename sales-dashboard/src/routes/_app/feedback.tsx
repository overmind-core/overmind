import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";

import { Failure } from "~/components/failure";
import { type FeedbackRow, overwatchGet, type Page } from "~/lib/api";
import { formatAgo, humanize, SEVERITY_VARIANT } from "~/lib/format";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/page-header";
import { SectionCard } from "@/components/ui/section-card";
import { LoadingState } from "@/components/ui/spinner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { TablePagination } from "@/components/ui/table-pagination";

const SOURCES = [
  { label: "All", value: undefined },
  { label: "Coding agents", value: "agent" },
  { label: "Console", value: "console" },
] as const;

type FeedbackSearch = { page: number; page_size: number; source?: "agent" | "console" };

export const Route = createFileRoute("/_app/feedback")({
  component: FeedbackPage,
  validateSearch: (search: Record<string, unknown>): FeedbackSearch => ({
    page: Math.max(1, Number(search.page) || 1),
    page_size: [10, 25, 50, 100].includes(Number(search.page_size)) ? Number(search.page_size) : 25,
    source: search.source === "agent" || search.source === "console" ? search.source : undefined,
  }),
});

function FeedbackPage() {
  const { days, page, page_size, source } = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });
  const query = useQuery({
    placeholderData: keepPreviousData,
    queryFn: () => overwatchGet<Page<FeedbackRow>>("feedback", { days, page, page_size, source }),
    queryKey: ["feedback", days, page, page_size, source],
  });

  return (
    <>
      <PageHeader
        actions={
          <fieldset aria-label="Source" className="flex gap-1">
            {SOURCES.map((item) => (
              <Button
                aria-pressed={item.value === source}
                key={item.label}
                onClick={() =>
                  void navigate({ search: (prev) => ({ ...prev, page: 1, source: item.value }) })
                }
                size="sm"
                variant={item.value === source ? "default" : "secondary"}
              >
                {item.label}
              </Button>
            ))}
          </fieldset>
        }
        className="flex-col sm:flex-row"
        count={query.data?.count}
        description={`Reports from the last ${days} UTC days. Seen counts reports of the same problem in the 7 days before each one.`}
        title="Feedback"
      />
      {query.isPending ? (
        <LoadingState fullPage label="Loading feedback" />
      ) : query.isError ? (
        <Failure error={query.error} onRetry={query.refetch} />
      ) : (
        <SectionCard contentClassName="p-0" title="Reports">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Report</TableHead>
                <TableHead>Severity</TableHead>
                <TableHead>Kind</TableHead>
                <TableHead>Area</TableHead>
                <TableHead>From</TableHead>
                <TableHead>Seen</TableHead>
                <TableHead>Received</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {query.data.results.map((row) => (
                <TableRow key={row.id}>
                  <TableCell className="max-w-96 truncate" title={row.title}>
                    <Link
                      className="hover:underline"
                      params={{ feedbackId: row.id }}
                      search={{ days }}
                      to="/feedback/$feedbackId"
                    >
                      {row.title}
                    </Link>
                  </TableCell>
                  <TableCell>
                    {row.severity ? (
                      <Badge size="chip" variant={SEVERITY_VARIANT[row.severity]}>
                        {humanize(row.severity)}
                      </Badge>
                    ) : (
                      "—"
                    )}
                  </TableCell>
                  <TableCell className="whitespace-nowrap">{humanize(row.kind)}</TableCell>
                  <TableCell className="whitespace-nowrap">{humanize(row.area)}</TableCell>
                  <TableCell className="max-w-48 truncate">
                    {row.source === "agent" ? row.client || "Coding agent" : "Console"}
                  </TableCell>
                  <TableCell className="font-mono tabular-nums">{row.seen}×</TableCell>
                  <TableCell className="whitespace-nowrap">{formatAgo(row.created_at)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <TablePagination
            className="border-t border-border/70"
            count={query.data.count}
            onPageChange={(next) => void navigate({ search: (prev) => ({ ...prev, page: next }) })}
            onPageSizeChange={(size) =>
              void navigate({ search: (prev) => ({ ...prev, page: 1, page_size: size }) })
            }
            page={page}
            pageSize={page_size}
          />
        </SectionCard>
      )}
    </>
  );
}
