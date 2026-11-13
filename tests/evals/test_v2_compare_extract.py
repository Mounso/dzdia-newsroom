"""The `extract` and `match` steps of the v2 comparison (ADR-0013, point 3): what is extracted,
idempotence and retry, the extractor guard, match statuses, counters only. No network."""

from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from dzdia_newsroom.llm import Structured
from dzdia_newsroom.llm.fake import FAKE_USAGE
from evals.baseline_v1.jsonl import read_jsonl, write_jsonl
from evals.baseline_v1.schemas import (
    CriticalElement,
    ElementRecord,
    ElementType,
    ExtractedElements,
    ExtractionRecord,
)
from evals.v2.compare import CompareError
from evals.v2.compare import __main__ as compare_main
from evals.v2.compare.extract import extract_compare, extractable_text, match_compare
from evals.v2.compare.paths import ComparePaths
from tests.evals.test_v2_compare_run import NOW, _baseline_run, _paths, _record

EXTRACTOR = "gemini-extractor"
THINKING = "low"
PLAIN = "Texte secret du bras plain avec Karim Ziani."
RENDERED = "Texte secret rendu du bras v2, le 3 mai."
FAILED: dict[str, Any] = {"outcome": "failed", "rendered_text": "Texte d'un échec"}


def _elements(*pairs: tuple[ElementType, str]) -> ExtractedElements:
    return ExtractedElements(
        elements=[CriticalElement(type=t, value=v, context=f"Phrase avec {v}.") for t, v in pairs]
    )


def _extraction(run_id: str, **update: Any) -> ExtractionRecord:
    record = ExtractionRecord(
        run_id=run_id,
        model=EXTRACTOR,
        thinking_level=THINKING,
        prompt_version="extract-v1",
        created_at=NOW,
    )
    return record.model_copy(update=update)


def _setup(
    tmp_path: Path, runs: list[Any], baseline_extractions: list[ExtractionRecord] | None = None
) -> ComparePaths:
    paths = _paths(tmp_path)
    if baseline_extractions is None:
        baseline_extractions = [
            _extraction("tsa-00:page_text:article:0"),
            _extraction("tsa-01:page_text:article:0"),
        ]
    write_jsonl(paths.baseline.extractions, baseline_extractions)
    write_jsonl(paths.runs, runs)
    return paths


class _Extractor:
    """The extractor's calls run concurrently, in no fixed order: each reply is chosen by a
    marker of the text in the prompt, not by call order."""

    def __init__(
        self,
        replies: dict[str, ExtractedElements | Exception],
        model: str,
        thinking_level: str | None,
    ) -> None:
        self.model = model
        self.thinking_level = thinking_level
        self.prompts: list[str] = []
        self._replies = replies

    async def generate_structured[M: BaseModel](
        self, prompt: str, schema: type[M]
    ) -> Structured[M]:
        self.prompts.append(prompt)
        [reply] = [r for marker, r in self._replies.items() if marker in prompt]
        if isinstance(reply, Exception):
            raise reply
        return Structured(schema.model_validate(reply.model_dump()), FAKE_USAGE, latency_ms=1)


def _llm(
    replies: dict[str, ExtractedElements | Exception] | None = None,
    *,
    model: str = EXTRACTOR,
    thinking_level: str | None = THINKING,
) -> _Extractor:
    return _Extractor(replies or {}, model, thinking_level)


# --- what is extracted -----------------------------------------------------------------------


def test_extractable_text() -> None:
    assert extractable_text(_record("tsa-00", "plain_prompt", output_text=PLAIN)) == PLAIN
    assert extractable_text(_record("tsa-00", "v2", rendered_text=RENDERED)) == RENDERED
    assert extractable_text(_record("tsa-00", "v2", **FAILED)) is None
    error = {"error": "TimeoutError()", "output_text": PLAIN, "rendered_text": RENDERED}
    assert extractable_text(_record("tsa-00", "plain_prompt", **error)) is None
    assert extractable_text(_record("tsa-00", "v2", **error)) is None
    assert extractable_text(_record("tsa-00", "plain_prompt")) is None  # no output text


