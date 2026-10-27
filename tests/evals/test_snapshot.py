import pytest

from evals.baseline_v1.snapshot import PaywallError, clean_text, extract_main_text

ARTICLE_HTML = """<html><head><title>T</title></head><body><article>
<h1>Titre</h1>
<p>Le ministre a inauguré mardi une nouvelle station de dessalement à Oran, d'une capacité de
300 000 mètres cubes par jour, selon un communiqué publié par le ministère de l'Hydraulique.</p>
<p>Le projet a coûté 450 millions de dollars et alimentera trois wilayas de l'ouest du pays,
a précisé le directeur de l'Algerian Energy Company lors de la cérémonie d'inauguration.</p>
</article></body></html>"""


def test_clean_text_drops_related_article_links() -> None:
    text = (
        "Premier paragraphe.\n"
        "🟢 À LIRE AUSSI : Air Algérie ouvre une ligne : billets dès 88 071 DA\n"
        "A LIRE AUSSI : Autre titre\n"
        "Il faut lire aussi le rapport complet.\n"
        "\n"
        "Dernier paragraphe."
    )
    assert clean_text(text) == (
        "Premier paragraphe.\nIl faut lire aussi le rapport complet.\nDernier paragraphe."
    )


def test_extract_main_text_prepends_the_headline() -> None:
    text = extract_main_text(ARTICLE_HTML, "Oran : une station de 300 000 m3 par jour")
    assert text.startswith("Oran : une station de 300 000 m3 par jour\n")
    assert "450 millions de dollars" in text


def test_extract_main_text_rejects_paywalled_articles() -> None:
    paywalled = ARTICLE_HTML.replace(
        "</article>", "<p>Abonnez-vous ou connectez-vous pour lire la suite de cet article.</p>"
    )
    with pytest.raises(PaywallError):
        extract_main_text(paywalled, "Titre")
