# Boîte à idées — aci-mcp

> Idées en vrac, moins mûres/concrètes que `todo.md` (qui liste des tâches
> d'ingénierie précises). Ici : des pistes à ne pas perdre, pas encore
> décidées, pas encore scoping-ées. À faire remonter dans `todo.md` ou un
> fichier dédié le jour où une idée devient un vrai projet.

## Paper — évaluation empirique de l'usage du MCP par des agents

Voir le fichier dédié [`paper-mcp-agent-usage.md`](./paper-mcp-agent-usage.md)
— assez développé pour mériter son propre document plutôt qu'une ligne ici.

## opencode + modèle local comme deuxième piste de test formelle

Aujourd'hui (20/07) on a testé en direct, à la main, en copiant-collant les
réponses d'opencode + Qwen3.6 35B MoE dans la conversation. Idée : si
opencode a un mode headless/scriptable (comme notre `claude -p`), on pourrait
rejouer automatiquement le même banc de 56 tâches du harnais privé dessus,
avec la même grille de notation code+juge — ça donnerait une vraie deuxième
piste statistiquement comparable, pas seulement un test exploratoire manuel.
À vérifier avant de s'engager dessus. Lié à RQ1/RQ3 du paper.

## Mitigation logicielle de la réutilisation de contexte périmé

Discuté suite aux échecs Q5/Q-C/Q-D du test croisé (le modèle réutilise un
résultat d'une question antérieure au lieu de rappeler l'outil). Deux pistes
possibles, aucune n'annule le problème, juste des compromis :
- Isolation de contexte par sous-tâche (ce qu'opencode essaie déjà de faire
  avec son "Explore Task" — n'a pourtant pas empêché la confusion à Q3, donc
  pas suffisant seul).
- Marquer explicitement certaines données comme "ne jamais réutiliser d'une
  question à l'autre, toujours rappeler l'outil" — au niveau prompt/skill,
  pas au niveau protocole.
Pas une tâche à faire maintenant — plutôt un point à documenter dans la
section discussion du paper, avec le compromis coût/latence explicite.

## Sélection d'outils par recherche sémantique (RAG-style), en alternative/complément à search_classes

Idée déclenchée par la recherche en cours (agent lancé le 20/07) sur comment
d'autres gèrent des API à 15k+ ressources — certains patterns utilisent une
recherche sémantique/embedding sur un registre d'outils plutôt qu'un mot-clé
simple. À creuser une fois le rapport de l'agent revenu : est-ce que
`search_classes` gagnerait à s'inspirer de ce pattern, ou est-ce que notre
approche (recherche lexicale + priors structurels) reste suffisante vu les
bons chiffres de recall déjà mesurés (78,4%) ?
