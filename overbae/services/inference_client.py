"""Inference client for the vLLM serverless platform.

Requests go through InferenceAPIServer. Routing (gpu / weights / max_model_len)
is read from Postgres on the Django side and sent as headers — the gateway
does not keep a Volume JSON of deployed models.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import requests
from django.conf import settings

from modal_shared.modelfam import serve_image_key
from modal_shared.shared import WEIGHTS_PATH_HEADER
from modal_shared.shared import routing_headers as _routing_headers


class InferenceClientError(Exception):
    pass


class InferenceClient:
    def __init__(
        self,
        api_url: str | None = None,
        api_key: str | None = None,
        timeout: int = 120,
        *,
        base_url: str | None = None,
    ) -> None:
        url = base_url or api_url or settings.INFERENCE_API_URL
        self._base_url = url.rstrip("/")
        self._api_key = api_key or settings.INFERENCE_API_KEY
        self._timeout = timeout

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _headers_for(self, deployed=None, **routing) -> dict[str, str]:
        headers = dict(self._headers)
        gpu_type = routing.get("gpu_type") or getattr(deployed, "gpu_type", "") or ""
        weights_path = routing.get("weights_path") or getattr(deployed, "weights_path", "") or ""
        max_model_len = routing.get("max_model_len")
        if max_model_len is None:
            max_model_len = getattr(deployed, "max_model_len", None)
        serve_image = routing.get("serve_image") or ""
        if not serve_image:
            serve_image = serve_image_key(
                getattr(deployed, "base_model_id", "") or "",
                getattr(deployed, "model_id", "") or "",
            )
        adapter_path = routing.get("adapter_path") or getattr(deployed, "adapter_path", "") or ""
        lora_rank = routing.get("lora_rank") or getattr(deployed, "lora_rank", 0) or 0
        if gpu_type and weights_path and max_model_len is not None:
            headers.update(
                _routing_headers(
                    gpu_type=gpu_type,
                    weights_path=weights_path,
                    max_model_len=int(max_model_len),
                    serve_image=serve_image,
                    adapter_path=adapter_path,
                    lora_rank=int(lora_rank),
                )
            )
        return headers

    def _get(self, path: str) -> dict:
        url = f"{self._base_url}{path}"
        try:
            resp = requests.get(url, headers=self._headers, timeout=self._timeout)
        except requests.RequestException as exc:
            raise InferenceClientError(f"GET {url} failed: {exc}") from exc
        if not resp.ok:
            raise InferenceClientError(f"GET {url} returned {resp.status_code}: {resp.text[:400]}")
        return resp.json()

    def is_model_ready(self, model_id: str) -> bool:
        from overbae.models import DeployedModel

        return DeployedModel.objects.filter(
            model_id=model_id, status=DeployedModel.Status.READY
        ).exists()

    def chat_completions(
        self,
        *,
        model_id: str,
        messages: list[dict],
        stream: bool = False,
        temperature: float = 1.0,
        max_tokens: int | None = None,
        deployed=None,
        gpu_type: str = "",
        weights_path: str = "",
        max_model_len: int | None = None,
        **kwargs: Any,
    ) -> Any:
        payload: dict[str, Any] = {
            "model": model_id,
            "messages": messages,
            "stream": stream,
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        payload.update(kwargs)

        url = f"{self._base_url}/v1/chat/completions"
        headers = self._headers_for(
            deployed,
            gpu_type=gpu_type,
            weights_path=weights_path,
            max_model_len=max_model_len,
        )
        try:
            resp = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=self._timeout,
                stream=stream,
            )
        except requests.RequestException as exc:
            raise InferenceClientError(f"Request to InferenceAPIServer failed: {exc}") from exc

        if not resp.ok:
            raise InferenceClientError(
                f"InferenceAPIServer returned {resp.status_code}: {resp.text[:400]}"
            )
        if stream:
            return resp
        result = resp.json()
        if isinstance(result, dict) and "error" in result:
            raise InferenceClientError(f"InferenceAPIServer failed: {result['error']}")
        return result

    def stream_chat_completions(
        self,
        *,
        model_id: str,
        messages: list[dict],
        temperature: float = 1.0,
        max_tokens: int | None = None,
        deployed=None,
        gpu_type: str = "",
        weights_path: str = "",
        max_model_len: int | None = None,
        **kwargs: Any,
    ) -> Iterator[str]:
        """Yields raw SSE data lines (OpenAI-formatted) from the vLLM worker."""
        resp = self.chat_completions(
            model_id=model_id,
            messages=messages,
            stream=True,
            temperature=temperature,
            max_tokens=max_tokens,
            deployed=deployed,
            gpu_type=gpu_type,
            weights_path=weights_path,
            max_model_len=max_model_len,
            **kwargs,
        )
        try:
            for line in resp.iter_lines(chunk_size=1):
                if line:
                    decoded = line.decode("utf-8") if isinstance(line, bytes) else line
                    yield decoded + "\n\n"
        finally:
            resp.close()

    def check_health(self) -> bool:
        if not self._base_url:
            return False
        try:
            self._get("/health")
            return True
        except Exception:
            return False

    def delete_model(self, model_id: str, weights_path: str = "") -> None:
        """Delete this deployment's weights on the Volume, then mark the row deleted.

        An adapter deployment owns only its adapter — ``weights_path`` is a base shared with
        every other adapter on that model, so deleting it would break all of them.
        """
        from overbae.models import DeployedModel

        row = DeployedModel.objects.filter(model_id=model_id).first()
        if row is not None and row.adapter_path:
            weights_path = row.adapter_path
        elif not weights_path and row is not None:
            weights_path = row.weights_path

        if self._base_url:
            try:
                del_headers = dict(self._headers)
                if weights_path:
                    del_headers[WEIGHTS_PATH_HEADER] = weights_path
                resp = requests.delete(
                    f"{self._base_url}/models/{model_id}",
                    headers=del_headers,
                    timeout=30,
                )
                if not resp.ok and resp.status_code != 404:
                    raise InferenceClientError(
                        f"InferenceAPIServer delete returned {resp.status_code}: {resp.text[:400]}"
                    )
            except requests.RequestException as exc:
                raise InferenceClientError(f"Delete request failed: {exc}") from exc

        DeployedModel.objects.filter(model_id=model_id).update(
            status=DeployedModel.Status.DELETED,
            inference_url="",
        )


_client: InferenceClient | None = None


def get_inference_client() -> InferenceClient:
    global _client
    if _client is None:
        _client = InferenceClient()
    return _client
