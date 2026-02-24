import { createFileRoute } from "@tanstack/react-router";
import { Building2, Loader2 } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useOrganisationsList } from "@/hooks/use-organisations";

export const Route = createFileRoute("/_auth/organisations")({
  component: OrganisationsPage,
});

function OrganisationsPage() {
  const { data: orgsData, isLoading, error } = useOrganisationsList();
  if (isLoading) {
    return (
      <div className="page-wrapper">
        <Loader2 className="size-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="page-wrapper">
        <Alert variant="destructive">
          Failed to load organisations: {(error as Error).message}
        </Alert>
      </div>
    );
  }

  if (!orgsData || orgsData.organisations.length === 0) {
    return (
      <div className="page-wrapper">
        <EmptyState />
      </div>
    );
  }

  return (
    <div className="page-wrapper">
      <p className="text-muted-foreground">Manage your organisations and their settings.</p>
      <div>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Slug</TableHead>
              <TableHead>Members</TableHead>
              <TableHead>Projects</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {orgsData.organisations.map((org) => (
              <TableRow key={org.organisationId}>
                <TableCell className="font-medium">{org.name ?? "Unnamed"}</TableCell>
                <TableCell className="font-mono text-sm text-muted-foreground">
                  {org.slug ?? org.organisationId?.slice(0, 8)}
                </TableCell>
                <TableCell>{org.memberCount ?? "—"}</TableCell>
                <TableCell>{org.projectCount ?? "—"}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center py-12 text-center">
      <Building2 className="mb-4 size-12 text-muted-foreground" />
      <p className="text-muted-foreground">No organisations found.</p>
      <p className="mt-1 text-sm text-muted-foreground">
        You may need to be invited to an organisation.
      </p>
    </div>
  );
}
