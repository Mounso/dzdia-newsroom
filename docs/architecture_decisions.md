# Architecture Decision Records

Format court : contexte → décision → alternatives écartées → conséquences. Un ADR n'est jamais modifié après coup ; on en écrit un nouveau qui le remplace.

---

## ADR-001 — Protocole de mesure de la baseline d'hallucination de la v1

**Date :** 2026-10-27 · **Statut :** accepté

### Contexte
Le bot v1 transmet l'URL brute au LLM, sans jamais télécharger la page : le texte de l'URL tient lieu de `content` pour `get_summary` comme pour `get_article`. On veut chiffrer ce défaut avant de le corriger, puis mesurer la v2 sur exactement les mêmes sources.

### Décision
1. **Ce qu'on mesure : la fidélité à la source**, pas l'exactitude dans le monde réel. Un élément vrai mais absent de l'article source compte comme une hallucination. Le grain de mesure est l'**élément critique** : personne, organisation, lieu, nombre, date ou citation. Ces éléments sont relevés dans le résumé et dans l'article, sans découper les phrases d'opinion ou de style. Deux métriques :
   - le **taux d'éléments non supportés**, par type et au total ;
   - le **taux de sorties contenant au moins un élément non supporté**.
2. **Échantillon : 16 articles + 4 doublons.**
   - Les 16 articles sont les plus récents des flux RSS de TSA Algérie et d'El Watan, 8 par média.
   - Les 4 doublons pointent vers le même article TSA via une URL opaque (`?p=ID`), ce qui isole l'effet du slug sans rien ajouter à étiqueter.
3. **Copie de chaque page** : le HTML brut, le texte extrait par trafilatura puis **relu à la main**, la date de récupération et une empreinte SHA-256. La référence pour l'étiquetage est le texte relu. Ces copies restent hors de Git (droit d'auteur) ; le dépôt ne garde que les métadonnées et les empreintes.
4. **Exécution.**
   - Les prompts de la v1 sont copiés à l'identique (`v1-frozen`), avec le modèle et les paramètres par défaut de la v1 (`gemini-3.1-flash-lite-preview`). Si ce modèle a été retiré, on prend le successeur le plus proche et on l'indique dans le rapport.
   - 3 passages pour le résumé, 1 pour l'article.
   - Trois conditions comparées :
     - `url_only` : la v1 telle quelle ;
     - `page_text` : les prompts de la v1 avec le texte réel de la page ;
     - `v2` : plus tard.

     Les doublons opaques ne passent que par `url_only`, puisque leur texte est identique à celui de l'article d'origine.
5. **Étiquetage en couches.**
   1. Un LLM extrait les éléments critiques, en sortie structurée.
   2. Le code cherche chaque élément dans la source : correspondance exacte après normalisation pour les nombres, les dates et les citations ; correspondance approchée par fenêtres de mots (rapidfuzz, seuil réglable) pour les noms.
   3. L'humain étiquette tout ce que le code n'a pas trouvé, plus un échantillon de 20 % de ce qu'il a trouvé, afin de mesurer la précision du code. Les étiquettes possibles sont `SUPPORTED`, `CONTRADICTED` et `NOT_IN_SOURCE`.
   4. Un LLM-juge pourra tourner en parallèle sur les mêmes éléments. Ses verdicts sont stockés à part pour calibrer le juge en semaine 5, et ne comptent jamais dans le résultat.
6. **Présentation du résultat** : chaque taux est donné avec un intervalle de confiance à 95 % obtenu par bootstrap en tirant les articles (pas les éléments), car les éléments d'un même article ne sont pas indépendants.

### Alternatives écartées
- **Une note binaire par sortie, et rien d'autre.** Elle sature autour de 100 % pour la v1, et passer au grain de l'élément plus tard obligerait à tout réétiqueter.
- **Un échantillon « au hasard » sans liste de départ définie.** C'est en réalité un échantillon de convenance, donc biaisé.
- **Le LLM-juge comme seule vérité.** Il n'est pas calibré : on ne pourrait pas défendre ses chiffres.
- **Une correspondance exacte partout.** Toutes les variantes de translittération partiraient en revue humaine.

