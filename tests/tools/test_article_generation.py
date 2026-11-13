"""Specification tests of `find_issues` and `generate_article` (ADR-0009 point 4, ADR-0011,
ADR-0012, review finding V-9), plus the outcome log line of `generate_article`."""

import logging

import pytest

from dzdia_newsroom.llm import FakeLLM, StructuredOutputError
from dzdia_newsroom.tools.article import (
    ArticleDraft,
    GenerationFailedError,
    IssueKind,
    Paragraph,
    QuoteSegment,
    SentenceSegment,
    build_prompt,
    find_issues,
    generate_article,
    retry_feedback,
)
from dzdia_newsroom.tools.evidence import build_evidence

EVIDENCE = build_evidence(
    "Festival du film d’Oran\n"
    "Le festival s’est ouvert le 3 mai au théâtre régional.\n"
    "« Nous reviendrons l’an prochain », a déclaré Karim Ziani."
)
GOOD_QUOTE = "Nous reviendrons l’an prochain"
BAD_QUOTE = "Nous reviendrons l’année prochaine"


def sentence(text: str, *ids: str) -> SentenceSegment:
    return SentenceSegment(kind="sentence", text=text, evidence_ids=list(ids))


def quote(text: str, evidence_id: str = "E3") -> QuoteSegment:
    return QuoteSegment(kind="quote", speaker="Karim Ziani", text=text, evidence_id=evidence_id)


def draft(
    *paragraphs: list[SentenceSegment | QuoteSegment], title: str = "Festival"
) -> ArticleDraft:
    return ArticleDraft(title=title, paragraphs=[Paragraph(segments=p) for p in paragraphs])


OPENING = sentence("Le festival s’est ouvert le 3 mai.", "E2")
VALID = draft([OPENING, quote(GOOD_QUOTE)])


# --- find_issues -----------------------------------------------------------------------------


def test_a_valid_draft_has_no_issue() -> None:
    assert find_issues(VALID, EVIDENCE) == []


def test_a_sentence_without_id() -> None:
    [issue] = find_issues(draft([OPENING, sentence("Il a plu.")]), EVIDENCE)
    assert (issue.kind, issue.paragraph, issue.segment) == (IssueKind.NO_ID, 0, 1)
    assert issue.text == "Il a plu."


def test_one_issue_per_unknown_id() -> None:
    issues = find_issues(draft([sentence("Ouverture.", "E2", "E7", "E9")]), EVIDENCE)
    assert [(i.kind, i.detail) for i in issues] == [
        (IssueKind.UNKNOWN_ID, "E7"),
        (IssueKind.UNKNOWN_ID, "E9"),
    ]


def test_a_quote_citing_an_unknown_id() -> None:
    [issue] = find_issues(draft([quote(GOOD_QUOTE, evidence_id="E42")]), EVIDENCE)
    assert (issue.kind, issue.detail) == (IssueKind.QUOTE_UNKNOWN_ID, "E42")
    assert issue.text == GOOD_QUOTE


def test_a_quote_not_found_in_its_evidence() -> None:
    [issue] = find_issues(draft([OPENING], [quote(BAD_QUOTE)]), EVIDENCE)
    assert (issue.kind, issue.paragraph, issue.segment) == (IssueKind.QUOTE_NOT_IN_EVIDENCE, 1, 0)
    assert (issue.text, issue.detail) == (BAD_QUOTE, "E3")


def test_a_quote_checked_against_the_wrong_item_fails() -> None:
    [issue] = find_issues(draft([quote(GOOD_QUOTE, evidence_id="E2")]), EVIDENCE)
    assert issue.kind is IssueKind.QUOTE_NOT_IN_EVIDENCE


def test_typography_differences_are_not_issues() -> None:
    typographic = quote("« Nous reviendrons l'an prochain »")
    assert find_issues(draft([OPENING, typographic]), EVIDENCE) == []


