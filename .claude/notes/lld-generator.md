# Générateur de LLD (Low Level Design) professionnel via Typst

> Brainstorm en cours (21/07), pas encore un plan arrêté. Premier cas
> d'usage concret à mettre en place autour du MCP, choisi par le
> mainteneur parmi plusieurs pistes brainstormées le même jour.

## Objectif

Générer une documentation ACI complète et un LLD professionnel, rendu dans
le template Typst existant du mainteneur, à partir de l'état réel d'une
fabric — plutôt qu'un document maintenu à la main qui périme toujours.

## Architecture centrale : ETL déterministe + templating, pas de LLM dans la boucle factuelle

Pour un livrable professionnel remis à un client, on ne peut pas se
permettre un agent qui "raisonne et rédige" à la volée — c'est exactement
le mode où on a trouvé la fabrication lors du test croisé du 20/07 (codes
de fault inventés, décomptes faux). Deux couches nettement séparées :

1. **Extraction** — du code déterministe, pas un agent conversationnel.
   **En deux phases**, pour éviter une liste de classes figée à l'avance
   qui raterait des implémentations propres à l'environnement d'un client
   (point du mainteneur, confirmé comme principe directeur) :
   - *Découverte* : balayer `class-descriptions.json` (flag `isConfigurable`)
     + `count()` sur chaque classe candidate pour déterminer quels types
     d'objets sont réellement peuplés dans CETTE fabric précise.
   - *Extraction* : pour chaque classe découverte comme réellement en
     usage, extraction complète (propriétés, relations, containment).
2. **Rendu** — le modèle de données intermédiaire structuré (qui mirror la
   structure logique ACI, pas la structure du document final) alimente le
   template Typst existant (Typst lit nativement JSON/YAML, système de
   fonctions/templating natif). Style/mise en page/sommaire viennent du
   template, rien à générer par un LLM.

Modèle de données intermédiaire réutilisable pour 3 applications, pas
juste celle-ci : détection de dérive (diff de deux snapshots), doc/
topologie toujours à jour, et ce générateur de LLD.

## Narration — le vrai défi, en 3 paliers de risque

- **Palier 1 (risque nul)** : tableaux de configuration bruts, chaque
  valeur tracée à un appel d'outil.
- **Palier 2 (risque bas, faisable maintenant)** : bibliothèque de
  patterns curée à la main (comme la table de synonymes de
  `search_classes`) — correspondance figée pattern-de-config→prose
  d'interprétation, pas de génération LLM à l'exécution.
- **Palier 3 (le vrai défi)** : constructions ambiguës/non-évidentes sans
  équivalent dans une taxonomie connue — nécessite soit une bibliothèque
  de patterns plus riche, soit du RAG documentaire Cisco (déjà dans la
  boîte à idées), avec flag explicite "déduit, pas certain" + relecture
  humaine obligatoire avant tout envoi client.

### Découverte importante (21/07, vérifiée en direct) : `faultInst` porte déjà une taxonomie structurée par Cisco

Pas seulement `descr` (texte libre) — de vrais champs catégoriels en
lecture seule, définis par Cisco :

```
code: F0104
cause: port-down          ← enum fermé, centaines de valeurs possibles
title: (souvent vide)
rule: cnw-aggr-if-down    ← identifiant de règle interne, catégoriel
descr: "Bond Interface po1.1 on node 1... is now Down"
type: operational
subject: equipment
```

Parfois `descr` contient carrément la remédiation en clair (ex. fault
licensing : *"Navigate to System -> Smart Licensing window to configure
network settings."*).

Conséquence : pour tout ce qui est fault-related, le palier 2 (mapping
figé) peut être largement bootstrappé depuis la taxonomie `cause`/`rule`/
`type`/`subject` déjà curée par Cisco — pas besoin d'interpréter depuis
rien, juste établir une correspondance depuis un ensemble fini et connu de
codes vers une prose professionnelle. Le RAG documentaire (palier 3)
garde sa valeur mais son périmètre réel se réduit aux constructions SANS
fault associée (config BD/EPG/contrat inhabituelle mais valide, sans
taxonomie Cisco toute faite à exploiter).

## Investigation SSH sur l'APIC (21/07) — inventaire des outils CLI natifs

Objectif : identifier d'autres sources de données utiles au-delà de l'API
REST/MCP actuel, en particulier des outils souvent sous-exploités par les
équipes opérationnelles. Investigation en lecture seule (`--help` partout,
rien exécuté qui modifie l'état). Shell de login APIC = shell restreint
(`/mgmt/usr/bin/loginshell`), pas un bash standard — les alias legacy
(`moquery`, `icurl`, `acidiag`...) fonctionnent, mais les scripts shell
complexes (boucles combinées à des invocations de binaires par chemin
complet, `timeout` empaqueté) sont rejetés par son parseur ; invoquer les
outils par leur nom d'alias direct fonctionne.

**Accès au modèle objet** (`moquery`, `mobrowser`, `icurl`) — recoupe ce
que le MCP fait déjà via l'API REST ; `moconfig`/`mocreate`/`modelete`/
`moset`/`moprint`/`mostats` existent mais sont **dépréciés** (à ne pas
utiliser pour du neuf).

**Faults/santé/diagnostics** (le plus pertinent pour le générateur de
LLD/triage) : `faults`, `health`, `eventlog`, `auditlog`, `diagnostics`,
`deployment`, `debug` (conflits d'encapsulation), `troubleshoot`
(framework endpoint-à-endpoint). **`ftriage`** en particulier — triage de
flux automatisé, prend des champs de paquet en entrée et trace le chemin
réel à travers la fabric (`bridge`, `route`, `gwping`, `gwarp`, `arp`) —
exactement le genre d'outil sous-exploité mentionné, candidat sérieux pour
enrichir le palier 2/3 de la narration avec de la vraie donnée de trace.

**Fabric/nodes** : `attach <node>` — SSH direct vers un leaf/spine, point
d'entrée normal pour `vsh`/`vsh_lc`/ELAM *sur le switch*, pas sur l'APIC.
Sonde tentée sur node-101, pas encore concluante sur ce simulateur (échec
de syntaxe shell restreint, pas un refus d'accès) — **à refaire
proprement avant de conclure si ELAM est exploitable dans ce lab**.
`trafficmap` (carte de trafic entre deux nodes/vPC) et `switchport`
(activation/localisation physique) aussi disponibles.

**Système/config** : `showconfig [xml|json]` — dump de toute la config
système en un coup, à comparer avec notre extraction MCP phase-par-phase
(pourrait être un raccourci ou une validation croisée intéressante).
`techsupport` pour bundle de support complet.

**Interne cluster** (sbin) : `cluster_health`, `avread`/`rvread`/
`fnvread` — lecture des bases internes de cluster APIC, pas vraiment
exploitable pour le LLD, pour mémoire seulement.

**Prochaine étape si on pousse cette piste** : refaire la sonde `attach
101` proprement (hors du shell restreint, ex. `ssh -t` avec un vrai pty)
pour vérifier si `vsh`/ELAM sont réellement exploitables sur ce simulateur
avant d'investir dessus.

## Décisions déjà prises

- Périmètre "complet" = découverte empirique de ce qui est réellement
  configuré, pas une liste figée à l'avance.
- Instantané T d'abord, versionné/diff plus tard (pas maintenant).
- On brainstorm avant de fixer une architecture définitive — rien
  d'implémenté pour l'instant.
