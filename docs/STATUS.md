# Statut — 2026-10-28 (fin de session : semaine 1 terminée côté code)

## Pour reprendre dans une nouvelle session
- **Par où commencer :** lire ce fichier, puis attaquer l'étape 2 des « Prochaines étapes » (le déploiement sur la VM). Comme c'est un nouveau module, commencer par poser 3 ou 4 questions d'architecture à Abdou, puis écrire l'**ADR-007**. Pistes de questions : sauvegardes `pg_dump` (fréquence, destination, rétention), procédure de déploiement (`git pull` puis `compose up --build`, ou un script), pare-feu (règles de sécurité Oracle et ufw, ports 22, 80 et 443 seulement) et accès SSH (clé seule, utilisateur non-root), DuckDNS en attendant `bot.dzdia.com`.
- **Dernier commit de code :** `18780b4 Add Docker image, compose stack and Caddy entry point (ADR-006)`, suivi du commit de passation qui met ce fichier à jour. `main` est poussée et l’arbre de travail est propre. Les ADR 001 à 006 sont acceptés.
- **Dépôt GitHub :** `origin` = https://github.com/Mounso/dzdia-newsroom. Tout est sur `main` (la branche `feat/postgres-job-queue` y a été fusionnée en avance rapide). Le 2026-10-27, l'historique a été réécrit et poussé en force pour retirer les mentions de co-auteur : **aucun commit ne porte de ligne `Co-Authored-By`**, et il ne faut pas en ajouter.
- **CI :** verte sur le commit `3a2a5f3`, test de bout en bout compris (vérifiée par Abdou dans l'onglet Actions). Pour `18780b4`, ce n'est pas vérifié : demander à Abdou. Claude ne peut pas la lire : `gh` n'est pas installé et le dépôt n'est pas lisible par l'API publique.
- **Fichiers locaux, jamais commités** (exclus via `.git/info/exclude`) :
  - `Refactorisation DzdiaBot v2.1.md` : le cahier des charges complet (périmètre, architecture cible, feuille de route, questions de découverte) ;
  - `docs/fiche_de_suivi.md` : la fiche de préparation aux entretiens d'Abdou. **La mettre à jour à la fin de chaque étape**, en même temps que ce fichier.
- **Environnement :** Windows 11, avec WSL (Ubuntu) et Docker Desktop installés le 2026-10-27. Les tests Postgres tournent dans un conteneur Linux (commande dans `CLAUDE.md`), car psycopg en async ne fonctionne pas avec la boucle asyncio par défaut de Windows. **Docker Desktop doit être lancé** avant toute commande `docker` (il ne l'était pas à la fin de la session).
- **Vérifications :** ruff, `ruff format` et mypy strict ne signalent rien. Sous Windows, 114 tests passent et 11 sont sautés (les tests Postgres). Dans Docker, les 125 passent.
- **`.env.example` est illisible pour Claude** (règle de `.claude/settings.json`). Abdou doit y ajouter lui-même les nouvelles variables (voir les étapes 8 et 9 ci-dessous).
- **`.env` d'Abdou :** il contient un token de bot de dev (actif) et un token de bot de prod (en commentaire), `POSTGRES_PASSWORD`, `TELEGRAM_WEBHOOK_SECRET` et `TELEGRAM_ALLOWED_USER_IDS` (son identifiant Telegram : `5271201903`). Claude ne lit jamais `.env` ; pour vérifier une variable non secrète, utiliser `docker inspect` sur le conteneur concerné.

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
9. **Pile Docker** (ADR-006) : `Dockerfile` (deux étages, utilisateur non-root, image de 406 Mo dont 162 Mo de `.venv`), `.dockerignore` en liste blanche, `docker-compose.yml`, `Caddyfile` :
   - services `postgres`, `migrate` (lancé une fois), `api`, `worker` ; profils `dev` (`cloudflared`), `prod` (`caddy`) et `tools` (`telegram-cli`, seul service à recevoir le token et le secret, pour `set-webhook`) ;
   - nouvelles variables de `.env` : `POSTGRES_PASSWORD` (obligatoire), `PUBLIC_HOST` (prod), et en option `API_PORT`, `POSTGRES_PORT` ;
   - `config.py` : `env_ignore_empty=True`, car compose passe `""` pour une variable optionnelle absente ;
   - **validé avec le vrai bot de dev** (profil `dev`, tunnel cloudflared, `set-webhook`) : Abdou a reçu « Bien reçu. ». Le job est passé de `queued` à `done` en 149 ms, dont 139 ms pour `sendMessage`. Les deux premiers messages avaient été refusés, car `TELEGRAM_ALLOWED_USER_IDS` valait encore l'exemple `123` : la liste blanche a fait son travail ;
   - test de fumée avec des variables factices (projet `dzdia-smoke`, supprimé ensuite) : migration, healthchecks, webhook 200/401, job traité, l'API n'a pas le token, utilisateur `app` (uid 10001), arrêt du worker en 1 s avec le code 0 ; via Caddy : `/health` passe, `/docs` et `/` donnent 404, un corps de 2 Mo est refusé (413) ; `telegram-cli` atteint bien Telegram.

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
2. **Déploiement sur la VM Oracle Ampere (ARM64)**, avec Caddy et DuckDNS en attendant le sous-domaine. Pré-requis côté Abdou : compte Oracle Cloud, bot BotFather, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`.
3. **Reprendre `extract` puis `match`**, et inspecter la répartition trouvé / non trouvé par type. Ajuster `--name-threshold` si nécessaire.
4. **Étiquetage :** `export-labels`, étiquetage S/C/N par Abdou dans Excel, puis `import-labels` et `report`. Commiter `labels.jsonl` et `report.md`.
5. **Passe parallèle du LLM-juge** sur les mêmes éléments. Les verdicts sont stockés à part, pour la calibration en semaine 5.

## Petits points en suspens
- **Le webhook du bot de dev pointe encore sur une URL trycloudflare morte**, car la pile de dev est arrêtée. Telegram garde les updates en attente (environ 24 h). Pour reprendre les tests en local : `docker compose --profile dev up -d --build`, lire la nouvelle URL dans `docker compose logs cloudflared`, puis relancer `set-webhook`. Le volume `pgdata` de dev contient le job de test (statut `done`).
- **`.env.example`** : Abdou doit vérifier qu'il liste toutes les variables (`POSTGRES_PASSWORD`, `TELEGRAM_*`, `LOG_FORMAT`, `LOG_LEVEL`, `PUBLIC_HOST`, `API_PORT`, `POSTGRES_PORT`), car Claude ne peut pas le lire.
- **Avertissements de pytest, sans effet aujourd'hui :** `StarletteDeprecationWarning`, car `TestClient` utilise httpx ; `_UnionGenericAlias`, qui vient de google-genai.
- **Image Docker de 406 Mo :** les dépendances des évals (trafilatura, google-genai) y sont incluses. On les garde, car la v2 s'en servira dès la semaine 2.
