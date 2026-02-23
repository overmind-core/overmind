import { useState } from "react";

import { useQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { Calendar, CheckCircle, Loader2, Lock, Mail, User, XCircle } from "lucide-react";

import apiClient from "@/client";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { formatDate } from "@/lib/utils";

export const Route = createFileRoute("/_auth/account")({
  component: AccountPage,
});

function AccountPage() {
  const {
    data: userData,
    isLoading,
    error,
  } = useQuery({
    queryFn: () => apiClient.users.getCurrentUserProfileApiV1IamUsersMeGet(),
    queryKey: ["current-user"],
  });

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
        </CardContent>
      </Card>

      <ChangePasswordCard />
    </div>
  );
}

function ChangePasswordCard() {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

  const handleChangePassword = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setSuccess("");

    if (newPassword !== confirmPassword) {
      setError("New passwords do not match");
      return;
    }
    if (newPassword.length < 4) {
      setError("New password must be at least 4 characters");
      return;
    }

    setLoading(true);
    try {
      const token = localStorage.getItem("token");
      const res = await fetch(`${baseUrl}/api/v1/iam/users/me/password`, {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
        }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data?.detail?.message ?? data?.detail ?? "Failed to change password");
      }
      setSuccess("Password changed successfully");
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
    } catch (err) {
      setError((err as Error).message || "Failed to change password");
    } finally {
      setLoading(false);
    }
  };

  return (
    <Card className="overflow-hidden border-border">
      <div className="border-b border-border bg-primary/5 px-6 py-4">
        <div className="flex items-center gap-2">
          <Lock className="size-4 text-primary" />
          <p className="font-medium text-foreground">Change Password</p>
        </div>
        <p className="text-sm text-muted-foreground">Update your login password</p>
      </div>
      <CardContent className="pt-6">
        <form className="max-w-sm space-y-4" onSubmit={handleChangePassword}>
          <div className="space-y-1.5">
            <Label htmlFor="current-password">Current Password</Label>
            <Input
              autoComplete="current-password"
              id="current-password"
              onChange={(e) => setCurrentPassword(e.target.value)}
              required
              type="password"
              value={currentPassword}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="new-password">New Password</Label>
            <Input
              autoComplete="new-password"
              id="new-password"
              onChange={(e) => setNewPassword(e.target.value)}
              required
              type="password"
              value={newPassword}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="confirm-password">Confirm New Password</Label>
            <Input
              autoComplete="new-password"
              id="confirm-password"
              onChange={(e) => setConfirmPassword(e.target.value)}
              required
              type="password"
              value={confirmPassword}
            />
          </div>
          {error && <Alert variant="destructive">{error}</Alert>}
          {success && <Alert variant="success">{success}</Alert>}
          <Button disabled={loading} type="submit">
            {loading && <Loader2 className="mr-2 size-4 animate-spin" />}
            Change Password
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
