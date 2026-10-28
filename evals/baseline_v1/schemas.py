"""Records exchanged between the baseline pipeline steps (one JSONL file per step)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Condition = Literal["url_only", "page_text"]
Task = Literal["summary", "article"]
ElementType = Literal["person", "organization", "place", "number", "date", "quote"]
Label = Literal["SUPPORTED", "CONTRADICTED", "NOT_IN_SOURCE"]
MatchStatus = Literal["found", "not_found"]


class SampleItem(BaseModel):
    item_id: str
    source: str
    url: str
    title: str
    published: str | None = None
    # Item whose snapshot is the reference text: itself, or the original for opaque duplicates.
    snapshot_id: str
    is_opaque: bool = False


class SnapshotMeta(BaseModel):
    snapshot_id: str
    url: str
    final_url: str
    fetched_at: datetime
    html_sha256: str
    text_sha256: str
    n_chars: int
    reviewed: bool = False


class RunRecord(BaseModel):
    run_id: str
    item_id: str
    condition: Condition
    task: Task
    repeat: int
    model: str
    prompt_version: str
    created_at: datetime
    output_text: str | None = None
    error: str | None = None
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


class CriticalElement(BaseModel):
    """Structured output schema of the extraction LLM."""

    type: ElementType
    value: str = Field(description="Exact span copied from the text, without normalization")
    context: str = Field(description="Sentence of the text containing the element (<= 200 chars)")


class ExtractedElements(BaseModel):
    elements: list[CriticalElement]


class ExtractionRecord(BaseModel):
    run_id: str
    model: str
    prompt_version: str
    created_at: datetime
    n_elements: int = 0
    error: str | None = None
    latency_ms: int | None = None


class ElementRecord(BaseModel):
    element_id: str  # derived from the content, see `extract.element_id`
    run_id: str
    item_id: str
    condition: Condition
    task: Task
    type: ElementType
    value: str
    context: str
    match_status: MatchStatus | None = None
    match_score: float | None = None


class HumanLabel(BaseModel):
    element_id: str
    label: Label
    note: str = ""
