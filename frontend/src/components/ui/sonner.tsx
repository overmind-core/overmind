import { Toaster as Sonner, type ToasterProps } from "sonner";

import { Icon } from "@/components/ui/icons";
import { Spinner } from "@/components/ui/spinner";

const Toaster = ({ ...props }: ToasterProps) => {
  return (
    <Sonner
      icons={{
        error: <Icon.failed className="size-4 text-destructive" />,
        info: <Icon.notify className="size-4 text-muted-foreground" />,
        loading: <Spinner className="size-4 text-muted-foreground" />,
        success: <Icon.success className="size-4 text-success" />,
        warning: <Icon.warning className="size-4 text-warning" />,
      }}
      toastOptions={{
        classNames: {
          actionButton:
            "h-6 shrink-0 self-center rounded-sm border border-border bg-transparent px-2 text-xs font-medium text-foreground transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          cancelButton:
            "h-6 shrink-0 self-center rounded-sm px-2 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          content: "flex min-w-0 flex-1 flex-col gap-0.5",
          description: "text-xs leading-relaxed text-muted-foreground",
          icon: "mt-0.5 flex size-4 shrink-0 items-center justify-center [&>svg]:size-4",
          // Sonner absolutely centres `.sonner-loader` over the whole toast; pin it back
          // into the icon slot it renders inside.
          loader: "!relative !left-auto !top-auto flex items-center justify-center !transform-none",
          title: "text-sm font-medium leading-tight text-popover-foreground",
          // Collapsed toasts behind the front one hide their content: sonner only does
          // this for its own styled cards, which `unstyled` opts out of.
          toast:
            "pointer-events-auto flex w-full items-start gap-2.5 rounded-md border border-border bg-popover p-3 text-popover-foreground data-[expanded=false]:data-[front=false]:*:opacity-0",
        },
        unstyled: true,
      }}
      {...props}
    />
  );
};

export { Toaster };
