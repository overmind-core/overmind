"""Hosted GLiNER PII NER service on Modal, and the SOLE PII engine: the backend runs no local
regex/checksum validators, so every class the system detects must be emitted here. Checksum-precise
types GLiNER misses (card / IBAN / SSN / API keys) are therefore not detected at all.

Deploy to the overmind workspace, main environment:
    MODAL_PROFILE=overmind uv run modal deploy overbae/modal/modal_pii_ner.py --env main

Auth: ``Authorization: Bearer <token>``, where the token lives in the Modal secret named by
``PII_NER_SECRET_NAME`` under key ``PII_NER_TOKEN``. The backend reads that token and the deployed
URL from Django settings (``PII_NER_ENDPOINT_URL`` / ``PII_NER_TOKEN``).
"""

from __future__ import annotations

import os

import modal
from pydantic import BaseModel

APP_NAME = os.environ.get("PII_NER_APP_NAME", "overclaw-pii-ner")
SECRET_NAME = os.environ.get("PII_NER_SECRET_NAME", "overclaw-pii-ner")
# T4 beats L4 here on cost AND throughput (~13.5 vs ~8.4 rows/s per container);
# ~12 of them sustain the accepted ~120–140 rows/s.
GPU = os.environ.get("PII_NER_GPU", "T4")
MAX_CONTAINERS = int(os.environ.get("PII_NER_MAX_CONTAINERS", "12"))
# One request per container: each gets the whole GPU, and throughput scales by
# container count rather than by packing.
MAX_INPUTS = int(os.environ.get("PII_NER_MAX_INPUTS", "1"))
# Idle seconds before scaledown. Higher buys warm reuse across back-to-back scans,
# lower cuts idle GPU cost on one-shot scans.
SCALEDOWN_WINDOW = int(os.environ.get("PII_NER_SCALEDOWN", "60"))
INFER_BATCH = int(os.environ.get("PII_NER_INFER_BATCH", "32"))
# fp16 roughly doubles GLiNER throughput at no recall cost for NER; PII_NER_FP16=0
# is the kill switch if an op ever misbehaves.
USE_FP16 = os.environ.get("PII_NER_FP16", "1") == "1"

MODEL_NAME = os.environ.get("PII_NER_MODEL", "urchade/gliner_multi_pii-v1")

# The full detection surface, kept small so inference stays fast. Checksum types
# GLiNER misses (card/IBAN/SSN/keys) are deliberately absent.
GLINER_LABELS = ["person", "location", "organization", "email", "phone number"]
LABEL_MAP = {
    "person": "PERSON_NAME",
    "location": "LOCATION",
    "organization": "ORG",
    "email": "EMAIL",
    "phone number": "PHONE",
}

# GLiNER truncates at a few hundred tokens, so long rows are scanned as overlapping
# char windows and merged.
WINDOW_CHARS = 1500
WINDOW_OVERLAP = 150
MAX_TEXT_CHARS = 16_384
SCORE_THRESHOLD = 0.45


class InferRequest(BaseModel):
    texts: list[str]
    labels: list[str] | None = None
    threshold: float | None = None


def _download_model() -> None:
    """Bake the GLiNER weights into the image at build time (no cold re-download)."""
    from gliner import GLiNER

    GLiNER.from_pretrained(MODEL_NAME)


image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "gliner==0.2.13",
        "torch==2.4.1",
        "transformers==4.45.2",
        "huggingface_hub>=0.24",
        "fastapi[standard]",
    )
    .run_function(_download_model)
)

app = modal.App(APP_NAME)


@app.cls(
    gpu=GPU,
    image=image,
    secrets=[modal.Secret.from_name(SECRET_NAME)],
    max_containers=MAX_CONTAINERS,
    scaledown_window=SCALEDOWN_WINDOW,
    timeout=180,
)
@modal.concurrent(max_inputs=MAX_INPUTS)
class PiiNer:
    @modal.enter()
    def load(self) -> None:
        import torch
        from gliner import GLiNER

        torch.set_grad_enabled(False)
        torch.backends.cudnn.benchmark = True
        self.model = GLiNER.from_pretrained(MODEL_NAME)
        self.cuda = torch.cuda.is_available()
        if self.cuda:
            self.model = self.model.to("cuda")
            if USE_FP16:
                self.model = self.model.half()
        self.model.eval()
        dev = "cuda-fp16" if (self.cuda and USE_FP16) else ("cuda" if self.cuda else "cpu")
        print(f"[PiiNer] model={MODEL_NAME} device={dev} infer_batch={INFER_BATCH}")

    def _windows(self, text: str) -> list[tuple[int, str]]:
        """Yield ``(offset, window_text)`` slices covering the whole text."""
        text = text[:MAX_TEXT_CHARS]
        if len(text) <= WINDOW_CHARS:
            return [(0, text)]
        out: list[tuple[int, str]] = []
        step = WINDOW_CHARS - WINDOW_OVERLAP
        for start in range(0, len(text), step):
            chunk = text[start : start + WINDOW_CHARS]
            if chunk:
                out.append((start, chunk))
            if start + WINDOW_CHARS >= len(text):
                break
        return out

    def _predict(self, texts: list[str], labels: list[str], threshold: float) -> list[list[dict]]:
        flat: list[str] = []
        owners: list[int] = []
        offsets: list[int] = []
        for i, text in enumerate(texts):
            for off, win in self._windows(text or ""):
                flat.append(win)
                owners.append(i)
                offsets.append(off)
        if not flat:
            return [[] for _ in texts]

        # GLiNER 0.2.x has no micro-batching arg, so chunk the flattened windows here
        # to bound GPU memory.
        batch: list[list[dict]] = []
        for i in range(0, len(flat), INFER_BATCH):
            batch.extend(
                self.model.batch_predict_entities(
                    flat[i : i + INFER_BATCH], labels, threshold=threshold
                )
            )
        per_text: list[dict[tuple[int, int, str], None]] = [dict() for _ in texts]
        for win_idx, ents in enumerate(batch):
            owner = owners[win_idx]
            base = offsets[win_idx]
            for ent in ents:
                canon = LABEL_MAP.get(str(ent.get("label", "")).lower())
                if canon is None:
                    continue
                start = base + int(ent["start"])
                end = base + int(ent["end"])
                per_text[owner][(start, end, canon)] = None
        results: list[list[dict]] = []
        for spans in per_text:
            merged = [{"start": s, "end": e, "label": lab} for (s, e, lab) in sorted(spans)]
            results.append(merged)
        return results

    @modal.asgi_app()
    def web(self):
        from fastapi import Body, FastAPI, Header, HTTPException

        api = FastAPI(title=APP_NAME)
        expected = os.environ.get("PII_NER_TOKEN", "")

        def _auth(authorization: str | None) -> None:
            if not expected or authorization != f"Bearer {expected}":
                raise HTTPException(status_code=401, detail="invalid or missing bearer token")

        @api.get("/health")
        def health(authorization: str | None = Header(default=None)) -> dict:
            _auth(authorization)
            return {"status": "ok", "model": MODEL_NAME, "labels": GLINER_LABELS}

        @api.post("/infer")
        def infer(
            payload: InferRequest = Body(...),
            authorization: str | None = Header(default=None),
        ) -> dict:
            _auth(authorization)
            labels = payload.labels or GLINER_LABELS
            threshold = payload.threshold if payload.threshold is not None else SCORE_THRESHOLD
            results = self._predict(payload.texts, labels, threshold)
            return {"results": results}

        return api
