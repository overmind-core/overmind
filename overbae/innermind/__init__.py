"""Innermind: one scoring pipeline for live spans, trajectories, dataset rows and signals.

A span, a dataset row and a generated output are all scored by the same
``SpanProcessor`` against the rows of the eval matrix plus the span's own
``overmind.expectations``.
"""
