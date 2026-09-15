import type { ReactNode } from "react";
import { useEffect, useState } from "react";

import { Link, useRouter } from "@tanstack/react-router";
import * as z from "zod";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Icon } from "@/components/ui/icons";
import { ApiError } from "@/lib/api-error";
import { hasChunkReloadFlag, isChunkLoadError, tryReloadOnceOnChunkError } from "@/lib/chunk-error";
import { errorMessage } from "@/lib/notify";

export function DetailErrorState({
  action,
  error,
  fallback,
}: {
  action?: ReactNode;
  error: unknown;
  fallback: string;
}) {
  return (
    <div className="space-y-4">
      <Alert variant="destructive">{errorMessage(error, fallback)}</Alert>
      {action ? <div>{action}</div> : null}
    </div>
  );
}

/**
 * Walks the `cause` chain because TanStack wraps `validateSearch` failures in a
 * SearchParamError, and duck-types on `issues` because `instanceof z.ZodError`
 * returns false when the bundle carries two copies of Zod.
 */
function findZodError(err: unknown, depth = 0): z.ZodError | null {
  if (!err || typeof err !== "object" || depth > 3) return null;
  if (err instanceof z.ZodError) return err;
  const issues = (err as { issues?: unknown }).issues;
  if (Array.isArray(issues) && issues.length > 0) return err as z.ZodError;
  return findZodError((err as { cause?: unknown }).cause, depth + 1);
}

export function RouteErrorFallback({ error, reset }: { error: unknown; reset?: () => void }) {
  const router = useRouter();
  const chunkError = isChunkLoadError(error);
  const awaitingChunkReload = chunkError && !hasChunkReloadFlag();
  const [reloading, setReloading] = useState(awaitingChunkReload);

  useEffect(() => {
    if (!awaitingChunkReload) return;
    tryReloadOnceOnChunkError(error);
    setReloading(true);
  }, [awaitingChunkReload, error]);

  if (reloading || awaitingChunkReload) return null;

  const notFound = error instanceof ApiError && error.status === 404;
  // A `validateSearch` failure carries the raw issue array as its `.message`,
  // and retrying the same URL throws again — hence its own title and no retry.
  const zodError = findZodError(error);
  const invalidLink = zodError !== null;

  if (chunkError) {
    return (
      <div className="flex h-full min-h-0 flex-1 items-center justify-center py-8">
        <div className="max-w-md space-y-3 text-center">
          <EmptyState
            action={
              <>
                <Button onClick={() => window.location.reload()}>Refresh page</Button>
                <Button asChild variant="outline">
                  <Link to="/">Go to home</Link>
                </Button>
              </>
            }
            description="The app was updated while you had it open. Refresh to load the latest version."
            icon={Icon.warning}
            title="New version available"
          />
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-1 items-center justify-center py-8">
      <div className="max-w-md space-y-3 text-center">
        <EmptyState
          action={
            <>
              {!invalidLink && (
                <Button
                  onClick={() => {
                    reset?.();
                    router.invalidate();
                  }}
                  variant="outline"
                >
                  Try again
                </Button>
              )}
              <Button asChild>
                <Link to="/">Go to home</Link>
              </Button>
            </>
          }
          description={
            invalidLink
              ? "That link isn't valid — part of the URL isn't in a format this page understands."
              : errorMessage(error, "This page hit an unexpected error.")
          }
          icon={Icon.warning}
          title={notFound ? "Not found" : invalidLink ? "Invalid link" : "Something went wrong"}
        />
        {invalidLink && (
          <details className="text-left text-muted-foreground text-xs">
            <summary className="cursor-pointer select-none text-center">Details</summary>
            <pre className="mt-2 overflow-x-auto rounded-md border border-border/60 bg-wash-raised p-3 text-xs">
              {JSON.stringify(zodError?.issues ?? error, null, 2)}
            </pre>
          </details>
        )}
      </div>
    </div>
  );
}

export function RouteNotFound() {
  return (
    <div className="flex h-full min-h-0 flex-1 items-center justify-center py-8">
      <EmptyState
        action={
          <Button asChild>
            <Link to="/">Go to home</Link>
          </Button>
        }
        description="This page doesn't exist or has moved."
        icon={Icon.warning}
        title="Page not found"
      />
    </div>
  );
}
