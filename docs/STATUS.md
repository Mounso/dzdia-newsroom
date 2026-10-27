# Statut — 2026-10-27

**Fait et testé** (ruff + mypy strict + 44 tests pytest verts)
1. Socle du dépôt : uv, ruff, mypy strict, pytest, CI GitHub Actions, `CLAUDE.md`, `.claude/settings.json` (lecture de `.env` bloquée), `config.py` (pydantic-settings).
2. Harnais `evals/baseline_v1/` complet, en suivant les ADR-001 et 002 : échantillon, copies des pages, réextraction, relecture, exécution des prompts v1, extraction, matcher, étiquetage CSV, rapport avec intervalles de confiance.
3. `sample` et `snapshot` exécutés : 16 articles (TSA, Algérie 360) + 4 doublons opaques, redirections vérifiées, renvois « À LIRE AUSSI » retirés.

**Prochaines étapes, dans l'ordre**
4. Abdou : remplir `.env` (`GEMINI_API_KEY`), relire les 16 textes dans `evals/.data/baseline_v1/snapshots/`, puis lancer `review-done <ids>`.
5. `run` : 144 appels avec le modèle de la v1 (s'il est retiré, en choisir un autre et le noter dans un ADR), puis vérifier les erreurs 429 et relancer si besoin.
6. `extract` puis `match`, et inspecter la répartition trouvé / non trouvé par type. Ajuster `--name-threshold` si nécessaire.
7. `export-labels`, étiquetage S/C/N par Abdou dans Excel, puis `import-labels` et `report`. Commiter `labels.jsonl` et `report.md`.
8. À coder ensuite : la passe parallèle du LLM-juge sur les mêmes éléments (verdicts stockés à part, pour la calibration en semaine 5).
9. Semaine 1, suite : installer WSL et Docker, puis coder le webhook Telegram via FastAPI, Postgres et la file de travail, et le déployer sur une VM.
10. Avant tout cela : l'échange de découverte avec les journalistes (§9 du cahier des charges v2.1).
