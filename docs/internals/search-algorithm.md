# Algorithme de recherche — search_classes

Ce document décrit en détail le problème posé par la recherche de classes ACI, les deux axes d'amélioration implémentés, les mécanismes précis de chaque algorithme, et les gains mesurés. Il sert de référence pour toute évolution future de `registry/descriptions.py`.

---

## 1. Contexte : le problème de la recherche dans un corpus ACI

### Le corpus

Le modèle objet Cisco ACI compte **15 152 classes** documentées dans les fichiers jsonmeta fournis par l'APIC. Chaque classe représente un objet manageable — une politique, une relation, une configuration réseau, un objet de monitoring, un artefact interne. La grande majorité de ces classes est invisible pour un opérateur réseau : seules quelques centaines correspondent à des objets directement manipulables.

Le fichier `data/class-descriptions.json` indexe ces classes avec trois champs :

```json
{
  "fvBD": {
    "label":   "Bridge Domain",
    "comment": "A bridge domain is a unique layer 2 forwarding domain..."
  }
}
```

C'est sur cet index que `search_classes` opère.

### Ce que l'agent LLM demande

Quand un agent LLM appelle `search_classes`, il peut formuler sa requête de plusieurs manières :

| Type de requête | Exemple | Ce qui la rend difficile |
|---|---|---|
| Nom de classe approximatif | `"fvbd"`, `"vzbrcp"` | Pas de majuscules, pas de séparateurs |
| Label exact ou proche | `"bridge domain"`, `"tenant"` | Plusieurs classes partagent le même label |
| Concept fonctionnel | `"ARP flooding"`, `"dead interval"` | Absent du label et du commentaire de la classe |
| Synonyme pur | `"gateway"`, `"security policy"` | Aucun ancrage textuel dans l'APIC |

Un LLM entraîné sur de la documentation ACI gère souvent les deux premiers types intuitivement. C'est à partir des requêtes fonctionnelles et des synonymes que la recherche textuelle atteint ses limites.

---

## 2. L'approche naive (baseline)

### Fonctionnement

La fonction `search()` effectue un **scan linéaire** sur les 15 152 classes. Pour chaque classe, elle calcule un score par additivité de correspondances :

```
score = 0
si keyword ∈ nom_de_classe  (insensible à la casse)  → score += 3
si keyword ∈ label          (insensible à la casse)  → score += 2
si keyword ∈ comment        (insensible à la casse)  → score += 1
```

Les classes avec `score > 0` sont triées par score décroissant. En cas d'égalité, l'ordre d'insertion dans le JSON est conservé.

### Les poids choisis

Les poids 3/2/1 reflètent la confiance décroissante sur chaque champ :
- Le **nom de classe** est l'identifiant technique exact : si le keyword y apparaît, la correspondance est quasi certaine.
- Le **label** est le nom humain officiel donné par Cisco : forte valeur sémantique.
- Le **commentaire** est une description de quelques phrases : correspondance plus ambiguë (beaucoup de classes mentionnent "tenant", "VRF", "bridge domain" en passant).

### Mesures baseline

Évalué sur un golden set de **39 requêtes** réparties en 4 tiers de difficulté croissante (APIC mo-apic-v6.0_9c, 15 152 classes) :

| Métrique | Score |
|---|---|
| Recall@1 | 15.4% |
| Recall@5 | 35.9% |
| MRR | 0.229 |
| Tier 1 — label/nom direct | R@1 = 10%  /  R@5 = 50% |
| Tier 2 — nom camelCase | R@1 = 80%  /  R@5 = 80% |
| Tier 3 — prop fonctionnelle | R@1 = 0%   /  R@5 = 0% |
| Tier 4 — synonyme pur | R@1 = 0%   /  R@5 = 0% |
| Temps moyen de requête | 3.2 ms |

### Analyse des échecs

**Pourquoi le tier 1 échoue à 90% en Recall@1 ?**

Le problème n'est pas que la bonne classe est absente des résultats — elle est présente dans 50% des cas en top 5. Le problème est le classement. Exemple concret :

