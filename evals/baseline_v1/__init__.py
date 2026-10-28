"""Hallucination baseline of the legacy v1 bot (protocol: docs/adr/0001-*.md, ADR-0001).

Pipeline, one CLI step each (`python -m evals.baseline_v1 <step>`):
sample -> snapshot -> review-done -> run -> extract -> match -> export-labels
-> (human labels the CSV) -> import-labels -> report
"""
