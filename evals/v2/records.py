"""Record fields shared by the runs of the tryout and of the comparison: one arm output
(`PlainOutput` or `V2Output`) as the output fields of a run record.

The record classes (`TryoutRun`, `CompareRun`) declare these fields under the same names; the
functions below return them as an `OutputFields` mapping, passed to `model_copy(update=...)`.
"""

from collections.abc import Mapping
from typing import Any, Literal, TypedDict

from pydantic import BaseModel

from evals.v2.arms import PlainOutput, V2Output


class IssueDetailRecord(BaseModel):
    """One issue of an attempt (decision U1): read by the tryout's `issues` step."""

    kind: str
    paragraph: int | None = None
    segment: int | None = None
    text: str = ""  # the flagged segment's text, or the title
    detail: str = ""  # e.g. the unknown or cited evidence id


class AttemptRecord(BaseModel):
    issues: dict[str, int]  # number of issues per kind
    input_tokens: int | None = None
    output_tokens: int | None = None
    thinking_tokens: int | None = None
    latency_ms: int | None = None
    raw_text: str | None = None  # unparsable outputs only
    # In the order the validator reports them; empty in records written before decision U1.
    issue_details: list[IssueDetailRecord] = []


class OutputFields(TypedDict, total=False):
    """The output fields of a run record, by name. Both arms: `n_output_chars` (length of the
    stripped output text; v2: rendered), tokens and latency (v2: summed over the attempts)."""

    n_output_chars: int | None
    input_tokens: int | None
    output_tokens: int | None
    thinking_tokens: int | None
    latency_ms: int | None
    # v2 only
    outcome: Literal["ok", "failed"]
    n_evidence: int
    attempts: list[AttemptRecord]
    retried: bool
    removed_quotes: int | None
    kept_quotes: int | None
    n_sentences: int | None
    rendered_text: str | None
    draft: dict[str, Any] | None
    # plain only
    output_text: str


def plain_fields(plain: PlainOutput) -> OutputFields:
    return OutputFields(
        output_text=plain.text,
        n_output_chars=len(plain.text.strip()),
        input_tokens=plain.usage.input_tokens,
        output_tokens=plain.usage.output_tokens,
        latency_ms=plain.latency_ms,
    )


def v2_fields(v2: V2Output) -> OutputFields:
    usage = v2.usage
    return OutputFields(
        outcome=v2.outcome,
        n_evidence=v2.n_evidence,
        attempts=[
            AttemptRecord(
                issues=a.issues,
                input_tokens=a.usage.input_tokens,
                output_tokens=a.usage.output_tokens,
                thinking_tokens=a.usage.thinking_tokens,
                latency_ms=a.latency_ms,
                raw_text=a.raw_text,
                issue_details=[
                    IssueDetailRecord(
                        kind=d.kind,
                        paragraph=d.paragraph,
                        segment=d.segment,
                        text=d.text,
                        detail=d.detail,
                    )
                    for d in a.issue_details
                ],
            )
            for a in v2.attempts
        ],
        retried=v2.retried,
        removed_quotes=v2.removed_quotes,
        kept_quotes=v2.kept_quotes,
        n_sentences=v2.n_sentences,
        rendered_text=v2.rendered_text,
        draft=v2.draft,
        n_output_chars=len(v2.rendered_text.strip()) if v2.rendered_text else None,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        thinking_tokens=usage.thinking_tokens,
        latency_ms=v2.latency_ms,
    )


def issue_kinds(issues: Mapping[str, int]) -> str:
    """`kind=n` pairs in kind order, or `-`: counters only, no model text."""
    return ",".join(f"{kind}={n}" for kind, n in sorted(issues.items())) or "-"