def test_issues_come_in_document_order() -> None:
    issues = find_issues(
        draft([sentence("A."), quote(BAD_QUOTE)], [sentence("B.", "E9")]), EVIDENCE
    )
    assert [(i.paragraph, i.segment, i.kind) for i in issues] == [
        (0, 0, IssueKind.NO_ID),
        (0, 1, IssueKind.QUOTE_NOT_IN_EVIDENCE),
        (1, 0, IssueKind.UNKNOWN_ID),
    ]


@pytest.mark.parametrize("mark", ["«", "»", "“", "”", "„", '"'])
def test_quotation_marks_in_a_sentence(mark: str) -> None:
    marked = sentence(f"Le festival s’est ouvert {mark}le 3 mai.", "E2")
    [issue] = find_issues(draft([OPENING, marked]), EVIDENCE)
    assert (issue.kind, issue.paragraph, issue.segment) == (IssueKind.QUOTATION_MARKS, 0, 1)
    assert issue.text == marked.text


def test_apostrophes_alone_are_not_quotation_marks() -> None:
    apostrophes = sentence("L’ouverture s'est tenue le ‘3 mai’.", "E2")
    assert find_issues(draft([apostrophes, quote(GOOD_QUOTE)]), EVIDENCE) == []


def test_quotation_marks_in_the_title() -> None:
    title = "Le « Festival » s’ouvre"
    [issue] = find_issues(draft([OPENING, quote(GOOD_QUOTE)], title=title), EVIDENCE)
    assert (issue.kind, issue.paragraph, issue.segment) == (IssueKind.QUOTATION_MARKS, None, None)
    assert issue.text == title


def test_an_unknown_id_comes_before_quotation_marks_in_a_sentence() -> None:
    issues = find_issues(draft([sentence("Il a dit « oui ».", "E2", "E9")]), EVIDENCE)
    assert [i.kind for i in issues] == [IssueKind.UNKNOWN_ID, IssueKind.QUOTATION_MARKS]


def test_a_missing_id_comes_before_quotation_marks_in_a_sentence() -> None:
    issues = find_issues(draft([sentence("Il a dit « oui ».")]), EVIDENCE)
    assert [i.kind for i in issues] == [IssueKind.NO_ID, IssueKind.QUOTATION_MARKS]


def test_quotation_marks_inside_a_quote_are_not_an_issue() -> None:
    verbatim = quote("Nous reviendrons l’an prochain », a déclaré Karim Ziani")
    speaker = QuoteSegment(kind="quote", speaker="“Karim” Ziani", text=GOOD_QUOTE, evidence_id="E3")
    assert find_issues(draft([OPENING, verbatim, speaker]), EVIDENCE) == []


def test_a_draft_with_no_paragraph_is_empty() -> None:
    [issue] = find_issues(draft(), EVIDENCE)
    assert issue.kind is IssueKind.EMPTY
    assert issue.paragraph is None and issue.segment is None and issue.text == ""


def test_a_draft_with_only_an_empty_paragraph_is_empty() -> None:
    assert [i.kind for i in find_issues(draft([]), EVIDENCE)] == [IssueKind.EMPTY]


def test_an_empty_paragraph_next_to_a_valid_one_is_not_an_issue() -> None:
    assert find_issues(draft([], [OPENING, quote(GOOD_QUOTE)]), EVIDENCE) == []


def test_the_title_issue_comes_before_the_empty_issue() -> None:
    issues = find_issues(draft(title="« Festival »"), EVIDENCE)
    assert [i.kind for i in issues] == [IssueKind.QUOTATION_MARKS, IssueKind.EMPTY]


def test_a_draft_whose_only_sentence_is_blank_is_empty() -> None:
    [issue] = find_issues(draft([sentence("", "E2")]), EVIDENCE)
    assert issue.kind is IssueKind.EMPTY
    assert issue.paragraph is None and issue.segment is None and issue.text == ""


