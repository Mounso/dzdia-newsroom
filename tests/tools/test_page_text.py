"""Shared extraction core (ADR-0018, points 9 to 11)."""

import pytest

from dzdia_newsroom.tools.page_text import (
    MIN_SOURCE_CHARS,
    NoReadableTextError,
    PageText,
    extract_page_text,
)
from evals.baseline_v1.snapshot import PaywallError, extract_main_text

TITLE = "Oran : une station de dessalement de 300 000 m3 par jour"

PARAGRAPHS = (
    "<p>Le ministre a inauguré mardi une nouvelle station de dessalement à Oran, d'une capacité "
    "de 300 000 mètres cubes par jour, selon un communiqué publié par le ministère de "
    "l'Hydraulique.</p>\n"
    "<p>🟢 À LIRE AUSSI : Air Algérie ouvre une ligne : billets dès 88 071 DA</p>\n"
    "<p>Le projet a coûté 450 millions de dollars et alimentera trois wilayas de l'ouest du pays, "
    "a précisé le directeur de l'Algerian Energy Company lors de la cérémonie "
    "d'inauguration.</p>\n"
    "<p>Les travaux avaient commencé il y a quatre ans, et la mise en service progressive doit "
    "s'achever avant l'été prochain, selon le même communiqué du ministère.</p>"
)


def _page(paragraphs: str = PARAGRAPHS, *, title: str = TITLE, extra: str = "") -> str:
    return (
        f'<html><head><title>{title}</title><meta property="og:title" content="{title}">'
        f"</head><body><article><h1>{title}</h1>\n{paragraphs}\n{extra}</article></body></html>"
    )


def test_production_and_eval_give_the_same_text_with_the_page_title() -> None:
    html = _page()
    page = extract_page_text(html)
    assert page.title == TITLE  # read from the page metadata
    assert page.text == extract_main_text(html, TITLE)
    assert page.text.startswith(TITLE)
    assert "450 millions de dollars" in page.text
    assert page.paywall_suspected is False


def test_the_related_article_line_is_removed() -> None:
    text = extract_page_text(_page()).text
    assert "LIRE AUSSI" not in text
    assert "Air Algérie" not in text


def test_a_paywall_marker_is_flagged_and_the_visible_text_is_kept() -> None:
    html = _page(extra="<p>Ce contenu est réservé aux abonnés.</p>")
    page = extract_page_text(html)
    assert page.paywall_suspected is True
    assert "450 millions de dollars" in page.text
    with pytest.raises(PaywallError):  # the eval still refuses it
        extract_main_text(html, TITLE)


def test_a_page_under_the_floor_has_no_readable_text() -> None:
    short = "<p>Le ministre a inauguré mardi une station de dessalement à Oran.</p>"
    with pytest.raises(NoReadableTextError):
        extract_page_text(_page(short, title="Oran"))


@pytest.mark.parametrize(
    "html",
    ["", "<html><head><title>Accueil</title></head><body><nav>Menu</nav></body></html>"],
)
def test_an_empty_or_non_article_page_has_no_readable_text(html: str) -> None:
    with pytest.raises(NoReadableTextError):
        extract_page_text(html)


def test_the_floor_is_300_characters() -> None:
    assert MIN_SOURCE_CHARS == 300
    page = extract_page_text(_page())
    assert isinstance(page, PageText) and len(page.text) >= MIN_SOURCE_CHARS
