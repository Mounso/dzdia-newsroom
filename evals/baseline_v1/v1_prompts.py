"""Verbatim copy of the legacy v1 prompts (dzdia-bot-main/services/gemini_api.py).

Do not edit: the baseline measures the v1 bot exactly as it shipped.
"""

from evals.baseline_v1.schemas import Task

PROMPT_VERSION = "v1-frozen"


def summary_prompt(content: str) -> str:
    return (
        f"Fais un résumé factuel de 3 lignes maximum de ce contenu, sous l'angle de la culture "
        f"algérienne ou de la diaspora : {content}\n\n"
        "ATTENTION : Si le texte fourni est une simple salutation (ex: bonjour), une phrase de "
        "test sans information, "
        "ou un contenu vide de sens, réponds EXACTEMENT et UNIQUEMENT par le mot-clé "
        "[TEXTE_INSUFFISANT]. "
        "Ne fais aucune phrase d'introduction."
    )


def article_prompt(content: str) -> str:
    return (
        "En tant que journaliste pour le média culturel algérien Dzdia, rédige un article "
        f"engageant, moderne et inclusif à partir de ces informations : {content}. Mets en "
        "valeur les talents et la culture. Ajoute un titre accrocheur."
    )


def build_prompt(task: Task, content: str) -> str:
    return summary_prompt(content) if task == "summary" else article_prompt(content)


INSUFFICIENT_TEXT_MARKER = "[TEXTE_INSUFFISANT]"
