import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";

import { Failure } from "~/components/failure";
import { Freshness } from "~/components/freshness";
import { overwatchGet, type Page, type User } from "~/lib/api";
import {
  formatAgo,
  formatDate,
  formatInt,
  formatUsd,
  humanize,
  milestoneLabel,
} from "~/lib/format";

import { Badge } from "@/components/ui/badge";
import { PageHeader } from "@/components/ui/page-header";
import { SectionCard } from "@/components/ui/section-card";
import { SortableHeader, type SortState } from "@/components/ui/sortable-header";
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

const COLUMNS = [
  { key: "date_joined", label: "Joined" },
  { key: "projects", label: "Projects" },
  { key: "seats", label: "Seats" },
  { key: "last_span_at", label: "Last activity" },
  { key: "spans", label: "Spans" },
  { key: "training_jobs", label: "Training runs" },
  { key: "live_deployments", label: "Live" },
  { key: "spend_usd", label: "Spend" },
  { key: "topups_usd", label: "Top-ups" },
  { key: "balance_usd", label: "Balance" },
] as const;

type SortKey = "email" | (typeof COLUMNS)[number]["key"];
const SORT_KEYS: string[] = ["email", ...COLUMNS.map((column) => column.key)];
type AccountsSearch = { page: number; page_size: number; ordering: string };

export const Route = createFileRoute("/_app/accounts")({
  component: AccountsPage,
  validateSearch: (search: Record<string, unknown>): AccountsSearch => {
    const ordering = String(search.ordering ?? "");
    return {
      ordering: SORT_KEYS.includes(ordering.replace(/^-/, "")) ? ordering : "-date_joined",
      page: Math.max(1, Number(search.page) || 1),
      page_size: [10, 25, 50, 100].includes(Number(search.page_size))
        ? Number(search.page_size)
        : 25,
    };
  },
});

function AccountsPage() {
  const { days, ordering, page, page_size } = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });
  const query = useQuery({
    placeholderData: keepPreviousData,
    queryFn: () => overwatchGet<Page<User>>("users", { days, ordering, page, page_size }),
    queryKey: ["users", days, ordering, page, page_size],
  });
  const sort: SortState<SortKey> = {
    dir: ordering.startsWith("-") ? "desc" : "asc",
    key: ordering.replace(/^-/, "") as SortKey,
  };
  const onSort = (key: SortKey) =>
    void navigate({
      search: (prev) => ({
        ...prev,
        ordering: sort.key === key && sort.dir === "desc" ? key : `-${key}`,
        page: 1,
      }),
    });

  return (
    <>
      <PageHeader
        actions={<Freshness data={query.data} />}
        className="flex-col sm:flex-row"
        count={query.data?.count}
        description={`Spans and spend cover ${days} UTC days. Training and top-ups are lifetime totals. Activity is shared across each user's projects.`}
        title="Users"
      />
      {query.isPending ? (
        <LoadingState fullPage label="Loading users" />
      ) : query.isError ? (
        <Failure error={query.error} onRetry={query.refetch} />
      ) : (
        <SectionCard contentClassName="p-0" title="User activity">
          <Table>
            <TableHeader>
              <TableRow>
                <SortableHeader label="User" onSort={onSort} sort={sort} sortKey="email" />
                <TableHead>Milestone reached</TableHead>
                <TableHead>Subscription</TableHead>
                {COLUMNS.map((column) => (
                  <SortableHeader
                    key={column.key}
                    label={column.label}
                    onSort={onSort}
                    sort={sort}
                    sortKey={column.key}
                  />
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {query.data.results.map((user) => (
                <UserRow days={days} key={user.id} user={user} />
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

function UserRow({ user, days }: { user: User; days: number }) {
  const number = "font-mono tabular-nums";
  return (
    <TableRow>
      <TableCell className="max-w-64 truncate" title={user.email}>
        <Link
          className="hover:underline"
          params={{ accountId: String(user.id) }}
          search={{ days }}
          to="/accounts/$accountId"
        >
          {user.email}
        </Link>
      </TableCell>
      <TableCell>
        <Badge size="chip" variant={user.stage === "called_alias" ? "success" : "info"}>
          {milestoneLabel(user.stage)}
        </Badge>
      </TableCell>
      <TableCell>
        <Badge
          size="chip"
          variant={
            user.subscription_status === "active"
              ? "success"
              : user.subscription_status === "past_due" || user.subscription_status === "unpaid"
                ? "error"
                : user.subscription_status === "incomplete"
                  ? "warning"
                  : "neutral"
          }
        >
          {user.subscription_status ? humanize(user.subscription_status) : "Free"}
        </Badge>
      </TableCell>
      <TableCell className="whitespace-nowrap">{formatDate(user.date_joined)}</TableCell>
      <TableCell className={number}>{formatInt(user.projects)}</TableCell>
      <TableCell className={number}>{formatInt(user.seats)}</TableCell>
      <TableCell className="whitespace-nowrap">{formatAgo(user.last_span_at)}</TableCell>
      <TableCell className={number}>{formatInt(user.spans)}</TableCell>
      <TableCell className={number}>{formatInt(user.training_jobs)}</TableCell>
      <TableCell className={number}>{formatInt(user.live_deployments)}</TableCell>
      <TableCell className={number}>{formatUsd(user.spend_usd)}</TableCell>
      <TableCell className={number}>{formatUsd(user.topups_usd)}</TableCell>
      <TableCell className={`${number} ${Number(user.balance_usd) < 5 ? "text-warning" : ""}`}>
        {formatUsd(user.balance_usd)}
      </TableCell>
    </TableRow>
  );
}
