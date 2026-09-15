import { useEffect, useMemo, useState } from "react";

import { createFileRoute, Link } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";

import { CreateProjectDialog } from "@/components/create-project";
import { NoProjectsEmptyState } from "@/components/project-required-empty-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DataTable } from "@/components/ui/data-table";
import { DateTime } from "@/components/ui/datetime";
import { Icon } from "@/components/ui/icons";
import { PageHeader } from "@/components/ui/page-header";
import { PageShell } from "@/components/ui/page-shell";
import { selectProject } from "@/hooks/use-project-search-sync";
import { useProjectsList } from "@/hooks/use-projects";
import { useOnboardingStatus } from "@/hooks/use-query";
import { parseDrfOrdering } from "@/lib/drf-ordering";
import type { ProjectsSearch } from "@/lib/schemas";
import { projectsSearchSchema } from "@/lib/schemas";

export const Route = createFileRoute("/_auth/projects/")({
  component: ProjectsPage,
  validateSearch: projectsSearchSchema,
});

type SortField = ProjectsSearch["sortBy"];

type ProjectRow = NonNullable<ReturnType<typeof useProjectsList>["data"]>["projects"][number];

/** Sorting is client-side, but the column ids double as the `sortBy` values so
 *  DataTable's DRF-shaped ordering string round-trips through the URL. */
function MembersCell({ project }: { project: ProjectRow }) {
  const myEmail = useOnboardingStatus().data?.email;
  if (!myEmail) {
    return (
      <span className="text-sm tabular-nums text-muted-foreground">{project.memberCount}</span>
    );
  }
  const others = project.memberEmails.filter((email) => email !== myEmail);
  return (
    <span className="text-sm text-muted-foreground" title={others.join(", ") || undefined}>
      {others.length === 0 ? "you" : `you + ${others.length}`}
    </span>
  );
}

const columns: ColumnDef<ProjectRow, unknown>[] = [
  {
    accessorKey: "name",
    cell: ({ row }) => (
      <span className="flex min-w-0 items-center gap-2">
        <span className="truncate font-mono text-sm">{row.original.name ?? "Unnamed"}</span>
        {row.original.memberCount > 1 && (
          <Badge size="chip" variant="secondary">
            Shared
          </Badge>
        )}
      </span>
    ),
    header: "Name",
    id: "name",
    meta: { label: "Name", orderingField: "name" },
    size: 280,
  },
  {
    accessorKey: "memberCount",
    cell: ({ row }) => <MembersCell project={row.original} />,
    header: "Members",
    id: "memberCount",
    meta: { label: "Members", orderingField: "memberCount" },
    size: 120,
  },
  {
    accessorKey: "createdAt",
    cell: ({ row }) => (
      <DateTime className="text-sm text-muted-foreground" value={row.original.createdAt} />
    ),
    header: "Created",
    id: "createdAt",
    meta: { label: "Created", orderingField: "createdAt" },
    size: 160,
  },
  {
    cell: ({ row }) => (
      <Button
        aria-label={`Add member to ${row.original.name ?? "project"}`}
        asChild
        onClick={(e) => e.stopPropagation()}
        size="xs"
        variant="secondary"
      >
        <Link
          onClick={() => selectProject(row.original.projectId)}
          params={{ projectId: row.original.projectId }}
          search={{ projectId: row.original.projectId, section: "members" }}
          to="/projects/$projectId"
        >
          <Icon.addUser />
          Add member
        </Link>
      </Button>
    ),
    enableResizing: false,
    header: "Actions",
    id: "actions",
    meta: { label: "Actions" },
    size: 140,
  },
];

function ProjectsPage() {
  const navigate = Route.useNavigate();
  const searchParams = Route.useSearch();
  const { sortBy, sortDirection } = searchParams;
  const { data, isLoading, error } = useProjectsList();
  const [createOpen, setCreateOpen] = useState(false);

  useEffect(() => {
    if (!searchParams.createProject) {
      return;
    }
    setCreateOpen(true);
    navigate({
      replace: true,
      search: { sortBy, sortDirection },
    });
  }, [searchParams.createProject, sortBy, sortDirection, navigate]);

  const createProjectDialog = (
    <CreateProjectDialog onOpenChange={setCreateOpen} open={createOpen} />
  );

  const ordering = `${sortDirection === "desc" ? "-" : ""}${sortBy}`;

  // Cycling a column past descending clears the ordering, but the list still
  // needs an order — fall back to the default.
  const handleOrderingChange = (next: string | undefined) => {
    const parsed = parseDrfOrdering(next);
    navigate({
      search: (prev) => ({
        ...prev,
        sortBy: (parsed?.field as SortField) ?? "createdAt",
        sortDirection: parsed?.dir ?? "desc",
      }),
    });
  };

  const sortedProjects = useMemo(() => {
    if (!data?.projects) return [];
    return [...data.projects].sort((a, b) => {
      let cmp = 0;
      switch (sortBy) {
        case "name":
          cmp = (a.name ?? "").localeCompare(b.name ?? "");
          break;
        case "memberCount":
          cmp = (a.memberCount ?? 0) - (b.memberCount ?? 0);
          break;
        default:
          cmp =
            (a.createdAt ? new Date(a.createdAt).getTime() : 0) -
            (b.createdAt ? new Date(b.createdAt).getTime() : 0);
      }
      return sortDirection === "desc" ? -cmp : cmp;
    });
  }, [data, sortBy, sortDirection]);

  const header = (
    <PageHeader
      actions={
        <Button onClick={() => setCreateOpen(true)} type="button">
          <Icon.projectAdd />
          New project
        </Button>
      }
      description="Create and manage workspace projects."
      icon={<Icon.project aria-hidden className="size-6 shrink-0 [image-rendering:pixelated]" />}
      title="Projects"
    />
  );

  return (
    <PageShell header={header} variant="full">
      {createProjectDialog}

      <DataTable<ProjectRow>
        columns={columns}
        data={{ count: sortedProjects.length, results: sortedProjects }}
        emptyState={<NoProjectsEmptyState />}
        error={error}
        getRowId={(row) => row.projectId}
        hidePagination
        isLoading={isLoading}
        onOrderingChange={handleOrderingChange}
        onPageChange={() => {}}
        onPageSizeChange={() => {}}
        onRowClick={(project) => {
          selectProject(project.projectId);
          navigate({
            params: { projectId: project.projectId },
            search: (prev) => ({ ...prev, projectId: project.projectId }),
            to: "/projects/$projectId",
          });
        }}
        ordering={ordering}
        page={1}
        pageSize={8}
        storageKey="projects:table-cols"
      />
    </PageShell>
  );
}