async def test_extracts_plain_outputs_and_successful_v2_articles_only(tmp_path: Path) -> None:
    paths = _setup(
        tmp_path,
        [
            _record("tsa-00", "v2", rendered_text=RENDERED),
            _record("tsa-00", "plain_prompt", output_text=PLAIN),
            _record("tsa-01", "v2", **FAILED),  # failed validation: never extracted
            _record("tsa-01", "plain_prompt", output_text=PLAIN, error="TimeoutError()"),
        ],
    )
    llm = _llm(
        {RENDERED: _elements(("date", "3 mai")), PLAIN: _elements(("person", "Karim Ziani"))}
    )
    summary = await extract_compare(paths, llm)

    assert len(llm.prompts) == 2
    assert all("Texte d'un échec" not in p for p in llm.prompts)
    extractions = read_jsonl(paths.extractions, ExtractionRecord)
    assert sorted(x.run_id for x in extractions) == [
        "tsa-00:plain_prompt:article:0",
        "tsa-00:v2:article:0",
    ]
    assert all(x.model == EXTRACTOR and x.thinking_level == THINKING for x in extractions)
    elements = read_jsonl(paths.elements, ElementRecord)
    assert sorted((e.condition, e.item_id, e.type, e.value) for e in elements) == [
        ("plain_prompt", "tsa-00", "person", "Karim Ziani"),
        ("v2", "tsa-00", "date", "3 mai"),
    ]
    assert all(e.element_id.startswith(f"{e.run_id}#") for e in elements)
    assert all(e.match_status is None for e in elements)
    assert (summary.extracted, summary.errors, summary.n_elements) == (2, 0, 2)
    assert (summary.input_tokens, summary.output_tokens, summary.thinking_tokens) == (20, 10, 0)
    assert not paths.baseline.elements.exists()  # the baseline is never written


async def test_extract_is_idempotent_and_retries_an_extraction_error(tmp_path: Path) -> None:
    paths = _setup(
        tmp_path,
        [
            _record("tsa-00", "v2", rendered_text=RENDERED),
            _record("tsa-00", "plain_prompt", output_text=PLAIN),
        ],
    )
    first = await extract_compare(
        paths, _llm({RENDERED: TimeoutError("slow"), PLAIN: _elements(("person", "Karim Ziani"))})
    )
    assert (first.extracted, first.errors, first.n_elements) == (2, 1, 1)

    llm = _llm({RENDERED: _elements(("date", "3 mai"))})
    second = await extract_compare(paths, llm)
    assert len(llm.prompts) == 1
    assert (second.extracted, second.errors, second.n_elements) == (1, 0, 2)
    in_file = read_jsonl(paths.extractions, ExtractionRecord)
    assert sorted(x.run_id for x in in_file[:2]) == [
        "tsa-00:plain_prompt:article:0",
        "tsa-00:v2:article:0",
    ]
    assert [(x.run_id, x.error is None) for x in in_file[2:]] == [("tsa-00:v2:article:0", True)]
    assert sum(x.error is not None for x in in_file) == 1  # the error record stays

    third = _llm()
    assert (await extract_compare(paths, third)).extracted == 0
    assert third.prompts == []
    assert len(read_jsonl(paths.elements, ElementRecord)) == 2


# --- the extractor guard ---------------------------------------------------------------------


async def _refused(paths: ComparePaths, match: str, **llm: Any) -> None:
    fake = _llm(**llm)
    with pytest.raises(CompareError, match=match):
        await extract_compare(paths, fake)
    assert fake.prompts == []
    assert not paths.extractions.exists() and not paths.elements.exists()


RUNS = [_record("tsa-00", "plain_prompt", output_text=PLAIN)]


@pytest.mark.parametrize(
    "llm", [{"model": "gemini-other"}, {"thinking_level": "high"}, {"thinking_level": None}]
)
async def test_refuses_an_extractor_that_differs(tmp_path: Path, llm: dict[str, Any]) -> None:
    await _refused(_setup(tmp_path, RUNS), "differs from the baseline", **llm)


async def test_refuses_when_baseline_extractions_disagree(tmp_path: Path) -> None:
    extractions = [
        _extraction("tsa-00:page_text:article:0"),
        _extraction("tsa-01:page_text:article:0", model="gemini-other"),
    ]
    await _refused(_setup(tmp_path, RUNS, extractions), "differs from the baseline")


