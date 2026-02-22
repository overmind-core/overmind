import { useState } from "react";

import { useQuery } from "@tanstack/react-query";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { Calendar, CheckCircle, Loader2, Mail, Trash2, User, XCircle } from "lucide-react";

import apiClient from "@/client";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { formatDate } from "@/lib/utils";

export const Route = createFileRoute("/_auth/account")({
  component: AccountPage,
});

function AccountPage() {
  const navigate = useNavigate();
  const [deactivating, setDeactivating] = useState(false);
  const [deactivateError, setDeactivateError] = useState("");

  const {
    data: userData,
    isLoading,
    error,
  } = useQuery({
    queryFn: () => apiClient.users.getCurrentUserProfileApiV1IamUsersMeGet(),
    queryKey: ["current-user"],
  });

  const handleDeactivate = async () => {
    try {
      setDeactivating(true);
      setDeactivateError("");
      await apiClient.users.deactivateCurrentUserApiV1IamUsersMeDelete();
      localStorage.removeItem("token");
      localStorage.removeItem("auth_token");
      localStorage.removeItem("auth_user");
      navigate({ to: "/login" });
    } catch (err) {
      const msg =
        (err as { data?: { detail?: { message?: string } } })?.data?.detail?.message ??
        (err as Error)?.message ??
        "Failed to deactivate";
      setDeactivateError(msg);
    } finally {
      setDeactivating(false);
    }
  };

  if (isLoading) {
    return (
      <div className="space-y-6 pb-8">
        <Card className="overflow-hidden">
          <div className="bg-primary/5 p-6">
            <Skeleton className="h-4 w-32" />
            <Skeleton className="mt-2 h-3 w-48" />
          </div>
          <CardContent className="grid gap-6 pt-6 md:grid-cols-2">
            {[1, 2, 3, 4, 5, 6].map((i) => (
              <div className="flex items-center gap-4" key={i}>
                <Skeleton className="size-10 shrink-0 rounded-lg" />
                <div className="flex-1 space-y-2">
                  <Skeleton className="h-3 w-20" />
                  <Skeleton className="h-4 w-full" />
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
        <Skeleton className="h-[100px] w-full rounded-xl" />
      </div>
    );
  }

  if (error || !userData) {
    return (
      <div className="space-y-6 pb-8">
        <Alert variant="destructive">Failed to load user: {(error as Error).message}</Alert>
      </div>
    );
  }
  return (
    <div className="space-y-6 pb-8">
      <Card className="overflow-hidden border-border">
        <div className="border-b border-border bg-primary/5 px-6 py-4">
          <p className="font-medium text-foreground">Profile Information</p>
          <p className="text-sm text-muted-foreground">View your account details and status</p>
        </div>
        <CardContent className="grid gap-6 pt-6 md:grid-cols-2">
          <div className="flex items-center gap-4">
            <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-primary/15">
              <User className="size-5 text-primary" />
            </div>
            <div className="min-w-0">
              <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                Full Name
              </p>
              <p className="truncate font-medium text-foreground">{userData.fullName ?? "N/A"}</p>
            </div>
          </div>
          <div className="flex items-center gap-4">
            <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-accent/15">
              <Mail className="size-5 text-accent" />
            </div>
            <div className="min-w-0">
              <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                Email
              </p>
              <p className="truncate font-medium text-foreground">{userData.email ?? "N/A"}</p>
            </div>
          </div>
          <div className="flex items-center gap-4">
            <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-muted">
              <span aria-hidden className="text-base">
                🆔
              </span>
            </div>
            <div className="min-w-0">
              <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                User ID
              </p>
              <p className="truncate font-mono text-sm text-foreground">
                {userData.userId ?? "N/A"}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-4">
            <div
              className={`flex size-10 shrink-0 items-center justify-center rounded-lg ${
                userData.isActive ? "bg-secondary/20 dark:bg-secondary/30" : "bg-muted"
              }`}
            >
              {userData.isActive ? (
                <CheckCircle className="size-5 text-secondary" />
              ) : (
                <XCircle className="size-5 text-muted-foreground" />
              )}
            </div>
            <div>
              <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                Status
              </p>
              <div className="flex flex-wrap gap-2">
                <Badge variant={userData.isActive ? "success" : "secondary"}>
                  {userData.isActive ? "Active" : "Inactive"}
                </Badge>
                <Badge variant={userData.isVerified ? "success" : "warning"}>
                  {userData.isVerified ? "Verified" : "Unverified"}
                </Badge>
              </div>
            </div>
          </div>
          <div className="flex items-center gap-4">
            <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-primary/10">
              <Calendar className="size-5 text-primary/80" />
            </div>
            <div className="min-w-0">
              <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                Account Created
              </p>
              <p className="text-sm text-foreground">
                {formatDate(userData.createdAt?.toISOString())}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-4">
            <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-accent/10">
              <Calendar className="size-5 text-accent/80" />
            </div>
            <div className="min-w-0">
              <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                Last Login
              </p>
              <p className="text-sm text-foreground">
                {formatDate(userData.lastLogin?.toISOString())}
              </p>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card className="overflow-hidden border-destructive/30 dark:border-destructive/40">
        <div className="border-b border-destructive/20 bg-destructive/5 px-6 py-4 dark:bg-destructive/10">
          <p className="font-medium text-destructive dark:text-destructive">Danger Zone</p>
          <p className="text-sm text-muted-foreground">Irreversible and destructive actions</p>
        </div>
        <CardContent className="flex items-center justify-between gap-4 py-6">
          <div>
            <p className="font-medium text-foreground">Deactivate Account</p>
            <p className="text-sm text-muted-foreground">
              Temporarily disable access. Contact support to reactivate.
            </p>
          </div>
          <Dialog>
            <DialogTrigger asChild>
              <Button size="icon" variant="destructive">
                <Trash2 className="size-4" />
              </Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Deactivate Account</DialogTitle>
                <DialogDescription>
                  Are you sure? Your account will be disabled and you will be logged out. Contact
                  support to reactivate.
                </DialogDescription>
              </DialogHeader>
              {deactivateError && <Alert variant="destructive">{deactivateError}</Alert>}
              <DialogFooter>
                <DialogClose asChild>
                  <Button disabled={deactivating} variant="outline">
                    Cancel
                  </Button>
                </DialogClose>
                <Button disabled={deactivating} onClick={handleDeactivate} variant="destructive">
                  {deactivating ? <Loader2 className="size-4 animate-spin" /> : "Deactivate"}
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        </CardContent>
      </Card>
    </div>
  );
}
