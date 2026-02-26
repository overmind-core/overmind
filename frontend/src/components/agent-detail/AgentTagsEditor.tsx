import { useCallback, useEffect, useRef, useState } from "react";
import { Plus, X } from "lucide-react";

export function AgentTagsEditor({
  initialTags,
  onSave,
  isSaving,
}: {
  initialTags: string[];
  onSave: (tags: string[]) => void;
  isSaving: boolean;
}) {
  const [tags, setTags] = useState<string[]>(initialTags);
  const [adding, setAdding] = useState(false);
  const [input, setInput] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setTags(initialTags);
  }, [initialTags]);

  useEffect(() => {
    if (adding) inputRef.current?.focus();
  }, [adding]);

  const closeInput = useCallback(() => {
    setAdding(false);
    setInput("");
  }, []);

  useEffect(() => {
    if (!adding) return;
    function onClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        closeInput();
      }
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, [adding, closeInput]);

  function addTag() {
    const trimmed = input.trim();
    if (!trimmed || tags.includes(trimmed) || trimmed.length > 50) return;
    const newTags = [...tags, trimmed];
    setTags(newTags);
    setInput("");
    onSave(newTags);
  }

  function removeTag(tag: string) {
    const newTags = tags.filter((t) => t !== tag);
    setTags(newTags);
    onSave(newTags);
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      addTag();
    }
    if (e.key === "Escape") closeInput();
  }

  return (
    <div ref={containerRef} className="flex flex-wrap items-center gap-1.5">
      {tags.map((tag) => (
        <span
          className="group/tag inline-flex items-center gap-1 rounded-full border border-border bg-muted/60 px-2.5 py-0.5 text-xs font-medium text-muted-foreground"
          key={tag}
        >
          {tag}
          <button
            className="ml-0.5 rounded-full text-muted-foreground/60 hover:text-destructive disabled:opacity-50"
            disabled={isSaving}
            onClick={() => removeTag(tag)}
            title={`Remove tag "${tag}"`}
            type="button"
          >
            <X className="size-3" />
          </button>
        </span>
      ))}
      {tags.length < 20 &&
        (adding ? (
          <input
            ref={inputRef}
            className="h-6 w-28 rounded-full border border-border bg-muted/40 px-2.5 text-xs transition-all focus:outline-none focus:ring-1 focus:ring-ring"
            disabled={isSaving}
            maxLength={50}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Type tag name…"
            value={input}
          />
        ) : (
          <button
            className="inline-flex h-6 items-center gap-1 rounded-full px-2.5 text-xs text-muted-foreground transition-colors hover:bg-muted/60"
            disabled={isSaving}
            onClick={() => setAdding(true)}
            type="button"
          >
            <Plus className="size-3" />
            Add tag
          </button>
        ))}
    </div>
  );
}