### Conséquences et limites
- **Langue :** l'échantillon est entièrement en français, faute de source arabophone. L'arabe sera évalué plus tard.
- **Taille :** 20 URL donnent des intervalles larges. On le montre plutôt que de le cacher.
- **Extraction par LLM :** l'extracteur peut manquer des éléments, et nos chiffres ne voient pas ces manques. L'humain contrôle donc la liste extraite pendant l'étiquetage.
- **Faux positifs du code :** un élément peut être « trouvé » dans la source alors qu'il y figure dans un autre contexte. L'échantillon de 20 % mesure ce biais.
- **Disponibilité d'El Watan :** `elwatan.dz` coupait les connexions (reset TCP) le 27/10/2026. Si cela persiste, on le remplace par une autre source francophone, avec un nouvel ADR.

---

## ADR-002 — Algérie 360 remplace El Watan dans l'échantillon de la baseline

**Date :** 2026-10-27 · **Statut :** accepté · **Modifie :** ADR-001 (point 2)

### Contexte
Le 27/10/2026, `elwatan.dz` réinitialise toutes les connexions, que ce soit avec curl, urllib ou httpx, sur `/feed/` comme sur `/rss`. On ne peut donc pas en tirer d'échantillon. On a testé des remplaçants francophones :
- **Algérie 360** : 25 articles, généraliste, liens courts `?p=ID` ;
- **Algérie Eco** : 10 articles, économie uniquement ;
- **L'Expression, Le Soir d'Algérie, El Moudjahid** : erreur 403.

### Décision
Les 8 articles d'El Watan sont remplacés par les 8 articles les plus récents d'**Algérie 360**. Les doublons opaques restent tirés de TSA.

### Alternatives écartées
- **Algérie Eco** : sa ligne thématique est trop étroite, l'échantillon serait moins représentatif.
- **Contourner le blocage d'El Watan** (en se faisant passer pour un navigateur, en passant par un proxy) : contraire à l'intention du site, et fragile.

### Conséquences
- Algérie 360 publie beaucoup de brèves et d'articles repris d'autres médias. Les textes seront plus courts que ceux d'El Watan, donc il y aura moins d'éléments critiques par article.
- L'échantillon perd un grand quotidien de référence. À reconsidérer si El Watan redevient accessible avant l'étiquetage.
- Algérie 360 insère dans le corps de ses articles des renvois « À LIRE AUSSI » vers d'autres articles : 20 lignes sur les 8 articles. Ces lignes sont retirées par `clean_text` (couvert par un test), en repartant du HTML stocké. Sinon, des faits étrangers à l'article seraient comptés comme « trouvés » dans la référence.

---

## ADR-003 — Relecture des références automatisée, titres inclus, articles payants exclus

**Date :** 2026-10-27 · **Statut :** accepté · **Modifie :** ADR-001 (points 2 et 3)

### Contexte
L'ADR-001 prévoyait qu'Abdou relise à la main les 16 textes de référence. Abdou a choisi de déléguer cette relecture. Claude l'a remplacée par une vérification systématique : pour chaque article, on compare l'extraction précise avec une extraction plus permissive (`favor_recall`) pour repérer les paragraphes perdus, et on cherche du texte parasite (abonnement, cookies, « lire la suite »). Cette vérification a trouvé deux défauts :
1. `tsa-07` était un article **payant**, tronqué par l'appel « Abonnez-vous… pour lire la suite ». Sa référence était donc incomplète.
2. **Aucune référence ne contenait le titre**, qui porte pourtant des faits (noms, chiffres). C'est même ce que la v1 devine à partir du slug de l'URL.

### Décision
- Les articles payants sont exclus : `extract_main_text` lève `PaywallError` quand le HTML contient un marqueur de paywall. `tsa-07` est remplacé par l'entrée suivante du flux au moment de l'échantillonnage, `tsa-08` (`?p=304027`).
- Le titre RSS est placé en tête de chaque texte de référence.
- Les 16 textes sont marqués `reviewed` après la vérification automatique, qui ne signale plus aucun défaut.

