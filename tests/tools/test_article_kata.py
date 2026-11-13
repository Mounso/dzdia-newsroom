"""Specification of Abdou's kata: `find_issues` and `generate_article` (ADR-0009 point 4,
ADR-0011, review finding V-9).

The bodies raise NotImplementedError until Abdou writes them, so every test here is an expected
failure on NotImplementedError only: any other failure (a wrong result) is reported as a real
failure, and a test that passes shows as XPASS. Remove the marker once both functions exist.
"""

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

pytestmark = pytest.mark.xfail(
    raises=NotImplementedError, reason="kata: Abdou writes find_issues and generate_article"
)

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


def draft(*paragraphs: list[SentenceSegment | QuoteSegment]) -> ArticleDraft:
    return ArticleDraft(title="Festival", paragraphs=[Paragraph(segments=p) for p in paragraphs])


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
    assert (issue.kind, issue.detail) == (IssueKind.UNKNOWN_ID, "E42")


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


async def test_id_errors_win_over_quote_errors_after_the_retry() -> None:
    bad = draft([sentence("Ouverture."), quote(BAD_QUOTE)])
    llm = FakeLLM([bad, bad])
    with pytest.raises(GenerationFailedError):
        await generate_article(llm, EVIDENCE)


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
