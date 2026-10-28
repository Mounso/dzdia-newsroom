# Statut — 2026-10-28 (fin de session : audit terminé côté code, prochaine étape le déploiement sur la VM)

## Pour reprendre dans une nouvelle session
- **Première chose à faire : le déploiement sur la VM Oracle Ampere (ARM64).** C'est un nouveau module, donc d'abord **3 ou 4 questions d'architecture** à Abdou (chacune avec une recommandation), puis l'**ADR-0007** (`docs/adr/0007-<titre>.md`, en anglais, format MADR, ajouté à l'index `docs/adr/README.md`). Ne rien coder avant ses réponses. Questions à préparer :
  - **sauvegardes :** `pg_dump` planifié (fréquence, chiffrement, destination hors de la VM, rétention) et un test de restauration ;
  - **procédure de déploiement :** `git pull` puis `docker compose --profile prod up -d --build` à la main, ou un script versionné (vérifications, migration, rollback) ;
  - **accès et pare-feu :** SSH par clé seule avec un utilisateur non-root, règles de sécurité Oracle et `ufw` (ports 22, 80 et 443 seulement), mises à jour automatiques de sécurité (`unattended-upgrades`) ;
  - **DNS et HTTPS :** DuckDNS en attendant `bot.dzdia.com`, Caddy (Let's Encrypt), puis `set-webhook` avec le bot de prod.

  Pré-requis côté Abdou : compte Oracle Cloud et VM créée, bot de prod (BotFather), `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`. Ce sont des actions sur ses comptes : lui donner les commandes, il les lance.
- **À surveiller lundi :** les premières PR de Dependabot. Vérifier comment il regroupe les mises à jour de digest seules. Une PR qui touche l'image Postgres ou uv sera rouge tant que `ci.yml` n'est pas aligné à la main : c'est le test de garde qui fait son travail (`docs/development/conventions.md`, section « Dependencies and supply chain »).
- **Reste ouvert dans l'audit** (petits points, à traiter quand ils deviennent utiles) : B7 (deux nombres collés lus comme un seul, avant `match`), R3, R7, R9, R11, S5, S6. Prévus plus tard : R4, R5, R6, R8, R10, S3, et A1 à A5 (semaine 2).
- **Méthode de travail validée par Abdou** (à suivre à chaque étape) :
  1. options présentées en tableau (principe, pour, contre), une recommandation, et les petits choix à part ; Abdou répond en général « A + mes choix » ;
  2. code et tests, puis ruff, `ruff format`, mypy strict et pytest sous Windows, puis la suite Postgres dans Docker (procédure dans `CLAUDE.md`, en arrière-plan) ;
  3. mise à jour de la doc concernée et du statut du point dans `docs/reviews/2026-10-28-design-review.md`, puis de ce fichier et de `docs/fiche_de_suivi.md` (journal, tableau d'état, concepts, questions de recruteurs, chiffres, limites) ;
  4. demander avant de commiter. « Commite et pousse » couvre les deux. Commits sans `Co-Authored-By` ni mention de Claude ; indexer les fichiers un par un (jamais `git add -A`) et vérifier que la fiche n'est pas dedans. **Mettre ce fichier dans son état « commité et poussé » avant le commit**, pour qu'il ne soit pas faux juste après. Après un push, Claude vérifie lui-même la CI avec `gh` et le dit à Abdou.
- **Pièges déjà rencontrés :**
  - un script Python avec du texte français passé par un heredoc Bash échoue (unicode, guillemets) : écrire le script dans le scratchpad **avec l'outil Write**, puis `uv run python -I -X utf8 <fichier>`. `-I` ignore les variables `PYTHON*`, donc `PYTHONUTF8=1` est sans effet : c'est `-X utf8` qui compte (sinon un `print` d'un caractère comme `≥` plante en cp1252) ;
  - un `write_text` Python sous Windows écrit des fins de ligne CRLF : écrire avec `write_bytes(texte.encode("utf-8"))` ;
  - `caplog` ne capture pas le niveau INFO par défaut : `caplog.set_level(logging.INFO, logger=...)` ;
  - le hook de formatage retire un import ajouté avant son premier usage : ajouter l'usage d'abord, ou vérifier les imports après coup ;
  - `sleep` au premier plan est bloqué : lancer les commandes longues en arrière-plan et attendre la notification ;
  - un hook d'arrêt refuse de clore un tour qui n'a ni vérification (tests, typage) ni question de décision en attente : finir chaque tour par ruff, mypy et pytest, ou par un vrai choix pour Abdou (options en tableau) ;
  - pour épingler une image ou une action (S1) : `docker buildx imagetools inspect <image:tag>` donne le digest d'index multi-architecture ; `git ls-remote https://github.com/<owner>/<repo> 'refs/tags/<tag>^{}'` donne le SHA de commit d'une action (sans `gh`) ; l'API `https://hub.docker.com/v2/repositories/<repo>/tags?name=<préfixe>` liste les tags. uv ne publie plus de tags versionnés `bookworm` : la procédure de test Docker utilise `ghcr.io/astral-sh/uv:0.13.6-python3.12-trixie-slim`.
- **Sessions du 2026-10-28 :**
  - **Audit complet :** `docs/reviews/2026-10-28-design-review.md`. Chaque point a un identifiant et un statut. Corrigés : B1, B2, B3, B4, B5, B6, R1, R2, S1, S2. Ouverts : B7, R3, R7, R9, R11, S5, S6 ; S4 documenté ; prévus plus tard : R4, R5, R6, R8, R10, S3, et les questions d'architecture A1 à A5 (semaine 2).
  - **Documentation professionnelle en anglais :** index dans `docs/README.md`. Les ADR sont **un fichier par décision** dans `docs/adr/` (format MADR, modèle `template.md`). Les originaux en français restent dans l'historique Git (commit `8e32200`). Les ADR 0001 à 0006 sont acceptés.
  - **Règle :** quand un comportement change, mettre à jour la doc concernée et le statut du point dans la revue de conception.
- **Derniers commits, tous poussés sur `main` :**
  - `dbd3e51` : le lot rapide de l'audit (B2, B3, B4, R2) ;
  - `b58dbc9` : la documentation et le découpage des ADR ;
  - `1152c3f` : une passation ;
  - `6ed8199` : B1 et R1 ;
  - `debd2fc` : B5 ;
  - `e5d612d` : une passation, qui met à jour ce fichier ;
  - `df96fa1` : B6 ;
  - `c578779` : S1, S2 et la couverture ;
  - `d2e1090` : une passation ;
  - un dernier commit de passation, qui note l'accès de Claude à la CI.

  L'arbre de travail est propre.

- **Dépôt GitHub :** `origin` = https://github.com/Mounso/dzdia-newsroom. Tout est sur `main`. Le 2026-10-27, l'historique a été réécrit et poussé en force pour retirer les mentions de co-auteur : **aucun commit ne porte de ligne `Co-Authored-By`**, et il ne faut pas en ajouter.
- **CI :** trois jobs, tous verts sur `c578779` et `d2e1090` (B6 compris) : `checks` 38 s, `audit` 11 s, `image` 2 min 08 (build arm64 sous QEMU compris). Le job `audit` tourne aussi chaque lundi. Dependabot a déjà fait un premier passage sur ses 4 écosystèmes, sans PR : tout était à jour.
- **Claude peut lire la CI** avec `gh` (installé le 2026-10-28, version 2.103.3), connecté avec un jeton fine-grained en lecture seule d'Abdou, limité à ce dépôt et stocké dans le gestionnaire d'identifiants Windows. Tant que l'app n'est pas redémarrée, appeler `gh` par son chemin complet : `"/c/Program Files/GitHub CLI/gh.exe"`. Commandes utiles : `gh run list -R Mounso/dzdia-newsroom`, `gh run view <id> --log-failed`. Le jeton ne permet ni d'écrire ni de relancer un run, et il expire : s'il est refusé, demander à Abdou de le renouveler.
- **Fichiers locaux, jamais commités** (exclus via `.git/info/exclude`) :
  - `Refactorisation DzdiaBot v2.1.md` : le cahier des charges complet (périmètre, architecture cible, feuille de route, questions de découverte) ;
  - `docs/fiche_de_suivi.md` : la fiche de préparation aux entretiens d'Abdou. **La mettre à jour à la fin de chaque étape**, en même temps que ce fichier ;
  - `evals/.data/` (ignoré par Git) : copies des articles, `runs.jsonl`, et dans `pre_b5/` les 20 extractions mises de côté par B5.
- **Environnement :** Windows 11, avec WSL (Ubuntu) et Docker Desktop. Les tests Postgres tournent dans un conteneur Linux (commande dans `CLAUDE.md`), car psycopg en async ne fonctionne pas avec la boucle asyncio par défaut de Windows. **Docker Desktop doit être lancé** avant toute commande `docker`. S'il ne l'est pas, Claude peut le démarrer avec `Start-Process` sur `C:\Users\abdou\AppData\Local\Programs\DockerDesktop\Docker Desktop.exe`, puis attendre que `docker info` réponde.
- **Vérifications (2026-10-28, après S1 et S2) :** ruff, `ruff format` et mypy strict ne signalent rien. Sous Windows, 147 tests passent et 13 sont sautés (les tests Postgres). Dans Docker, les 160 passent, couverture de branches 69 % (plancher 68). `actionlint` ne signale rien sur `ci.yml`. L'image se construit en amd64 (35 s) et en arm64 sous émulation (72 s), et le test de fumée passe sur les deux (Python 3.12.16, uid 10001). `pip-audit` : aucune vulnérabilité connue sur les 90 paquets verrouillés.
- **Attention, `uv run pytest --cov` sous Windows échoue au plancher** (environ 64 %, car les tests Postgres sont sautés) : c'est normal. `uv run pytest` sans `--cov` n'est pas concerné. Pour le vrai chiffre, ajouter `--cov` à la procédure Docker.
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
14. **B6** (2026-10-28, option B) :
    - les éléments en attente sont tous des `not_found`, donc pas manquants au hasard. Le taux par élément et le taux par sortie sont maintenant des **bornes de Manski** dans `evals/baseline_v1/report.py` (`RateBounds`, `_rate_bounds`, `_fmt_bounds`) : attente comptée comme étayée (basse), puis comme non étayée (haute). Elles se rejoignent quand tout est étiqueté ;
    - une sortie est « indéterminée » seulement si elle a des éléments en attente et aucun non étayé (`GroupStats.undetermined_runs`) ;
    - IC affiché : du bas de l'IC de la borne basse au haut de l'IC de la borne haute (bootstraps appariés) ;
    - le rapport gagne une colonne « Undetermined outputs », le nombre en attente dans le tableau par type, et un bandeau « Provisional report » tant qu'il reste de l'attente. Le taux de contradictions et les taux par type restent sur les éléments tranchés ;
    - **options écartées :** (A) exclure les sorties indéterminées (toujours biaisé) ; (C) un seul chiffre présenté comme une borne basse ;
    - 3 nouveaux tests, 2 tests adaptés. Doc mise à jour : `docs/evals/baseline-v1.md` (méthode et limites), roadmap, revue de conception (B6 `Fixed`).
15. **S1, S2 et couverture** (2026-10-28, option B pour S1 et S2) :
    - **épinglage :** toutes les images par tag exact et digest d'index multi-architecture (`Dockerfile`, compose, CI, y compris la ligne `# syntax=`, `tonistiigi/binfmt` et `moby/buildkit`), les actions par SHA de commit avec la version en commentaire. Versions : Python 3.12.16-slim-bookworm, uv 0.13.6, pgvector 0.8.7-pg16-bookworm (`pg16` pointait déjà dessus : aucun changement pour le volume), Caddy 2.11.7, cloudflared 2026.10.5, checkout v7.0.1, setup-uv v10.3.3 ;
    - **Dockerfile :** l'étage de build part de la même image Python que l'étage final, et le binaire uv est copié depuis une image uv épinglée. Avant, les deux étages pouvaient avoir des versions de Python différentes. uv ne publie d'ailleurs plus de tags versionnés `bookworm` ;
    - **Dependabot** (`.github/dependabot.yml`) : `uv`, `github-actions`, `docker`, `docker-compose`, chaque lundi ; mineures et correctifs groupés, majeures à part ; Python reste en 3.12 ;
    - **test de garde** `tests/test_supply_chain.py` (15 tests) : toute référence non épinglée échoue, ainsi que les écarts que Dependabot ne corrige pas (image Postgres de la CI, version de uv dans `ci.yml`, les deux étages Python). Il fige aussi `pg16-bookworm` ;
    - **CI** (`ci.yml`) : trois jobs. `checks` (comme avant, plus `--cov` et un résumé de couverture), `audit` (`pip-audit --strict` sur `uv.lock` exporté avec hashes, aussi chaque lundi ; exceptions dans `.github/pip-audit-ignore.txt`, avec raison et date de revue), `image` (build amd64 et arm64, puis test de fumée de l'image amd64). Jeton en lecture seule, `persist-credentials: false`, délais maximaux, `concurrency` ;
    - **couverture :** `pytest-cov`, couverture de branches de `src/` et `evals/`, 69 % avec Postgres, plancher `fail_under = 68` (cliquet) ;
    - **options écartées :** S1-A (tags exacts sans digest), S1-C (Renovate), S2-A (build amd64 seul), S2-C (scan Trivy, à reconsidérer une fois la VM en service) ;
    - doc mise à jour : `docs/development/conventions.md` (nouvelle section « Dependencies and supply chain »), `testing.md` (CI et couverture), `security.md`, `deployment.md`, amendement en tête de l'ADR-0006, revue de conception (S1 et S2 `Fixed`, couverture), roadmap, et les commandes de test Docker dans `CLAUDE.md` ;
    - `.gitignore` ignore maintenant les fichiers de couverture.

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
  - **R8 :** le retry de `GeminiClient` ignore `retryDelay`, et rien ne limite le débit.

## Prochaines étapes, dans l'ordre
1. **Côté Abdou, en parallèle :** l'échange de découverte avec les journalistes (§9 du cahier des charges), avant la mise en service pour l'équipe. Questions à ajouter :
   - un sous-domaine de dzdia.com pour le webhook (par exemple `bot.dzdia.com`) ;
   - l'identifiant du groupe Telegram de l'équipe ;
   - les identifiants Telegram des journalistes, pour la liste blanche.
2. **Déploiement sur la VM Oracle Ampere (ARM64)** : questions d'architecture, ADR-0007, puis mise en place (voir en haut). Caddy et DuckDNS en attendant le sous-domaine.
3. **Reprendre `extract` puis `match`** (choisir d'abord le modèle extracteur, voir « En pause » ; corriger B7 avant `match`), et inspecter la répartition trouvé / non trouvé par type. Ajuster `--name-threshold` si nécessaire.
4. **Étiquetage :** `export-labels`, étiquetage S/C/N par Abdou dans Excel, puis `import-labels` et `report`. Commiter `labels.jsonl` et `report.md`.
5. **Passe parallèle du LLM-juge** sur les mêmes éléments. Les verdicts sont stockés à part, pour la calibration en semaine 5.

## Petits points en suspens
- **Le webhook du bot de dev pointe encore sur une URL trycloudflare morte**, car la pile de dev est arrêtée. Telegram garde les updates en attente (environ 24 h). Pour reprendre les tests en local : `docker compose --profile dev up -d --build`, lire la nouvelle URL dans `docker compose logs cloudflared`, puis relancer `set-webhook`. Le volume `pgdata` de dev contient le job de test (statut `done`).
- **`.env.example`** : Abdou doit vérifier qu'il liste toutes les variables (`POSTGRES_PASSWORD`, `TELEGRAM_*`, `LOG_FORMAT`, `LOG_LEVEL`, `PUBLIC_HOST`, `API_PORT`, `POSTGRES_PORT`), car Claude ne peut pas le lire.
- **Avertissements de pytest, sans effet aujourd'hui :** `StarletteDeprecationWarning`, car `TestClient` utilise httpx ; `_UnionGenericAlias`, qui vient de google-genai.
- **Image Docker de 401 Mo** (406 Mo avant S1) : les dépendances des évals (trafilatura, google-genai) y sont incluses. On les garde, car la v2 s'en servira dès la semaine 2.
