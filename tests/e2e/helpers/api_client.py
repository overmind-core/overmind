"""Thin httpx wrapper for Overmind REST API endpoints used in E2E tests."""

from __future__ import annotations

import httpx
from typing import Any


class OvermindAPIClient:
    """Synchronous client that talks to a running Overmind instance over HTTP."""

    def __init__(self, base_url: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout)
        self._jwt: str | None = None
        self._project_id: str | None = None

    def close(self):
        self._http.close()

    # -- auth -----------------------------------------------------------------

    def login(self, email: str = "admin", password: str = "admin") -> dict:
        resp = self._http.post(
            "/api/v1/iam/users/login",
            json={"email": email, "password": password},
        )
        resp.raise_for_status()
        data = resp.json()
        self._jwt = data["access_token"]
        return data

    @property
    def auth_headers(self) -> dict[str, str]:
        assert self._jwt, "Call login() first"
        return {"Authorization": f"Bearer {self._jwt}"}

    def token_headers(self, api_token: str) -> dict[str, str]:
        return {"X-API-Token": api_token}

    # -- users ----------------------------------------------------------------

    def get_me(self) -> dict:
        resp = self._http.get("/api/v1/iam/users/me", headers=self.auth_headers)
        resp.raise_for_status()
        return resp.json()

    # -- projects -------------------------------------------------------------

    def create_project(self, name: str) -> dict:
        resp = self._http.post(
            "/api/v1/iam/projects/",
            json={"name": name},
            headers=self.auth_headers,
        )
        resp.raise_for_status()
        data = resp.json()
        self._project_id = data["project_id"]
        return data

    def list_projects(self) -> list[dict]:
        resp = self._http.get("/api/v1/iam/projects/", headers=self.auth_headers)
        resp.raise_for_status()
        data = resp.json()
        return data.get("projects", data) if isinstance(data, dict) else data

    def delete_project(self, project_id: str):
        resp = self._http.delete(
            f"/api/v1/iam/projects/{project_id}", headers=self.auth_headers
        )
        if resp.status_code == 404:
            return
        resp.raise_for_status()

    # -- tokens ---------------------------------------------------------------

    def create_token(self, project_id: str, name: str = "e2e-token") -> dict:
        resp = self._http.post(
            "/api/v1/iam/tokens/",
            json={"project_id": project_id, "name": name},
            headers=self.auth_headers,
        )
        resp.raise_for_status()
        return resp.json()

    def list_tokens(self, project_id: str) -> list[dict]:
        resp = self._http.get(
            "/api/v1/iam/tokens/",
            params={"project_id": project_id},
            headers=self.auth_headers,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("tokens", data) if isinstance(data, dict) else data

    # -- traces / spans -------------------------------------------------------

    def list_traces(
        self,
        project_id: str,
        filters: list[str] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict:
        params: dict[str, Any] = {
            "project_id": project_id,
            "limit": limit,
            "offset": offset,
        }
        if filters:
            params["filter"] = filters
        resp = self._http.get(
            "/api/v1/traces/list",
            params=params,
            headers=self.auth_headers,
        )
        resp.raise_for_status()
        return resp.json()

    def get_trace(self, trace_id: str, project_id: str) -> dict:
        resp = self._http.get(
            f"/api/v1/traces/trace/{trace_id}",
            params={"project_id": project_id},
            headers=self.auth_headers,
        )
        resp.raise_for_status()
        return resp.json()

    # -- agents ---------------------------------------------------------------

    def list_agents(self, project_id: str | None = None) -> dict:
        params = {}
        if project_id:
            params["project_id"] = project_id
        resp = self._http.get(
            "/api/v1/agents/",
            params=params,
            headers=self.auth_headers,
        )
        resp.raise_for_status()
        return resp.json()

    def get_agent_detail(self, slug: str, project_id: str | None = None) -> dict:
        params = {}
        if project_id:
            params["project_id"] = project_id
        resp = self._http.get(
            f"/api/v1/agents/{slug}/detail",
            params=params,
            headers=self.auth_headers,
        )
        resp.raise_for_status()
        return resp.json()

    # -- prompts --------------------------------------------------------------

    def get_prompt(self, prompt_id: str) -> dict:
        resp = self._http.get(
            f"/api/v1/prompts/{prompt_id}",
            headers=self.auth_headers,
        )
        resp.raise_for_status()
        return resp.json()

    def list_prompts(self, project_id: str) -> list[dict]:
        resp = self._http.get(
            "/api/v1/prompts/",
            params={"project_id": project_id},
            headers=self.auth_headers,
        )
        resp.raise_for_status()
        return resp.json()

    def get_prompt_criteria(self, prompt_id: str) -> dict:
        resp = self._http.get(
            f"/api/v1/prompts/{prompt_id}/criteria",
            headers=self.auth_headers,
        )
        resp.raise_for_status()
        return resp.json()

    # -- jobs -----------------------------------------------------------------

    def list_jobs(
        self,
        project_id: str | None = None,
        job_type: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> dict:
        params: dict[str, Any] = {"limit": limit}
        if project_id:
            params["project_id"] = project_id
        if job_type:
            params["job_type"] = job_type
        if status:
            params["status"] = status
        resp = self._http.get("/api/v1/jobs/", params=params, headers=self.auth_headers)
        resp.raise_for_status()
        return resp.json()

    def get_job(self, job_id: str) -> dict:
        resp = self._http.get(f"/api/v1/jobs/{job_id}", headers=self.auth_headers)
        resp.raise_for_status()
        return resp.json()

    def delete_job(self, job_id: str):
        resp = self._http.delete(f"/api/v1/jobs/{job_id}", headers=self.auth_headers)
        if resp.status_code == 404:
            return
        resp.raise_for_status()

    def trigger_extract_templates(self, project_id: str | None = None) -> dict:
        """Trigger agent discovery, or adopt an already-running job.

        Celery Beat may have already kicked off discovery before the test
        gets here.  When the endpoint returns 400 ("already in progress"),
        find the existing PENDING/RUNNING job and return it so the test
        can poll it to completion as usual.
        """
        params = {}
        if project_id:
            params["project_id"] = project_id
        resp = self._http.post(
            "/api/v1/jobs/extract-templates",
            params=params,
            headers=self.auth_headers,
        )
        if resp.status_code == 400 and "already in progress" in resp.text.lower():
            jobs_resp = self.list_jobs(
                project_id=project_id,
                job_type="agent_discovery",
            )
            for job in jobs_resp.get("jobs", []):
                if job["status"] in ("pending", "running", "completed"):
                    return job
            resp.raise_for_status()
        resp.raise_for_status()
        return resp.json()

    def trigger_scoring(self, prompt_slug: str, project_id: str) -> dict:
        resp = self._http.post(
            f"/api/v1/jobs/{prompt_slug}/score",
            params={"project_id": project_id},
            headers=self.auth_headers,
        )
        if resp.status_code != 200:
            raise httpx.HTTPStatusError(
                f"{resp.status_code} for {resp.url}: {resp.text}",
                request=resp.request,
                response=resp,
            )
        return resp.json()

    def trigger_tuning(self, prompt_slug: str, project_id: str | None = None) -> dict:
        params = {}
        if project_id:
            params["project_id"] = project_id
        resp = self._http.post(
            f"/api/v1/jobs/{prompt_slug}/tune",
            params=params,
            headers=self.auth_headers,
        )
        if resp.status_code != 200:
            raise httpx.HTTPStatusError(
                f"{resp.status_code} for {resp.url}: {resp.text}",
                request=resp.request,
                response=resp,
            )
        return resp.json()

    # -- backtesting ----------------------------------------------------------

    def list_backtest_models(self) -> list[dict]:
        resp = self._http.get("/api/v1/backtesting/models", headers=self.auth_headers)
        resp.raise_for_status()
        return resp.json()

    def trigger_backtesting(
        self, prompt_id: str, models: list[str], max_spans: int = 50
    ) -> dict:
        resp = self._http.post(
            "/api/v1/backtesting/run",
            json={
                "prompt_id": prompt_id,
                "models": models,
                "max_spans": max_spans,
            },
            headers=self.auth_headers,
        )
        if resp.status_code != 200:
            raise httpx.HTTPStatusError(
                f"{resp.status_code} for {resp.url}: {resp.text}",
                request=resp.request,
                response=resp,
            )
        return resp.json()

    # -- suggestions ----------------------------------------------------------

    def list_suggestions(self, page: int | None = None) -> dict:
        params = {}
        if page is not None:
            params["page"] = page
        resp = self._http.get(
            "/api/v1/suggestions/",
            params=params,
            headers=self.auth_headers,
        )
        resp.raise_for_status()
        return resp.json()

    def get_suggestion(self, suggestion_id: str) -> dict:
        resp = self._http.get(
            f"/api/v1/suggestions/{suggestion_id}",
            headers=self.auth_headers,
        )
        resp.raise_for_status()
        return resp.json()

    # -- agent reviews --------------------------------------------------------

    def get_review_spans(self, slug: str, project_id: str | None = None) -> dict:
        params = {}
        if project_id:
            params["project_id"] = project_id
        resp = self._http.get(
            f"/api/v1/agent-reviews/{slug}/review-spans",
            params=params,
            headers=self.auth_headers,
        )
        resp.raise_for_status()
        return resp.json()

    def submit_span_feedback(
        self,
        span_id: str,
        feedback_type: str,
        rating: str,
        text: str | None = None,
    ) -> dict:
        resp = self._http.patch(
            f"/api/v1/spans/{span_id}/feedback",
            json={
                "feedback_type": feedback_type,
                "rating": rating,
                "text": text,
            },
            headers=self.auth_headers,
        )
        resp.raise_for_status()
        return resp.json()

    def sync_refresh_description(
        self,
        slug: str,
        span_ids: list[str],
        feedback: dict[str, dict[str, str]] | None = None,
        project_id: str | None = None,
    ) -> dict:
        params = {}
        if project_id:
            params["project_id"] = project_id
        body: dict[str, Any] = {"span_ids": span_ids}
        if feedback:
            body["feedback"] = feedback
        resp = self._http.post(
            f"/api/v1/agent-reviews/{slug}/sync-refresh-description",
            json=body,
            params=params,
            headers=self.auth_headers,
        )
        resp.raise_for_status()
        return resp.json()

    def update_description(
        self,
        slug: str,
        description: str,
        criteria: dict[str, list[str]],
        project_id: str | None = None,
    ) -> dict:
        params = {}
        if project_id:
            params["project_id"] = project_id
        resp = self._http.post(
            f"/api/v1/agent-reviews/{slug}/update-description",
            json={"description": description, "criteria": criteria},
            params=params,
            headers=self.auth_headers,
        )
        resp.raise_for_status()
        return resp.json()

    def mark_initial_review_complete(
        self, slug: str, project_id: str | None = None
    ) -> dict:
        params = {}
        if project_id:
            params["project_id"] = project_id
        resp = self._http.post(
            f"/api/v1/agent-reviews/{slug}/mark-initial-review-complete",
            params=params,
            headers=self.auth_headers,
        )
        resp.raise_for_status()
        return resp.json()

    def complete_review(
        self, slug: str, current_span_count: int, project_id: str | None = None
    ) -> dict:
        params: dict[str, Any] = {"current_span_count": current_span_count}
        if project_id:
            params["project_id"] = project_id
        resp = self._http.post(
            f"/api/v1/agent-reviews/{slug}/complete-review",
            params=params,
            headers=self.auth_headers,
        )
        resp.raise_for_status()
        return resp.json()

    # -- health ---------------------------------------------------------------

    def health(self) -> bool:
        try:
            resp = self._http.get("/health")
            return resp.status_code == 200
        except httpx.RequestError:
            return False
