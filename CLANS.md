# Wallets de clans FOMO — résolu

## Résultat

**56 wallets sur 84**, trouvés gratuitement. Ils sont dans `followed_wallets.txt`
sous leur groupe, et suivis partout dans l'app.

| Clan | Membres | Wallets trouvés |
|---|---|---|
| Dabal | 26 | 18 |
| Grand | 22 | 14 |
| Nobi Ventures | 23 | 15 |
| Fantom Troupe | 13 | 9 |
| **Total** | **84** | **56** |

55 des 56 ont aussi une adresse Ethereum, exploitée par la couche EVM du scanner.

## Comment, sans payer

L'API FomoScan facture **2 500 CU par handle** et son plan gratuit `pilot`
accorde `monthlyUnits: 0` — zéro, et rien ne se recharge d'un mois sur l'autre.
Les 100 000 CU du premier compte étaient un bonus d'inscription ponctuel.
Créer d'autres comptes ne sert donc à rien.

Mais FomoScan publie une **fiche gratuite par trader** sur `fomoscan.sh/<handle>`,
rendue côté serveur, qui contient les wallets vérifiés Solana et EVM. C'est la
même donnée que la route API payante. Le module `mmscanner/fomoscan_web.py` lit
ces fiches ; l'API n'est plus qu'un secours si le site ne répond pas.

Vérification faite : le wallet renvoyé pour un handle déjà résolu par une autre
voie était identique. Contrôle on-chain sur un échantillon — toutes actives.

## Les 28 manquants

Deux cas, tous les deux sans recours :

- **20 inconnus de FomoScan** — leur handle n'est pas dans l'index. Vérifié à la
  main : chercher `ventikohi` ne donne rien, alors que `venti` sort `@venti`,
  `@ventia`, `@VentiDos`. Ce n'est pas une faute de frappe, ils n'y sont pas.
- **8 sans wallet vérifié** — FomoScan a leur fiche mais aucun wallet prouvé.

Seule la route payante `POST /resolve` (100 000 CU l'appel) enverrait des
crawlers en chercher un. Hors de proportion pour 28 comptes, dont plusieurs
sont en PnL négatif.

Si tu récupères l'un de ces wallets à la main dans l'app FOMO, remplis
`clans/A_REMPLIR.txt` et lance :

```bash
python add_clan.py --remplir
```

## Ajouter un clan

Dépose un fichier `clans/<Nom du clan>.txt`, un @handle par ligne, triés par PnL
décroissant. Le scanner les résout tout seul au cycle suivant.
