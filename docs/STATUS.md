# Statut — 2026-11-13 (palier 2, étapes 1 et 2 faites : ADR-0009, 0010 et 0011 ; prochaine étape : le code de l'étape 3, en session Opus)

## Pour reprendre dans une nouvelle session
- **À faire en premier : l'étape 3 du palier 2** (preuves, `llm/`, génération, validateur, citations ; section « Palier 2 »). C'est du code : **session Opus 5.5**, à partir de ce fichier et des ADR-0009, 0010 et 0011. Avant d'écrire : présenter à Abdou 2 ou 3 options pour les fonctions centrales (règle de `CLAUDE.md`), lister les causes de C-1 et C-2 et attendre son accord, et ne pas écrire le cœur de son kata (boucle validation + réessai : signatures, docstrings et tests seulement). Si Abdou préfère traiter d'abord l'option de revue B (chiffre v1, avant l'envoi du CV), c'est une décision de formulation : session Fable.
- **ADR-0010 accepté le 2026-11-13** (`docs/adr/0010-v2-comparison-protocol.md`, Abdou : « tout valider ») ; il amende l'ADR-0001 pour cette comparaison. L'essentiel :
  - tâche article, 16 articles d'origine, **trois bras**, une génération par article : `page_text` (runs v1 réutilisés), `plain_prompt` (pipeline v1, prompt `v1-plain-1` : mêmes consignes éditoriales et mêmes réglages que la v2, sans preuves ni validateur ; c'est l'ablation, V-5), `v2` (ADR-0009 + 0011, prompt `v2-article-1`) ;
  - même texte d'entrée pour tous : le texte figé des copies (V-10) ; les prompts sont figés avant le passage, mis au point sur des pages hors échantillon ;
  - **tout est étiqueté à la main** dans les trois bras (V-4) : environ 700 lignes, près de 4 h pour Abdou ; `plain_prompt` est le bras à couper si le temps manque ;
  - locuteur de chaque citation étiqueté (V-2) : un `C` pour mauvais locuteur porte `locuteur` dans `note` ; pas de garde en code dans cette tranche ;
  - **étiquetage à l'aveugle** (E-5) : bras, verdict et score du matcher cachés, lignes mélangées par article avec une graine fixe, clé opaque, guide figé avant l'export, contrôles mécaniques commités ;
  - **critère principal :** différence appariée `v2 − page_text` du taux d'éléments non soutenus, bootstrap sur les articles ; secondaires : les deux moitiés (`plain_prompt − page_text`, `v2 − plain_prompt`), taux hors citations, citations gardées / retirées / mal attribuées, compteurs du validateur ;
  - **conclusion écrite à l'avance** (V-3) : si l'intervalle contient 0, « pas de différence détectable sur 16 articles », et le CV ne revendique que la garantie sur les citations ; un gain n'est attribué à l'architecture que pour la part `v2 − plain_prompt` ;
  - température 0 à vérifier par quelques appels hors échantillon avant le passage (V-6).

  **Deux seuils ajoutés par Claude à la rédaction, à confirmer par Abdou :** plus de 2 générations v2 en échec sur 16 : pas de comparaison publiée avant d'avoir compris la cause ; un bras avec moins des deux tiers des éléments de `page_text` : l'écart est écrit à côté de son taux. **Point laissé ouvert par l'ADR :** le rapport v1 changerait s'il était reconstruit avec les nouvelles étiquettes de `page_text / article` ; la présentation des chiffres v1 se décide avec l'option de revue B.
- **Revue indépendante : l'option de revue A est tranchée (Abdou, le 2026-11-13, « tout valider »).** Le suivi des décisions est dans la section « Decisions » en tête de `docs/reviews/2026-11-13-independent-review.md`.
  - **V-1, accepté : ADR-0011** (`docs/adr/0011-failed-quote-is-removed.md`), qui amende l'ADR-0009 (un ADR accepté ne s'édite pas). Une citation encore en échec après le réessai est supprimée ; un paragraphe vide est supprimé ; un article vide fait échouer le job. Le message du réessai laisse le modèle recopier les mots ou réécrire le fait en phrase sourcée. Le message Sources donne le nombre de citations retirées, sans le locuteur ni le texte. L'éval compte les citations gardées et retirées.
  - **V-2, V-3, V-4, V-5 et E-5, acceptés :** dans l'ADR-0010 (ci-dessus). V-6 et V-10 y sont pris en compte.
  - **V-12, accepté :** nouvel ordre des étapes du palier 2 (section « Palier 2 ») : la génération et l'éval passent avant le téléchargement, la garde SSRF et Telegram.
  - **Encore ouverts :** l'option de revue B (E-1, E-2, E-4), à faire avant l'envoi du CV ; l'option de revue C (C-1, C-2), prévue à la nouvelle étape 3 du palier 2 ; les autres constats ne sont ni acceptés ni refusés.
