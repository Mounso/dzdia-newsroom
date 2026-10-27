# Statut — 2026-10-27 (webhook et worker codés)

## Pour reprendre dans une nouvelle session
- **Dépôt GitHub :** `origin` = https://github.com/Mounso/dzdia-newsroom. Tout est sur `main` (la branche `feat/postgres-job-queue` y a été fusionnée en avance rapide). Le 2026-10-27, l'historique a été réécrit et poussé en force pour retirer les mentions de co-auteur : **aucun commit ne porte de ligne `Co-Authored-By`**, et il ne faut pas en ajouter.
- **CI :** pas encore vérifiée (`gh` n'est pas installé sur le poste et le dépôt n'est pas lisible par l'API publique). Vérifier l'onglet Actions de GitHub.
- **Fichiers locaux, jamais commités** (exclus via `.git/info/exclude`) :
  - `Refactorisation DzdiaBot v2.1.md` : le cahier des charges complet (périmètre, architecture cible, feuille de route, questions de découverte) ;
  - `docs/fiche_de_suivi.md` : la fiche de préparation aux entretiens d'Abdou. **La mettre à jour à la fin de chaque étape**, en même temps que ce fichier.
- **Environnement :** Windows 11, avec WSL (Ubuntu) et Docker Desktop installés le 2026-10-27. Les tests Postgres tournent dans un conteneur Linux (commande dans `CLAUDE.md`), car psycopg en async ne fonctionne pas avec la boucle asyncio par défaut de Windows.
- **Vérifications :** ruff, `ruff format` et mypy strict ne signalent rien. Sous Windows, 113 tests passent et 11 sont sautés (les tests Postgres). Dans Docker, les 124 passent.
- **`.env.example` est illisible pour Claude** (règle de `.claude/settings.json`). Abdou doit y ajouter lui-même les nouvelles variables (voir l'étape 8 ci-dessous).

## Fait et testé
1. **Socle du dépôt** : uv, ruff, mypy strict, pytest, CI GitHub Actions, `CLAUDE.md`, `.claude/settings.json` (lecture de `.env` bloquée), `config.py` (pydantic-settings).
2. **Harnais `evals/baseline_v1/`** (ADR-001 et 002) : échantillon, copies des pages, réextraction, relecture, exécution des prompts v1, extraction, matcher, étiquetage CSV, rapport avec intervalles de confiance.
3. **`sample` et `snapshot` exécutés** : 16 articles (TSA, Algérie 360) et 4 doublons opaques.
   - Les redirections sont vérifiées, les renvois « À LIRE AUSSI » retirés et les titres ajoutés.
   - L'article payant `tsa-07` est remplacé par `tsa-08` (ADR-003).
   - Les références sont vérifiées automatiquement et marquées relues (ADR-003).
4. **`.env` rempli** : `GEMINI_API_KEY`, sur un projet Google en free tier.
5. **`run` exécuté** : 144/144 runs réussis avec `gemini-3.1-flash-lite-preview`.
   - Le modèle est toujours disponible, donc pas d'ADR.
   - Au premier passage, 15 erreurs 503 et aucune 429 ; toutes ont réussi à la relance.
   - Latence médiane 7,7 s, maximum 87,9 s (le timeout est à 90 s).
6. **ADR-004**, socle d'exécution : `api` et `worker` séparés, file Postgres maison « au moins une fois » avec bail, psycopg 3 avec migrations SQL numérotées, client Telegram en `httpx`, VM Oracle Ampere, Caddy pour le HTTPS.
7. **File de travail** (option 2 : bail prolongé par heartbeat, jeton de prise, réessais avec jitter, `PermanentJobError`) :
   - code : `db/migrations/0001_jobs.sql`, `db/migrate.py`, `db/models.py`, `db/repository.py`, `worker/queue.py`, et `DatabaseSettings` dans `config.py` ;
   - tests : 14 unitaires et 10 d'intégration contre `pgvector/pgvector:pg16`, qui passent tous dans Docker ;
   - la CI a un service Postgres et définit `TEST_DATABASE_URL`.
8. **Entrée Telegram et points d'entrée** (ADR-005 ; webhook option B, client option A, worker option B) :
   - `channels/base.py` (`InboundMessage`, `Channel`, `ChannelError`) et `channels/telegram.py` (`parse_update`, `TelegramClient` avec réessais et respect de `retry_after`, CLI `set-webhook` / `webhook-info`) ;
   - `api/` : `POST /webhooks/telegram` (secret à temps constant, liste blanche, `enqueue`), `/health`, sans `/docs` ; lancement par `python -m dzdia_newsroom.api` ;
   - `worker/handlers.py` (`inbound_message` répond « Bien reçu. ») et `python -m dzdia_newsroom.worker`, avec arrêt en douceur (25 s) ; `Worker.run_forever` accepte un événement `stop` ;
   - `logging_setup.py` (logs JSON, `LOG_FORMAT=text` en local), `db/pool.py`, `runtime.py` (`SelectorEventLoop` sous Windows) ;
   - réglages : `TELEGRAM_WEBHOOK_SECRET` (32 à 256 caractères parmi `A-Za-z0-9_-`), `TELEGRAM_ALLOWED_USER_IDS` (séparés par des virgules), `TELEGRAM_TEAM_CHAT_ID`, `TELEGRAM_BOT_TOKEN`, `LOG_FORMAT`, `LOG_LEVEL` ;
   - 54 nouveaux tests, dont un test de bout en bout contre Postgres. Un test de fumée dans Docker (migration, API, worker, SIGTERM) a réussi.

## En pause : étape `extract`
- **État :** 20/144 extractions faites avec `gemini-2.5-flash`. Les 124 autres ont pris des 429 : le free tier est limité à 5 requêtes par minute et 20 par jour. `match` n'est pas lancé.
- **Modèle écarté :** `gemini-3.5-flash-lite`, testé sur les 20 mêmes runs (sorties dans `evals/.data/baseline_v1/compare_gemini-3.5-flash-lite/`). Il retrouve 76 % des éléments distincts, mais seulement 2 citations sur 7 et 8 organisations sur 16 : il sous-estimerait les hallucinations. Le modèle évalué (3.1 Flash Lite) est écarté aussi, à cause du biais d'auto-évaluation.
- **Pistes, à trancher par Abdou :**
  - un extracteur **Claude**, si ses crédits API sont disponibles vers le **2026-11-03** (à vérifier : l'abonnement Max ne donne a priori pas de crédits API) ;
  - **Groq** (gpt-oss-120b, free tier), déjà prévu comme second fournisseur dans le cahier ;
  - activer la facturation Gemini, pour quelques centimes ;
  - relancer `extract` une fois par jour sur le free tier, soit environ 7 jours.

  Si on change de modèle : questions d'architecture, nouvel ADR, puis on refait les **144** extractions avec un seul modèle. Le script de comparaison est à réécrire, car il était dans un scratchpad.
- **À corriger :** le retry de `GeminiClient` ignore le `retryDelay` des 429, et rien ne limite le débit par minute.

## Prochaines étapes, dans l'ordre
1. **Avant tout :** l'échange de découverte avec les journalistes (§9 du cahier des charges). Questions à ajouter :
   - un sous-domaine de dzdia.com pour le webhook (par exemple `bot.dzdia.com`) ;
   - l'identifiant du groupe Telegram de l'équipe ;
   - les identifiants Telegram des journalistes, pour la liste blanche.
2. **Dockerfile et `docker-compose.yml`** (fin de la semaine 1, ADR-004) :
   - une seule image pour `api` et `worker`, en arm64 et amd64 ;
   - les services `migrate` (lancé une fois), `api`, `worker`, `postgres`, et `caddy` en production ;
   - `stop_grace_period: 30s` sur le worker, aligné sur `SHUTDOWN_GRACE_S = 25` ;
   - le profil `dev` déjà cité dans `CLAUDE.md`.

   Poser les questions d'architecture avant de commencer. Puis tester en local avec un vrai bot : BotFather, et un tunnel HTTPS (par exemple `cloudflared`) pour `set-webhook`.
3. **Déploiement sur la VM Oracle Ampere (ARM64)**, avec Caddy et DuckDNS en attendant le sous-domaine. Pré-requis côté Abdou : compte Oracle Cloud, bot BotFather, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`.
4. **Reprendre `extract` puis `match`**, et inspecter la répartition trouvé / non trouvé par type. Ajuster `--name-threshold` si nécessaire.
5. **Étiquetage :** `export-labels`, étiquetage S/C/N par Abdou dans Excel, puis `import-labels` et `report`. Commiter `labels.jsonl` et `report.md`.
6. **Passe parallèle du LLM-juge** sur les mêmes éléments. Les verdicts sont stockés à part, pour la calibration en semaine 5.

## Petits points en suspens
- Ajouter un `.gitattributes` (`* text=auto eol=lf`) : Git signale des conversions CRLF → LF.
