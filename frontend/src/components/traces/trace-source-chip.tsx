import Langfuse from "@lobehub/icons/es/Langfuse";

import galileoLogo from "@/assets/galileo.png";
import { Badge } from "@/components/ui/badge";
import { Icon } from "@/components/ui/icons";
import { cn } from "@/lib/utils";

const SOURCE_LABEL: Record<string, string> = {
  braintrust: "Braintrust",
  galileo: "Galileo",
  langfuse: "Langfuse",
  langsmith: "LangSmith",
  overmind: "Overmind",
};

type TraceSourceChipProps = {
  source: string;
  className?: string;
};

/**
 * Path from `@/assets/braintrust.svg`; @lobehub/icons ships no Braintrust mark.
 * Inked with currentColor rather than their blue, which goes muddy at 14px on
 * both washes and cannot invert with the theme.
 */
function Braintrust({ className }: { className?: string }) {
  return (
    <svg
      aria-hidden
      className={className}
      fill="none"
      viewBox="0 0 32 32"
      xmlns="http://www.w3.org/2000/svg"
    >
      <path
        d="M11.8623 0C13.576 0.000127038 14.9658 1.38669 14.9658 3.09668V6.19336C14.9658 7.90339 13.576 9.28991 11.8623 9.29004H10.3105V11.3545H11.8623C13.5758 11.3546 14.9656 12.7413 14.9658 14.4512V17.5488C14.9656 19.2587 13.5758 20.6454 11.8623 20.6455H10.3105V22.71H11.8623C13.576 22.7101 14.9658 24.0966 14.9658 25.8066V28.9033C14.9658 30.6133 13.576 31.9999 11.8623 32H8.75879C7.04503 32 5.65532 30.6134 5.65527 28.9033V26.3223H4.10352C2.38972 26.3223 1 24.9357 1 23.2256V20.1289C1.00007 18.4189 2.38976 17.0322 4.10352 17.0322H5.65527V14.9678H4.10352C2.38976 14.9678 1.00007 13.5811 1 11.8711V8.77441C1 7.06431 2.38972 5.67773 4.10352 5.67773H5.65527V3.09668C5.65532 1.38662 7.04503 0 8.75879 0H11.8623ZM23.2412 0C24.955 0 26.3447 1.38662 26.3447 3.09668V5.67773H27.8965C29.6103 5.67773 31 7.06431 31 8.77441V11.8711C30.9999 13.5811 29.6102 14.9678 27.8965 14.9678H26.3447V17.0322H27.8965C29.6102 17.0322 30.9999 18.4189 31 20.1289V23.2256C31 24.9357 29.6103 26.3223 27.8965 26.3223H26.3447V28.9033C26.3447 30.6134 24.955 32 23.2412 32H20.1377C18.424 31.9999 17.0342 30.6133 17.0342 28.9033V25.8066C17.0342 24.0966 18.424 22.7101 20.1377 22.71H21.6895V20.6455H20.1377C18.4241 20.6454 17.0344 19.2587 17.0342 17.5488V14.4512C17.0344 12.7413 18.4241 11.3546 20.1377 11.3545H21.6895V9.29004H20.1377C18.424 9.28992 17.0342 7.9034 17.0342 6.19336V3.09668C17.0342 1.38669 18.424 0.000115963 20.1377 0H23.2412Z"
        fill="currentColor"
      />
    </svg>
  );
}

/**
 * Path from `@/assets/langsmith.svg`. @lobehub/icons still ships the pre-2026
 * parrot-in-a-pill, which is both the wrong mark and a solid slab at 14px.
 */
function LangSmith({ className }: { className?: string }) {
  return (
    <svg
      aria-hidden
      className={className}
      fill="none"
      viewBox="0 0 21.19 21.19"
      xmlns="http://www.w3.org/2000/svg"
    >
      <path
        d="M6.64803 14.103C7.89438 12.8566 8.595 11.1643 8.595 9.40177C8.595 7.63928 7.89378 5.94697 6.64803 4.70058L1.94697 0C0.701225 1.24639 0 2.9387 0 4.70119C0 6.46368 0.701225 8.15599 1.94697 9.40238L6.64742 14.103H6.64803Z"
        fill="currentColor"
      />
      <path
        d="M16.4845 14.5379C15.2388 13.2921 13.5459 12.5908 11.7841 12.5908C10.0222 12.5908 8.32936 13.2921 7.08301 14.5379L11.7841 19.239C13.0298 20.4848 14.7227 21.1861 16.4851 21.1861C18.2476 21.1861 19.9398 20.4848 21.1862 19.239L16.4851 14.5379H16.4845Z"
        fill="currentColor"
      />
      <path
        d="M1.95832 19.228C3.20468 20.4738 4.89693 21.1751 6.65938 21.1751V14.5269H0.0107422C0.0113472 16.2893 0.711968 17.9817 1.95832 19.228Z"
        fill="currentColor"
      />
      <path
        d="M18.2997 7.58717C17.0533 6.34138 15.3611 5.63953 13.598 5.64014C11.8356 5.64014 10.1433 6.34138 8.89697 7.58777L13.598 12.289L18.2997 7.58717Z"
        fill="currentColor"
      />
    </svg>
  );
}

function Galileo({ className }: { className?: string }) {
  return <img alt="" className={cn("brightness-0 invert", className)} src={galileoLogo} />;
}

/** No vendor glyph ships for every connector, so the rest get the generic one. */
function SourceGlyph({ source }: { source: string }) {
  if (source === "braintrust") return <Braintrust className="size-3.5 shrink-0" />;
  if (source === "galileo") return <Galileo className="size-3.5 shrink-0" />;
  if (source === "langfuse") return <Langfuse className="size-3.5 shrink-0" />;
  if (source === "langsmith") return <LangSmith className="size-3.5 shrink-0" />;
  if (source === "overmind") return <Icon.overmind className="size-3.5 shrink-0" />;
  return <Icon.integrations className="size-3.5 shrink-0" />;
}

export function TraceSourceChip({ source, className }: TraceSourceChipProps) {
  const label = SOURCE_LABEL[source] ?? source;
  return (
    <Badge
      className={cn("max-w-full gap-1.5 overflow-hidden bg-wash-raised", className)}
      size="chip"
      title={label}
      variant="outline"
    >
      <SourceGlyph source={source} />
      <span className="min-w-0 truncate">{label}</span>
    </Badge>
  );
}
