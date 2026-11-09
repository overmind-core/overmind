import { Icon } from "@/components/ui/icons";
import { cn } from "@/lib/utils";

export function FileTypeIcon({ filename, className }: { filename: string; className?: string }) {
  const extension = filename.replace(/\.gz$/i, "").split(".").at(-1)?.toLowerCase();
  const table = ["csv", "tsv", "parquet", "xlsx", "xls"].includes(extension ?? "");
  const structured = ["json", "jsonl", "ndjson"].includes(extension ?? "");
  const document = ["pdf", "docx", "doc"].includes(extension ?? "");
  const Glyph =
    extension === "py" ? Icon.python : table ? Icon.dataset : structured ? Icon.code : Icon.file;
  return (
    <Glyph
      className={cn(
        "shrink-0",
        table
          ? "text-cat-2"
          : structured
            ? "text-cat-3"
            : document
              ? "text-cat-5"
              : "text-muted-foreground",
        className
      )}
    />
  );
}
