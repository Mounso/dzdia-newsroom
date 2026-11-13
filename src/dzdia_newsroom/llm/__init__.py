"""LLM client shared by production and the evals (ADR-0009, point 1): the client that is
measured is the client that runs."""

from dzdia_newsroom.llm.base import (
    Generation,
    Structured,
    StructuredGenerator,
    StructuredOutputError,
    TextGenerator,
    Usage,
)
from dzdia_newsroom.llm.fake import FakeLLM
from dzdia_newsroom.llm.gemini import GeminiClient
from dzdia_newsroom.llm.retry import RequestSpacer, retry_async

__all__ = [
    "FakeLLM",
    "GeminiClient",
    "Generation",
    "RequestSpacer",
    "Structured",
    "StructuredGenerator",
    "StructuredOutputError",
    "TextGenerator",
    "Usage",
    "retry_async",
]
