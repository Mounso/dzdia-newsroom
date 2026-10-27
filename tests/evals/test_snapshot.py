from evals.baseline_v1.snapshot import clean_text


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