- Requête : `"bridge domain"`
- `fvBD` : label = `"Bridge Domain"` → `"bridge domain"` dans label → **score 2**
- `fvABDPol` : label = `"Bridge Domain"` → `"bridge domain"` dans label → **score 2**
- `eqptcapacityBDEntry` : label = `"Bridge Domain Entry"` → `"bridge domain"` dans label → **score 2**

Cisco utilise le même label humain pour la classe primaire et pour toutes les classes qui y sont liées (politiques, relations, variantes). Une dizaine de classes partagent le label `"Bridge Domain"`. Toutes obtiennent le même score 2. L'ordre d'insertion dans le JSON — arbitraire — décide du classement. `fvBD` peut finir rank 5 ou rank 8.

**Le problème des classes Rs/Rt**

Les classes de relation ACI suivent une convention de nommage précise :
- `fvRsCtx` : relation **R**e**s**olution **s**ource — de fvBD vers fvCtx
- `l3extRtVrfValidationPol` : relation **R**ela**t**ion target — back-reference vers une policy VRF

Ces classes héritent systématiquement du **label de leur classe cible**. Exemple :

```
fvRsCtx        → label "Private Network"    (label de fvCtx)
l3extRtVrfValidationPol → label "VRF"       (label de fvCtx)
plannerRsBdVrf → label "VRF"                (label de fvCtx)
```

De plus, le nom de la classe relation **contient** souvent le concept cible : `l3extRtVrfValidationPol` contient `Vrf`. Résultat pour la requête `"VRF"` :

- `l3extRtVrfValidationPol` : `"vrf"` dans le nom (+3) + `"VRF"` label exact (+2) + `"vrf"` dans le commentaire (+1) = **score 6**
- `plannerRsBdVrf` : `"vrf"` dans le nom (+3) + `"VRF"` label exact (+2) + commentaire (+1) = **score 6**
- `fvCtx` : `"vrf"` absent du nom `fvctx` (0) + `"VRF"` label exact (+2) + `"vrf"` dans le commentaire (+1) = **score 3**

`fvCtx`, la vraie classe VRF, est battu par ses propres classes de relation parce que ces dernières encodent le concept dans leur nom camelCase.

---

## 3. Axe 1 — Pénalité Rs/Rt

### Le diagnostic

Les classes Rs et Rt sont des **artefacts internes** du modèle objet APIC. Elles ne représentent pas des objets qu'un opérateur réseau crée, modifie ou interroge directement — elles encodent les relations entre objets primaires. En pratique, un agent LLM qui appelle `query()` ne cible jamais une classe Rs/Rt : il cible la classe primaire (`fvBD`, `fvCtx`, `vzBrCP`…) et navigue éventuellement via les relations ensuite.

Le problème est donc structurel et non statistique : les Rs/Rt **ne devraient pas** apparaître en tête des résultats de recherche. Ce n'est pas une question de score ambigu — c'est une règle sémantique du modèle objet APIC.

### Le pattern de détection

La convention de nommage ACI est stricte et cohérente. Un classe relation se reconnaît à la présence de `Rs` ou `Rt` (majuscule incluse) immédiatement après le préfixe de package dans le nom camelCase :

```
fvRsCtx           → préfixe "fv"    + Rs + "Ctx"
l3extRtVrfValidationPol → préfixe "l3ext" + Rt + "VrfValidationPol"
infraRsVpcBndlGrp → préfixe "infra" + Rs + "VpcBndlGrp"
```

La regex de détection :

```python
_RS_RT_RE = re.compile(r"^[a-z][a-z0-9]*(?:Rs|Rt)[A-Z]")
```

Détails du pattern :
- `^[a-z]` : le nom de classe commence toujours par une minuscule (convention ACI)
- `[a-z0-9]*` : le préfixe peut contenir des chiffres (`l3`, `pol2`, `iso8583`)
- `(?:Rs|Rt)` : le marqueur de relation, toujours en majuscule initiale
- `[A-Z]` : suivi immédiatement d'une majuscule (début du nom de la relation cible)