def test_whitespace_only_text_counts_as_blank() -> None:
    blank = draft([sentence(" \n \t", "E2")], [quote("   ")])
    assert [i.kind for i in find_issues(blank, EVIDENCE)] == [
        IssueKind.EMPTY,
        IssueKind.QUOTE_NOT_IN_EVIDENCE,
    ]


def test_blank_segments_keep_their_other_issues() -> None:
    issues = find_issues(draft([sentence("  ")]), EVIDENCE)
    assert [(i.kind, i.paragraph, i.segment) for i in issues] == [
        (IssueKind.EMPTY, None, None),
        (IssueKind.NO_ID, 0, 0),
    ]


def test_a_blank_segment_next_to_a_real_one_is_not_an_issue() -> None:
    assert find_issues(draft([sentence(" ", "E2"), OPENING], [quote(GOOD_QUOTE)]), EVIDENCE) == []


# --- generate_article ------------------------------------------------------------------------


async def test_a_valid_first_draft_uses_one_call() -> None:
    llm = FakeLLM([VALID])
    result = await generate_article(llm, EVIDENCE)
    assert result.article == VALID
    assert len(result.attempts) == 1 and result.attempts[0].issues == ()
    assert result.removed_quotes == 0
    assert result.model == llm.model and result.prompt_version == "v2-article-1"
    assert llm.prompts == [build_prompt(EVIDENCE)]


async def test_the_retry_prompt_is_the_prompt_plus_the_feedback() -> None:
    first = draft([sentence("Ouverture.", "E9")])
    llm = FakeLLM([first, VALID])
    result = await generate_article(llm, EVIDENCE)
    issues = find_issues(first, EVIDENCE)
    assert llm.prompts[1] == f"{build_prompt(EVIDENCE)}\n\n{retry_feedback(issues)}"
    assert result.article == VALID
    assert [a.issues for a in result.attempts] == [tuple(issues), ()]
    assert result.attempts[0].draft == first


async def test_an_id_error_left_after_the_retry_fails_the_generation() -> None:
    bad = draft([sentence("Ouverture.")])
    llm = FakeLLM([bad, bad])
    with pytest.raises(GenerationFailedError) as info:
        await generate_article(llm, EVIDENCE)
    assert len(info.value.attempts) == 2
    assert len(llm.prompts) == 2


async def test_a_quote_fixed_at_the_retry_is_kept() -> None:
    llm = FakeLLM([draft([OPENING, quote(BAD_QUOTE)]), VALID])
    result = await generate_article(llm, EVIDENCE)
    assert result.article == VALID and result.removed_quotes == 0


async def test_a_quote_turned_into_a_sentence_at_the_retry() -> None:
    rewritten = draft([OPENING, sentence("Karim Ziani a promis de revenir.", "E3")])
    llm = FakeLLM([draft([OPENING, quote(BAD_QUOTE)]), rewritten])
    result = await generate_article(llm, EVIDENCE)
    assert result.article == rewritten and result.removed_quotes == 0


async def test_a_quote_still_failing_after_the_retry_is_removed() -> None:
    bad = draft([OPENING, quote(BAD_QUOTE)])
    llm = FakeLLM([bad, bad])
    result = await generate_article(llm, EVIDENCE)
    assert result.article == draft([OPENING])
    assert result.removed_quotes == 1
    assert len(result.attempts) == 2


async def test_a_paragraph_emptied_by_the_removal_disappears() -> None:
    bad = draft([OPENING], [quote(BAD_QUOTE)])
    llm = FakeLLM([bad, bad])
    result = await generate_article(llm, EVIDENCE)
    assert result.article == draft([OPENING]) and result.removed_quotes == 1


async def test_an_article_emptied_by_the_removal_fails_the_generation() -> None:
    bad = draft([quote(BAD_QUOTE)])
    llm = FakeLLM([bad, bad])
    with pytest.raises(GenerationFailedError):
        await generate_article(llm, EVIDENCE)


