import { useEffect, useState } from "react";

import { createFileRoute } from "@tanstack/react-router";

import { InferenceToolbar } from "@/components/inference/inference-toolbar";
import { InferenceModelCard } from "@/components/inference/model-card";
import { ProjectRequiredEmptyState } from "@/components/project-required-empty-state";
import { DetailErrorState } from "@/components/route-error";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Icon } from "@/components/ui/icons";
import { PageHeader } from "@/components/ui/page-header";
import { PageShell } from "@/components/ui/page-shell";
import { LoadingState } from "@/components/ui/spinner";
import { TablePagination } from "@/components/ui/table-pagination";
import { useDeployedModelsQuery } from "@/hooks/use-inference";
import { type InferenceSearch, inferenceSearchSchema } from "@/lib/schemas";

const INFERENCE_HEADER = (
  <PageHeader
    description="Serve, monitor and call your fine-tuned models."
    icon={
      <Icon.inferenceTitle
        aria-hidden
        className="size-6 shrink-0 [image-rendering:pixelated] invert"
      />
    }
    title="Inference"
  />
);

export const Route = createFileRoute("/_auth/inference")({
  component: InferencePage,
  validateSearch: inferenceSearchSchema,
});

function InferencePage() {
  const {
    inf_deployment: deploymentType = "all",
    inf_ordering: ordering = "-created_at",
    inf_capability: capability,
    inf_search: search,
    page,
    page_size: pageSize,
    projectId,
  } = Route.useSearch();
  const navigate = Route.useNavigate();
  const [searchText, setSearchText] = useState(search);
  useEffect(() => {
    const next = searchText.trim();
    if (next === search) return;
    const timer = setTimeout(() => {
      void navigate({
        replace: true,
        resetScroll: false,
        search: (prev) => ({ ...prev, inf_search: next, page: 1 }),
      });
    }, 400);
    return () => clearTimeout(timer);
  }, [searchText, search, navigate]);

  // Back/forward wins over stale keystrokes; trim-equal keeps in-flight typing.
  useEffect(() => {
    setSearchText((cur) => (search === cur.trim() ? cur : search));
  }, [search]);

  const { data, isLoading, error, refetch } = useDeployedModelsQuery({
    capability: capability === "all" ? undefined : capability,
    deploymentType: deploymentType === "all" ? undefined : deploymentType,
    ordering,
    page,
    pageSize,
    projectId,
    search,
  });

  const hasActiveFilters = Boolean(
    searchText.trim() || deploymentType !== "all" || capability !== "all"
  );

  const patchFilters = (updates: Partial<InferenceSearch>) =>
    void navigate({
      replace: true,
      resetScroll: false,
      search: (prev) => ({ ...prev, ...updates, page: 1 }),
    });

  const handleClearFilters = () => {
    setSearchText("");
    patchFilters({
      inf_capability: "all",
      inf_deployment: "all",
      inf_search: "",
    });
  };

  if (!projectId) {
    return (
      <PageShell header={INFERENCE_HEADER} variant="full">
        <ProjectRequiredEmptyState />
      </PageShell>
    );
  }

  return (
    <PageShell header={INFERENCE_HEADER} variant="full">
      <div className="flex min-h-0 flex-1 flex-col">
        <div className="relative z-10 shrink-0">
          <InferenceToolbar
            capabilityFilter={capability}
            deploymentFilter={deploymentType}
            hasActiveFilters={hasActiveFilters}
            onCapabilityFilterChange={(value) => patchFilters({ inf_capability: value })}
            onClearFilters={handleClearFilters}
            onDeploymentFilterChange={(value) =>
              patchFilters({
                inf_deployment: value,
              })
            }
            onOrderingChange={(value) => patchFilters({ inf_ordering: value })}
            onSearchChange={setSearchText}
            onSearchClear={() => {
              setSearchText("");
              patchFilters({ inf_search: "" });
            }}
            ordering={ordering}
            projectId={projectId}
            resultCount={data?.count}
            search={searchText}
          />
          <div
            aria-hidden
            className="pointer-events-none absolute inset-x-0 top-full h-4 backdrop-blur-sm [mask-image:linear-gradient(to_bottom,black_0%,black_20%,transparent_100%)]"
          />
        </div>
        <div className="flex-1 space-y-4 overflow-y-auto py-4">
          {isLoading && <LoadingState label="Loading models" />}
          {error && (
            <DetailErrorState
              action={
                <Button onClick={() => void refetch()} variant="secondary">
                  Retry
                </Button>
              }
              error={error}
              fallback="Couldn't load models."
            />
          )}
          {data?.results.map((model) => (
            <InferenceModelCard key={model.id} model={model} projectId={projectId} />
          ))}
          {!isLoading && !error && !data?.results.length && (
            <EmptyState
              action={
                hasActiveFilters ? (
                  <Button onClick={handleClearFilters} variant="secondary">
                    Clear filters
                  </Button>
                ) : undefined
              }
              description={
                hasActiveFilters ? undefined : "Deploy a fine-tuned model from Training."
              }
              icon={Icon.inferenceTitle}
              title={hasActiveFilters ? "No matching models" : "No deployed models"}
            />
          )}
        </div>
        {data && (data.count > 0 || page > 1) && (
          <TablePagination
            count={data.count}
            onPageChange={(next) => void navigate({ search: (prev) => ({ ...prev, page: next }) })}
            onPageSizeChange={(size) =>
              void navigate({ search: (prev) => ({ ...prev, page: 1, page_size: size }) })
            }
            page={page}
            pageSize={pageSize}
          />
        )}
      </div>
    </PageShell>
  );
}
