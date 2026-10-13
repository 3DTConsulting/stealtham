# Stealth AM · Suivi des taux courts

Application web de suivi des taux courts souverains de la zone euro (France, Allemagne, Italie, Espagne), du calendrier des émissions et du paysage des fonds monétaires en euros concurrents. Elle sert à la contre-valorisation et au contrôle des exécutions.

Adresse : https://stealtham.vercel.app (raccourcis : `/suivitaux` et `/suivitaux/concurrence`).

## Contenu du dépôt

| Élément | Rôle |
| --- | --- |
| `index.html` | L'application (une seule page, bilingue français / anglais) |
| `manifest.webmanifest`, `sw.js`, `icons/` | Installation sur téléphone et ordinateur, ouverture hors ligne avec les dernières données consultées |
| `data/` | Les données affichées, au format JSON (voir plus bas) |
| `scripts/update_data.py` | Collecte quotidienne des sources officielles |
| `.github/workflows/update-data.yml` | Lance la collecte du lundi au vendredi vers 18 h (heure de Paris) et enregistre les nouvelles données |
| `vercel.json` | Raccourcis d'adresse et règles de cache pour Vercel |

## Données

| Fichier | Contenu | Mise à jour |
| --- | --- | --- |
| `data/series.json` | Courbe Bundesbank (Svensson) et TEC Banque de France, 1 et 2 ans, depuis avril 2026 | Automatique, chaque jour ouvré |
| `data/snapshots.json` | Relevés par ISIN : prix de référence Bundesbank, cours des BOT sur le MOT, repères d'adjudication France et Espagne | Automatique pour l'Allemagne et l'Italie |
| `data/calendar.json` | Calendrier des adjudications des quatre émetteurs | Manuelle (voir plus bas) |
| `data/competitors.json` | Fonds monétaires concurrents, encours et rendements | Manuelle |
| `data/status.json` | Date et résultat de la dernière collecte, source par source | Automatique |

Le script ne supprime jamais de données. Si une source échoue, les données existantes restent en place et l'échec est noté dans `data/status.json`, affiché en haut de l'application.

## Mise en service (une seule fois)

1. **Clé Webstat** : dans GitHub, ouvrir le dépôt, puis Settings > Secrets and variables > Actions > New repository secret. Nom : `WEBSTAT_API_KEY`, valeur : la clé de l'API Webstat de la Banque de France.
2. **Droits d'écriture du robot** : Settings > Actions > General > Workflow permissions, cocher « Read and write permissions », puis Save.
3. **Premier lancement** : onglet Actions > « Mise à jour des données » > Run workflow. Vérifier que l'exécution se termine en vert, puis ouvrir `data/status.json` pour voir le résultat de chaque source.
4. **Vercel** : le projet `stealtham` est relié au dépôt. Chaque enregistrement (y compris celui du robot) redéploie automatiquement l'application en quelques secondes.

## Mettre à jour le calendrier ou la liste des concurrents

Modifier `data/calendar.json` ou `data/competitors.json` directement dans GitHub (bouton crayon, puis « Commit changes »). Chaque texte libre a son équivalent anglais dans un champ suffixé `_en` (par exemple `detail` et `detail_en`). Claude peut préparer ces mises à jour à partir des sources (Finanzagentur, AFT, MEF, Tesoro, fiches des fonds).

## Tester la collecte en local

```
pip install -r scripts/requirements.txt
WEBSTAT_API_KEY=... python scripts/update_data.py --dry-run
```

L'option `--dry-run` affiche le résultat de chaque source sans rien écrire.

## Limites connues

- Les prix par ISIN de la Bundesbank sont lus dans un fichier Excel mensuel dont la structure n'a pas pu être testée à l'avance : si la ligne `bbk_isin` de `data/status.json` indique un échec, le script doit être ajusté au format réel.
- Les cours des BOT proviennent d'un marché de détail en données différées ; leur réutilisation professionnelle automatisée peut relever d'une licence de Borsa Italiana.
- La France sous un an et l'Espagne restent des repères d'adjudication tant qu'aucune source quotidienne n'est branchée.
