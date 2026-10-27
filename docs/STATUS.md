# Statut — 2026-10-27

**Fait et testé** (ruff + mypy strict + 46 tests pytest verts)
1. Socle du dépôt : uv, ruff, mypy strict, pytest, CI GitHub Actions, `CLAUDE.md`, `.claude/settings.json` (lecture de `.env` bloquée), `config.py` (pydantic-settings).
2. Harnais `evals/baseline_v1/` complet, en suivant les ADR-001 et 002 : échantillon, copies des pages, réextraction, relecture, exécution des prompts v1, extraction, matcher, étiquetage CSV, rapport avec intervalles de confiance.
3. `sample` et `snapshot` exécutés : 16 articles (TSA, Algérie 360) + 4 doublons opaques, redirections vérifiées, renvois « À LIRE AUSSI » retirés, titres ajoutés, article payant `tsa-07` remplacé par `tsa-08`. Les 16 textes sont vérifiés automatiquement et marqués relus (ADR-003).
4. `.env` rempli (`GEMINI_API_KEY`, projet Google en free tier).
5. `run` exécuté : 144/144 runs réussis avec `gemini-3.1-flash-lite-preview`, toujours disponible, donc pas d'ADR. 15 erreurs 503 « high demand » au premier passage, aucune 429, toutes réussies à la relance. Latence médiane 7,7 s, maximum 87,9 s (timeout à 90 s).

**En pause : étape 6 (`extract`)**
- 20/144 extractions faites avec `gemini-2.5-flash`. Les 124 autres sont en 429 : le free tier est limité à 5 requêtes par minute et 20 par jour, quota du jour épuisé. `match` n'est pas lancé.
- Test de `gemini-3.5-flash-lite` sur ces 20 runs (sorties dans `evals/.data/baseline_v1/compare_gemini-3.5-flash-lite/`) : il retrouve 76 % des éléments distincts, mais seulement 2 citations sur 7 et 8 organisations sur 16. Écarté, car il sous-estimerait les hallucinations là où elles sont les plus fréquentes.
- Piste retenue : un extracteur Claude, à confirmer selon les crédits API d'Abdou (dans environ 7 jours, vers le 2026-11-03). Cela demande un nouveau client, des questions d'architecture et un ADR, puis de refaire les 144 extractions avec ce seul modèle.
- À corriger en parallèle : le retry de `GeminiClient` ignore le `retryDelay` des 429, et rien ne limite le débit par minute.

**Prochaines étapes, dans l'ordre**
6. `extract` puis `match`, et inspecter la répartition trouvé / non trouvé par type. Ajuster `--name-threshold` si nécessaire.
7. `export-labels`, étiquetage S/C/N par Abdou dans Excel, puis `import-labels` et `report`. Commiter `labels.jsonl` et `report.md`.
8. À coder ensuite : la passe parallèle du LLM-juge sur les mêmes éléments (verdicts stockés à part, pour la calibration en semaine 5).
9. Semaine 1, suite : architecture tranchée dans l'ADR-004 (psycopg 3, file maison « au moins une fois » avec bail, Oracle Ampere, Caddy). File codée selon l'option 2 (bail prolongé par heartbeat, jeton de prise, réessais avec jitter, erreurs permanentes) : `db/migrations/0001_jobs.sql`, `db/migrate.py`, `db/repository.py`, `worker/queue.py`. Les 14 tests unitaires passent ; les 10 tests d'intégration Postgres (`TEST_DATABASE_URL`, service Postgres ajouté à la CI) passent contre `pgvector/pgvector:pg16` dans Docker (suite complète : 70/70 sous Linux). WSL et Docker sont installés. À faire : coder le webhook Telegram et le point d'entrée du worker, puis déployer sur la VM. Sous-domaine HTTPS à confirmer.
10. Avant tout cela : l'échange de découverte avec les journalistes (§9 du cahier des charges v2.1).