async def test_the_guard_reads_the_latest_successful_page_text_article_extractions(
    tmp_path: Path,
) -> None:
    runs = [
        _baseline_run("tsa-00"),
        _baseline_run("tsa-00", run_id="u", condition="url_only"),
        _baseline_run("tsa-00", run_id="s", task="summary"),
    ]
    extractions = [
        _extraction("tsa-00:page_text:article:0", model="old"),  # superseded
        _extraction("tsa-00:page_text:article:0"),
        _extraction("u", model="other-1"),
        _extraction("s", model="other-2"),
        _extraction("tsa-01:page_text:article:0", model="other-3", error="TimeoutError()"),
    ]
    paths = _setup(tmp_path, RUNS, extractions)
    write_jsonl(paths.baseline.runs, runs)
    summary = await extract_compare(paths, _llm({PLAIN: _elements()}))
    assert summary.extracted == 1


@pytest.mark.parametrize(
    "extractions",
    [
        [],
        [_extraction("tsa-00:page_text:article:0", error="TimeoutError()")],
        [_extraction("not-a-baseline-run")],
    ],
)
async def test_refuses_when_the_baseline_has_no_successful_extraction(
    tmp_path: Path, extractions: list[ExtractionRecord]
) -> None:
    await _refused(_setup(tmp_path, RUNS, extractions), "No successful extraction")


# --- match -----------------------------------------------------------------------------------


async def test_match_writes_the_status_and_score_of_every_element(tmp_path: Path) -> None:
    paths = _setup(
        tmp_path,
        [
            _record("tsa-00", "v2", rendered_text=RENDERED),
            _record("tsa-01", "plain_prompt", output_text=PLAIN),
        ],
    )
    await extract_compare(
        paths,
        _llm(
            {
                RENDERED: _elements(("date", "3 mai"), ("place", "Constantine")),
                PLAIN: _elements(("person", "Karim Ziani"), ("quote", "Nous partirons demain")),
            }
        ),
    )
    assert match_compare(paths) == 4
    elements = read_jsonl(paths.elements, ElementRecord)
    assert sorted((e.value, e.match_status, e.match_score) for e in elements) == [
        ("3 mai", "found", 100.0),
        ("Constantine", "not_found", 0.0),
        ("Karim Ziani", "found", 100.0),
        ("Nous partirons demain", "not_found", 0.0),
    ]


# --- CLI: counters only ----------------------------------------------------------------------


def test_the_cli_prints_counters_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _setup(
        tmp_path,
        [
            _record("tsa-00", "v2", rendered_text=RENDERED),
            _record("tsa-00", "plain_prompt", output_text=PLAIN),
        ],
    )
    llm = _llm(
        {RENDERED: _elements(("date", "3 mai")), PLAIN: _elements(("person", "Karim Ziani"))}
    )
    monkeypatch.setattr(ComparePaths, "default", lambda: paths)
    monkeypatch.setattr(compare_main, "_extractor", lambda: llm)
    compare_main.main(["extract"])
    compare_main.main(["match"])
    out = capsys.readouterr().out

    assert out.splitlines() == [
        f"Extracting 2 runs with {EXTRACTOR} ({THINKING})",
        "Extracted 2 runs, 0 extraction errors (re-run the step to retry them).",
        "2 elements in total.",
        "Tokens this pass: input 20, output 10, thinking 0",
        "Matched 2 elements.",
    ]
    secrets = [PLAIN, RENDERED, "3 mai", "Karim Ziani", "found", "plain_prompt", ":v2:"]
    secrets += [e.element_id for e in read_jsonl(paths.elements, ElementRecord)]
    assert all(s not in out for s in secrets)


def test_the_cli_prints_the_extractor_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _setup(tmp_path, RUNS)
    monkeypatch.setattr(ComparePaths, "default", lambda: paths)
    monkeypatch.setattr(compare_main, "_extractor", lambda: _llm(model="gemini-other"))
    with pytest.raises(SystemExit, match="differs from the baseline"):
        compare_main.main(["extract"])