### La pénalité appliquée

Après calcul du score habituel (nom/label/commentaire), les classes Rs/Rt reçoivent une pénalité de **-3 points** :

```python
if score > 0:
    if _RS_RT_RE.match(cls):
        score -= 3
    if score > 0:
        results.append(...)
```

La pénalité est appliquée **après** le score initial pour deux raisons :
1. Elle préserve l'ordre relatif entre classes Rs/Rt elles-mêmes (celles qui matchent mieux restent mieux classées entre elles)
2. Les classes dont le score tombe à 0 ou moins sont **exclues des résultats** — un résultat non-pertinent n'a pas de valeur même en position 10

### Cas concrets après application

**Requête `"VRF"` :**

| Classe | Score brut | Pénalité | Score final |
|---|---|---|---|
| `l3extRtVrfValidationPol` | 6 (nom+label+comment) | -3 (Rt) | **3** |
| `plannerRsBdVrf` | 6 (nom+label+comment) | -3 (Rs) | **3** |
| `fvCtx` | 3 (label+comment) | 0 (pas Rs/Rt) | **3** |

Les trois classes finissent à score 3. L'égalité persiste — mais `fvCtx` est maintenant **dans la course**, ce qui n'était pas le cas avant (score 3 contre score 6 des Rs/Rt).

**Requête `"bridge domain"` :**

| Classe | Score brut | Pénalité | Score final |
|---|---|---|---|
| `fvBD` | 3 (label+comment) | 0 | **3** |
| `fvRsSvcBDToBDAtt` | 3 (label+comment) | -3 (Rs) | **0 → exclu** |
| `fvRtBd` | 3 (label+comment) | -3 (Rt) | **0 → exclu** |

Les classes relation sont exclues. `fvBD` reste mais affronte toujours les classes non-Rs/Rt qui partagent le label (`fvABDPol`, `eqptcapacityBDEntry`…).

### Gains mesurés

| Métrique | Baseline | + Axe 1 | Delta |
|---|---|---|---|
| Recall@1 | 15.4% | **28.2%** | +12.8% |
| Recall@5 | 35.9% | **41.0%** | +5.1% |
| MRR | 0.229 | **0.338** | +0.109 |
| Tier 1 R@1 | 10% | **35%** | +25% |
| Tier 1 R@5 | 50% | **55%** | +5% |
| Tier 2 R@5 | 80% | **100%** | +20% |
| Tier 3 R@1 | 0% | 0% | 0% |
| Temps moyen | 3.2 ms | **3.2 ms** | 0 |

Le gain en tier 1 est substantiel (+25% Recall@1). Le gain en tier 2 sur Recall@5 (+20%) est moins évident mais s'explique : pour les requêtes de type nom camelCase (`"l3extout"`), des classes `l3extRt*` encombrant les premières positions sont désormais pénalisées, libérant des places pour `l3extOut`.

**Ce que l'axe 1 ne résout pas :** Le problème du label partagé entre classes primaires subsiste. `fvBD` et `fvABDPol` ont toutes les deux label = `"Bridge Domain"` et aucune n'est une relation Rs/Rt. Elles continuent à se partager le rank 1 au gré de l'insertion. Les tier 3 et 4 restent à 0%.

---

## 4. Axe 2 — Enrichissement par prop_labels

### Le diagnostic

La recherche fonctionnelle (tier 3) — `"ARP flooding"`, `"dead interval"`, `"link aggregation"`, `"data plane learning"` — échoue totalement parce que ces termes n'apparaissent **ni dans le label, ni dans le commentaire** des classes concernées.

Pourtant, ces informations existent dans les fichiers jsonmeta APIC. Chaque fichier jsonmeta décrit non seulement la classe elle-même (son label, son commentaire) mais aussi **toutes ses propriétés** : chaque attribut configurable a son propre label humain fourni par Cisco.

