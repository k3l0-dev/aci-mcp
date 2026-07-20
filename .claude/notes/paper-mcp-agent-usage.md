# Paper — évaluation empirique de l'usage du MCP aci-mcp par des agents

> Idée à développer prochainement. Notes de travail, pas encore un plan arrêté.
> Public visé : arXiv et/ou blog technique — le sujet porte sur l'usage du MCP
> par des agents, PAS sur la méthodologie de construction du MCP elle-même,
> ce qui désamorce la contrainte de confidentialité ("notre secret de
> fabrication") établie par ailleurs pour ce dépôt.

## Thèse centrale

Face à un système avec une surface d'API très riche (ici : le MIT Cisco ACI,
15k+ classes d'objets), ne pas anticiper et coder en dur un outil par tâche
prévisible — non scalable, explosion du nombre d'outils, noie l'agent. À la
place : exposer une poignée d'outils génériques qui miment les primitives du
système lui-même (découverte par mot-clé, introspection de schéma à la
demande, requête/filtre/comptage scoping), plus un document de skill en
langage naturel qui porte la connaissance de navigation du domaine que les
outils génériques seuls ne peuvent pas auto-enseigner (comment les DN/le
containment fonctionnent, comment traverser les objets de relation Rs/Rt,
quel outil utiliser quand). Validé empiriquement : deux agents LLM
indépendants (Claude via un harnais structuré, et un modèle local ouvert —
Qwen3.6 35B MoE — via opencode) construisent tous deux une compréhension
correcte d'une fabric ACI jamais vue, via une boucle découverte→schéma→
requête→synthèse, sans prolifération d'outils par tâche.

## Questions de recherche

- **RQ1** : un design "outils génériques + skill embarqué" permet-il un usage
  correct par des agents hétérogènes (modèle frontier propriétaire vs modèle
  local ouvert plus petit) ?
- **RQ2** : quelle taxonomie d'échecs émerge côté agent, même quand les
  données/outils sous-jacents du MCP sont vérifiés justes à chaque fois ?
- **RQ3** (issue du test croisé du 20/07) : la fidélité d'usage d'outils se
  dégrade-t-elle avec la longueur de la conversation/du contexte accumulé, et
  une consigne explicite "revérifie toujours, ne réutilise jamais un résultat
  d'une question antérieure" atténue-t-elle ça ?

## Méthodologie envisagée

Deux pistes complémentaires, pas redondantes :
1. **Harnais automatisé structuré** (déjà construit, privé/interne) — banc de
   56 tâches par palier de complexité, notation code+juge isolé, répétitions
   pour distinguer bruit et régression, sur un modèle frontier (Claude Haiku).
2. **Validation croisée exploratoire** — un modèle local indépendant
   (Qwen3.6 35B MoE via opencode + plugin oh-my-opencode) sur le même serveur
   MCP, avec vérité terrain revérifiée manuellement en direct à chaque
   désaccord constaté (jamais un résultat accepté sans recontre-vérification
   contre le serveur live).

## Preuves déjà en main (session du 2026-07-20)

- Recall@1 de `search_classes` : 30,8% → 78,4% (mesuré, 74 requêtes golden).
- Test croisé Claude vs Qwen3.6 35B (9 questions, dont 5 factuelles simples +
  4 questions de négation/différence d'ensembles) :
  - Q1, Q2 : match exact entre les deux modèles, vérifié indépendamment.
  - Q3 : codes de fault fabriqués (F1318, F1480) — première apparition.
  - Q4 : non-conclusion, sur-exploration sur une question pourtant répondable
    par le seul schéma (pas besoin de vérifier l'état réel).
  - Q5 : conclusion juste, décompte faux — réutilisation d'un total de
    tenants d'une requête antérieure au lieu d'un `fetch_all` frais.
  - Q-A : bug de clé d'agrégation non scopée par tenant (noms de VRF
    partagés entre tenants fusionnés à tort).
  - Q-B : chiffres exacts, mais pas de contre-vérification d'un résultat "0
    partout" pourtant suspect (aurait révélé des contrats existants ailleurs
    dans la fabric, hors périmètre `fvAEPg`).
  - Q-C : même bug que Q-A, littéralement recopié du script précédent —
    l'erreur finale reste proche du vrai chiffre par coïncidence (deux biais
    qui s'annulent presque), point méthodologique intéressant en soi.
  - Q-D : **conclusion inversée** — réutilisation des codes fabriqués de Q3,
    deux questions plus tard, sans revérification. La vraie réponse (le seul
    leaf de la fabric n'a AUCUNE fault, toutes sévérités confondues) est
    l'exact opposé de ce que le modèle a conclu.
  - Pattern dominant sur les 9 questions : pas "le modèle ne sait pas utiliser
    le MCP" — plutôt "le modèle ne revérifie pas assez souvent, préfère
    réutiliser un résultat déjà en contexte." Argument central pour RQ3.

## À faire avant d'écrire quoi que ce soit

- [ ] Recherche en cours (agent lancé le 20/07) : comparaison avec comment
  d'autres construisent des MCP pour des systèmes à API très riche
  (Kubernetes, cloud providers, ServiceNow, pattern "code execution with
  MCP" d'Anthropic, littérature sur la sélection d'outils à grande échelle) —
  pour la section travaux connexes et pour situer honnêtement ce qui est
  vraiment nouveau dans notre combinaison vs. déjà établi ailleurs.
- [ ] Décider du canal (arXiv, blog technique, ou les deux — pas exclusif).
  arXiv : barre de qualité plus basse qu'on ne croit (pas de peer review),
  mais friction pratique d'endorsement en tant que non-académicien à vérifier.
- [ ] Vérifier si opencode a un mode headless/scriptable pour rejouer le
  même banc de 56 tâches automatiquement (sinon la piste opencode reste
  qualitative/exploratoire, pas statistiquement comparable au harnais Claude).
- [ ] Poser la thèse centrale par écrit (2-3 paragraphes) avant de construire
  le squelette complet du papier.
