import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";

import { Failure } from "~/components/failure";
import { Freshness } from "~/components/freshness";
import { type Account, type Page, salesGet } from "~/lib/api";
import { formatAgo, formatDate, formatInt, formatUsd, humanize } from "~/lib/format";

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
import { cn } from "@/lib/utils";

const COLUMNS = [
  { key: "date_joined", label: "Joined" },
  { key: "projects", label: "Projects" },
  { key: "seats", label: "Seats" },
  { key: "last_span_at", label: "Last span" },
  { key: "spans", label: "Spans" },
  { key: "spend_usd", label: "Spend" },
  { key: "topups_usd", label: "Top-ups" },
  { key: "balance_usd", label: "Balance" },
  { key: "training_jobs", label: "Training" },
  { key: "live_deployments", label: "Live" },
] as const;

type SortKey = "email" | (typeof COLUMNS)[number]["key"];
const SORT_KEYS: string[] = ["email", ...COLUMNS.map((column) => column.key)];
const LOW_BALANCE_USD = 5;

const SUBSCRIPTION_VARIANT: Record<string, "success" | "warning" | "error" | "neutral"> = {
  active: "success",
  canceled: "neutral",
  incomplete: "warning",
  past_due: "error",
  unpaid: "error",
};

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
    queryFn: () => salesGet<Page<Account>>("accounts", { days, ordering, page, page_size }),
    queryKey: ["accounts", days, ordering, page, page_size],
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
        count={query.data?.count}
        description={`Spans and spend cover the last ${days} days. Top-ups and balance are lifetime.`}
        title="Accounts"
      />
      {query.isPending ? (
        <LoadingState fullPage label="Loading accounts" />
      ) : query.isError ? (
        <Failure error={query.error} onRetry={query.refetch} />
      ) : (
        <SectionCard contentClassName="p-0" title="Customer accounts">
          <Table>
            <TableHeader>
              <TableRow>
                <SortableHeader label="Email" onSort={onSort} sort={sort} sortKey="email" />
                <TableHead>Plan</TableHead>
                <TableHead>Stage</TableHead>
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
              {query.data.results.map((account) => (
                <AccountRow account={account} days={days} key={account.id} />
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

function AccountRow({ account, days }: { account: Account; days: number }) {
  const status = account.subscription_status;
  const lowBalance = Number(account.balance_usd) < LOW_BALANCE_USD;
  const number = "font-mono tabular-nums";
  return (
    <TableRow>
      <TableCell className="max-w-64 truncate" title={account.email}>
        <Link
          className="hover:underline"
          params={{ accountId: String(account.id) }}
          search={{ days }}
          to="/accounts/$accountId"
        >
          {account.email}
        </Link>
      </TableCell>
      <TableCell>
        <Badge
          size="chip"
          variant={status ? (SUBSCRIPTION_VARIANT[status] ?? "neutral") : "neutral"}
        >
          {status ? humanize(status) : "Free"}
        </Badge>
      </TableCell>
      <TableCell>
        <Badge size="chip" variant={account.stage === "called_alias" ? "success" : "info"}>
          {humanize(account.stage)}
        </Badge>
      </TableCell>
      <TableCell className="whitespace-nowrap">{formatDate(account.date_joined)}</TableCell>
      <TableCell className={number}>{formatInt(account.projects)}</TableCell>
      <TableCell className={number}>{formatInt(account.seats)}</TableCell>
      <TableCell className="whitespace-nowrap">{formatAgo(account.last_span_at)}</TableCell>
      <TableCell className={number}>{formatInt(account.spans)}</TableCell>
      <TableCell className={number}>{formatUsd(account.spend_usd)}</TableCell>
      <TableCell className={number}>{formatUsd(account.topups_usd)}</TableCell>
      <TableCell className={cn(number, lowBalance && "text-warning")}>
        {formatUsd(account.balance_usd)}
      </TableCell>
      <TableCell className={number}>{formatInt(account.training_jobs)}</TableCell>
      <TableCell className={number}>{formatInt(account.live_deployments)}</TableCell>
    </TableRow>
  );
}
