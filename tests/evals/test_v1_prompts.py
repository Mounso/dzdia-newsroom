from evals.baseline_v1.v1_prompts import article_prompt, build_prompt, summary_prompt

# Literal strings from dzdia-bot-main/services/gemini_api.py, reassembled on one line each.
LEGACY_SUMMARY = (
    "Fais un résumé factuel de 3 lignes maximum de ce contenu, sous l'angle de la culture "
    "algérienne ou de la diaspora : X\n\nATTENTION : Si le texte fourni est une simple "
    "salutation (ex: bonjour), une phrase de test sans information, ou un contenu vide de sens, "
    "réponds EXACTEMENT et UNIQUEMENT par le mot-clé [TEXTE_INSUFFISANT]. Ne fais aucune phrase "
    "d'introduction."
)
LEGACY_ARTICLE = (
    "En tant que journaliste pour le média culturel algérien Dzdia, rédige un article "
    "engageant, moderne et inclusif à partir de ces informations : X. Mets en valeur les talents "
    "et la culture. Ajoute un titre accrocheur."
)


def test_prompts_are_verbatim_copies_of_v1() -> None:
    assert summary_prompt("X") == LEGACY_SUMMARY
    assert article_prompt("X") == LEGACY_ARTICLE


def test_build_prompt_dispatches_on_task() -> None:
    assert build_prompt("summary", "X") == LEGACY_SUMMARY
    assert build_prompt("article", "X") == LEGACY_ARTICLE
