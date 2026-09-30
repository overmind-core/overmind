import { useState } from "react";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import api from "@/client";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import { errorMessage } from "@/lib/notify";
import type { CapabilityList, TraceGroup } from "@/openapi";

function GroupRow({
  group,
  projectId,
  capabilities,
  value,
  onChange,
  disabled,
}: {
  group: TraceGroup;
  projectId: string;
  capabilities: CapabilityList[];
  value: string;
  onChange: (value: string) => void;
  disabled: boolean;
}) {
  return (
    <Card className="p-4">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <p className="break-words text-sm font-medium">{group.name}</p>
          <p className="mt-1 text-xs text-muted-foreground">
            {group.traceCount.toLocaleString()} traces ·{" "}
            {group.evidence.tools.length
              ? group.evidence.tools.join(" · ")
              : `${group.evidence.spanCount} spans per trace`}
          </p>
          {group.evidence.incomplete && (
            <p className="mt-1 text-xs text-warning">Incomplete trace structure</p>
          )}
          {group.evidence.agentSteps > 1 && (
            <p className="mt-1 text-xs text-warning">
              {group.evidence.agentSteps} agent steps per trace
            </p>
          )}
        </div>
        <div className="w-full shrink-0 sm:w-56">
          <Select disabled={disabled} onValueChange={onChange} value={value}>
            <SelectTrigger aria-label={`Capability for ${group.name}`}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="unassigned">Leave unassigned</SelectItem>
              {capabilities.map((capability) => (
                <SelectItem key={capability.id} value={capability.id}>
                  {capability.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>
      <details className="mt-3 text-xs">
        <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
          View examples and pattern
        </summary>
        <div className="mt-3 space-y-3">
          <div className="flex flex-wrap gap-3">
            {group.sampleTraceIds.map((traceId, i) => (
              <Link
                className="underline underline-offset-4"
                key={traceId}
                params={{ traceId }}
                search={{ projectId }}
                target="_blank"
                to="/observability/$traceId"
              >
                Example {i + 1}
              </Link>
            ))}
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <p className="mb-1 text-muted-foreground">Input fields</p>
              <pre className="whitespace-pre-wrap break-words">
                {JSON.stringify(group.evidence.inputShape, null, 2)}
              </pre>
            </div>
            <div>
              <p className="mb-1 text-muted-foreground">Output fields</p>
              <pre className="whitespace-pre-wrap break-words">
                {JSON.stringify(group.evidence.outputShape, null, 2)}
              </pre>
            </div>
          </div>
        </div>
      </details>
    </Card>
  );
}

interface ReviewProps {
  credentialId: string;
  projectId: string;
  name: string;
  open: boolean;
  onClose: () => void;
}

export function ConnectorReviewDialog(props: ReviewProps) {
  return props.open ? <ReviewDialogContent {...props} key={props.credentialId} /> : null;
}

function ReviewDialogContent({ credentialId, projectId, name, onClose }: ReviewProps) {
  const qc = useQueryClient();
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const groups = useQuery({
    queryFn: async () => {
      const results: TraceGroup[] = [];
      let page = 1;
      while (true) {
        const response = await api.connectorCredentials.connectorCredentialsGroupsList({
          id: credentialId,
          page,
          pageSize: 100,
          pendingOnly: true,
        });
        results.push(...response.results);
        if (!response.next) return results;
        page++;
      }
    },
    queryKey: ["connector-review", credentialId],
    refetchOnMount: "always",
    refetchOnReconnect: false,
    refetchOnWindowFocus: false,
    // Revisions and drafts must describe the same snapshot until explicit reload.
    staleTime: 0,
  });
  const capabilities = useQuery({
    queryFn: async () => {
      const results: CapabilityList[] = [];
      let page = 1;
      while (true) {
        const response = await api.capabilities.capabilitiesList({
          ordering: "name",
          page,
          pageSize: 100,
          project: projectId,
        });
        results.push(...response.results.filter((capability) => capability.status === "current"));
        if (!response.next) return results;
        page++;
      }
    },
    queryKey: ["connector-capability-options", projectId],
    refetchOnWindowFocus: false,
  });
  const selection = (group: TraceGroup) =>
    drafts[group.id] ??
    (!group.mixed && capabilities.data?.some((capability) => capability.id === group.capabilityId)
      ? group.capabilityId!
      : "unassigned");
  const confirm = useMutation({
    mutationFn: () =>
      api.connectorCredentials.connectorCredentialsReviewCreate({
        id: credentialId,
        reviewTraceGroupsRequest: {
          assignments: (groups.data ?? []).map((group) => ({
            capabilityId: selection(group) === "unassigned" ? null : selection(group),
            expectedRevision: group.revision,
            groupId: group.id,
          })),
        },
      }),
    onError: async (failure) =>
      setError(await errorMessage(failure, "Could not confirm assignments.")),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["connector-credentials", projectId] });
      qc.invalidateQueries({ queryKey: ["connector-review", credentialId] });
      qc.invalidateQueries({ queryKey: ["traces"] });
      onClose();
    },
  });
  const loading = groups.isFetching || capabilities.isPending;
  const failed = groups.isError || capabilities.isError;
  return (
    <Dialog
      onOpenChange={(open) => {
        if (!open && !confirm.isPending) onClose();
      }}
      open
    >
      <DialogContent showCloseButton={!confirm.isPending} size="lg">
        <DialogHeader>
          <DialogTitle>Review trace assignments</DialogTitle>
          <DialogDescription>
            {name} · Choices also apply to future traces with the same pattern.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-3">
          {error && (
            <Alert variant="destructive">
              {error}
              <Button
                onClick={() => {
                  setDrafts({});
                  setError(null);
                  groups.refetch();
                  capabilities.refetch();
                }}
                size="sm"
                variant="link"
              >
                Reload groups
              </Button>
            </Alert>
          )}
          {failed ? (
            <Alert variant="destructive">
              Could not load trace groups or capabilities.
              <Button
                onClick={() => {
                  groups.refetch();
                  capabilities.refetch();
                }}
                size="sm"
                variant="link"
              >
                Retry
              </Button>
            </Alert>
          ) : loading ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Spinner size="sm" />
              Loading groups…
            </div>
          ) : !groups.data?.length ? (
            <p className="text-sm text-muted-foreground">No patterns need review.</p>
          ) : (
            <>
              <p className="text-xs text-muted-foreground">
                {groups.data.length.toLocaleString()} patterns ·{" "}
                {groups.data.reduce((total, group) => total + group.traceCount, 0).toLocaleString()}{" "}
                traces
              </p>
              {!capabilities.data?.length && (
                <p className="text-sm text-muted-foreground">
                  No capabilities in this project. Groups can be left unassigned.
                </p>
              )}
              {groups.data.map((group) => (
                <GroupRow
                  capabilities={capabilities.data ?? []}
                  disabled={confirm.isPending}
                  group={group}
                  key={group.id}
                  onChange={(value) => setDrafts((current) => ({ ...current, [group.id]: value }))}
                  projectId={projectId}
                  value={selection(group)}
                />
              ))}
            </>
          )}
        </DialogBody>
        <DialogFooter className="flex-wrap">
          <Button disabled={confirm.isPending} onClick={onClose} variant="secondary">
            Cancel
          </Button>
          <Button
            disabled={loading || failed || !!error || !groups.data?.length || confirm.isPending}
            onClick={() => confirm.mutate()}
          >
            {confirm.isPending && <Spinner size="sm" />}Confirm all assignments
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
