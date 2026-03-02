import { useMemo, useState } from "react";

import { createFileRoute, Link } from "@tanstack/react-router";
import { ArrowDown, ArrowUp, ChevronsVertical as ChevronsUpDown, EyeOff, Folder as FolderKanban } from "pixelarticons/react";

import { CreateProjectDialog } from "@/components/create-project";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { useProjectsList } from "@/hooks/use-projects";
import { formatTimestamp } from "@/lib/formatters";
import { projectsSearchSchema } from "@/lib/schemas";
import type { ProjectsSearch } from "@/lib/schemas";

export const Route = createFileRoute("/_auth/projects/")({
  component: ProjectsPage,
  validateSearch: projectsSearchSchema,
});

type SortField = ProjectsSearch["sortBy"];

function SortableHead({
  field,
  label,
  sortBy,
  sortDirection,
  onSort,
  onHide,
  className,
}: {
  field: SortField;
  label: string;
  sortBy: SortField;
  sortDirection: "asc" | "desc";
  onSort: (field: SortField) => void;
  onHide: (field: SortField) => void;
  className?: string;
}) {
  const isActive = sortBy === field;
  return (
    <TableHead className={className}>
      <div className="group flex items-center gap-0.5">
        <Button
          className="-ml-3 h-8 gap-1"
          onClick={() => onSort(field)}
          size="sm"
          variant="ghost"
        >
          {label}
          {isActive ? (
            sortDirection === "asc" ? (
              <ArrowUp className="size-3.5" />
            ) : (
              <ArrowDown className="size-3.5" />
            )
          ) : (
            <ChevronsUpDown className="size-3.5 text-muted-foreground/60" />
          )}
        </Button>
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                className="size-6 opacity-0 transition-opacity group-hover:opacity-100"
                onClick={() => onHide(field)}
                size="icon"
                variant="ghost"
              >
                <EyeOff className="size-3.5 text-muted-foreground/60" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>Hide column</TooltipContent>
          </Tooltip>
        </TooltipProvider>
      </div>
    </TableHead>
  );
}

function ProjectsPage() {
  const navigate = Route.useNavigate();
  const searchParams = Route.useSearch();
  const { sortBy, sortDirection } = searchParams;
  const { data, isLoading, error } = useProjectsList();
  const [hiddenCols, setHiddenCols] = useState<Set<SortField>>(new Set());

  const setSearch = (updates: Partial<ProjectsSearch>) => {
    navigate({
      search: (prev) => ({ ...prev, ...updates }),
    });
  };

  const handleSort = (field: SortField) => {
    if (sortBy === field) {
      setSearch({ sortDirection: sortDirection === "asc" ? "desc" : "asc" });
    } else {
      setSearch({ sortBy: field, sortDirection: "asc" });
    }
  };

  const handleHide = (field: SortField) => {
    setHiddenCols((prev) => new Set([...prev, field]));
  };

  const sortedProjects = useMemo(() => {
    if (!data?.projects) return [];
    return [...data.projects].sort((a, b) => {
      let cmp = 0;
      switch (sortBy) {
        case "name":
          cmp = (a.name ?? "").localeCompare(b.name ?? "");
          break;
        case "description":
          cmp = (a.description ?? "").localeCompare(b.description ?? "");
          break;
        case "organisationName":
          cmp = (a.organisationName ?? "").localeCompare(b.organisationName ?? "");
          break;
        case "createdAt":
        default:
          cmp = (a.createdAt?.getTime() ?? 0) - (b.createdAt?.getTime() ?? 0);
      }
      return sortDirection === "desc" ? -cmp : cmp;
    });
  }, [data, sortBy, sortDirection]);

  if (isLoading) {
    return <ProjectsSkeleton />;
  }

  if (error) {
    return <Alert variant="destructive">Failed to load projects: {(error as Error).message}</Alert>;
  }

  if (!data || data.projects?.length === 0) {
    return (
      <div className="space-y-6 pb-8">
        <div className="flex flex-col items-center justify-center py-12 text-center">
          <FolderKanban className="mb-4 size-12 text-muted-foreground" />
          <p className="text-muted-foreground">No projects found.</p>
          <p className="mt-1 text-sm text-muted-foreground">
            Create a project or get invited to one.
          </p>
          <CreateProjectDialog
            trigger={<Button className="mt-4">Create your first project</Button>}
          />
        </div>
      </div>
    );
  }

  const sortProps = { onHide: handleHide, onSort: handleSort, sortBy, sortDirection };
  const show = (field: SortField) => !hiddenCols.has(field);

  return (
    <div className="page-wrapper">
      <div className="flex items-center justify-between">
        <CreateProjectDialog />
      </div>
      <Table>
        <TableHeader>
          <TableRow>
            {show("name") && <SortableHead field="name" label="Name" {...sortProps} />}
            {show("description") && (
              <SortableHead field="description" label="Description" {...sortProps} />
            )}
            {show("organisationName") && (
              <SortableHead
                className="hidden md:table-cell"
                field="organisationName"
                label="Organisation"
                {...sortProps}
              />
            )}
            {show("createdAt") && (
              <SortableHead
                className="hidden lg:table-cell"
                field="createdAt"
                label="Created"
                {...sortProps}
              />
            )}
            <TableHead className="w-10" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {sortedProjects.map((project) => (
            <TableRow
              className="cursor-pointer hover:bg-muted/50"
              key={project.projectId}
              onClick={() =>
                navigate({
                  params: { projectId: project.projectId },
                  to: "/projects/$projectId/traces",
                })
              }
            >
              {show("name") && (
                <TableCell className="font-medium">{project.name ?? "Unnamed"}</TableCell>
              )}
              {show("description") && (
                <TableCell className="max-w-xs truncate text-sm text-muted-foreground">
                  {project.description || <span className="italic">No description</span>}
                </TableCell>
              )}
              {show("organisationName") && (
                <TableCell className="hidden text-sm text-muted-foreground md:table-cell">
                  {project.organisationName}
                </TableCell>
              )}
              {show("createdAt") && (
                <TableCell className="hidden text-sm text-muted-foreground lg:table-cell">
                  {project.createdAt ? formatTimestamp(project.createdAt.toISOString()) : "—"}
                </TableCell>
              )}
              <TableCell className="text-right">
                <Button
                  aria-label={`Details for ${project.name ?? "project"}`}
                  asChild
                  onClick={(e) => e.stopPropagation()}
                  size="sm"
                  variant="ghost"
                >
                  <Link params={{ projectId: project.projectId }} to="/projects/$projectId">
                    Details
                  </Link>
                </Button>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

const ProjectsSkeleton = () => {
  return (
    <div className="space-y-2">
      {[1, 2, 3].map((i) => (
        <Skeleton className="h-12 w-full" key={i} />
      ))}
    </div>
  );
};
