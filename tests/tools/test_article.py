"""The article tool outside validation and retry: schema, prompt, quote check, removal,
rendering."""

import unicodedata

import pytest

from dzdia_newsroom.tools.article import (
    PROMPT_VERSION,
    ArticleDraft,
    Issue,
    IssueKind,
    Paragraph,
    QuoteSegment,
    SentenceSegment,
    build_prompt,
    check_quote,
    has_quotation_marks,
    remove_failed_quotes,
    render_text,
    retry_feedback,
)
from dzdia_newsroom.tools.evidence import build_evidence

EVIDENCE = build_evidence(
    "Festival du film d’Oran\n"
    "Le festival s’est ouvert le 3 mai au théâtre régional.\n"
    "« Nous reviendrons l’an prochain avec plus de films », a déclaré Karim Ziani."
)


def sentence(text: str, *ids: str) -> SentenceSegment:
    return SentenceSegment(kind="sentence", text=text, evidence_ids=list(ids))


def quote(text: str, evidence_id: str = "E3", speaker: str = "Karim Ziani") -> QuoteSegment:
    return QuoteSegment(kind="quote", speaker=speaker, text=text, evidence_id=evidence_id)


# --- schema ----------------------------------------------------------------------------------


def test_the_kind_field_selects_the_segment_type_when_parsing() -> None:
    raw = (
        '{"title": "T", "paragraphs": [{"segments": ['
        '{"kind": "sentence", "text": "Une phrase.", "evidence_ids": ["E2"]},'
        '{"kind": "quote", "speaker": "K", "text": "Nous reviendrons", "evidence_id": "E3"}'
        "]}]}"
    )
    segments = ArticleDraft.model_validate_json(raw).paragraphs[0].segments
    assert isinstance(segments[0], SentenceSegment) and isinstance(segments[1], QuoteSegment)


def test_the_schema_has_no_discriminator() -> None:
    """Spike V-18: the SDK rejects the `oneOf` of a discriminated union in `response_schema`."""
    schema = str(ArticleDraft.model_json_schema())
    assert "discriminator" not in schema and "oneOf" not in schema


# --- prompt ----------------------------------------------------------------------------------


def test_the_prompt_carries_the_evidence_inside_delimiters() -> None:
    prompt = build_prompt(EVIDENCE)
    assert PROMPT_VERSION == "v2-article-1"
    assert prompt.rstrip().endswith("</sources>")
    assert '<evidence id="E2">Le festival s’est ouvert le 3 mai' in prompt
    assert "non fiable" in prompt


def test_the_prompt_forbids_quotation_marks_in_the_title() -> None:
    assert "- Le titre ne cite aucune preuve et ne contient pas de guillemets." in build_prompt(
        EVIDENCE
    )


# --- quotation marks -------------------------------------------------------------------------


@pytest.mark.parametrize("mark", ["«", "»", "“", "”", "„", '"'])
def test_each_quotation_mark_is_detected(mark: str) -> None:
    assert has_quotation_marks(f"Il a dit {mark}oui.")


def test_apostrophes_are_not_quotation_marks() -> None:
    assert not has_quotation_marks("L’ouverture s'est tenue.")
    assert not has_quotation_marks("‘ʼ")


# --- quote check -----------------------------------------------------------------------------


def test_an_exact_quote_returns_the_source_passage() -> None:
    assert check_quote("Nous reviendrons", EVIDENCE.items[2].text) == "Nous reviendrons"


def test_typography_and_whitespace_are_folded_but_the_source_passage_is_returned() -> None:
    passage = check_quote(
        "Nous reviendrons l'an prochain avec  plus de films", EVIDENCE.items[2].text
    )
    assert passage == "Nous reviendrons l’an prochain avec plus de films"


def test_quotation_marks_around_the_quote_are_ignored() -> None:
    assert check_quote("« Nous reviendrons »", EVIDENCE.items[2].text) == "Nous reviendrons"


@pytest.mark.parametrize(
    "reworded",
    [
        "nous reviendrons",  # case counts
        "Nous reviendrons l'année prochaine",  # a reworded word
        "Nous reviendrons … plus de films",  # no ellipsis support
        "Nous reviendrons, l'an prochain",  # punctuation counts
        "   ",
    ],
)
def test_anything_but_the_exact_words_fails(reworded: str) -> None:
    assert check_quote(reworded, EVIDENCE.items[2].text) is None


def test_decomposed_accents_match_their_composed_form() -> None:
    decomposed = unicodedata.normalize("NFD", "au théâtre régional")
    assert decomposed != "au théâtre régional"
    assert check_quote(decomposed, EVIDENCE.items[1].text) == "au théâtre régional"


# --- removal ---------------------------------------------------------------------------------


def _failed(paragraph: int, segment: int) -> Issue:
    return Issue(IssueKind.QUOTE_NOT_IN_EVIDENCE, paragraph, segment, "x", "E3")


def test_failed_quotes_are_removed_and_counted() -> None:
    draft = ArticleDraft(
        title="T",
        paragraphs=[Paragraph(segments=[sentence("Ouverture.", "E2"), quote("faux")])],
    )
    cleaned, removed = remove_failed_quotes(draft, [_failed(0, 1)])
    assert removed == 1
    assert cleaned.paragraphs == [Paragraph(segments=[sentence("Ouverture.", "E2")])]
    assert draft.paragraphs[0].segments[1] == quote("faux")  # the input is not modified


