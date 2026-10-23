import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";

export function Failure({ error, onRetry }: { error: Error; onRetry: () => unknown }) {
  return (
    <Alert className="items-center" variant="destructive">
      <Icon.warning />
      <span className="flex-1">{error.message}</span>
      <Button onClick={() => void onRetry()} size="sm" variant="secondary">
        Try again
      </Button>
    </Alert>
  );
}
