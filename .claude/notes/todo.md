# Backlog — aci-mcp

> Notes de suivi personnelles, hors CHANGELOG (pas encore des décisions
> engagées, juste des pistes à ne pas perdre). Committé localement malgré le
> `.gitignore` sur `.claude/` (force-add), jamais destiné au repo public tel
> quel — à faire remonter dans CHANGELOG/issues GitHub le jour où un item est
> réellement traité.

## SKILL.md — remplacer les recettes jq par des recettes python -c

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
