"""Standalone package shared by Modal workers, Baseten training, and Django.

Deliberately lives OUTSIDE the ``overbae`` package: ``overbae/__init__.py``
imports Celery, which configures Django settings on import. Modal entrypoint
files (``modal_sft_worker.py`` etc.) get re-imported inside bare containers —
and by CI, via `modal deploy`, without Django ever having been set up — so
nothing under here may import anything from ``overbae`` or depend on Django,
torch, or any non-stdlib package.
"""
