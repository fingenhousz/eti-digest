# Qualification commerciale — aperçu V2

Cette version est indépendante des envois Telegram. Le digest actuel, ses statuts
et son historique ne sont pas modifiés. Aucun abonnement supplémentaire requis.

## Fonctionnement

1. Collecte presse sur 30 jours par défaut (`--lookback-days`), 15 requêtes ciblant
   IA, préparation de vente et risques climatiques physiques. Les dates de
   publication sont conservées ; une publication récente ne prouve pas un événement récent.
   Le collecteur Bodacc de difficultés reste dans le digest historique, hors de cet aperçu.
2. Présélection structurée de zéro à cinq situations avec citation exacte,
   offre, hypothèse de mission, rôle acheteur potentiel et question de qualification.
3. SIREN issu de la source pour la même entreprise, sinon recherche officielle
   avec nom exact et résultat unique. Les homonymes restent non résolus.
4. Enrichissement Pappers limité à cinq appels par défaut, cache local de 30 jours.
5. Qualification financière déterministe et rapprochement LinkedIn local.
6. Rapports Markdown/JSON, sans Telegram et sans écriture dans sent_history.json.

## Hypothèses financières à calibrer

Base conservatrice : 15 k€/mois, soit 180 k€/an, première mission de 45 k€.
« Favorable » exige deux exercices annuels comparables bénéficiaires, dernier
exercice clos depuis moins de 730 jours, EBE >= 10 fois le retainer annualisé,
trésorerie >= 2 fois la première mission et dette financière <= 3 fois l'EBE.
Ces seuils sont une politique de présélection modifiable dans POLICY, pas une
norme financière ni la preuve d'un budget. Les valeurs sont en euros.

Une perte, un EBE non positif, une trésorerie inférieure à la première mission,
une cessation ou une procédure collective signalée abaissent le confort.
Les comptes absents/confidentiels/anciens et champs manquants restent inconnus.
La trésorerie publiée ne mesure pas le cash disponible aujourd'hui.
Les chiffres concernent le SIREN exact : ne pas les attribuer au groupe entier.
L'éligibilité de taille est un filtre commercial, pas la définition légale ETI.

## Exécuter

Depuis la racine eti-digest, avec ANTHROPIC_API_KEY et PAPPERS_API_KEY dans
l'environnement :

```powershell
python -m unittest -v test_alerts test_digest_output test_prospecting
python prospecting_preview.py
python prospecting_preview.py --connections private/Connections.csv
python prospecting_preview.py --match-report private/run/report.json --connections private/Connections.csv --output private/matched
```

Les sorties vont dans private/prospecting-preview, exclu de Git. Ne jamais
committer l'export LinkedIn. L'import conserve nom, entreprise, poste et URL,
pas les emails. Le réseau n'est pas transmis au modèle ni à Pappers.
Les correspondances ne prouvent pas la force du lien, le poste actuel ou
l'absence d'homonymie. Aucun réseau de second degré n'est inventé.

Un workflow manuel `Prospecting qualification preview` est fourni pour utiliser
les secrets GitHub existants. Il n'a ni planification, ni secret Telegram, ni
permission d'écrire le dépôt. Les rapports sont conservés trois jours comme
artifacts ; les exports LinkedIn ne sont pas inclus dans ce workflow.
Le cache est local à chaque job GitHub : le plafond s'applique à chaque exécution.
Deux appels IA maximum (un essai + une reprise) ; coûts dépendants des sources.

## Limites connues avant activation

- Validation locale sur fixtures synthétiques ; validation réelle Pappers/IA à
  réaliser avec les secrets GitHub. Leur présence ne prouve pas leur validité.
- Les citations sont vérifiées textuellement, pas leur interprétation métier.
- Recherche au nom exact : certaines marques/groupes resteront à rapprocher
  manuellement d'un SIREN ; ne pas inventer d'association.
- Les extraits RSS peuvent manquer de contexte ; l'anticipation de vente repose
  principalement sur la presse. Les erreurs de collecte sont suivies par requête.
- Pas encore de données géographiques d'aléas climatiques, de cartographie
  complète des participations ou de suivi longitudinal des événements.
- Une piste sans taille confirmée reste dans la file de qualification.
- Aucun envoi automatique V2 activé : examiner les aperçus avant intégration.
