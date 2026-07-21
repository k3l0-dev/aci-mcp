# Licence PolyForm — EULA, auto-upgrade, télémétrie

> Décisions de design (21/07), informées par deux recherches web
> (conformité RGPD/ePrivacy + précédents dans l'écosystème MCP). Ceci
> est un cadrage d'ingénierie, PAS un avis juridique — à faire valider
> par un DPO/avocat RGPD (idéalement habitué à la pratique CNIL, vendeur
> français) avant toute mise en œuvre, en particulier la détermination
> de base légale et le texte de notice.

## Décisions finales

### 1. EULA + collecte email/domaine

- Licence elle-même : **honor-system**, pas de clé de licence, pas de
  phone-home de validation — cohérent avec la nature de PolyForm et
  avec la pratique de tout l'écosystème MCP (aucun serveur trouvé
  n'enforce une licence au runtime).
- Gate d'acceptation technique au premier lancement + relance à chaque
  version majeure (pattern `ACCEPT_EULA`, ex. image Docker SQL Server) —
  peu coûteux, donne une trace d'acceptation auditable.
- Base légale : **6.1.b RGPD (nécessité contractuelle)**, jamais
  consentement pour cette collecte obligatoire (un consentement forcé
  ne serait pas valide légalement — problème de "librement donné",
  art. 7.4). 6.1.f (intérêt légitime) en filet de secours.
- **Raffinement décidé** : ne pas classifier perso/commercial
  uniquement par heuristique de domaine email (signal bruité — un
  freelance avec son propre domaine paraît "commercial", un salarié sur
  gmail perso paraît "perso"). Ajouter une **question d'auto-déclaration
  directe** ("cet usage est-il personnel ou professionnel ?") à
  l'acceptation, dans l'esprit du modèle Docker (auto-déclaration
  contractuelle + clause d'audit, pas surveillance silencieuse). Le
  domaine email devient une donnée de contact/référence, pas le seul
  signal de classification.

### 2. Auto-upgrade (optionnel)

- Opt-in, interrupteur de préférence uniquement.
- **Ne pas collecter une deuxième fois** — référence l'identité déjà
  connue depuis l'étape 1 (EULA). Un seul point de collecte, pas deux.

### 3. Télémétrie

- Stats d'usage + taux d'erreur uniquement, jamais de contenu.
- Log local d'abord (auditable par le client), sync périodique vers le
  backend.
- **Opt-in, désactivé par défaut** — confirmé par les deux recherches :
  la tendance générale de l'industrie (précédent Next.js/Vercel, backlash
  sur l'opt-out) ET le seul précédent MCP vraiment comparable
  (`mcp-confluent`, télémétrie liée à un domaine email) explicitement
  identifié comme le pattern le plus risqué de l'écosystème, avec une
  vraie plainte RGPD déposée contre `blender-mcp` pour exactement ce
  type de collecte en opt-out.
- Honorer `DO_NOT_TRACK` (signal communautaire émergent dans l'écosystème
  MCP : Confluent, MongoDB, AWS DynamoDB MCP, Neo4j, Dynatrace).

## Résumé des deux recherches

**RGPD/ePrivacy** : article 13 (notice à l'acceptation : identité du
responsable de traitement, finalité+base légale par élément, durée de
conservation, droits, droit de réclamation CNIL) ; registre de
traitement (art. 30) nécessaire malgré la taille PME (exemption ne
s'applique quasi certainement pas, traitement récurrent pas occasionnel) ;
rétention 30-90 jours télémétrie, ~5 ans preuves d'acceptation ; DPA
nécessaire avec l'hébergeur cloud (processeur), **pas** avec les clients
finaux (l'éditeur est responsable de traitement, pas processeur, pour
cette donnée précise — jamais de contenu/config client touché) ;
processus d'effacement manuel documenté suffisant à cette échelle.
Précédents : JetBrains (meilleur match — classification perso/commercial
+ télémétrie opt-out intérêt légitime pour non-commercial vs consentement
explicite pour commercial), Docker (auto-déclaration + audit, pas
collecte silencieuse), GitLab (modèle multi-base légale), Nextcloud/Odoo.

**Écosystème MCP** : quasi zéro précédent d'enforcement de licence au
runtime — le seul analogue proche (commercetools Commerce MCP, plafond
1M d'appels) a son fichier LICENSE en MIT pur, la restriction commerciale
vit uniquement dans la prose du README. Les gros vendeurs (MongoDB,
Datadog, Snowflake, Elastic) gardent leur code MCP permissif, le péage
vit dans le produit sous-jacent. Les plateformes de monétisation MCP
(Composio, Klavis, Moesif, x402/Coinbase) identifient/facturent par
clé API/OAuth à l'inscription — infrastructure de facturation, pas de
consentement de licence. Télémétrie : norme actuelle plutôt opt-out
anonymisé, MAIS le seul exemple qui lie la télémétrie à une identité
(email/domaine, comme `mcp-confluent`) est signalé comme le plus risqué,
et `blender-mcp` a eu une vraie plainte RGPD pour opt-out + données
identifiantes.

## Verdict

Le design initial était déjà bien aligné avec le légal et la pratique.
Seul ajustement réel : remplacer/compléter l'inférence par domaine email
par une auto-déclaration explicite à l'acceptation.
