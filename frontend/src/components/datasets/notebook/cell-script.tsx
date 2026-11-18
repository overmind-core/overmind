import { useState } from "react";

import { useInfiniteQuery, useQuery } from "@tanstack/react-query";

import apiClient from "@/client";
import { Field } from "@/components/datasets/notebook/field";
import { ScriptCode } from "@/components/datasets/notebook/script-code";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { LoadingState, Spinner } from "@/components/ui/spinner";
import type { Cell, PackageSource } from "@/openapi";

/** The step's entrypoint, and any other file of its retained package on request. */
export function CellScript({ cell }: { cell: Cell }) {
  const entrypoint = cell.transformation.entrypoint;
  const [selected, setSelected] = useState(entrypoint);
  const [listing, setListing] = useState(false);
  const packageId = cell.transformation._package;
  const other = selected !== entrypoint;
  const inventory = useQuery({
    enabled: listing && !!packageId,
    queryFn: () =>
      apiClient.datasetPipelinePackages.datasetPipelinePackagesRetrieve({ id: packageId! }),
    queryKey: ["dataset-package", packageId],
    staleTime: Infinity,
  });
  const file = useInfiniteQuery({
    enabled: other && !!packageId,
    getNextPageParam: (page: PackageSource) => page.nextOffset ?? undefined,
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      apiClient.datasetPipelinePackages.datasetPipelinePackagesSourceRetrieve({
        file: selected,
        id: packageId!,
        limit: 8000,
        offset: pageParam,
      }),
    queryKey: ["dataset-package-source", packageId, selected],
    staleTime: Infinity,
  });
  const content = file.data?.pages.map((page) => page.content).join("") ?? "";
  const files = inventory.data?.inventory.map((item) => item.path) ?? [entrypoint];
  return (
    <div className="nodrag nowheel px-3 pt-1 pb-2.5">
      {packageId && (
        <div className="mb-2 flex min-w-0 flex-wrap items-center gap-2">
          <Select
            onOpenChange={(open) => open && setListing(true)}
            onValueChange={setSelected}
            value={selected}
          >
            <SelectTrigger
              aria-label={`Package file for ${cell.title}`}
              className="max-w-full font-mono"
              size="xs"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {(files.includes(selected) ? files : [selected, ...files]).map((path) => (
                <SelectItem className="font-mono" key={path} value={path}>
                  {path}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Field label="Revision">{cell.transformation.revision}</Field>
        </div>
      )}
      {!other ? (
        <ScriptCode aria-label={`Script of ${cell.title}`} source={cell.script} />
      ) : (
        <>
          {file.isLoading && <LoadingState label="Loading retained source" />}
          {file.isError && (
            <div className="flex items-center gap-2 text-xs text-muted-foreground" role="alert">
              Retained source could not be loaded.
              <Button onClick={() => void file.refetch()} size="xs" variant="outline">
                Retry
              </Button>
            </div>
          )}
          {file.data && (
            <>
              <ScriptCode aria-label={`${selected} retained source`} source={content} />
              {file.hasNextPage && (
                <Button
                  className="mt-2"
                  disabled={file.isFetchingNextPage}
                  onClick={() => void file.fetchNextPage()}
                  size="xs"
                  variant="outline"
                >
                  {file.isFetchingNextPage ? <Spinner size="sm" /> : null}
                  Load more source
                </Button>
              )}
            </>
          )}
        </>
      )}
    </div>
  );
}