Exemple — `fvBD.json` (extrait) :

```json
{
  "fv:BD": {
    "label":   "Bridge Domain",
    "comment": ["A bridge domain is a unique layer 2 forwarding domain..."],
    "properties": {
      "arpFlood": {
        "label": "ARP Flooding",
        "comment": ["Enable ARP flooding"],
        ...
      },
      "unicastRoute": {
        "label": "Unicast Routing",
        ...
      },
      "mac": {
        "label": "MAC Address",
        ...
      },
      "mtu": {
        "label": "MTU Size",
        ...
      }
    }
  }
}
```

Ces labels de propriétés — `"ARP Flooding"`, `"Unicast Routing"`, `"MAC Address"` — sont la terminologie officielle Cisco pour décrire les fonctionnalités d'un objet. Un agent LLM qui cherche `"ARP flooding"` cherche précisément la classe qui **possède** cette fonctionnalité. L'information existe, elle est juste absente de l'index de recherche.

### La solution en deux composantes

#### Composante A — schema-collector : enrichir l'index

La fonction `_extract_prop_labels()` dans `schema-collector/collect.py` lit les `properties` de chaque fichier jsonmeta et extrait les labels utiles :

```python
def _extract_prop_labels(properties: dict) -> list[str]:
    _GENERIC_PROP_LABELS = frozenset({
        "Name", "Description", "Annotation", "Tag", "Owner",
        "Display Name", "Managed By", "Monitoring policy",
    })
    seen, labels = set(), []
    for prop_name, pmeta in properties.items():
        if pmeta.get("isHidden", False):           # propriétés cachées ignorées
            continue
        lbl = (pmeta.get("label") or "").strip()
        if not lbl or len(lbl) <= 3:               # labels trop courts ignorés
            continue
        if lbl in _GENERIC_PROP_LABELS:            # labels génériques ignorés
            continue
        if lbl.lower() == prop_name.lower():       # label = nom technique → pas de valeur
            continue
        if lbl in seen:                            # dédupliqué
            continue
        seen.add(lbl)
        labels.append(lbl)
    return labels
```

**Pourquoi filtrer ?**

Sans filtrage, l'index serait pollué par des labels omniprésents et inutiles pour la recherche :
- `"Name"` : présent dans 99% des classes → une requête `"name"` retournerait toutes les 15k classes
- `"Description"` : idem
- `"Managed By"`, `"Monitoring policy"` : labels d'infrastructure, toujours les mêmes
- Labels identiques au nom de propriété technique (`label = "arpFlood"` au lieu de `"ARP Flooding"`) : Cisco n'a pas documenté cette propriété — pas de valeur ajoutée

Le résultat est écrit dans `class-descriptions.json` sous un nouveau champ `prop_labels` :

```json
{
  "fvBD": {
    "label":       "Bridge Domain",
    "comment":     "A bridge domain is a unique layer 2 forwarding domain...",
    "prop_labels": [
      "ARP Flooding",
      "Unicast Routing",
      "MAC Address",
      "MTU Size",
      "EP Move Detection Mode",
      "Multicast Allow",
      "Unknown Mac Unicast Action",
      "Virtual MAC Address"
    ]
  }
}
```

**Impact sur l'index :** 12 856 classes sur 15 152 possèdent au moins un prop_label utile après filtrage. 549 classes qui n'avaient ni label ni commentaire exploitable entrent dans l'index pour la première fois grâce à leurs prop_labels.

#### Composante B — MCP server : consulter les prop_labels en fallback

La modification de `search()` dans `mcp/registry/descriptions.py` est volontairement minimale. Le scan des prop_labels ne s'effectue que si la classe n'a **pas encore marqué de points** sur les trois champs habituels :

```python
if score == 0:
    for pl in meta.get("prop_labels", ()):
        if kw in pl.lower():
            score = 1
            break   # un seul match suffit — pas de cumul
```

**Trois décisions de conception :**

