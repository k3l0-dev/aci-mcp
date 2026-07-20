# Backlog — aci-mcp

> Notes de suivi personnelles, hors CHANGELOG (pas encore des décisions
> engagées, juste des pistes à ne pas perdre). Committé localement malgré le
> `.gitignore` sur `.claude/` (force-add), jamais destiné au repo public tel
> quel — à faire remonter dans CHANGELOG/issues GitHub le jour où un item est
> réellement traité.

## [FAIT — v1.2.0, commit 9be999b] SKILL.md — remplacer les recettes jq par des recettes python -c

`mcp/client/SKILL.md` contient plusieurs recettes de traitement des réponses
JSON des tools écrites en `jq` (ex. les recettes `group_by` pour l'agrégation,
section comptage/pagination). À remplacer par des équivalents `python -c` :
plus lisible pour l'agent, plus facile à faire varier/déboguer sur un
résultat inattendu (structure imbriquée, `results` vs top-level list une fois
le fix pagination/`fetch_all` en place), et évite une dépendance à la
syntaxe jq que l'agent maîtrise visiblement moins bien en pratique.

À faire au moment de retravailler SKILL.md (par ex. en même temps que la mise
à jour de la section pagination pour `fetch_all`/`truncated` — voir la
branche `fix/query-truncation-fetch-all` en cours) : recenser chaque recette
jq du fichier, la reformuler en `python -c` équivalent, vérifier qu'elle reste
correcte avec la nouvelle enveloppe `query()`.

## SKILL.md — 4e contre-exemple de grounding : résumé de liste en clusters/tableau

Découvert en live-testant un modèle local (opencode + Qwen3.6 35B) sur le MCP,
question faults critical/major : le modèle a produit un résumé globalement
correct (22 faults, 1 critical/21 major) mais avec 2 codes de fault entièrement
fabriqués (F1318 "PSU non détecté", F1480 "échec upgrade firmware" sur
node-101) qui n'existent nulle part dans les données réelles — vérifié deux
fois en direct sur le serveur live. Comptages des vrais codes aussi faux
(F609026, F2247, F3951 tous sous-comptés). Erreur de catégorisation en plus :
des faults fabric/infra-scoped (`uni/vmmp-VMware/...`, `configpush/...`)
attribuées à de faux "tenants" ("niwaki-it", "configpush") dans son tableau
récap.

La règle de grounding (section 11 du SKILL.md actuel) a déjà 3 contre-exemples
(`contains`/`relationTo`, `properties`/`property_details`, étymologie de nom
de classe). Ajouter un 4e, ciblé sur ce pattern précis : au moment de résumer
une liste retournée en clusters/tableau/groupes, chaque code/compte/objet cité
doit être traçable à un item réellement présent dans le résultat du tool —
ne jamais ajouter d'éléments plausibles pour "compléter" un tableau qui
semblerait sinon incomplet.

Root cause probable (visible dans la transcription du modèle testé) : passage
par un sous-agent/sous-tâche qui a d'abord confondu une réponse `get_schema`
avec des données réelles de `query`, puis a re-exécuté la requête — mais la
confusion initiale semble avoir contaminé la synthèse finale malgré tout.
Chaque saut de résumé/narration (plutôt qu'un calcul direct en code sur les
données structurées) est un facteur de risque.

À faire en même temps : ajouter ce cas précis (22 faults, breakdown exact
vérifié : F0104×1, F0103×1, F609026×7, F2247×11, F3951×2) comme test de
non-régression dans le banc d'éval privé — vérité terrain déjà confirmée
deux fois en direct, ne pas la reperdre.