### Conséquences et limites
- **Personne n'a relu les textes.** Une erreur d'extraction plus subtile reste possible, par exemple un paragraphe absent des deux modes d'extraction. On compensera pendant l'étiquetage : chaque élément `NOT_IN_SOURCE` est vérifié sur le site lui-même avant d'être confirmé.
- `tsa-07` n'était pas l'original d'un doublon opaque, donc les 4 doublons ne changent pas.
- Les articles payants biaisent l'échantillon vers les contenus gratuits. C'est acceptable : le bot ne pourra de toute façon pas lire les articles payants.

---

## ADR-004 — Socle d'exécution : webhook Telegram, file Postgres, worker et déploiement

**Date :** 2026-10-27 · **Statut :** accepté (sauf le nom d'hôte HTTPS, à confirmer)

### Contexte
Livrable de la semaine 1 (cahier des charges v2.1, §7) : un webhook Telegram en FastAPI, Postgres et une file de travail, déployés sur une VM. Le cahier fixe déjà :
- la file : une table Postgres avec `SELECT … FOR UPDATE SKIP LOCKED`, sans Redis (§3.1, point 5) ;
- les services `api`, `worker` et `postgres+pgvector` dans docker compose, sur une VM et pas sur Cloud Run (§2.4, §3.2) ;
- une abstraction `Channel`, dont Telegram n'est qu'un adaptateur ;
- une liste blanche d'identifiants Telegram (§3.7).

Restaient à trancher : le driver Postgres et les migrations, les garanties de la file, la VM et le HTTPS.

### Décision
1. **Deux processus, une seule image.**
   - `api` (FastAPI) ne fait qu'acquitter et mettre en file.
   - `worker` exécute les jobs.
   - Ils tournent dans compose avec `postgres` (`pgvector/pgvector:pg16`) et, en production, `caddy`.
2. **Webhook `POST /webhooks/telegram`.**
   - Il vérifie l'en-tête `X-Telegram-Bot-Api-Secret-Token` par une comparaison à temps constant.
   - Il valide un `Update` minimal et applique la liste blanche (utilisateurs et groupe d'équipe).
   - Il insère un job avec la clé unique `telegram:<update_id>` (`ON CONFLICT DO NOTHING`), puis répond 200. Il ne fait rien d'autre.
   - Les updates refusés reçoivent aussi un 200, pour que Telegram ne les renvoie pas, et ils sont journalisés.
3. **File maison, livraison « au moins une fois ».**
   - **Table `jobs`** : `kind`, `payload jsonb`, `status`, `attempts`, `run_after`, `locked_until`, `dedup_key` unique.
   - **Prise d'un job** : `FOR UPDATE SKIP LOCKED`, qui le passe à `running` et pose un bail (`locked_until`). Le worker prolonge le bail pendant les jobs longs. Un job dont le bail a expiré redevient éligible, ce qui couvre le cas d'un worker mort.
   - **Échec** : nouvel essai avec un backoff exponentiel via `run_after`. Après `max_attempts`, le job passe au statut `dead` pour être inspecté.
   - **Réveil** : `LISTEN/NOTIFY` sur le canal `jobs`, plus un polling de secours toutes les quelques secondes, au cas où une notification se perdrait.
4. **psycopg 3 async, en SQL brut.**
   - Requêtes dans `db/repository.py`, lignes mappées en modèles Pydantic dans `db/models.py`.
   - Migrations : des fichiers SQL numérotés dans `db/migrations/`. Un petit runner les applique, chacun dans une transaction, et les enregistre dans `schema_migrations`. Il tourne au déploiement, avant `api` et `worker`.
   - Ce choix s'écarte du brouillon du cahier, qui prévoyait asyncpg. psycopg 3 est mieux typé pour mypy strict, gère nativement `LISTEN/NOTIFY` en async (`conn.notifies()`) et c'est le driver de référence. L'écart de performance ne compte pas à notre volume.
5. **Client Telegram écrit directement en `httpx`**, dans `channels/telegram.py`, derrière le protocole `Channel` de `channels/base.py`.
   - On n'écrit que les méthodes nécessaires (`setWebhook`, `sendMessage`…), avec un timeout, des réessais bornés et une journalisation de chaque appel.
   - On respecte le `retry_after` que Telegram renvoie avec ses 429.
   - On n'applique jamais `parse_mode` Markdown à une sortie LLM (bug n°7 de la v1). On envoie du texte brut, ou du HTML échappé par le code.
6. **Hébergement sur Oracle Cloud, Ampere A1 (ARM64, Always Free).**
   - Les images de base (`python:3.12-slim`, `pgvector/pgvector:pg16`) existent en arm64.
   - Si le compte Oracle est refusé, on se replie sur une GCP e2-micro (1 Go de RAM, avec du swap).
7. **HTTPS : Caddy, qui gère Let's Encrypt automatiquement.**
   - Nom d'hôte cible : un sous-domaine de dzdia.com (par exemple `bot.dzdia.com`), à demander pendant l'échange de découverte.
   - En attendant, un sous-domaine gratuit DuckDNS.
   - Le domaine `*.onrender.com` déjà utilisé par Abdou ne sert pas : il ne vaut que pour les services hébergés chez Render.

### Alternatives écartées
- **Redis + Celery ou RQ** : un service de plus pour un débit de quelques messages par heure.
- **procrastinate ou pgqueuer** : ces bibliothèques reposent sur les mêmes mécanismes Postgres. Le code maison tient en environ 150 lignes, se contrôle entièrement et constitue une matière d'entretien.
- **Garder la transaction ouverte pendant tout le job**, sans bail : cela bloque une connexion pendant des appels LLM de plusieurs minutes.
- **Polling seul** : il est conservé, mais seulement en secours de `LISTEN/NOTIFY`.
- **asyncpg** : son typage passe par des stubs tiers.
- **SQLAlchemy 2 avec Alembic** : trop de concepts pour quelques tables, et pgvector et `tsvector` demandent quand même du SQL.
- **aiogram ou python-telegram-bot** : ces frameworks gèrent eux-mêmes le cycle de vie et les handlers, en concurrence avec notre file et notre boucle d'agent.
- **Long polling (`getUpdates`)** : il se passerait de HTTPS, mais un processus devrait tourner en permanence pour lire les messages, et le cahier retient le webhook.
- **Un worker en tâche asyncio dans l'API** : un job bloquant ou un crash retarderait les acquittements.
- **Un certificat auto-signé transmis à `setWebhook`** : renouvellement manuel, et une solution peu standard.

### Conséquences
- **Livraison « au moins une fois » : un job peut être exécuté deux fois.** Les handlers doivent être idempotents, ou enregistrer leurs effets. Exemple : l'outil WordPress note le brouillon créé avant de rendre la main. Un message Telegram envoyé en double est toléré en semaine 1.
- **La table `jobs` grossit avec le temps** : il faudra purger les jobs terminés au-delà de N jours (plus tard).
- **Périmètre de la semaine 1** : un seul type de job, qui répond dans Telegram pour prouver la chaîne webhook → file → worker → réponse. Les vrais flux arrivent en semaine 2.
- **Ce qu'il faut à Abdou** : WSL et Docker sur son poste, un compte Oracle Cloud, un bot créé avec BotFather. Il faut aussi, dans `.env`, les variables `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET` et les identifiants de la liste blanche.
- **À ajouter aux questions de découverte (§9)** : obtenir un sous-domaine de dzdia.com et l'identifiant du groupe Telegram de l'équipe.

---

## ADR-005 — Entrée Telegram : message normalisé, liste blanche, enregistrement du webhook et logs JSON

**Date :** 2026-10-27 · **Statut :** accepté

### Contexte
L'ADR-004 fixe la chaîne webhook → file → worker → réponse, mais pas quatre points nécessaires pour écrire `api/` et `channels/` :
- ce que l'API met dans le `payload` du job ;
- où vit la liste blanche, et quelle règle elle applique dans le groupe d'équipe ;
- qui appelle `setWebhook` ;
- le format des logs.

### Décision
1. **Le job porte un message normalisé, avec l'`Update` brut en annexe.**
   - L'API convertit l'`Update` en `InboundMessage` (dans `channels/base.py`) : `channel`, `chat_id`, `user_id`, `message_id`, `text`, `sent_at`, plus un champ `raw` qui garde l'`Update` complet.
   - Les identifiants sont des chaînes, pour ne pas supposer un format propre à Telegram.
   - Le worker ne lit que les champs normalisés. `raw` sert au débogage et au rejeu.
   - En semaine 1, seuls les messages texte (`message.text`) sont acceptés. Les autres updates (messages modifiés, photos sans légende, messages de canal sans auteur…) reçoivent un 200 et sont journalisés comme ignorés.
2. **Liste blanche dans les variables d'environnement, et l'auteur doit y figurer partout.**
   - Variables : `TELEGRAM_ALLOWED_USER_IDS` (liste d'identifiants) et `TELEGRAM_TEAM_CHAT_ID`.
   - Un message est accepté si son auteur (`from.id`) est dans la liste **et** s'il arrive soit en privé, soit dans le groupe d'équipe. Un journaliste listé qui écrit depuis un autre groupe est refusé.
   - La règle échoue fermé : une liste vide refuse tout le monde.
   - Les refus reçoivent un 200 (ADR-004) et sont journalisés avec l'identifiant de l'utilisateur et du chat, **sans le texte du message**.
3. **`setWebhook` passe par une commande CLI lancée au déploiement**, sur le modèle de `db.migrate` : `python -m dzdia_newsroom.channels.telegram set-webhook`.
   - Elle transmet l'URL publique, le `secret_token` et `allowed_updates=["message"]`. L'option `--drop-pending` vide les updates en attente.
   - L'appel est idempotent : on peut le relancer à chaque déploiement.
   - L'API ne connaît donc que le secret du webhook, jamais le token du bot. Seuls le worker et la CLI en ont besoin (moindre privilège).
4. **Logs JSON, une ligne par événement, avec la stdlib seule.**
   - Un module `logging_setup.py` fournit un `JsonFormatter`. `api` et `worker` l'installent au démarrage, et les loggers d'uvicorn passent par lui.
   - Champs fixes : `ts`, `level`, `logger`, `msg`. S'y ajoutent les champs passés en `extra` (`job_id`, `update_id`, `latency_ms`…), qui deviennent filtrables dans `docker logs`.
   - Jamais de secret ni de texte de message dans les logs.
   - `LOG_FORMAT=text` garde une sortie lisible en développement.

### Alternatives écartées
- **Payload brut seul** : le worker dépendrait du format Telegram, ce qui casse l'abstraction `Channel`.
- **Payload normalisé seul** : on perdrait les champs non prévus, utiles pour déboguer ou rejouer.
- **Liste blanche en table Postgres** : il faudrait une migration et un moyen d'administrer la table, pour environ 5 journalistes et une liste qui change rarement.
- **Accepter tout membre du groupe d'équipe** : une personne ajoutée au groupe par erreur aurait accès aux outils.
- **`setWebhook` au démarrage de l'API** : l'appel serait refait à chaque redémarrage, et l'API devrait connaître le token du bot et son URL publique.
- **structlog** : une dépendance de plus, alors qu'un formatter d'une trentaine de lignes suffit.

### Conséquences
- Nouvelles dépendances : `fastapi` et `uvicorn`.
- Ajouter un journaliste revient à modifier `.env` puis à redémarrer `api`.
- Les réglages Telegram sont séparés : l'API reçoit le secret et la liste blanche ; le worker et la CLI reçoivent le token du bot.
- Les messages non textuels (photos, documents, notes vocales) sont ignorés jusqu'à ce qu'un flux en ait besoin (semaine 2 ou plus tard).
- À l'avenir, un second canal (WhatsApp, interface web) n'aura qu'à produire un `InboundMessage`.
