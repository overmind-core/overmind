import { useState } from "react";

import { toast } from "sonner";

import { CapabilityCombobox } from "@/components/capability-combobox";
import { EVALUATOR_KIND_LABEL } from "@/components/evaluations/evaluator-kind";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { DismissibleAlert } from "@/components/ui/dismissible-alert";
import { EmptyState } from "@/components/ui/empty-state";
import { Icon } from "@/components/ui/icons";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { SearchInput } from "@/components/ui/search-input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useCreateEvalSetMutation,
  useEvaluatorCatalogQuery,
  useProjectCapabilitiesQuery,
} from "@/hooks/use-evaluations";
import { errorMessage } from "@/lib/notify";
import type { EvaluatorCatalog } from "@/openapi";

function EvaluatorLabel({ evaluator }: { evaluator: EvaluatorCatalog }) {
  return (
    <>
      <span className="min-w-0 flex-1 truncate">{evaluator.displayName || evaluator.name}</span>
      <Badge size="chip" variant="neutral">
        {EVALUATOR_KIND_LABEL[evaluator.kind] ?? evaluator.kind}
      </Badge>
      {evaluator.capabilityName && (
        <span className="max-w-40 truncate text-xs text-muted-foreground">
          {evaluator.capabilityName}
        </span>
      )}
    </>
  );
}

export function CreateEvalSetDialog({ projectId }: { projectId: string }) {
  const [open, setOpen] = useState(false);
  return (
    <Dialog onOpenChange={setOpen} open={open}>
      <DialogTrigger asChild>
        <Button>
          <Icon.add />
          New eval set
        </Button>
      </DialogTrigger>
      {open && <CreateEvalSetForm onClose={() => setOpen(false)} projectId={projectId} />}
    </Dialog>
  );
}

function CreateEvalSetForm({ projectId, onClose }: { projectId: string; onClose: () => void }) {
  const catalog = useEvaluatorCatalogQuery(projectId);
  const capabilities = useProjectCapabilitiesQuery(projectId);
  const create = useCreateEvalSetMutation(projectId);
  const [name, setName] = useState("");
  const [capabilityId, setCapabilityId] = useState("");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<EvaluatorCatalog[]>([]);
  const [error, setError] = useState<Error | null>(null);
  const query = search.trim().toLowerCase();
  const visible = (catalog.data ?? []).filter((ev) =>
    `${ev.name} ${ev.displayName} ${EVALUATOR_KIND_LABEL[ev.kind]} ${ev.capabilityName ?? ""}`
      .toLowerCase()
      .includes(query)
  );
  const remove = (id: string) => setSelected((rows) => rows.filter((ev) => ev.id !== id));
  const ready = !!name.trim() && selected.length > 0 && !create.isPending;

  async function submit() {
    if (!ready) return;
    setError(null);
    try {
      await create.mutateAsync({
        capability: capabilityId || null,
        evaluatorIds: selected.map((ev) => ev.id),
        name: name.trim(),
        project: projectId,
      });
      toast.success("Eval set created");
      onClose();
    } catch (err) {
      setError(new Error(errorMessage(err, "Couldn't create eval set.")));
    }
  }

  return (
    <DialogContent
      onEscapeKeyDown={(event) => {
        if (create.isPending) event.preventDefault();
      }}
      onInteractOutside={(event) => {
        if (create.isPending) event.preventDefault();
      }}
      showCloseButton={!create.isPending}
      size="lg"
    >
      <DialogHeader>
        <div>
          <DialogTitle>New eval set</DialogTitle>
          <DialogDescription>Select evaluators from the library.</DialogDescription>
        </div>
      </DialogHeader>
      <DialogBody className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="eval-set-name">Name</Label>
            <Input
              disabled={create.isPending}
              id="eval-set-name"
              maxLength={255}
              onChange={(e) => setName(e.target.value)}
              placeholder="Eval set name"
              value={name}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="eval-set-capability">Capability (optional)</Label>
            <CapabilityCombobox
              ariaLabel="Capability (optional)"
              className="w-full"
              disabled={create.isPending || capabilities.isLoading}
              id="eval-set-capability"
              onChange={(id) => setCapabilityId(id === "none" ? "" : id)}
              options={[
                { label: "None", value: "none" },
                ...(capabilities.data?.results ?? []).map((c) => ({
                  label: c.name || c.slug,
                  value: c.id,
                })),
              ]}
              value={capabilityId || "none"}
            />
            {capabilities.error && <Alert variant="destructive">Couldn't load capabilities.</Alert>}
          </div>
        </div>
        <section aria-label="Selected evaluators" className="space-y-2">
          <p className="text-sm">{selected.length} selected</p>
          <ul className="space-y-2">
            {selected.map((ev) => (
              <li
                className="flex items-center gap-3 rounded-sm border border-border bg-muted px-3 py-2 text-sm"
                key={ev.id}
              >
                <EvaluatorLabel evaluator={ev} />
                <Button
                  aria-label={`Remove ${ev.displayName || ev.name}`}
                  disabled={create.isPending}
                  onClick={() => remove(ev.id)}
                  size="icon-sm"
                  variant="ghost"
                >
                  <Icon.close />
                </Button>
              </li>
            ))}
          </ul>
        </section>
        <div className="space-y-2">
          <SearchInput
            disabled={create.isPending}
            label="Search evaluators"
            onChange={(e) => setSearch(e.target.value)}
            onClear={() => setSearch("")}
            placeholder="Search by name, type or capability"
            value={search}
          />
          {catalog.isLoading ? (
            <Skeleton className="h-40 w-full" />
          ) : catalog.error ? (
            <Alert variant="destructive">Couldn't load evaluators.</Alert>
          ) : visible.length === 0 ? (
            <EmptyState icon={Icon.evaluations} size="section" title="No evaluators found" />
          ) : (
            <ul
              aria-label="Evaluator library"
              className="max-h-64 overflow-y-auto rounded-sm border border-border"
            >
              {visible.map((ev) => (
                <li className="border-b border-border/70 last:border-0" key={ev.id}>
                  <label className="flex cursor-pointer items-center gap-3 px-3 py-2.5 text-sm hover:bg-muted">
                    <Checkbox
                      aria-label={`Select ${ev.displayName || ev.name}`}
                      checked={selected.some((row) => row.id === ev.id)}
                      disabled={create.isPending || ev.applicableRoles.length === 0}
                      onCheckedChange={(checked) =>
                        checked
                          ? setSelected((rows) =>
                              rows.some((row) => row.id === ev.id) ? rows : [...rows, ev]
                            )
                          : remove(ev.id)
                      }
                    />
                    <EvaluatorLabel evaluator={ev} />
                    {ev.applicableRoles.length === 0 && (
                      <span className="text-xs text-muted-foreground">Unavailable</span>
                    )}
                  </label>
                </li>
              ))}
            </ul>
          )}
        </div>
        <DismissibleAlert error={error} variant="destructive" />
      </DialogBody>
      <DialogFooter>
        <Button disabled={create.isPending} onClick={onClose} variant="secondary">
          Cancel
        </Button>
        <Button disabled={!ready} onClick={() => void submit()}>
          {create.isPending ? "Creating…" : "Create eval set"}
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}
