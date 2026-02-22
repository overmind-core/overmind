import { useNavigate } from "@tanstack/react-router";
import { Sheet, SheetContent } from "./ui/sheet";

export const SheetWrapper = ({ children }: { children: React.ReactNode }) => {
  const navigate = useNavigate();
  const handleDrawerClose = (open: boolean) => {
    if (!open) navigate({ resetScroll: false, search: (x) => x, to: ".." });
  };
  return (
    <Sheet onOpenChange={handleDrawerClose} open={true}>
      <SheetContent
        className="flex w-full flex-col overflow-hidden border-l sm:max-w-2xl"
        showCloseButton={false}
        side="right"
      >
        <div className="-m-4 flex flex-1 flex-col overflow-y-auto p-6">{children}</div>
      </SheetContent>
    </Sheet>
  );
};
