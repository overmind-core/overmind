import { useState } from "react";

import { useInfiniteQuery, useQuery } from "@tanstack/react-query";

import apiClient from "@/client";
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

export function CellScript({ cell }: { cell: Cell }) {
  const [browsing, setBrowsing] = useState(false);
  const [selected, setSelected] = useState(cell.transformation.entrypoint);
  const packageId = cell.transformation._package;
  const inventory = useQuery({
    enabled: browsing && !!packageId,
    queryFn: () =>
      apiClient.datasetPipelinePackages.datasetPipelinePackagesRetrieve({ id: packageId! }),
    queryKey: ["dataset-package", packageId],
    staleTime: Infinity,
  });
  const file = useInfiniteQuery({
    enabled: browsing && !!packageId && !!selected,
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
  return (
    <div className="nodrag nowheel px-3 pt-1 pb-2.5">
      {packageId && (
        <div className="mb-2 flex min-w-0 flex-wrap items-center gap-2">
          <Button onClick={() => setBrowsing(!browsing)} size="xs" variant="outline">
            {browsing ? "Step script" : "Package files"}
          </Button>
          {browsing && inventory.data ? (
            <Select onValueChange={setSelected} value={selected}>
              <SelectTrigger
                aria-label={`Package file for ${cell.title}`}
                className="max-w-full"
                size="xs"
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {inventory.data.inventory.map((item) => (
                  <SelectItem key={item.path} value={item.path}>
                    {item.path} · {item.bytes.toLocaleString()} bytes
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : (
            <span className="break-all font-mono text-xs text-muted-foreground">
              {cell.transformation.entrypoint}
            </span>
          )}
          <span className="text-xs text-muted-foreground">
            Revision {cell.transformation.revision}
          </span>
        </div>
      )}
      {!browsing ? (
        <ScriptCode aria-label={`Script of ${cell.title}`} source={cell.script} />
      ) : (
        <>
          {(inventory.isLoading || file.isLoading) && (
            <LoadingState label="Loading retained source" />
          )}
          {(inventory.isError || file.isError) && (
            <div className="flex items-center gap-2 text-xs text-destructive" role="alert">
              Retained source could not be loaded.
              <Button
                onClick={() => {
                  void inventory.refetch();
                  void file.refetch();
                }}
                size="xs"
                variant="outline"
              >
                Retry
              </Button>
            </div>
          )}
          {file.data && (
            <>
              <p className="mb-2 break-all font-mono text-xs text-muted-foreground">
                SHA-256 {file.data.pages[0].sha256}
              </p>
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