- **Rappel des trois options de revue présentées** (`docs/reviews/2026-11-13-independent-review.md`, en anglais, faite le 2026-11-13 par Claude, en lecture seule : 49 constats, chacun avec une référence, une sévérité, un niveau de confiance et sa nature, puis huit priorités) :
  - **(A) amender l'ADR-0009 :** une citation en échec est supprimée au lieu de passer au discours indirect (V-1) ; toute citation v2 est étiquetée pour son locuteur (V-2) ; génération et éval avant la garde SSRF et Telegram, car l'éval rejoue les copies (V-12) ;
  - **(B) corriger la présentation du chiffre v1 :** par tâche et comme plancher, dans `docs/evals/baseline-v1.md`, le README et la puce de CV (E-1, E-2, E-4). « 28 % [21–35 %] » est l'intervalle du plancher ; l'estimation pondérée par l'audit donne 36 % [23–53 %] ; 28 % mélange 9 % (résumés) et 48 % (articles) ;
  - **(C) corriger deux défauts confirmés par exécution :** un seul heartbeat en échec annule le job et consomme un essai (C-1, `worker/queue.py`) ; le client LLM ne réessaie ni les timeouts ni les erreurs de connexion (C-2, `evals/baseline_v1/llm.py`). Lister d'abord les causes et attendre l'accord d'Abdou.

  Ordre retenu : A (fait), puis B avant l'envoi du CV, puis C à la nouvelle étape 3 du palier 2. Tant que B n'est pas tranchée, les puces de CV ci-dessous sont celles d'avant la revue.
- **Répartition des modèles (décidée par Abdou le 2026-11-13) :** **Fable 5.2 en effort high** pour les décisions et l'architecture (questions, options, ADR, relecture de diff) ; **Opus 5.5 en effort high** pour le code, les tests et les commandes. **Une session par rôle, jamais de changement de modèle dans une discussion** : le cache de prompt est propre à chaque modèle, et changer en cours de route renvoie toute la conversation sans cache. On change de modèle au moment d'un `/handoff`, de préférence à une frontière d'étape (décision écrite, ou tests verts) ; Abdou coupe de toute façon vers 200k tokens de contexte. Déroulé :
  1. session Fable : questions, options, ADR, et l'étape décrite dans ce fichier ;
  2. session Opus fraîche : le code et les vérifications de cette étape, à partir de ce fichier et de l'ADR ; une question d'architecture qui apparaît remonte à Abdou, elle n'est pas tranchée dans cette session ;
  3. session Fable fraîche : relecture du diff, relance des tests (le compte rendu d'une session n'est pas une preuve), puis décision suivante.

  **Option B, préparée mais pas utilisée par défaut :** un sous-agent Opus 5.5 high, `.claude/agents/implementer.md`, pour les petites tâches de code déléguées depuis une session Fable. Abdou dira quand s'en servir. Le dossier `.claude/agents/` est nouveau : le sous-agent n'est chargé qu'à partir d'une nouvelle session.
- **Vérifications (2026-11-13, pendant la revue) :** ruff, `ruff format --check` et mypy strict ne signalent rien ; 174 tests passent et 13 sont sautés. La suite Postgres n'a pas été relancée (Docker Desktop arrêté, aucun code de `src/` modifié).
- **Plan en trois paliers** (validé par Abdou le 2026-11-09) :
  - **palier 1 (terminé le 2026-11-13)** : le chiffre final du taux d'hallucination de la v1, pour le CV d'Abdou. L'échéance (mercredi 2026-11-11 au soir) a été dépassée de deux jours ;
  - **palier 2 (commencé le 2026-11-13)** : une tranche verticale de la v2, un lien Telegram devient un article sourcé, puis l'éval avec le protocole de la baseline. **Étape 1 faite :** Abdou a validé toutes les recommandations, consignées dans l'ADR-0009 (`docs/adr/0009-v2-link-to-sourced-article-slice.md`). **Date cible : 22 novembre au plus tard**, plus tôt si possible. Voir la section « Palier 2 » ci-dessous ;
  - **palier 3** : la VM Oracle et l'usage réel (checklist ci-dessous, pas commencée).
