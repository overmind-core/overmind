import { Icon } from "@/components/ui/icons";
import { cn } from "@/lib/utils";

const KINDS = [
  {
    accent: "text-cat-2",
    extensions: ["csv", "tsv", "parquet", "xlsx", "xls"],
    icon: Icon.dataset,
  },
  { accent: "text-cat-3", extensions: ["json", "jsonl", "ndjson"], icon: Icon.code },
  { accent: "text-cat-5", extensions: ["pdf", "docx", "doc"], icon: Icon.file },
  { accent: "text-muted-foreground", extensions: ["py"], icon: Icon.python },
];
const OTHER = { accent: "text-muted-foreground", icon: Icon.file };

/** A file's icon, in the accent of what the file holds. */
export function FileTypeIcon({ className, filename }: { className?: string; filename: string }) {
  const extension = filename.replace(/\.gz$/i, "").split(".").at(-1)?.toLowerCase() ?? "";
  const kind = KINDS.find((entry) => entry.extensions.includes(extension)) ?? OTHER;
  return <kind.icon className={cn("shrink-0", kind.accent, className)} />;
}
