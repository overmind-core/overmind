from __future__ import annotations

import itertools
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import modal
from asgiref.sync import sync_to_async
from modal.exception import NotFoundError

_ids = itertools.count(1)


class _Method:
    def __init__(self, sync: Callable[..., Any]) -> None:
        self._sync = sync

    def __call__(self, *args, **kwargs):
        return self._sync(*args, **kwargs)

    async def aio(self, *args, **kwargs):
        return await sync_to_async(self._sync)(*args, **kwargs)


@dataclass
class FakeCall:
    cloud: FakeModal
    name: str
    handler: Callable[..., Any]
    args: tuple
    kwargs: dict
    held: bool
    object_id: str = field(default_factory=lambda: f"fc-fake-{next(_ids)}")
    state: str = "pending"
    result: Any = None
    error: BaseException | None = None
    children: list[FakeCall] = field(default_factory=list)
    on_get: Callable[[], Any] | None = None
    unreachable: BaseException | None = None

    def run(self) -> None:
        try:
            self.result = self.handler(*self.args, **self.kwargs)
            self.state = "done"
        except Exception as exc:  # noqa: BLE001
            self.error = exc
            self.state = "failed"

    def _get(self, timeout: float | None = None):
        if self.on_get is not None:
            self.on_get()
        if self.unreachable is not None:
            raise self.unreachable
        if self.state == "pending":
            raise TimeoutError(self.object_id)
        if self.state == "cancelled":
            raise modal.exception.InputCancellation(self.object_id)
        if self.error is not None:
            raise self.error
        return self.result

    def _cancel(self, *args, **kwargs) -> None:
        self.cloud.cancelled.append((self.object_id, kwargs))
        if self.state == "pending":
            self.state = "cancelled"

    def _node(self):
        from modal.call_graph import InputStatus

        status = {
            "pending": InputStatus.PENDING,
            "done": InputStatus.SUCCESS,
            "failed": InputStatus.FAILURE,
            "cancelled": InputStatus.TERMINATED,
            "timeout": InputStatus.TIMEOUT,
        }[self.state]
        children = [child._node() for child in self.children]
        return SimpleNamespace(function_call_id=self.object_id, status=status, children=children)

    @property
    def get(self) -> _Method:
        return _Method(self._get)

    @property
    def cancel(self) -> _Method:
        return _Method(self._cancel)

    @property
    def get_call_graph(self) -> _Method:
        return _Method(lambda: [self._node()])


class FakeFunction:
    def __init__(self, cloud: FakeModal, app: str, name: str) -> None:
        self.cloud, self.app, self.name = cloud, app, name
        self.options: dict[str, Any] = {}

    def _handler(self) -> tuple[Callable[..., Any], bool]:
        key = (self.app, self.name)
        if key not in self.cloud.handlers:
            raise NotFoundError(f"Function {self.app}/{self.name} is not deployed.")
        return self.cloud.handlers[key]

    def _remote(self, *args, **kwargs):
        handler, _ = self._handler()
        self.cloud.log.append(("remote", self.name, args, kwargs))
        return handler(*args, **kwargs)

    def _spawn(self, *args, **kwargs) -> FakeCall:
        handler, held = self._handler()
        call = FakeCall(self.cloud, self.name, handler, args, kwargs, held)
        self.cloud.log.append(("spawn", self.name, args, kwargs))
        self.cloud.calls[call.object_id] = call
        if not held:
            call.run()
        return call

    @property
    def remote(self) -> _Method:
        return _Method(self._remote)

    @property
    def spawn(self) -> _Method:
        return _Method(self._spawn)

    def _current_stats(self):
        self.cloud.log.append(("stats", self.name, (), {}))
        stats = self.cloud.worker_stats
        if isinstance(stats, BaseException):
            raise stats
        if stats is None:
            raise NotFoundError(f"{self.app}/{self.name} has no running pool.")
        return SimpleNamespace(**stats)

    @property
    def get_current_stats(self) -> _Method:
        return _Method(self._current_stats)

    def with_options(self, **options) -> FakeFunction:
        self.options.update(options)
        return self


class _FakeInstance:
    def __init__(self, cloud: FakeModal, app: str, cls: str) -> None:
        self._cloud, self._app, self._cls = cloud, app, cls

    def __getattr__(self, method: str) -> FakeFunction:
        return FakeFunction(self._cloud, self._app, f"{self._cls}.{method}")


