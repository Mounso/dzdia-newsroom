# Statut — 2026-10-28 (audit, lot rapide, documentation, B1, R1 et B5 faits)

## Pour reprendre dans une nouvelle session
- **Session du 2026-10-28 :**
  - **Audit complet :** voir `docs/reviews/2026-10-28-design-review.md`. Chaque point a un identifiant et un statut.
  - **Lot rapide corrigé :** B2, B3, B4 et R2.
  - **B1 et R1 corrigés** (option A pour les deux, choisie par Abdou) : voir le point 12 de « Fait et testé ».
  - **B5 corrigé** (option A, avec la date d'`ExtractionRecord`) : voir le point 13.
  - **Documentation professionnelle en anglais :** index dans `docs/README.md`.
  - **ADR :** ils sont maintenant **un fichier par décision** dans `docs/adr/` (format MADR, modèle `template.md`, index dans `README.md`). `docs/architecture_decisions.md` est supprimé ; les originaux en français restent dans l'historique Git (commit `8e32200`). Le **prochain ADR est `docs/adr/0007-…md`**, en anglais. Il faut l'ajouter à l'index.
  - **Règle :** quand un comportement change, mettre à jour la doc concernée et le statut du point dans la revue de conception.
  - **Tout est commité et poussé sur `main`** (voir « Dernier commit » ci-dessous).
- **Par où commencer :** lire ce fichier, puis les correctifs restants de l'audit (B6, puis S1, S2 et la couverture ; voir « Prochaines étapes »), puis attaquer l'étape 2 des « Prochaines étapes » (le déploiement sur la VM). Comme c'est un nouveau module, commencer par poser 3 ou 4 questions d'architecture à Abdou, puis écrire l'**ADR-0007** (`docs/adr/0007-…md`). Pistes de questions : sauvegardes `pg_dump` (fréquence, destination, rétention), procédure de déploiement (`git pull` puis `compose up --build`, ou un script), pare-feu (règles de sécurité Oracle et ufw, ports 22, 80 et 443 seulement) et accès SSH (clé seule, utilisateur non-root), DuckDNS en attendant `bot.dzdia.com`.
- **Derniers commits :**
  - `dbd3e51` : le lot rapide de l'audit (B2, B3, B4, R2) ;
  - `b58dbc9` : la documentation et le découpage des ADR ;
  - `1152c3f` : le commit de passation, qui met à jour ce fichier ;
  - `6ed8199` : B1 et R1, avec la doc et ce fichier ;
  - le commit « Derive eval element ids from content and date extraction records » : B5.

  `main` est poussée et l’arbre de travail est propre. Les ADR 0001 à 0006 sont acceptés.
- **Dépôt GitHub :** `origin` = https://github.com/Mounso/dzdia-newsroom. Tout est sur `main` (la branche `feat/postgres-job-queue` y a été fusionnée en avance rapide). Le 2026-10-27, l'historique a été réécrit et poussé en force pour retirer les mentions de co-auteur : **aucun commit ne porte de ligne `Co-Authored-By`**, et il ne faut pas en ajouter.
- **CI :** verte sur le commit `3a2a5f3`, test de bout en bout compris (vérifiée par Abdou dans l'onglet Actions). Verte aussi jusqu'à `1152c3f` inclus (vérifiée par Abdou le 2026-10-28). Pour le commit B1/R1, ce n'est pas vérifié : demander à Abdou. Claude ne peut pas la lire : `gh` n'est pas installé et le dépôt n'est pas lisible par l'API publique.
- **Fichiers locaux, jamais commités** (exclus via `.git/info/exclude`) :
  - `Refactorisation DzdiaBot v2.1.md` : le cahier des charges complet (périmètre, architecture cible, feuille de route, questions de découverte) ;
  - `docs/fiche_de_suivi.md` : la fiche de préparation aux entretiens d'Abdou. **La mettre à jour à la fin de chaque étape**, en même temps que ce fichier.
- **Environnement :** Windows 11, avec WSL (Ubuntu) et Docker Desktop installés le 2026-10-27. Les tests Postgres tournent dans un conteneur Linux (commande dans `CLAUDE.md`), car psycopg en async ne fonctionne pas avec la boucle asyncio par défaut de Windows. **Docker Desktop doit être lancé** avant toute commande `docker`. S'il ne l'est pas, Claude peut le démarrer avec `Start-Process` sur `Docker Desktop.exe`, puis attendre que `docker info` réponde.
- **Vérifications (2026-10-28, après B1 et R1) :** ruff, `ruff format` et mypy strict ne signalent rien. Sous Windows, 124 tests passent et 13 sont sautés (les tests Postgres). Dans Docker, les 137 passent. Docker Desktop est installé dans `C:\Users\abdou\AppData\Local\Programs\DockerDesktop\`.
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

10. **Audit et lot rapide** (2026-10-28), détails dans `docs/reviews/2026-10-28-design-review.md` :
    - **B2 :** `db.migrate` utilise `event_loop_factory()` et `configure_logging`. Il plantait sous Windows (vérifié).
    - **B3 :** le verrou consultatif est pris avant le `CREATE TABLE schema_migrations`.
    - **B4 :** `Worker._mark_dead` et `_fail` vérifient le booléen renvoyé par la mise à jour clôturée. Nouveau test.
    - **R2 :** `check=AsyncConnectionPool.check_connection` dans `db/pool.py`.
11. **Documentation** (2026-10-28) :
    - `README.md` refait ;
    - `docs/architecture/` (overview, job-queue, data-model, security) ;
    - `docs/adr/` ;
    - `docs/operations/` (configuration, deployment, runbook) ;
    - `docs/development/` (getting-started, testing, conventions) ;
    - `docs/evals/baseline-v1.md`, `docs/roadmap.md`, `docs/reviews/`.

    Tous les liens internes ont été vérifiés par un script.
12. **B1 et R1** (2026-10-28, option A pour les deux) :
    - **B1 :** `ChannelError` porte `retry_after_s`. Le handler le traduit en `RetryLaterJobError(delay_s)`, une nouvelle exception de `worker/queue.py`. Le worker attend `max(backoff, delay_s × (1 + 0,1 × aléa))` ; l'essai compte toujours ; au-delà de `max_retry_after_s` (1 h), le job passe `dead`.
    - **R1 :** `JobRepository.release()`, clôturé par le jeton : `queued`, `attempts - 1`, jeton et bail effacés, `run_after` inchangé. Le worker l'appelle sur `CancelledError`, sous `asyncio.shield` et borné par `release_timeout_s` (3 s). Le bail reste le filet de sécurité.
    - **Options écartées :** pour B1, une hiérarchie d'erreurs commune (`errors.py`, à reconsidérer avec R8) et un « snooze » sans essai ; pour R1, raccourcir seulement le bail, ou allonger la grâce.
    - 10 nouveaux tests (dont 2 contre Postgres) et 1 test réécrit. Doc mise à jour : `job-queue.md`, runbook, deployment, roadmap, revue de conception (B1 et R1 `Fixed`).
13. **B5** (2026-10-28, option A) :
    - `element_id = <run_id>#<type>:<hash>` dans `evals/baseline_v1/extract.py` (`element_id()`). Le hash couvre le type et la valeur normalisée (NFC, casse, espaces ; accents gardés). Une valeur répétée dans un run reçoit `~2`, `~3` : la définition de la mesure (ADR-0001) ne change pas.
    - `ExtractionRecord` a maintenant `created_at` (obligatoire) et `latency_ms`. Les tokens viendront avec R8.
    - **Options écartées :** une empreinte dans `HumanLabel` (détecte sans prévenir), et des identifiants liés à une extraction (toute réextraction annule les étiquettes).
    - 5 nouveaux tests, 2 tests adaptés. Doc mise à jour : `docs/evals/baseline-v1.md`, roadmap, revue de conception (B5 `Fixed`).

## En pause : étape `extract`
- **État :** 0/144. Les 20 extractions faites avec `gemini-2.5-flash` (les 124 autres avaient pris des 429 : le free tier est limité à 5 requêtes par minute et 20 par jour) n'avaient pas de date. Elles ont été déplacées dans `evals/.data/baseline_v1/pre_b5/` (local, rien de supprimé), et `extract` repartira de zéro. `match` n'est pas lancé.
- **Modèle écarté :** `gemini-3.5-flash-lite`, testé sur les 20 mêmes runs (sorties dans `evals/.data/baseline_v1/compare_gemini-3.5-flash-lite/`). Il retrouve 76 % des éléments distincts, mais seulement 2 citations sur 7 et 8 organisations sur 16 : il sous-estimerait les hallucinations. Le modèle évalué (3.1 Flash Lite) est écarté aussi, à cause du biais d'auto-évaluation.
- **Pistes, à trancher par Abdou :**
  - un extracteur **Claude**, si ses crédits API sont disponibles vers le **2026-11-03** (à vérifier : l'abonnement Max ne donne a priori pas de crédits API) ;
  - **Groq** (gpt-oss-120b, free tier), déjà prévu comme second fournisseur dans le cahier ;
  - activer la facturation Gemini, pour quelques centimes ;
  - relancer `extract` une fois par jour sur le free tier, soit environ 7 jours.

  Si on change de modèle : questions d'architecture, nouvel ADR, puis on refait les **144** extractions avec un seul modèle. Le script de comparaison est à réécrire, car il était dans un scratchpad.
- **À corriger avant de relancer :**
  - **B6 :** le taux par sortie est une borne basse tant que des éléments sont en attente.
  - **R8 :** le retry de `GeminiClient` ignore `retryDelay`, et rien ne limite le débit.

## Prochaines étapes, dans l'ordre
0. **Correctifs restants de l'audit :** B6 (évals), puis S1, S2 et la couverture de tests en CI. B1, R1 et B5 sont faits. Pour B6, présenter les options à Abdou avant de coder.
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