- **Le chiffre** (`evals/datasets/baseline_v1/report.md`, ligne « url_only / all tasks », 16 articles d'origine, IC à 95 % par bootstrap sur les articles) : en `url_only` (la v1 livrée), **au moins 28 % [21–35 %]** des éléments ne sont pas soutenus par l'article (72/258), et 41 % [30–53 %] des sorties en ont au moins un (26/64). En `page_text` : 5 % [3–8 %] et 28 % [19–38 %]. **C'est un plancher** : les `found` hors audit comptent comme soutenus, et la précision de l'audit est de 86 % en `url_only` (30/35). Abdou a choisi de l'annoncer comme un plancher (option A) plutôt que d'étiqueter les 150 `found` restants (B) ou d'ajouter un estimateur corrigé (C). Détails et limites dans `docs/evals/baseline-v1.md`, sections « Results » et « Known limitations ».
- **Puces de CV proposées** (rien au-delà du rapport) : FR « Mesuré le taux d'hallucination de la v1, qui n'ouvrait pas les liens : sur 16 articles d'actualité algérienne (64 sorties), au moins 28 % des éléments factuels [IC 95 % : 21–35 %] n'étaient pas soutenus par la source, contre 5 % avec le texte de la page ; 252 éléments étiquetés à la main, IC par bootstrap sur les articles. » Faits corrigés pour son paragraphe : **16 articles** (20 URL dont 4 doublons opaques), « plus léger » et non « moins cher » pour `gemini-3.5-flash-lite`.
- **Étiquetage :** 252 éléments étiquetés par Abdou (le 2026-11-12), puis 20 lignes signalées par des contrôles mécaniques sans LLM (scripts dans un scratchpad, non gardés) et relues par lui (13 changées, le 2026-11-13). Règle ajoutée au guide : une citation reformulée est `C`. La dernière citation reformulée de tsa-02 (« Ceux qui nourrissent ce monstre… ») est passée en `C` ensuite : `page_text` passe de 23 à 24/445, sans effet sur `url_only`. **Ne jamais pré-remplir ni corriger d'étiquettes avec un LLM** : ce sont la référence pour calibrer le juge.
- **Prochaine étape : l'étape 3 du palier 2** (voir « À faire en premier » et « Palier 2 ») ; les conditions `plain_prompt` et `v2` dans le harnais viennent à l'étape 4. La passe du LLM-juge vient après le palier 2, sur les étiquettes v1 et v2 (kappa de Cohen, un kata d'Abdou : ne pas l'écrire à sa place).
- **Palier 3, pas commencé : le premier passage sur la vraie VM**, avec Abdou. Tout est décrit dans `docs/operations/deployment.md` (section « First installation ») ; décisions dans `docs/adr/0007-production-host-on-oracle-cloud.md`. Ce sont **ses comptes et sa machine** : lui donner les étapes et les commandes, il les lance et colle les sorties.
  1. **Console Oracle (Abdou) :** passer le compte en Pay As You Go avec une alerte de budget (sinon Oracle arrête l'instance « inactive ») ; créer la VM `VM.Standard.A1.Flex`, Ubuntu 24.04 aarch64, avec une **clé ed25519** (une clé RSA est refusée par `bootstrap.sh`) ; réserver l'IP publique ; security list 22/80/443 ; bucket `dzdia-backups` (rétention 7 jours, cycle de vie `daily/` et `predeploy/` 8 jours, `weekly/` 29 jours, et la politique `objectstorage-<région>`) ; groupe dynamique `dzdia-vm` et politique d'ajout seul (texte exact dans `deployment.md`) ; nom DuckDNS ; check healthchecks.io (optionnel) ; bot de prod chez BotFather.
  2. **Poste d'Abdou :** `age-keygen -o dzdia-backup.key` (clé privée dans son gestionnaire de mots de passe et sur papier, jamais sur la VM).
  3. **VM :** `sudo deploy/bootstrap.sh --dry-run`, puis sans `--dry-run` ; garder la session SSH ouverte et tester `ssh deploy@…` à côté ; `sudo passwd ubuntu` (mot de passe pour la console série seulement).
  4. **`.env` de prod** (Abdou, en tant que `deploy`) : nouveaux secrets, `PUBLIC_HOST`, variables `BACKUP_*` et `OCI_*` ; une copie dans son gestionnaire de mots de passe.
  5. `deploy/deploy.sh origin/main`, puis `set-webhook --drop-pending` avec le bot de prod, puis un message de test.
  6. `sudo systemctl start dzdia-backup.service` et lire le journal. **Points à valider sur la VM** (non testables en local) : `rclone` avec l'instance principal et une politique sans `OBJECT_OVERWRITE` (si `rcat` demande un autre droit, l'ajouter sauf écrasement et suppression) ; l'origine Docker dans `unattended-upgrade --dry-run --debug` ; `sshd -t` et la reconnexion ; l'accès au service de métadonnées (169.254.169.254) depuis le conteneur `rclone`.
  7. Premier exercice de restauration depuis le poste : `deploy/restore-drill.sh`.

  Ensuite : mettre à jour la doc avec ce qui a changé sur la VM, ajouter une ligne « validé sur la VM » à l'ADR-0007 si besoin (les métadonnées seulement), et cocher dans la fiche.
- **À surveiller lundi :** les premières PR de Dependabot. Vérifier comment il regroupe les mises à jour de digest seules. Une PR qui touche l'image Postgres ou uv sera rouge tant que `ci.yml` n'est pas aligné à la main : c'est le test de garde qui fait son travail (`docs/development/conventions.md`, section « Dependencies and supply chain »).
- **Reste ouvert dans l'audit** (petits points, à traiter quand ils deviennent utiles) : R3, R7, R9, R11, S5, S6. Prévus plus tard : R4, R5, R6, R10, S3, et A1 à A5 (semaine 2). B7 et R8 sont corrigés (palier 1). Ouverts depuis le 2026-11-13 : B8 (accents cassés dans 8 contextes copiés par l'extracteur) et B9 (le rapport cite « ADR-001 » au lieu de ADR-0001).
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
  - **scripts bash de `deploy/` :** pas de shellcheck sous Windows, le lancer en conteneur (`koalaman/shellcheck:stable`, commande dans `docs/development/testing.md`). Pour tester leur comportement : dans un conteneur `docker:cli` avec le socket Docker, sur une copie du dépôt dans un dossier dont le nom n'est **pas** `dzdia-newsroom` (sinon le projet compose et le volume `pgdata` d'Abdou seraient touchés) ; `deploy.sh` refuse root, donc créer un utilisateur dans le groupe du socket. Le pare-feu se teste dans `ubuntu:24.04` avec `--cap-add NET_ADMIN`. Pièges rencontrés : `age-keygen` refuse d'écraser un fichier ; un `| head` sur une commande avec `pipefail` renvoie 141 (SIGPIPE) ; un script sourcé par `<(…)` ne trouve plus `lib.sh` (son `BASH_SOURCE` est `/dev/fd`) ;
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
  - `01887cc` : une passation, qui note l'accès de Claude à la CI ;
  - `91dc0b8` : l'ADR-0007 et les scripts `deploy/` ;
  - `82833c2` : le palier 1 (ADR-0008, B7, R8, ligne globale par condition dans le rapport, `labels.jsonl`, `report.md` et la doc) ;
  - le dernier commit : la citation de tsa-02 passée en `C` (`page_text` 24/445), rapport et doc à jour (ce fichier compris).

  Depuis, trois commits, tous poussés : `63bd98b` (réglages optionnels de l'éval dans `.env.example`) ; `3cf625f`, le 2026-11-13 (l'étape 1 du palier 2 : ADR-0009, l'index des ADR, la revue du 2026-10-28, `roadmap.md` ; la revue indépendante `docs/reviews/2026-11-13-independent-review.md` ; le sous-agent `.claude/agents/implementer.md`) ; et un dernier commit qui met ce fichier à jour.

  Puis, le 2026-11-13, tous poussés : `ba2108e` (les ADR-0010 et 0011, l'index des ADR, les lignes « Amended by » des ADR-0001 et 0009, `roadmap.md`, la section « Decisions » de la revue indépendante et ce fichier) et un dernier commit qui met ce fichier à jour. L'arbre de travail est propre.

- **Dépôt GitHub :** `origin` = https://github.com/Mounso/dzdia-newsroom. Tout est sur `main`. Le 2026-10-27, l'historique a été réécrit et poussé en force pour retirer les mentions de co-auteur : **aucun commit ne porte de ligne `Co-Authored-By`**, et il ne faut pas en ajouter.
- **CI :** trois jobs, tous verts sur `c578779` et `d2e1090` (B6 compris) : `checks` 38 s, `audit` 11 s, `image` 2 min 08 (build arm64 sous QEMU compris). Le job `audit` tourne aussi chaque lundi. Dependabot a déjà fait un premier passage sur ses 4 écosystèmes, sans PR : tout était à jour.
- **Claude peut lire la CI** avec `gh` (installé le 2026-10-28, version 2.103.3), connecté avec un jeton fine-grained en lecture seule d'Abdou, limité à ce dépôt et stocké dans le gestionnaire d'identifiants Windows. Tant que l'app n'est pas redémarrée, appeler `gh` par son chemin complet : `"/c/Program Files/GitHub CLI/gh.exe"`. Commandes utiles : `gh run list -R Mounso/dzdia-newsroom`, `gh run view <id> --log-failed`. Le jeton ne permet ni d'écrire ni de relancer un run, et il expire : s'il est refusé, demander à Abdou de le renouveler.
- **Fichiers locaux, jamais commités** (exclus via `.git/info/exclude`) :
  - `Refactorisation DzdiaBot v2.1.md` : le cahier des charges complet (périmètre, architecture cible, feuille de route, questions de découverte) ;
  - `docs/fiche_de_suivi.md` : la fiche de préparation aux entretiens d'Abdou. **La mettre à jour à la fin de chaque étape**, en même temps que ce fichier ;
  - `evals/.data/` (ignoré par Git) : copies des articles, `runs.jsonl`, et dans `pre_b5/` les 20 extractions mises de côté par B5.
- **Environnement :** Windows 11, avec WSL (Ubuntu) et Docker Desktop. Les tests Postgres tournent dans un conteneur Linux (commande dans `CLAUDE.md`), car psycopg en async ne fonctionne pas avec la boucle asyncio par défaut de Windows. **Docker Desktop doit être lancé** avant toute commande `docker`. S'il ne l'est pas, Claude peut le démarrer avec `Start-Process` sur `C:\Users\abdou\AppData\Local\Programs\DockerDesktop\Docker Desktop.exe`, puis attendre que `docker info` réponde.
- **Vérifications (2026-10-28, après l'ADR-0007) :** ruff, `ruff format`, mypy strict et shellcheck ne signalent rien ; sous Windows, 153 tests passent et 13 sont sautés ; les essais de `deploy/` dans des conteneurs Linux passent tous. La suite Postgres n'a pas été relancée (aucun code Python de `src/` n'a changé).
- **Vérifications précédentes (2026-10-28, après S1 et S2) :** ruff, `ruff format` et mypy strict ne signalent rien. Sous Windows, 147 tests passent et 13 sont sautés (les tests Postgres). Dans Docker, les 160 passent, couverture de branches 69 % (plancher 68). `actionlint` ne signale rien sur `ci.yml`. L'image se construit en amd64 (35 s) et en arm64 sous émulation (72 s), et le test de fumée passe sur les deux (Python 3.12.16, uid 10001). `pip-audit` : aucune vulnérabilité connue sur les 90 paquets verrouillés.
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
16. **Déploiement sur la VM : ADR-0007 et `deploy/`** (2026-10-28 ; Abdou a validé les 4 recommandations, puis l'option A pour le provisioning) :
    - **ADR-0007** (`docs/adr/0007-production-host-on-oracle-cloud.md`, Accepted) : Ubuntu 24.04 sur A1.Flex, compte en Pay As You Go, IP réservée ; SSH public par clés ed25519 seules, utilisateurs `ubuntu` (sudo) et `deploy` (groupe `docker`) ; pare-feu Oracle + `iptables` de l'image (pas d'UFW) ; `unattended-upgrades` (sécurité + Docker, redémarrage à 04:00 UTC), Docker en `live-restore` ; `deploy.sh` avec rollback et règle expand/contract ; sauvegardes `pg_dump` + `age` + `rclone` vers Object Storage avec instance principal et politique d'ajout seul ; DuckDNS puis `bot.dzdia.com` ; S5 laissé ouvert ; provisioning par `bootstrap.sh` idempotent ;
    - **`deploy/`** : `lib.sh` (helpers, `env_get` qui lit `.env` sans le sourcer, `restore_check`), `bootstrap.sh` (`--dry-run`), `deploy.sh` (`<ref>`, `--rollback`, `--status`, `--dry-run` ; historique dans `~/.local/state/dzdia-newsroom/deployments.tsv` ; garde les images des 3 derniers déploiements réussis), `backup.sh` (`daily|predeploy`, `--keep-local DIR`), `restore-drill.sh` (sur le poste), `systemd/dzdia-backup.{service,timer}` ;
    - **compose** : nouveau service `rclone` (profil `ops`, image `rclone/rclone:1.75.2` épinglée par digest, sans identifiant) ;
    - **tests** : `tests/test_deploy_guards.py` (5 tests : seul `caddy` publie hors de `127.0.0.1`, les services applicatifs utilisent `dzdia-newsroom:local`, scripts stricts, en LF et finissant par `main "$@"`), plus l'image `rclone` dans le test de garde de la chaîne d'approvisionnement ; `shellcheck -x deploy/*.sh` dans le job `checks` de la CI ; essais dans des conteneurs Linux (sauvegarde, restauration, mauvaise clé, refus de `deploy.sh`, cibles de rollback, pare-feu Oracle et sa deuxième passe sans effet) ;
    - **doc** : `deployment.md` (installation, déploiement, rollback, sauvegardes, passage à `bot.dzdia.com`), `runbook.md` (sauvegardes, exercice mensuel, restauration, VM à reconstruire, déploiement raté, SSH perdu, pare-feu, mises à jour), `configuration.md` (6 variables), `security.md`, `conventions.md` (expand/contract, scripts shell), `testing.md`, roadmap, revue de conception, `README.md`, `CLAUDE.md`, liens des ADR 0004 et 0006.

## Fait : palier 1 (baseline v1)
- **Extracteur (ADR-0008, accepté) :** `gemini-3.1-pro-preview`, thinking `low`, température 0, une passe, facturation Gemini activée par Abdou (crédit de 5 €). Nouveaux réglages `EXTRACTION_MODEL`, `EXTRACTION_THINKING_LEVEL` (défaut `low`) et `LLM_MAX_RPM` (défaut 20), appliqués aux appels structurés seulement, jamais au modèle v1 évalué. **Abdou doit les ajouter lui-même à `.env.example`** (illisible pour Claude). `gemini-3.5-flash-lite` reste écarté (2 citations sur 7, 8 organisations sur 16 ; sorties dans `evals/.data/baseline_v1/compare_gemini-3.5-flash-lite/`).
- **B7 (option A) :** `_NUMBER` de `matcher.py` n'accepte un séparateur de milliers qu'après un premier groupe de 1 à 3 chiffres : « En 2023 450 » donne 2023 et 450 ; « 12 450 » reste un seul nombre (limite documentée).
- **R8 (option A) :** `retry_async` attend `max(backoff, retryDelay × (1 + 10 % de gigue))` et abandonne si `retryDelay` dépasse 120 s ; `gemini_retry_delay` lit `google.rpc.RetryInfo` ; `RequestSpacer` espace les débuts de requête de `60 / LLM_MAX_RPM` s ; `generate_structured` renvoie `Structured(value, usage, latency_ms)` ; `ExtractionRecord` stocke `thinking_level` et les jetons.
- **Résultats :**
  - `extract` : 144/144 sans échec, 733 éléments, 5 sorties sans élément ; 69 681 jetons en entrée, 45 264 en sortie, 96 597 de réflexion, environ 1,85 $. La latence enregistrée le 2026-11-09 inclut l'attente d'un créneau de concurrence (médiane 332 s) ; corrigé depuis (appel au modèle seul), noté dans `schemas.py` et `baseline-v1.md` ;
  - `match` : 600 `found`, 133 `not_found` ; `url_only` 195 / 93 (32 % non trouvés), `page_text` 405 / 40 (9 %) ;
  - `export-labels` : 252 lignes. Par type (`not_found` / audit `found`) : lieu 67 / 51, citation 22 / 2, organisation 20 / 15, personne 11 / 26, date 8 / 17, nombre 5 / 8. Estimation : 1 h 30 à 2 h avec Ctrl+F.
- **Pourquoi pas un LLM-juge maintenant :** un juge doit d'abord être calibré sur des étiquettes humaines (kappa, un kata d'Abdou). Ces 252 étiquettes sont le jeu de référence ; le juge vient ensuite.
- **Rapport :** une ligne « all tasks » par condition (16 articles d'origine) et la précision de l'audit par condition, avec un test (2026-11-13).
- **Vérifications (2026-11-13) :** ruff, `ruff format --check` et mypy strict ne signalent rien ; 174 tests passent et 13 sont sautés. La suite Postgres n'a pas été relancée (aucun code de base de données de `src/` n'a changé).
- **Données utiles :** limites du tier 1 : `gemini-3.1-pro-preview` 25 RPM et 250 RPD ; prix (USD par million de jetons) : 3.1 Pro 2 $ en entrée, 12 $ en sortie (réflexion comprise). À partir du 2026-12-01, `gemini-2.5-flash` est redirigé vers `gemini-3.5-flash-lite`. Doublons opaques : `tsa-00-opaque`, `tsa-01-opaque`, `tsa-02-opaque` et `tsa-05-opaque` utilisent les copies `tsa-00`, `tsa-01`, `tsa-02` et `tsa-05`.

## Palier 2 : tranche verticale de la v2 (commencé le 2026-11-13)
- **Demande d'Abdou :** un arrêt pour validation après chaque étape. Le rapport doit comparer la v2 à `page_text`, pas seulement à `url_only` : le passage de `url_only` à `page_text` vient du seul fait de télécharger la page ; l'apport de la v2 est l'écart entre `page_text` et `v2`. Lignes à comparer, tâche article : `page_text / article` 7 % [5–11 %] (19/256), 81 % des articles avec au moins un élément non soutenu (13/16) ; citations `page_text` 54 % [22–78 %] (7/13, toutes tâches).
- **Étape 1 faite (ADR-0009, toutes les recommandations validées) :** client LLM dans `src/dzdia_newsroom/llm/` (A3) ; sortie structurée phrase par phrase, chaque phrase cite ses preuves ; citations en segments typés, vérifiées par le code (sous-chaîne exacte de la preuve après normalisation), discours indirect si l'échec persiste après le réessai (**remplacé par l'ADR-0011 : la citation est supprimée**) ; preuves = paragraphes, identifiants `E1…En` ; garde SSRF contre le DNS rebinding (`guardrails/ssrf.py`) ; outils dans `tools/` ; un seul handler et une table `drafts` (A1 léger) ; erreurs visibles pour le journaliste (R5) ; deux messages en texte brut, découpés à 4 096 caractères (R10) ; générateur `gemini-3.1-flash-lite-preview`, température 0 (`GENERATION_MODEL`). Reportés : A2, A4, A5, R4, R6, R7, R9, l'alerte admin de R5.
- **Katas :** Claude écrit les signatures, docstrings et tests de la garde SSRF et de la boucle validation + réessai ; Abdou écrit leur cœur ; Claude relit. Le reste (dont la vérification des citations) est écrit par Claude.
- **Étapes suivantes, avec un arrêt après chacune** (ordre changé le 2026-11-13, constat V-12 de la revue : l'éval rejoue le HTML stocké, donc elle n'a besoin ni du réseau, ni de `drafts`, ni de Telegram ; le chiffre de la v2 est à l'abri si le câblage prend du retard) :
  2. **fait le 2026-11-13 :** ADR-0011 et ADR-0010 (protocole de comparaison, fixé avant tout résultat ; résumé en haut de ce fichier). Les points qui étaient à trancher, pour mémoire : le texte que reçoit l'extracteur (l'article rendu, sans les sources) ; le critère principal (différence appariée v2 − `page_text` sur la tâche article, bootstrap sur les articles) ; les garde-fous (nombre de citations gardées ou passées au discours indirect, nombre d'éléments, échecs) ; une ablation « v2 sans validateur » ou non ; l'audit des `found` à 20 % ; le rejeu du HTML stocké (`evals/.data`, `html_sha256`) au lieu du réseau ; l'exclusion des doublons opaques du chiffre ;
  3. preuves (`tools/evidence.py`), `llm/` (déplacement depuis `evals/`, `FakeLLM`), génération, validateur et citations, avec leurs tests. Le kata d'Abdou de la boucle validation + réessai est ici. Les défauts C-1 et C-2 de la revue se traitent ici (causes listées d'abord, accord d'Abdou avant toute édition) ;
  4. éval (ADR-0010) : conditions `plain_prompt` et `v2` dans le harnais, sur le texte figé des copies (`evals/.data`), export à l'aveugle, bootstrap apparié et rapport de comparaison ; puis `run`, `extract`, `match`, l'étiquetage (environ 700 lignes, près de 4 h pour Abdou), l'import, le rapport, la doc et les puces de CV ;
  5. téléchargement et garde SSRF (kata d'Abdou), avec leurs tests ;
  6. migration `0002_drafts.sql`, branchement dans le worker et Telegram, test de bout en bout avec le bot de dev.
- **Estimation :** environ 4 jours de sessions pour Claude, 8 à 10 h pour Abdou (katas, test du bot, étiquetage ; l'étiquetage est passé d'environ 45 min à près de 4 h avec l'ADR-0010).

## Prochaines étapes, dans l'ordre
1. **Palier 2**, étapes 2 à 6 (section ci-dessus).
2. **Passe du LLM-juge** sur les éléments étiquetés (v1, puis v2), calibrée contre les étiquettes humaines (kappa). Verdicts stockés à part, jamais à la place d'une étiquette.
3. **Optionnel :** étiqueter les 150 `found` hors audit de `url_only` pour remplacer le plancher par un chiffre exact (environ 1 h pour Abdou).
4. **Palier 3 :** premier passage sur la VM Oracle Ampere (ARM64), voir en haut, avec Caddy et DuckDNS en attendant le sous-domaine. **Côté Abdou, avant la mise en service pour l'équipe :** l'échange de découverte avec les journalistes (§9 du cahier des charges), avec ces questions en plus :
   - un sous-domaine de dzdia.com pour le webhook (par exemple `bot.dzdia.com`) ;
   - l'identifiant du groupe Telegram de l'équipe ;
   - les identifiants Telegram des journalistes, pour la liste blanche.

## Petits points en suspens
- **Le webhook du bot de dev pointe encore sur une URL trycloudflare morte**, car la pile de dev est arrêtée. Telegram garde les updates en attente (environ 24 h). Pour reprendre les tests en local : `docker compose --profile dev up -d --build`, lire la nouvelle URL dans `docker compose logs cloudflared`, puis relancer `set-webhook`. Le volume `pgdata` de dev contient le job de test (statut `done`).
- **`.env.example`** : Abdou doit vérifier qu'il liste toutes les variables (`POSTGRES_PASSWORD`, `TELEGRAM_*`, `LOG_FORMAT`, `LOG_LEVEL`, `PUBLIC_HOST`, `API_PORT`, `POSTGRES_PORT`, et depuis l'ADR-0007 `BACKUP_AGE_RECIPIENT`, `BACKUP_BUCKET`, `BACKUP_HEALTHCHECK_URL`, `OCI_NAMESPACE`, `OCI_COMPARTMENT_OCID`, `OCI_REGION`, et depuis l'ADR-0008 `EXTRACTION_MODEL`, `EXTRACTION_THINKING_LEVEL`, `LLM_MAX_RPM`), car Claude ne peut pas le lire.
- **S2-C (scan Trivy de l'image)** : à reconsidérer maintenant que la VM arrive.
- **Avertissements de pytest, sans effet aujourd'hui :** `StarletteDeprecationWarning`, car `TestClient` utilise httpx ; `_UnionGenericAlias`, qui vient de google-genai.
- **Image Docker de 401 Mo** (406 Mo avant S1) : les dépendances des évals (trafilatura, google-genai) y sont incluses. On les garde, car la v2 s'en servira dès la semaine 2.
