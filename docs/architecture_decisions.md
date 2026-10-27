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
