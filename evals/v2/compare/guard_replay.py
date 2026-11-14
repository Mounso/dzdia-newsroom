"""The `guard-replay` step of the comparison (ADR-0014, point 7): the quote guard of ADR-0014
applied to the recorded v2 drafts of the comparison.

- It calls no model and writes no file: each recorded draft is checked again with `find_issues`
  against the evidence rebuilt from the frozen snapshot text (`load_inputs`, then
  `build_evidence`), as the `run` step built it.
- Every comparison item needs a latest v2 record without a technical error. A failed generation
  has no draft: it is listed and counts nowhere else.
- A replayed draft may only gain QUOTE_NOT_QUOTED_IN_SOURCE issues; any other issue means the
  recorded draft no longer validates against the rebuilt evidence, and the step stops.
- It prints counters only: never a quote's text, a speaker, an element value or an element id.
"""

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import ValidationError

from dzdia_newsroom.tools.article import ArticleDraft, IssueKind, QuoteSegment, find_issues
from dzdia_newsroom.tools.evidence import build_evidence
from evals.baseline_v1.jsonl import latest_by, read_jsonl
from evals.baseline_v1.schemas import ElementRecord, Label
from evals.v2.compare import CompareError
from evals.v2.compare.labels import latest_labels
from evals.v2.compare.paths import ComparePaths
from evals.v2.compare.report import elements_by_run
from evals.v2.compare.run import CompareRun, compare_run_id, comparison_items, load_inputs
from evals.v2.records import issue_kinds

# Short names of the labels, in the order they are printed; "pending": no label yet.
LABEL_NAMES: Mapping[Label, str] = {"SUPPORTED": "S", "CONTRADICTED": "C", "NOT_IN_SOURCE": "N"}
PENDING = "pending"
_COUNTED = (*LABEL_NAMES.values(), PENDING)


@dataclass(frozen=True)
class ArticleReplay:
    item_id: str
    kept: int  # quote segments in the recorded draft: kept by the recorded prompt
    rejected: int  # QUOTE_NOT_QUOTED_IN_SOURCE issues: quotes the new rule rejects
    labels: Mapping[str, int]  # the article's v2 quote elements per label: S, C, N, pending


@dataclass(frozen=True)
class GuardReplay:
    articles: tuple[ArticleReplay, ...]  # the replayed articles, in sample order
    failed: tuple[str, ...]  # items whose v2 generation failed: not replayed
    prompt_versions: tuple[str, ...]  # of the replayed records

    @property
    def kept(self) -> int:
        return sum(a.kept for a in self.articles)

    @property
    def rejected(self) -> int:
        return sum(a.rejected for a in self.articles)

    @property
    def with_quotes_before(self) -> int:
        """Replayed articles with at least one quote before the rule."""
        return sum(a.kept > 0 for a in self.articles)

    @property
    def with_quotes_after(self) -> int:
        """Replayed articles with at least one quote the rule does not reject."""
        return sum(a.kept - a.rejected > 0 for a in self.articles)


def _latest_v2_record(
    runs: Mapping[str, CompareRun], item_id: str, paths: ComparePaths
) -> CompareRun:
    record = runs.get(compare_run_id(item_id, "v2"))
    if record is None:
        raise CompareError(f"No v2 record for {item_id} in {paths.runs}: run the `run` step first.")
    if record.error is not None:
        raise CompareError(
            f"The latest v2 record of {item_id} is a technical error: run the `run` step first."
        )
    return record


def _recorded_draft(record: CompareRun) -> ArticleDraft:
    if record.draft is None:
        raise CompareError(f"The v2 record of {record.item_id} has outcome ok but no draft.")
    try:
        return ArticleDraft.model_validate(record.draft)
    except ValidationError:
        # The pydantic message would quote the draft: name the article only.
        raise CompareError(
            f"The recorded v2 draft of {record.item_id} does not parse as an ArticleDraft."
        ) from None


def _label_counts(elements: list[ElementRecord], labels: Mapping[str, Label]) -> dict[str, int]:
    counts = Counter(
        LABEL_NAMES[label] if (label := labels.get(e.element_id)) is not None else PENDING
        for e in elements
        if e.type == "quote"
    )
    return {name: counts[name] for name in _COUNTED}


def guard_replay(paths: ComparePaths) -> GuardReplay:
    """The replay over the comparison items, in sample order. Raises `CompareError` on a missing
    or changed snapshot, a missing record, a latest record in technical error, or a recorded
    draft with an issue of a kind other than QUOTE_NOT_QUOTED_IN_SOURCE."""
    inputs = load_inputs(paths, comparison_items(paths))
    runs = latest_by(read_jsonl(paths.runs, CompareRun), lambda r: r.run_id)
    elements = elements_by_run(read_jsonl(paths.elements, ElementRecord))
    labels = {element_id: label.label for element_id, label in latest_labels(paths).items()}

    articles: list[ArticleReplay] = []
    failed: list[str] = []
    versions: set[str] = set()
    for given in inputs:
        item_id = given.item.item_id
        record = _latest_v2_record(runs, item_id, paths)
        if record.outcome == "failed":
            failed.append(item_id)
            continue
        draft = _recorded_draft(record)
        issues = find_issues(draft, build_evidence(given.text))
        other = [i for i in issues if i.kind is not IssueKind.QUOTE_NOT_QUOTED_IN_SOURCE]
        if other:
            kinds = issue_kinds(Counter(i.kind.value for i in other))
            raise CompareError(
                f"The recorded v2 draft of {item_id} no longer validates against the rebuilt "
                f"evidence: {kinds}."
            )
        articles.append(
            ArticleReplay(
                item_id=item_id,
                kept=sum(isinstance(s, QuoteSegment) for p in draft.paragraphs for s in p.segments),
                rejected=len(issues),
                labels=_label_counts(elements.get(record.run_id, []), labels),
            )
        )
        versions.add(record.prompt_version)
    return GuardReplay(tuple(articles), tuple(failed), tuple(sorted(versions)))


def replay_lines(replay: GuardReplay) -> list[str]:
    """Counters only, one line per replayed article, then the failed generations and the
    totals."""
    lines = [
        "Quote guard of ADR-0014 replayed on the recorded v2 drafts: no model call, no file "
        "written."
    ]
    for a in replay.articles:
        counts = ", ".join(f"{name} {a.labels.get(name, 0)}" for name in _COUNTED)
        lines.append(
            f"{a.item_id} quotes kept {a.kept}, rejected {a.rejected}; labels of the v2 quotes: "
            f"{counts}"
        )
    lines += [f"{item_id} failed generation, not replayed" for item_id in replay.failed]
    lines += [
        f"Replayed {len(replay.articles)} articles: quotes kept {replay.kept}, rejected "
        f"{replay.rejected}; articles with at least one quote: {replay.with_quotes_before} "
        f"before the rule, {replay.with_quotes_after} after.",
        f"Prompt versions of the replayed records: {', '.join(replay.prompt_versions) or 'n/a'}",
    ]
    return lines
