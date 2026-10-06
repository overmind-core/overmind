"""HTTP client for the optimizer-experiments write API."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import requests

from overmind.api import open_session


class OptimizerAPI:
    """``/api/optimizer-experiments/`` as the client-driven loop uses it."""

    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._session = open_session(api_key)

    def create_experiment(
        self,
        *,
        capability_id: str,
        dataset_id: str,
        eval_set_id: str = "",
        mode: str = "optimize",
        num_iterations: int = 5,
        num_candidates_per_iteration: int = 3,
        max_iterations_without_improvement: int = 3,
        model_ids: list[str] | None = None,
        openrouter_key_source: str = "local",
    ) -> dict:
        payload: dict[str, Any] = {
            "capability": capability_id,
            "dataset": dataset_id,
            "mode": mode,
            "num_iterations": num_iterations,
            "num_candidates_per_iteration": num_candidates_per_iteration,
            "max_iterations_without_improvement": max_iterations_without_improvement,
            "openrouter_key_source": openrouter_key_source,
        }
        if eval_set_id:
            payload["eval_set"] = eval_set_id
        if model_ids:
            payload["model_ids"] = model_ids
        return self._post(f"{self.base_url}/api/optimizer-experiments/", payload)

    def get_experiment(self, experiment_id: str) -> dict:
        experiment = self._get(self._experiment_url(experiment_id))
        # Retrieve does not nest iterations; the loop's state machine reads them.
        try:
            experiment["iterations"] = self.list_iterations(experiment_id)
        except Exception:  # noqa: BLE001 — status callers still work without the tree
            experiment.setdefault("iterations", [])
        return experiment

    def list_iterations(self, experiment_id: str) -> list[dict]:
        data = self._get(self._experiment_url(experiment_id, "iterations"))
        if isinstance(data, dict) and "results" in data:
            return list(data["results"])
        return data if isinstance(data, list) else []

    def set_template(self, experiment_id: str, template: str) -> dict:
        return self._post(self._experiment_url(experiment_id, "template"), {"template": template})

    def add_iteration(self, experiment_id: str, *, order: int, name: str, candidates: list[dict]) -> dict:
        body = {"order": order, "name": name, "candidates": candidates}
        return self._post(self._experiment_url(experiment_id, "add-iteration"), body)

    def post_results(self, experiment_id: str, results: list[dict]) -> dict:
        return self._post(self._experiment_url(experiment_id, "results"), {"results": results}, timeout=60)

    def evaluate(self, experiment_id: str, order: int) -> dict:
        return self._post(self._experiment_url(experiment_id, "evaluate"), {"order": order})

    def complete(self, experiment_id: str) -> dict:
        return self._post(self._experiment_url(experiment_id, "complete"), {})

    def export_dataset(self, dataset_id: str, cell_id: str, cache_dir: Path, *, fingerprint: str = "") -> Path:
        """The used version's JSONL, cached as ``<cell>.jsonl`` with its frame
        fingerprint beside it. A cache whose fingerprint is missing or differs
        from the version is fetched again."""
        if not cell_id:
            raise RuntimeError("The experiment has no used version.")
        cache_dir.mkdir(parents=True, exist_ok=True)
        cached = cache_dir / f"{cell_id}.jsonl"
        hash_file = cache_dir / f"{cell_id}.hash"
        if cached.exists() and hash_file.exists():
            stored = hash_file.read_text().strip()
            if stored and (not fingerprint or stored == fingerprint):
                return cached
        response = self._session.get(
            f"{self.base_url}/api/datasets/{dataset_id}/export/",
            params={"fmt": "jsonl", "cell": cell_id},
            timeout=120,
            stream=True,
        )
        _raise_with_detail(response)
        partial = cached.with_suffix(".part")
        with partial.open("wb") as fh:
            for chunk in response.iter_content(chunk_size=65_536):
                fh.write(chunk)
        partial.replace(cached)
        hash_file.write_text(response.headers.get("X-Overmind-Fingerprint") or fingerprint)
        return cached

    def _experiment_url(self, experiment_id: str, *parts: str) -> str:
        return "/".join([f"{self.base_url}/api/optimizer-experiments/{experiment_id}", *parts, ""])

    def _get(self, url: str) -> Any:
        response = self._session.get(url, timeout=30)
        _raise_with_detail(response)
        return response.json()

    def _post(self, url: str, body: dict[str, Any], *, timeout: int = 30) -> dict:
        response = self._session.post(url, json=body, timeout=timeout)
        _raise_with_detail(response)
        return response.json()


def _raise_with_detail(response: requests.Response) -> None:
    """Raise with every field-level reason the server gave: a refusal such as
    "X is a train dataset; this needs eval." is the message, not "Bad Request"."""
    if response.ok:
        return
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        detail = " ".join(
            f"{field}: {'; '.join(map(str, reason)) if isinstance(reason, list) else reason}"
            for field, reason in body.items()
        )
    else:
        detail = (response.text or "").strip()
    raise requests.HTTPError(
        f"HTTP {response.status_code} from {response.url}: {detail[:600] or response.reason}", response=response
    )
