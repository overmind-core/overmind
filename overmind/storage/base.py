"""Abstract storage backend for Overmind artifacts.

Every save / load / delete of Overmind setup data goes through a
``StorageBackend``.  The single concrete implementation is
:class:`overmind.storage.api.ApiBackend`, which talks to the Overmind backend
exclusively through the generated ``overmind.openapi_client`` SDK.

Local filesystem persistence is no longer supported: setup specs, datasets,
and policies are read from and written to the API.

Three artifact categories are exposed:

1. **Eval spec** — dict describing the agent's evaluation criteria
   (stored as fields on the ``Agent`` record).
2. **Dataset** — list of test-case dicts (stored as ``Dataset`` + ``Datapoint``
   rows; ``Agent.active_dataset`` points to the current version).
3. **Policy** — Markdown text plus optional structured data (stored on
   ``Agent.policy_markdown`` / ``Agent.policy_data``).
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class StorageBackend(ABC):
    """Common interface for reading and writing Overmind setup artifacts.

    A backend instance is bound to a single agent (identified by its remote
    UUID and local file path).  ``set_job_id`` may bind it to a specific
    optimization job for backends that need it; the default implementation is
    a no-op.
    """

    # ------------------------------------------------------------------
    # Eval spec
    # ------------------------------------------------------------------

    @abstractmethod
    def save_spec(self, spec: dict) -> None:
        """Persist the evaluation spec."""

    @abstractmethod
    def load_spec(self) -> dict | None:
        """Return the evaluation spec, or ``None`` if not found."""

    @abstractmethod
    def delete_spec(self) -> None:
        """Delete the evaluation spec (silently ignores missing items)."""

    # ------------------------------------------------------------------
    # Dataset
    # ------------------------------------------------------------------

    @abstractmethod
    def save_dataset(
        self,
        datapoints: list[dict],
        *,
        source: str = "synthetic",
        generator_model: str = "",
        policy_hash: str = "",
        metadata: dict | None = None,
        make_active: bool = True,
    ) -> dict | None:
        """Persist a dataset version.

        Returns a dict with at minimum ``{"id": str, "version": int}`` on
        success, or ``None`` on failure.  Callers should use ``result["id"]``
        for the dataset UUID and ``result.get("version")`` for the version
        number.
        """

    @abstractmethod
    def load_dataset(self) -> list[dict] | None:
        """Return the active dataset's datapoints, or ``None`` if not found."""

    @abstractmethod
    def delete_dataset(self) -> None:
        """Delete the active dataset (silently ignores missing items)."""

    # ------------------------------------------------------------------
    # Policy
    # ------------------------------------------------------------------

    @abstractmethod
    def save_policy(self, policy_md: str, policy_data: dict | None = None) -> None:
        """Persist the policy."""

    @abstractmethod
    def load_policy(self) -> str | None:
        """Return the policy Markdown, or ``None`` if not found."""

    @abstractmethod
    def delete_policy(self) -> None:
        """Delete the policy (silently ignores missing items)."""

    # ------------------------------------------------------------------
    # Identity helpers
    # ------------------------------------------------------------------

    def get_agent_id(self) -> str | None:
        """Return the remote agent UUID, or ``None`` if unknown."""
        return None

    def set_job_id(self, job_id: str) -> None:
        """Associate this backend with a specific optimization job."""

    # ------------------------------------------------------------------
    # Bulk cleanup
    # ------------------------------------------------------------------

    def clear_setup_spec(self) -> None:
        """Delete the eval spec, dataset, and policy in one call."""
        self.delete_spec()
        self.delete_dataset()
        self.delete_policy()
