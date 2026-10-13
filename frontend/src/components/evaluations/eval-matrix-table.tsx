import { useState } from "react";

import { toast } from "sonner";

import { MATRIX_TYPE_LABEL } from "@/components/evaluations/matrix-row-label";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { DismissibleAlert } from "@/components/ui/dismissible-alert";
import { EmptyState } from "@/components/ui/empty-state";
import { Icon } from "@/components/ui/icons";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { SearchInput } from "@/components/ui/search-input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { LoadingState } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { useCreateEvalMatrixRowMutation, useEvalMatrixQuery } from "@/hooks/use-evaluations";
import { useGuestGate } from "@/hooks/use-guest-gate";
import { errorMessage } from "@/lib/notify";
import type { EvalMatrix, EvalMatrixTypeEnum } from "@/openapi";

const SOURCE_LABEL: Record<string, string> = {
  deterministic: "Python check",
  llm: "Expectation",
};

const SOURCE_PLACEHOLDER: Record<string, string> = {
  deterministic: 'def evaluate(span):\n    return True, "reason"',
  llm: "The answer cites the policy it relies on.",
};

/** Project-wide rows apply to every capability; a capability page passes its
 *  id so new rows are scoped to it. */
export function EvalMatrixTable({
  projectId,
  capabilityId,
}: {
  projectId: string;
  capabilityId?: string;
}) {
  const matrix = useEvalMatrixQuery(projectId, capabilityId);
  const guard = useGuestGate();
  const [search, setSearch] = useState("");
  const [creating, setCreating] = useState(false);
  const [viewing, setViewing] = useState<EvalMatrix | null>(null);
  const query = search.trim().toLowerCase();
  const rows = (matrix.data ?? []).filter((row) =>
    `${row.name} ${row.type} ${row.spanType ?? ""} ${row.spanName ?? ""}`
      .toLowerCase()
      .includes(query)
  );

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-3">
        <SearchInput
          className="min-w-[200px] flex-1"
          label="Search matrix rows"
          onChange={(e) => setSearch(e.target.value)}
          onClear={() => setSearch("")}
          placeholder="Search matrix…"
          value={search}
        />
        <Button onClick={guard(() => setCreating(true))}>
          <Icon.add />
          New row
        </Button>
      </div>
      {matrix.isLoading ? (
        <LoadingState />
      ) : matrix.error ? (
        <DismissibleAlert
          error={new Error(errorMessage(matrix.error, "Couldn't load the eval matrix."))}
          variant="destructive"
        />
      ) : rows.length === 0 ? (
        <EmptyState
          className="rounded-md border border-dashed bg-wash-subtle"
          icon={Icon.evaluations}
          iconClassName="invert [image-rendering:pixelated]"
          size="section"
          title="No matrix rows"
        />
      ) : (
        <div className="overflow-auto rounded-md border border-border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Applies to</TableHead>
                <TableHead>Scope</TableHead>
                <TableHead className="w-20 text-center">Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TableRow key={row.id}>
                  <TableCell className="max-w-xs">
                    <button
                      className="truncate text-left font-mono text-sm hover:underline focus-visible:underline focus-visible:outline-none"
                      onClick={() => setViewing(row)}
                      title={row.name}
                      type="button"
                    >
                      {row.name}
                    </button>
                  </TableCell>
                  <TableCell>
                    <Badge size="chip" variant="neutral">
                      {MATRIX_TYPE_LABEL[row.type] ?? row.type}
                    </Badge>
                  </TableCell>
                  <TableCell className="font-mono text-xs text-muted-foreground">
                    {[row.spanType, row.spanName].filter(Boolean).join(" · ") || "Every span"}
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {row.capability ? "Capability" : "Project-wide"}
                  </TableCell>
                  <TableCell className="text-center">
                    <Badge size="chip" variant={row.enabled === false ? "neutral" : "default"}>
                      {row.enabled === false ? "Off" : "On"}
                    </Badge>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
      <Dialog onOpenChange={(open) => !open && setCreating(false)} open={creating}>
        {creating && (
          <MatrixRowForm
            capabilityId={capabilityId}
            onClose={() => setCreating(false)}
            projectId={projectId}
          />
        )}
      </Dialog>
      <MatrixRowDetail onClose={() => setViewing(null)} row={viewing} />
    </div>
  );
}

function MatrixRowDetail({ row, onClose }: { row: EvalMatrix | null; onClose: () => void }) {
  return (
    <Dialog onOpenChange={(open) => !open && onClose()} open={!!row}>
      <DialogContent size="lg">
        <DialogHeader>
          <div>
            <DialogTitle className="font-mono">{row?.name}</DialogTitle>
            <DialogDescription>
              {[
                row ? (MATRIX_TYPE_LABEL[row.type] ?? row.type) : "",
                row?.capability ? "Capability" : "Project-wide",
                [row?.spanType, row?.spanName].filter(Boolean).join(" · ") || "Every span",
              ].join(" · ")}
            </DialogDescription>
          </div>
        </DialogHeader>
        <DialogBody>
          <pre className="whitespace-pre-wrap break-words rounded-sm border border-border bg-muted p-3 font-mono text-xs">
            {row?.source}
          </pre>
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}

function MatrixRowForm({
  projectId,
  capabilityId,
  onClose,
}: {
  projectId: string;
  capabilityId?: string;
  onClose: () => void;
}) {
  const create = useCreateEvalMatrixRowMutation();
  const [name, setName] = useState("");
  const [type, setType] = useState<EvalMatrixTypeEnum>("llm");
  const [source, setSource] = useState("");
  const [spanType, setSpanType] = useState("");
  const [spanName, setSpanName] = useState("");
  const [projectWide, setProjectWide] = useState(!capabilityId);
  const [error, setError] = useState<Error | null>(null);
  const pending = create.isPending;
  const ready = !!name.trim() && !!source.trim() && !pending;

  async function submit() {
    if (!ready) return;
    setError(null);
    const body = {
      capability: projectWide ? null : (capabilityId ?? null),
      name: name.trim(),
      source,
      spanName: spanName.trim(),
      spanType: spanType.trim(),
      type,
    };
    try {
      await create.mutateAsync({ ...body, project: projectId });
      toast.success("Row created");
      onClose();
    } catch (err) {
      setError(new Error(errorMessage(err, "Couldn't save the row.")));
    }
  }

  return (
    <DialogContent showCloseButton={!pending} size="lg">
      <DialogHeader>
        <div>
          <DialogTitle>New matrix row</DialogTitle>
          <DialogDescription>
            {type === "llm"
              ? "An LLM judge scores each output against the expectation."
              : "evaluate(span) runs in a sandbox and returns a score, a bool, or None to abstain."}
          </DialogDescription>
        </div>
      </DialogHeader>
      <DialogBody className="space-y-4">
        <fieldset className="grid gap-4 sm:grid-cols-2" disabled={pending}>
          <div className="space-y-2">
            <Label htmlFor="matrix-name">Name</Label>
            <Input
              id="matrix-name"
              maxLength={255}
              onChange={(e) => setName(e.target.value)}
              value={name}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="matrix-type">Type</Label>
            <Select onValueChange={(v) => setType(v as EvalMatrixTypeEnum)} value={type}>
              <SelectTrigger className="w-full" id="matrix-type" size="default">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {Object.entries(MATRIX_TYPE_LABEL).map(([value, label]) => (
                  <SelectItem key={value} value={value}>
                    {label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2 sm:col-span-2">
            <Label htmlFor="matrix-source">{SOURCE_LABEL[type]}</Label>
            <Textarea
              className="min-h-40 font-mono text-xs"
              id="matrix-source"
              onChange={(e) => setSource(e.target.value)}
              placeholder={SOURCE_PLACEHOLDER[type]}
              value={source}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="matrix-span-type">Span type</Label>
            <Input
              id="matrix-span-type"
              onChange={(e) => setSpanType(e.target.value)}
              placeholder="Every span"
              value={spanType}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="matrix-span-name">Span name</Label>
            <Input
              id="matrix-span-name"
              onChange={(e) => setSpanName(e.target.value)}
              placeholder="Every span"
              value={spanName}
            />
          </div>
          {capabilityId && (
            <label className="flex items-center gap-2 text-sm sm:col-span-2">
              <Switch checked={projectWide} onCheckedChange={setProjectWide} size="sm" />
              Project-wide
            </label>
          )}
        </fieldset>
        <DismissibleAlert error={error} variant="destructive" />
      </DialogBody>
      <DialogFooter>
        <Button disabled={pending} onClick={onClose} variant="secondary">
          Cancel
        </Button>
        <Button disabled={!ready} onClick={() => void submit()}>
          {pending ? "Creating…" : "Create row"}
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}