1. **Fallback uniquement (score == 0).** Si une classe matche déjà sur son nom ou son label, le scan des prop_labels n'est pas déclenché. Cela évite d'inflater le score d'une classe qui matcherait à la fois sur son label et sur ses propriétés — ce qui avantagerait artificiellement les classes très riches en propriétés.

2. **Score fixe +1, sans cumul.** Une classe trouvée via prop_labels obtient exactement 1 point, même si dix de ses propriétés contiennent le keyword. Ce plafond empêche que des classes aux nombreuses propriétés (comme `fvBD` avec 20+ prop_labels) dominent sur des classes mieux ciblées. Le `break` après le premier match est critique.

3. **Poids +1 = même niveau que le commentaire.** Un prop_label est une information contextuelle, non une définition centrale. Le mettre au même niveau que le commentaire (le champ le moins discriminant) est intentionnel.

### Comportement avec les exemples concrets

**Requête `"ARP flooding"` :**

```
fvBD :
  - "arp flooding" dans le nom "fvbd" ? Non → 0
  - "arp flooding" dans le label "Bridge Domain" ? Non → 0
  - "arp flooding" dans le commentaire ? Non → score toujours 0
  - score == 0 → scan des prop_labels :
    - "ARP Flooding" → "arp flooding" in "arp flooding" → OUI → score = 1, break

Score final fvBD = 1.
```

```
uribv4Entity :
  - "arp flooding" dans le nom ? Non → 0
  - "arp flooding" dans le label "IPv4 Route" ? Non → 0
  - "arp flooding" dans le commentaire ? Non → score 0
  - scan des prop_labels : aucune prop_label ne contient "arp flooding" → score reste 0

Exclu des résultats.
```

**Requête `"dead interval"` :**

`ospfIfPol` (OSPF Interface Policy) possède une propriété `deadIntvl` dont le label est `"Dead Interval"`. Avant l'axe 2, cette information n'était pas dans l'index. Après :

```
ospfIfPol :
  - Aucun match sur nom/label/commentaire → score 0
  - Scan prop_labels : "Dead Interval" → "dead interval" in "dead interval" → OUI → score = 1
```

### Gains mesurés

| Métrique | Après axe 1 | + Axe 2 | Delta axe 2 |
|---|---|---|---|
| Recall@1 | 28.2% | **30.8%** | +2.6% |
| Recall@5 | 41.0% | **53.8%** | +12.8% |
| MRR | 0.338 | **0.400** | +0.062 |
| Tier 1 R@1 | 35% | **35%** | 0% |
| Tier 3 R@1 | 0% | **9%** | +9% |
| Tier 3 R@5 | 0% | **45%** | +45% |
| Tier 4 | 0% | **0%** | 0% |
| Temps moyen | 3.2 ms | **11.4 ms** | +8.2 ms |

### Interprétation des chiffres

**Pourquoi R@1 progresse peu (+2.6%) alors que R@5 saute (+12.8%) ?**

Les classes trouvées via prop_labels reçoivent toutes le score 1. C'est le score le plus bas possible — inférieur ou égal à toute classe qui matche sur son nom, label ou commentaire. Dans la grande majorité des cas, la classe attendue finit en position 2 à 5, battue par des classes qui contiennent le mot-clé dans leur label ou commentaire avec un score plus élevé.

Exemple — requête `"ARP flooding"` :
- `fvABD` (Attached Bridge Domain) : ses prop_labels contiennent aussi `"ARP Flooding"` (même modèle objet) → score 1
- `fvABDPol` : idem → score 1
- `fvBD` : score 1
- Égalité à 3 classes, insertion order décide. `fvABD` précède `fvBD` dans le JSON → rank 3 pour `fvBD`.

Le bénéfice réel est en **Recall@5**, car l'agent LLM lit les 10 résultats retournés. Trouver la bonne classe à rank 3 ou rank 4 est une victoire opérationnelle — le LLM peut l'identifier grâce au label visible dans la réponse.

**Pourquoi +8.2 ms de latence ?**

