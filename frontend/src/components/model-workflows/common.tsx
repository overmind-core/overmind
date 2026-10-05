import type { ReactNode } from "react";

import { useQuery } from "@tanstack/react-query";

import apiClient from "@/client";
import { ModelOptionLabel } from "@/components/model-option-label";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { errorMessage } from "@/lib/notify";
import type { Dataset } from "@/openapi";

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="flex min-w-0 flex-col gap-2 text-sm">
      {label}
      {children}
    </label>
  );
}
export function Choice({
  label,
  value,
  onChange,
  options,
  optional = false,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string; model?: string }[];
  optional?: boolean;
}) {
  return (
    <Field label={label}>
      <Select
        onValueChange={(next) => onChange(next === "none" ? "" : next)}
        value={value || "none"}
      >
        <SelectTrigger aria-label={label} className="w-full">
          <SelectValue placeholder="Select" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem disabled={!optional} value="none">
            {optional ? "None" : "Select"}
          </SelectItem>
          {options.map((option) => (
            <SelectItem key={option.value} value={option.value}>
              {option.model ? (
                <ModelOptionLabel model={option.model} name={option.label} />
              ) : (
                option.label
              )}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </Field>
  );
}
export function NumberField({
  label,
  value,
  onChange,
  min = 0,
  max,
  step = 1,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
  min?: number;
  max?: number;
  step?: number;
}) {
  return (
    <Field label={label}>
      <Input
        max={max}
        min={min}
        onChange={(event) => onChange(Number(event.target.value))}
        required
        step={step}
        type="number"
        value={value}
      />
    </Field>
  );
}
export function Failure({ error }: { error: unknown }) {
  return error ? <Alert variant="destructive">{errorMessage(error)}</Alert> : null;
}
export function WorkflowState({ state }: { state: string }) {
  return <Badge variant="outline">{state.replaceAll("_", " ")}</Badge>;
}
export function useVersions(projectId: string) {
  return useQuery({
    queryFn: async () => {
      const datasets: Dataset[] = [];
      for (let page = 1; ; page++) {
        const response = await apiClient.datasets.datasetsList({
          page,
          pageSize: 100,
          project: projectId,
        });
        datasets.push(...response.results);
        if (!response.next)
          return datasets.flatMap((dataset) =>
            (dataset.cells ?? [])
              .filter((cell) => cell.state === "ok" && cell.rows > 0)
              .map((cell) => ({
                cell,
                dataset: dataset.id,
                intent: dataset.intent,
                label: `${dataset.name} · ${cell.version} · ${cell.rows.toLocaleString()} rows`,
                value: cell.id,
              }))
          );
      }
    },
    queryKey: ["workflow-versions", projectId],
  });
}
export function saveBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export function SavedEvidence({
  value,
  label = "Saved protocol and evidence",
}: {
  value: unknown;
  label?: string;
}) {
  return (
    <details className="rounded-md border border-border px-3 py-2 text-sm">
      <summary className="cursor-pointer">{label}</summary>
      <pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap break-words text-xs text-muted-foreground">
        {JSON.stringify(value, null, 2)}
      </pre>
    </details>
  );
}
