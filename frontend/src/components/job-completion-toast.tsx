import { toast } from "sonner";

import { Icon } from "@/components/ui/icons";

type JobCompletionToastProps = {
  title: string;
  body?: string;
  onView: () => void;
  toastId: string | number;
};

/** Content only — the toast wrapper in ui/sonner.tsx already paints the card. */
const JobCompletionToast = ({ title, body, onView, toastId }: JobCompletionToastProps) => {
  const handleView = () => {
    onView();
    toast.dismiss(toastId);
  };

  const handleDismiss = () => {
    toast.dismiss(toastId);
  };

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-1.5" role="status">
      <div className="flex items-start justify-between gap-3">
        <p className="min-w-0 truncate text-sm font-medium leading-tight" title={title}>
          {title}
        </p>
        <button
          aria-label="Dismiss notification"
          className="-m-1 shrink-0 rounded-sm p-1 text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          onClick={handleDismiss}
          type="button"
        >
          <Icon.close className="size-3.5" />
        </button>
      </div>
      {/* Sonner mounts custom jsx inside its title node, so the body must reset
          font weight explicitly or it inherits the title's font-medium. */}
      {body ? (
        <p className="text-xs font-normal leading-relaxed text-muted-foreground">{body}</p>
      ) : null}
      <button
        aria-label={`View: ${title}`}
        className="mt-0.5 inline-flex h-6 items-center gap-1 self-start rounded-sm border border-border px-2 text-xs font-medium transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        onClick={handleView}
        type="button"
      >
        View
        <Icon.forward className="size-3" />
      </button>
    </div>
  );
};

export const showJobCompletionToast = (opts: {
  title: string;
  body?: string;
  onView: () => void;
}) => {
  toast.custom((id) => (
    <JobCompletionToast body={opts.body} onView={opts.onView} title={opts.title} toastId={id} />
  ));
};

// Dev console hook: `window.__showJobCompletionToast({ title: "…" })`.
if (import.meta.env.DEV && typeof window !== "undefined") {
  (
    window as Window & { __showJobCompletionToast?: typeof showJobCompletionToast }
  ).__showJobCompletionToast = showJobCompletionToast;
}
