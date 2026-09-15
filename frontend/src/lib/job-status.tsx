import { Badge } from "@/components/ui/badge";
import { Icon } from "@/components/ui/icons";
import { Spinner } from "@/components/ui/spinner";
import { domainStatus, type StatusTone, TONE_BADGE_VARIANT } from "@/lib/colors";
import { cn } from "@/lib/utils";

type StatusVariant = (typeof TONE_BADGE_VARIANT)[StatusTone];

export interface StatusMeta {
  tone: StatusTone;
  /** Derived from `tone`; do not hand-set. */
  variant: StatusVariant;
  icon: React.ReactNode;
  label: string;
  spinning?: boolean;
}

/**
 * Glyph and wording only — colour comes from `domainStatus`, which every other
 * status surface reads too.
 */
const STATUS_FACE: Record<string, { icon: React.ReactNode; label: string; spinning?: boolean }> = {
  cancelled: { icon: <Icon.failed className="size-3.5" />, label: "Cancelled" },
  completed: { icon: <Icon.success className="size-3.5" />, label: "Completed" },
  deploying: {
    icon: <Spinner className="text-current" size="sm" />,
    label: "Deploying",
    spinning: true,
  },
  failed: { icon: <Icon.failed className="size-3.5" />, label: "Failed" },
  partially_completed: {
    icon: <Icon.success className="size-3.5" />,
    label: "Partially completed",
  },
  paused: { icon: <Icon.warning className="size-3.5" />, label: "Paused" },
  pending: { icon: <Icon.job className="size-3.5" />, label: "Pending" },
  preparing: {
    icon: <Spinner className="text-current" size="sm" />,
    label: "Preparing",
    spinning: true,
  },
  queued: { icon: <Icon.job className="size-3.5" />, label: "Queued" },
  running: {
    icon: <Spinner className="text-current" size="sm" />,
    label: "Running",
    spinning: true,
  },
  skipped: { icon: <Icon.warning className="size-3.5" />, label: "Skipped" },
  succeeded: { icon: <Icon.success className="size-3.5" />, label: "Succeeded" },
  validating_files: {
    icon: <Spinner className="text-current" size="sm" />,
    label: "Validating",
    spinning: true,
  },
};

export function resolveStatus(status: string | null | undefined, fallback: string): StatusMeta {
  // `Object.hasOwn`, not `in`: a wire status of "toString" would otherwise
  // resolve to a prototype member and render an empty chip.
  const key = status && Object.hasOwn(STATUS_FACE, status) ? status : fallback;
  const face = Object.hasOwn(STATUS_FACE, key) ? STATUS_FACE[key] : STATUS_FACE.queued;
  // Tone follows the key actually rendered, so a fallback chip is never
  // coloured for the status it replaced.
  const tone = domainStatus(key);
  return { ...face, tone, variant: TONE_BADGE_VARIANT[tone] };
}

/**
 * In-progress uses inverted surface tokens, not a pinned white fill, which
 * vanishes into the cream canvas in light mode. h-7 matches Button size=sm.
 */
export function statusBadgeClassName(cfg: StatusMeta, solidProgress?: boolean): string {
  return cn(
    "gap-1",
    solidProgress && "h-7 py-0",
    solidProgress && cfg.tone === "info" && "border-transparent bg-foreground text-background"
  );
}

export function StatusBadge({
  status,
  fallback,
  solidProgress = false,
  className,
}: {
  status: string | null | undefined;
  fallback: string;
  solidProgress?: boolean;
  className?: string;
}) {
  const cfg = resolveStatus(status, fallback);
  return (
    <Badge
      className={cn(statusBadgeClassName(cfg, solidProgress), className)}
      size={solidProgress ? "default" : "chip"}
      variant={cfg.variant}
    >
      {cfg.icon}
      {cfg.label}
    </Badge>
  );
}