def test_a_paragraph_left_empty_is_removed() -> None:
    draft = ArticleDraft(
        title="T",
        paragraphs=[
            Paragraph(segments=[sentence("Ouverture.", "E2")]),
            Paragraph(segments=[quote("faux")]),
        ],
    )
    cleaned, removed = remove_failed_quotes(draft, [_failed(1, 0)])
    assert removed == 1 and len(cleaned.paragraphs) == 1


def test_other_issues_and_sentences_are_left_alone() -> None:
    draft = ArticleDraft(title="T", paragraphs=[Paragraph(segments=[sentence("Sans id.")])])
    no_id = Issue(IssueKind.NO_ID, 0, 0, "Sans id.")
    assert remove_failed_quotes(draft, [no_id, _failed(0, 0)]) == (draft, 0)


def test_a_quote_citing_an_unknown_id_is_removed() -> None:
    draft = ArticleDraft(
        title="T",
        paragraphs=[Paragraph(segments=[sentence("Ouverture.", "E2"), quote("vrai", "E42")])],
    )
    unknown = Issue(IssueKind.QUOTE_UNKNOWN_ID, 0, 1, "vrai", "E42")
    cleaned, removed = remove_failed_quotes(draft, [unknown])
    assert removed == 1
    assert cleaned.paragraphs == [Paragraph(segments=[sentence("Ouverture.", "E2")])]


def test_both_removable_kinds_are_removed_together() -> None:
    draft = ArticleDraft(
        title="T",
        paragraphs=[
            Paragraph(segments=[sentence("Ouverture.", "E2"), quote("vrai", "E42")]),
            Paragraph(segments=[quote("faux")]),
        ],
    )
    unknown = Issue(IssueKind.QUOTE_UNKNOWN_ID, 0, 1, "vrai", "E42")
    cleaned, removed = remove_failed_quotes(draft, [unknown, _failed(1, 0)])
    assert removed == 2
    assert cleaned.paragraphs == [Paragraph(segments=[sentence("Ouverture.", "E2")])]


def test_non_removable_kinds_remove_nothing() -> None:
    draft = ArticleDraft(
        title="T",
        paragraphs=[Paragraph(segments=[sentence("Il a dit « oui ».", "E2"), quote("faux")])],
    )
    issues = [
        Issue(IssueKind.NO_ID, 0, 1, "faux"),
        Issue(IssueKind.QUOTATION_MARKS, 0, 1, "faux"),
        Issue(IssueKind.QUOTATION_MARKS, 0, 0, "Il a dit « oui »."),
    ]
    assert remove_failed_quotes(draft, issues) == (draft, 0)


# --- retry message ---------------------------------------------------------------------------


def test_the_retry_message_quotes_each_error_and_offers_both_fixes() -> None:
    message = retry_feedback(
        [
            Issue(IssueKind.UNKNOWN_ID, 0, 0, "Le festival a ouvert.", "E9"),
            Issue(IssueKind.NO_ID, 0, 1, "Il a plu."),
            Issue(IssueKind.QUOTE_NOT_IN_EVIDENCE, 1, 0, "Nous revenons", "E3"),
            Issue(IssueKind.UNPARSABLE),
        ]
    )
    assert "« Le festival a ouvert. » cite E9" in message
    assert "« Il a plu. » ne cite aucune preuve" in message
    assert "« Nous revenons » ne se trouve pas mot pour mot dans la preuve E3" in message
    assert "JSON" in message
    assert "recopies les mots exactement" in message and 'segment "sentence"' in message


def test_the_retry_message_describes_the_adr_0012_issues() -> None:
    message = retry_feedback(
        [
            Issue(IssueKind.QUOTATION_MARKS, text="Le « festival » s’ouvre"),
            Issue(IssueKind.EMPTY),
            Issue(IssueKind.QUOTATION_MARKS, 0, 0, "Il a dit « oui »."),
            Issue(IssueKind.QUOTE_UNKNOWN_ID, 0, 1, "Nous reviendrons", "E42"),
        ]
    )
    assert message.splitlines()[1:5] == [
        "- le titre « Le « festival » s’ouvre » contient des guillemets : le titre n'en "
        "contient jamais.",
        "- l'article ne contenait aucun texte.",
        "- la phrase « Il a dit « oui ». » contient des guillemets : une citation directe va "
        'dans un segment "quote", et une phrase "sentence" n\'en contient jamais.',
        "- la citation « Nous reviendrons » cite la preuve E42, qui n'existe pas.",
    ]


# --- rendering -------------------------------------------------------------------------------


def test_render_text_shows_the_speaker_and_the_source_passage() -> None:
    article = ArticleDraft(
        title=" Le festival d’Oran s’ouvre ",
        paragraphs=[
            Paragraph(
                segments=[
                    sentence("Le festival s’est ouvert le 3 mai.", "E2"),
                    quote("Nous reviendrons l'an prochain"),
                ]
            ),
            Paragraph(segments=[sentence("Il se tient au théâtre régional.", "E2")]),
        ],
    )
    assert render_text(article, EVIDENCE) == (
        "Le festival d’Oran s’ouvre\n\n"
        "Le festival s’est ouvert le 3 mai. Karim Ziani : « Nous reviendrons l’an prochain »\n\n"
        "Il se tient au théâtre régional."
    )


def test_render_text_refuses_an_unverified_quote() -> None:
    article = ArticleDraft(title="T", paragraphs=[Paragraph(segments=[quote("inventé")])])
    with pytest.raises(ValueError, match="unverified quote"):
        render_text(article, EVIDENCE)