Le fallback prop_labels est déclenché pour chaque classe qui ne matche pas sur nom/label/commentaire. Pour une requête comme `"ARP flooding"`, la quasi-totalité des 15 152 classes échoue sur les trois champs habituels — la boucle `for pl in meta.get("prop_labels", ())` s'exécute donc ~15 000 fois. Chaque itération compare une chaîne courte (le keyword) contre des chaînes courtes (les prop_labels).

11 ms reste acceptable pour un MCP tool (le LLM ne fait pas ces appels en boucle serrée), mais la dégradation est réelle. Elle pourrait être optimisée par un index inversé pré-calculé sur les prop_labels au chargement, au prix d'une plus grande empreinte mémoire.

---

## 5. Synthèse des trois états de l'algorithme

| Stratégie | R@1 | R@5 | MRR | Avg ms | Classes indexées |
|---|---|---|---|---|---|
| Baseline — naive substring | 15.4% | 35.9% | 0.229 | 3.2 | 14 603 |
| + Axe 1 — pénalité Rs/Rt | 28.2% | 41.0% | 0.338 | 3.2 | 14 603 |
| + Axe 2 — prop_labels | **30.8%** | **53.8%** | **0.400** | 11.4 | **15 152** |

*Évalué sur 39 requêtes — golden set `mcp/tests/fixtures/search_golden.json`, APIC mo-apic-v6.0_9c.*

---

## 6. Limites résiduelles et pistes futures

### Ce qui reste non résolu

**Le problème du label partagé (tier 1 partiel)**

Cisco assigne le même label à plusieurs dizaines de classes liées. `fvBD`, `fvABDPol`, `fvSvcBD` ont toutes label = `"Bridge Domain"`. Une pénalité Rs/Rt ne peut pas résoudre ce cas — ces classes ne sont pas des relations. Un tri secondaire par longueur du nom de classe (les classes canoniques sont systématiquement plus courtes : `fvBD` = 4 car. vs `fvABDPol` = 8 car.) résoudrait une partie des cas mais n'a pas encore été validé sur l'ensemble du corpus.

**Les synonymes purs (tier 4 = 0%)**

`"gateway"` → `fvSubnet` : aucune propriété de `fvSubnet` ne s'appelle "gateway". Le label APIC de la propriété IP est `"Subnet"`. `"security policy"` → `vzBrCP` : les propriétés du contrat parlent de "QoS Class", "Scope", "DSCP" — pas de "security". Ces associations n'ont aucun ancrage textuel dans le corpus APIC.

Résoudre le tier 4 nécessiterait soit :
- Un modèle d'embeddings sémantiques (vecteur de similarité entre "gateway" et "first-hop IP address of a subnet")
- Un dictionnaire de synonymes ACI maintenu manuellement
- Un fine-tuning d'un modèle sur la documentation Cisco ACI

**Le coût du fallback prop_labels à grande échelle**

+8 ms par requête est acceptable avec 15k classes. Si le corpus s'étend significativement (nouvelles versions APIC avec plus de classes), le scan linéaire deviendra un goulot d'étranglement. Un index inversé (`{prop_label_word: [class_name, ...]}`), pré-calculé au chargement du serveur, réduirait la complexité de O(n × k) à O(1) sur les prop_labels.

### Guide pour les évolutions futures

Toute modification du scoring doit être :
1. **Testée sur le golden set** : `python mcp/tests/eval_search.py -v` depuis la racine du dépôt
2. **Documentée dans le tableau** de `mcp/tests/eval_search.py` (en-tête du fichier)
3. **Validée** sur le comportement à la limite : Rs/Rt qui survivent quand leur score dépasse 3, prop_labels qui ne cumulant pas, edge cases du vide

Le golden set couvre 4 tiers mais reste un échantillon de 39 requêtes sur 15 152 classes. Une amélioration qui progresse de +5% sur le golden set peut avoir des régressions non visibles. Augmenter le golden set à 100+ requêtes avant d'introduire des heuristiques plus agressives (tri secondaire, pondération par longueur) est recommandé.
