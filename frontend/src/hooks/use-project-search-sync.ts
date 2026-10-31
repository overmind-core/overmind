import { useEffect } from "react";

import { useNavigate, useRouterState, useSearch } from "@tanstack/react-router";

import { useProjectsList } from "@/hooks/use-projects";

const SELECTED_PROJECT_STORAGE_KEY = "__selectedProject";

function getStoredProjectId(): string {
  return localStorage.getItem(SELECTED_PROJECT_STORAGE_KEY) || "";
}

export function setStoredProjectId(projectId: string) {
  localStorage.setItem(SELECTED_PROJECT_STORAGE_KEY, projectId);
}

function clearStoredProjectId() {
  localStorage.removeItem(SELECTED_PROJECT_STORAGE_KEY);
}

export function selectProject(projectId: string) {
  setStoredProjectId(projectId);
}

/** Path id on `/projects/$projectId` (settings), not the list or nested routes. */
export function projectIdFromProjectsDetailPath(pathname: string): string | undefined {
  const match = pathname.match(/^\/projects\/([^/]+)\/?$/);
  return match?.[1];
}

export function useProjectSearchSync() {
  const { projectId } = useSearch({ from: "/_auth" });
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const navigate = useNavigate();
  const { data, isFetching } = useProjectsList();

  useEffect(() => {
    const projects = data?.projects;
    if (!projects) {
      return;
    }

    // /projects owns the create-project deep link (?createProject=new):
    // rewriting its search or redirecting away clobbers that before the modal opens.
    if (pathname === "/projects") {
      return;
    }

    const isValid = (id: string) => !!id && projects.some((p) => p.projectId === id);

    const pathProjectId = projectIdFromProjectsDetailPath(pathname);
    if (pathProjectId && isValid(pathProjectId)) {
      setStoredProjectId(pathProjectId);
      if (projectId !== pathProjectId) {
        navigate({
          replace: true,
          search: (prev) => ({ ...prev, projectId: pathProjectId }),
          to: ".",
        });
      }
      return;
    }

    if (projects.length === 0) {
      clearStoredProjectId();
      if (projectId) {
        navigate({
          replace: true,
          search: (prev) => ({ ...prev, projectId: undefined }),
          to: ".",
        });
      }
      return;
    }

    if (projectId && isValid(projectId)) {
      setStoredProjectId(projectId);
      return;
    }

    // The list refetch is still in flight after a project is created. The id
    // is real; bouncing home would drop the dataset dialog onto no project.
    if (projectId && !isValid(projectId)) {
      if (isFetching) return;
      clearStoredProjectId();
      navigate({
        replace: true,
        search: (prev) => ({ ...prev, projectId: undefined }),
        to: ".",
      });
      return;
    }

    const storedId = getStoredProjectId();
    const selectedId = isValid(storedId)
      ? storedId
      : projects.length === 1
        ? projects[0].projectId
        : undefined;
    if (selectedId) {
      // Pathname can flip to /datasets before this hook sees the new projectId.
      // The navigation already in flight carries that id; keep it.
      navigate({
        replace: true,
        search: (prev) => {
          if ("projectId" in prev && prev.projectId) return prev;
          return { ...prev, projectId: selectedId };
        },
        to: ".",
      });
      return;
    }

    if (storedId) {
      clearStoredProjectId();
    }
  }, [projectId, pathname, navigate, data, isFetching]);
}