async def test_an_article_left_with_no_text_by_the_removal_fails_the_generation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="dzdia_newsroom.tools.article")
    bad = draft([sentence("  ", "E2"), quote(BAD_QUOTE)])
    llm = FakeLLM([bad, bad])
    with pytest.raises(GenerationFailedError) as info:
        await generate_article(llm, EVIDENCE)
    assert str(info.value) == "no text left after removing 1 quote(s)"
    assert len(info.value.attempts) == 2 and len(llm.prompts) == 2
    [record] = [r for r in caplog.records if r.name == "dzdia_newsroom.tools.article"]
    assert (record.outcome, record.removed_quotes) == ("failed", 1)  # type: ignore[attr-defined]


async def test_a_real_sentence_next_to_a_removed_quote_still_succeeds() -> None:
    bad = draft([OPENING, sentence(" ", "E2"), quote(BAD_QUOTE)])
    result = await generate_article(FakeLLM([bad, bad]), EVIDENCE)
    assert result.article == draft([OPENING, sentence(" ", "E2")])
    assert result.removed_quotes == 1


async def test_id_errors_win_over_quote_errors_after_the_retry() -> None:
    bad = draft([sentence("Ouverture."), quote(BAD_QUOTE)])
    llm = FakeLLM([bad, bad])
    with pytest.raises(GenerationFailedError):
        await generate_article(llm, EVIDENCE)


async def test_a_quote_citing_an_unknown_id_after_the_retry_is_removed() -> None:
    bad = draft([OPENING, quote(GOOD_QUOTE, evidence_id="E42")])
    llm = FakeLLM([bad, bad])
    result = await generate_article(llm, EVIDENCE)
    assert result.article == draft([OPENING])
    assert result.removed_quotes == 1
    assert len(result.attempts) == 2
    assert "cite la preuve E42, qui n'existe pas" in llm.prompts[1]


async def test_both_removable_quote_errors_are_removed_after_the_retry() -> None:
    bad = draft([OPENING, quote(GOOD_QUOTE, evidence_id="E42"), quote(BAD_QUOTE)])
    llm = FakeLLM([bad, bad])
    result = await generate_article(llm, EVIDENCE)
    assert result.article == draft([OPENING]) and result.removed_quotes == 2


async def test_quotation_marks_left_after_the_retry_fail_the_generation() -> None:
    bad = draft([sentence("Il a dit « oui ».", "E2"), quote(GOOD_QUOTE)])
    llm = FakeLLM([bad, bad])
    with pytest.raises(GenerationFailedError) as info:
        await generate_article(llm, EVIDENCE)
    assert len(info.value.attempts) == 2 and len(llm.prompts) == 2


async def test_quotation_marks_fixed_at_the_retry() -> None:
    llm = FakeLLM([draft([sentence("Il a dit « oui ».", "E2"), quote(GOOD_QUOTE)]), VALID])
    result = await generate_article(llm, EVIDENCE)
    assert result.article == VALID and result.removed_quotes == 0


async def test_quotation_marks_win_over_quote_errors_after_the_retry() -> None:
    bad = draft([sentence("Il a dit « oui ».", "E2"), quote(BAD_QUOTE)])
    llm = FakeLLM([bad, bad])
    with pytest.raises(GenerationFailedError):
        await generate_article(llm, EVIDENCE)


async def test_an_empty_draft_left_after_the_retry_fails_the_generation() -> None:
    llm = FakeLLM([draft(), draft()])
    with pytest.raises(GenerationFailedError) as info:
        await generate_article(llm, EVIDENCE)
    assert len(info.value.attempts) == 2


async def test_a_blank_draft_left_after_the_retry_fails_the_generation() -> None:
    blank = draft([sentence("  ", "E2")])
    llm = FakeLLM([blank, blank])
    with pytest.raises(GenerationFailedError) as info:
        await generate_article(llm, EVIDENCE)
    assert [[i.kind for i in a.issues] for a in info.value.attempts] == [
        [IssueKind.EMPTY],
        [IssueKind.EMPTY],
    ]
    assert "empty" in str(info.value)


