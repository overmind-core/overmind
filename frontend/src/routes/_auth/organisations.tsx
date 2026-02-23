import { createFileRoute } from "@tanstack/react-router";
import { Building2 } from "lucide-react";

export const Route = createFileRoute("/_auth/organisations")({
  component: OrganisationsPage,
});

function OrganisationsPage() {
  return (
    <div className="page-wrapper">
      <div className="flex flex-col items-center justify-center py-16 text-center">
        <Building2 className="mb-4 size-12 text-muted-foreground" />
        <h1 className="text-2xl font-bold">Organisations</h1>
        <p className="mt-2 text-muted-foreground">
          Organisation management is available in Overmind Enterprise.
        </p>
        <p className="mt-1 text-sm text-muted-foreground">
          This open core edition runs as a single-user instance.
        </p>
      </div>
    </div>
  );
}
