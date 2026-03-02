import { useState } from "react";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { ArrowLeft, FileText, FolderOpen, Hash, Loader2, Plus, Save } from "lucide-react";

import { ResponseError } from "@/api";
import apiClient from "@/client";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useOrganisationId } from "@/hooks/use-query";


function generateProjectSlug(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9\s-]/g, "")
    .replace(/\s+/g, "-")
    .replace(/-+/g, "-")
    .trim();
}

interface CreateProjectFormData {
  name: string;
  slug: string;
  description: string;
}

const initialFormData: CreateProjectFormData = {
  description: "",
  name: "",
  slug: "",
};

function CreateProjectForm({
  onSuccess,
  onCancel,
}: {
  onSuccess?: () => void;
  onCancel?: () => void;
}) {
  const queryClient = useQueryClient();
  const organisationId = useOrganisationId();

  const [formData, setFormData] = useState<CreateProjectFormData>(initialFormData);


  const createMutation = useMutation({
    mutationFn: async (data: CreateProjectFormData) => {
      try {
        return await apiClient.projects.createProjectApiV1IamProjectsPost({
          createProjectRequest: {
            description: data.description,
            name: data.name,
            organisationId,
            slug: data.slug,
          },
        });
      } catch (err) {
        if (err instanceof ResponseError) {
          const body = await err.response.json().catch(() => null);
          const detail = body?.detail;
          const msg =
            typeof detail === "string" ? detail : (detail?.message ?? "Failed to create project");
          throw new Error(msg);
        }
        throw err;
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      onSuccess?.();
    },
  });

  const handleNameChange = (value: string) => {
    setFormData((prev) => ({
      ...prev,
      name: value,
      slug: generateProjectSlug(value),
    }));
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();

    if (
      !formData.name.trim() ||
      !formData.slug.trim() ||
      !formData.description.trim()
    ) {
      createMutation.reset();
      return;
    }

    createMutation.mutate(formData);
  };

  const error = createMutation.error instanceof Error ? createMutation.error.message : null;
  const loading = createMutation.isPending;

  return (
    <form className="space-y-6" onSubmit={handleSubmit}>
      {error && <Alert variant="destructive">{error}</Alert>}

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="project-name">Project Name</Label>
          <div className="relative">
            <FolderOpen className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              className="pl-9"
              id="project-name"
              onChange={(e) => handleNameChange(e.target.value)}
              placeholder="Enter project name"
              required
              value={formData.name}
            />
          </div>
        </div>

        <div className="space-y-2">
          <Label htmlFor="project-slug">Slug</Label>
          <div className="relative">
            <Hash className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              className="pl-9"
              id="project-slug"
              onChange={(e) => setFormData((prev) => ({ ...prev, slug: e.target.value }))}
              placeholder="project-slug"
              required
              value={formData.slug}
            />
          </div>
          <p className="text-xs text-muted-foreground">
            URL-friendly identifier (auto-generated from name)
          </p>
        </div>
      </div>

      <div className="space-y-2">
        <Label htmlFor="project-description">
          Description
          <span className="ml-1 text-destructive">*</span>
        </Label>
        <div className="relative">
          <FileText className="absolute left-3 top-3 size-4 text-muted-foreground" />
          <Textarea
            className="min-h-24 pl-9"
            id="project-description"
            onChange={(e) =>
              setFormData((prev) => ({
                ...prev,
                description: e.target.value,
              }))
            }
            placeholder="Describe what this project is for — this helps us understand your project's goals and requirements"
            required
            rows={3}
            value={formData.description}
          />
        </div>
        <p className="text-xs text-muted-foreground">
          Used by the AI to understand your project's domain and generate accurate evaluation
          criteria.
        </p>
      </div>

      <div className="flex justify-end gap-2 pt-4">
        <Button disabled={loading} onClick={onCancel} type="button" variant="outline">
          Cancel
        </Button>
        <Button
          className="gap-2"
          disabled={loading || !formData.description.trim()}
          type="submit"
        >
          {loading ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}
          {loading ? "Creating..." : "Create Project"}
        </Button>
      </div>
    </form>
  );
}

interface CreateProjectDialogProps {
  trigger?: React.ReactNode;
}

export function CreateProjectDialog({ trigger }: CreateProjectDialogProps) {
  const [open, setOpen] = useState(false);

  return (
    <Dialog onOpenChange={setOpen} open={open}>
      <DialogTrigger asChild>
        {trigger ?? (
          <Button className="gap-2">
            <Plus className="size-4" />
            Create Project
          </Button>
        )}
      </DialogTrigger>
      <DialogContent className="sm:max-w-[500px]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <FolderOpen className="size-5 text-amber-600" />
            Create New Project
          </DialogTitle>
        </DialogHeader>
        <CreateProjectForm
          key={String(open)}
          onCancel={() => setOpen(false)}
          onSuccess={() => setOpen(false)}
        />
      </DialogContent>
    </Dialog>
  );
}

export function CreateProject() {
  const navigate = useNavigate();

  return (
    <div className="space-y-6 pb-8">
      <div className="flex items-center gap-4">
        <Button asChild size="sm" variant="ghost">
          <Link search={(prev) => prev} to="..">
            <ArrowLeft className="size-4" />
          </Link>
        </Button>
        <h1 className="text-2xl font-bold">Create New Project</h1>
      </div>
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <FolderOpen className="size-5 text-amber-600" />
            <h2 className="text-lg font-semibold">Basic Information</h2>
          </div>
        </CardHeader>
        <CardContent>
          <CreateProjectForm
            onCancel={() => navigate({ to: "/projects" })}
            onSuccess={() => navigate({ to: "/projects" })}
          />
        </CardContent>
      </Card>
    </div>
  );
}