@dataclass
class FakeModal:
    handlers: dict[tuple[str, str], tuple[Callable[..., Any], bool]] = field(default_factory=dict)
    calls: dict[str, FakeCall] = field(default_factory=dict)
    log: list[tuple[str, str, tuple, dict]] = field(default_factory=list)
    cancelled: list[tuple[str, dict]] = field(default_factory=list)
    worker_stats: dict[str, Any] | BaseException | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def deploy(
        self, app: str, name: str, handler: Callable[..., Any], *, held: bool = False
    ) -> None:
        self.handlers[(app, name)] = (handler, held)

    def adopt(self, object_id: str, name: str = "operation", **fields: Any) -> FakeCall:
        call = FakeCall(self, name, lambda: None, (), {}, True, object_id=object_id, **fields)
        self.calls[object_id] = call
        return call

    def spawns(self) -> list[str]:
        return [name for kind, name, _, _ in self.log if kind == "spawn"]

    def pending(self, name_prefix: str) -> list[FakeCall]:
        return [
            c
            for c in self.calls.values()
            if c.state == "pending" and c.name.startswith(name_prefix)
        ]

    def release(self, name_prefix: str) -> int:
        calls = self.pending(name_prefix)
        for call in calls:
            call.run()
        return len(calls)

    def fail(self, name_prefix: str, error: Exception) -> int:
        calls = self.pending(name_prefix)
        for call in calls:
            call.error = error
            call.state = "failed"
        return len(calls)

    def called(self, name_prefix: str) -> list[str]:
        return [kind for kind, name, _, _ in self.log if name.startswith(name_prefix)]

    def install(self, monkeypatch) -> FakeModal:
        cloud = self

        class Function:
            @staticmethod
            def from_name(app: str, name: str, **_) -> FakeFunction:
                return FakeFunction(cloud, app, name)

        class FunctionCall:
            @staticmethod
            def from_id(call_id: str) -> FakeCall:
                if call_id not in cloud.calls:
                    raise NotFoundError(call_id)
                return cloud.calls[call_id]

        class Cls:
            @staticmethod
            def from_name(app: str, cls: str, **_):
                return lambda **__: _FakeInstance(cloud, app, cls)

        monkeypatch.setattr(modal, "Function", Function)
        monkeypatch.setattr(modal, "Cls", Cls)
        monkeypatch.setattr(modal, "FunctionCall", FunctionCall)
        return self


SFT_APP = "overmind-sft"


@dataclass
class SftBackend:
    cloud: FakeModal
    runs: dict[str, dict[str, Any]] = field(default_factory=dict)
    uploads: list[str] = field(default_factory=list)

    def install(self) -> SftBackend:
        from modal_shared.stacks import TRAIN_FUNCTION_NAMES

        for name in TRAIN_FUNCTION_NAMES.values():
            self.cloud.deploy(SFT_APP, name, self._train, held=True)
            self.cloud.deploy(SFT_APP, f"prepare_{name}", self._prepare, held=True)
        self.cloud.deploy(SFT_APP, "upload_dataset", self._upload)
        self.cloud.deploy(SFT_APP, "get_progress", self._progress)
        self.cloud.deploy(SFT_APP, "mark_cancelled", self._cancelled)
        self.cloud.deploy("overmind-register", "fetch_base_model", lambda **_: {"ok": True})
        return self

    def _prepare(self, preparation_id: str, request: dict) -> dict:
        return {
            "ready": True,
            "max_tokens": 64,
            "incompatible_rows": 0,
            "rows": len(request["rows"]),
        }

    def _upload(self, run_id: str, data_jsonl: str, val_jsonl=None, preparation_id=None) -> dict:
        self.uploads.append(run_id)
        self.runs[run_id] = {"status": "running", "steps": 2}
        return {"run_id": run_id, "rows": len(data_jsonl.splitlines())}

    def crash(self, message: str = "CUDA out of memory") -> None:
        for run in self.runs.values():
            run["status"] = "failed"
        self.cloud.fail("sft_", RuntimeError(message))

    def _train(self, run_id: str, env: dict, *, gpu_type: str = "", gpu_count: int = 1) -> dict:
        self.runs[run_id]["status"] = "succeeded"
        return {"run_id": run_id, "status": "succeeded"}

    def _cancelled(self, run_id: str) -> dict:
        self.runs.setdefault(run_id, {"steps": 0})["status"] = "cancelled"
        return {"run_id": run_id, "status": "cancelled"}

    def _progress(self, run_id: str) -> dict:
        run = self.runs.get(run_id)
        if run is None:
            return {"run_id": run_id, "found": False}
        metrics = [
            {
                "event": "BT_PROGRESS",
                "step": step,
                "total_steps": run["steps"],
                "loss": 1.0 / step,
                "epoch": 1.0,
            }
            for step in range(1, run["steps"] + 1)
        ]
        return {
            "run_id": run_id,
            "found": True,
            "meta": {"status": run["status"]},
            "metrics": metrics,
            "has_final_checkpoint": run["status"] == "succeeded" or bool(run.get("final")),
        }


@dataclass
class ServingBackend:
    cloud: FakeModal
    url: str
    warmed: list[str] = field(default_factory=list)

    def install(self) -> ServingBackend:
        register = "overmind-register"
        self.cloud.deploy(register, "fetch_base_model", lambda **_: {"ok": True})
        self.cloud.deploy(register, "sync_modal_checkpoint_to_s3", lambda **_: {"ok": True})
        self.cloud.deploy(register, "publish_adapter", self._publish_adapter)
        self.cloud.deploy(register, "RegisterAPIServer.register", self._merged)
        self.cloud.deploy(register, "RegisterAPIServer.register_base", self._merged)
        self.cloud.deploy("overmind-inference", "InferenceAPIServer.register_model", self._register)
        self.cloud.deploy("overmind-inference", "pre_warm", self._pre_warm)
        return self

    def _publish_adapter(self, *, base_model: str, **_) -> dict:
        return {
            "base_path": f"/weights/.base_models/{base_model.replace('/', '--')}",
            "lora_rank": 16,
        }

    def _merged(self, *, model_id: str, **_) -> dict:
        return {"weights_path": f"/weights/{model_id}", "quantization": "bf16", "num_parameters": 0}

    def _register(self, **_) -> str:
        return self.url

    def _pre_warm(self, *, model_id: str, **_) -> dict:
        self.warmed.append(model_id)
        return {"ok": True}