async def test_an_empty_draft_uses_the_retry() -> None:
    llm = FakeLLM([draft(), VALID])
    result = await generate_article(llm, EVIDENCE)
    assert result.article == VALID
    first = result.attempts[0]
    assert [issue.kind for issue in first.issues] == [IssueKind.EMPTY]
    assert first.draft == draft()


async def test_an_unparsable_output_uses_the_retry() -> None:
    llm = FakeLLM(['{"title": "Festival", "paragraphs": [', VALID])
    result = await generate_article(llm, EVIDENCE)
    assert result.article == VALID
    first = result.attempts[0]
    assert first.draft is None and first.raw_text == '{"title": "Festival", "paragraphs": ['
    assert [issue.kind for issue in first.issues] == [IssueKind.UNPARSABLE]
    assert "JSON" in llm.prompts[1]


async def test_two_unparsable_outputs_fail_the_generation() -> None:
    llm = FakeLLM(["", "not json"])
    with pytest.raises(GenerationFailedError) as info:
        await generate_article(llm, EVIDENCE)
    assert len(info.value.attempts) == 2 and len(llm.prompts) == 2


async def test_an_api_error_propagates_without_using_the_retry() -> None:
    llm = FakeLLM([TimeoutError("the client already retried")])
    with pytest.raises(TimeoutError):
        await generate_article(llm, EVIDENCE)
    assert len(llm.prompts) == 1


async def test_a_structured_output_error_is_not_mistaken_for_an_api_error() -> None:
    """The fake raises StructuredOutputError like the real client, not a pydantic error."""
    with pytest.raises(StructuredOutputError):
        await FakeLLM(["oops"]).generate_structured("p", ArticleDraft)
    await generate_article(FakeLLM(["oops", VALID]), EVIDENCE)


# --- outcome log line ------------------------------------------------------------------------

LOGGER = "dzdia_newsroom.tools.article"


def _outcome_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == LOGGER]


async def test_a_success_logs_one_line_with_counts_only(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER)
    bad = draft([OPENING, quote(BAD_QUOTE)])
    await generate_article(FakeLLM([bad, bad], model="m"), EVIDENCE)

    [record] = _outcome_records(caplog)
    assert record.levelno == logging.INFO
    assert (record.model, record.attempts, record.outcome) == ("m", 2, "ok")  # type: ignore[attr-defined]
    kinds = {"quote_not_in_evidence": 1}
    assert record.issues == [kinds, kinds]  # type: ignore[attr-defined]
    assert record.removed_quotes == 1  # type: ignore[attr-defined]
    logged = f"{record.getMessage()} {record.__dict__}"
    assert BAD_QUOTE not in logged and "Karim" not in logged and "festival" not in logged


async def test_a_failure_logs_one_line_and_keeps_text_out_of_the_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER)
    bad = draft([sentence("Il a dit « oui ».", "E2"), quote(BAD_QUOTE)])
    with pytest.raises(GenerationFailedError) as info:
        await generate_article(FakeLLM([bad, '{"title": "secret"'], model="m"), EVIDENCE)

    [record] = _outcome_records(caplog)
    assert record.levelno == logging.INFO
    assert (record.attempts, record.outcome, record.removed_quotes) == (2, "failed", 0)  # type: ignore[attr-defined]
    assert record.issues == [  # type: ignore[attr-defined]
        {"quotation_marks": 1, "quote_not_in_evidence": 1},
        {"unparsable": 1},
    ]
    for text in (f"{record.getMessage()} {record.__dict__}", str(info.value)):
        assert "oui" not in text and "secret" not in text and BAD_QUOTE not in text
    assert "unparsable" in str(info.value)
