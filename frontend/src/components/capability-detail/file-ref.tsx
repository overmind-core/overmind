import { cn } from "@/lib/utils";

const FILE_REF_BASE =
  "inline-flex max-w-full items-center gap-1 rounded-sm border border-border/60 bg-wash-raised px-1.5 py-0.5 font-mono text-xs text-muted-foreground";

export function FileRef({ reference, className }: { reference: string; className?: string }) {
  const label = reference.trim();
  return (
    <span className={cn(FILE_REF_BASE, className)} title={label}>
      <span className="truncate">{label}</span>
    </span>
  );
}

export function FileRefList({ references }: { references: string[] }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {references.map((reference) => (
        <FileRef key={reference} reference={reference} />
      ))}
    </div>
  );
}
