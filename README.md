# MSCAN — scanner memecoin Solana (méthode MikeMike)

Trouve les paires du moment, les **note sur 12**, les **grade A+ / A / A- / B+ …**,
détecte la **phase** (Early / Running / Compressing / Retest / Exhausted) et sort
pour chacune **Entrée / POI / T1-T3 / Cut** — le tout calqué sur la méthode du cours PDF.

En plus de ce que fait un scanner classique : **suivi du RSI 3 timeframes**, **flux
buy/sell**, **whale flow par photos de soldes**, **découverte automatique de smart
wallets**, et un onglet **adresses suivies** (tes KOLs).

---

## 1. Installation

```bash
pip install -r requirements.txt
cp .env.example .env      # puis colle ta clé Helius dedans
```

Clé Helius gratuite : https://dashboard.helius.dev
Sans elle, tout marche sauf la couche wallet et le whale flow (grades plafonnées ~A-).

## 2. Lancer

**Application de bureau (la plus simple)** — double-clic sur `dist/MSCAN.exe`,
ou sur le raccourci **MSCAN** du Bureau.

L'app s'ouvre dans une **vraie fenêtre native** (WebView2) : pas de console noire,
pas de barre d'adresse. Elle a sa propre icône dans la barre des tâches et peut y
être **épinglée** (clic droit sur l'icône → Épingler).

Fermer la fenêtre arrête le scanner. Voir `dist/LISEZ-MOI.txt`.

**Depuis les sources :**

```bash
python run.py --demo     # aperçu immédiat, sans clé ni réseau
python run.py --once     # un scan live, résultat dans le terminal
python run.py            # scan en boucle + interface + alertes Telegram
```

Interface : **http://127.0.0.1:8787**

## 2 bis. Reconstruire l'exe

```bash
pip install pyinstaller pywebview pillow
python make_icon.py            # (optionnel) regénère mscan.ico
pyinstaller MSCAN.spec --noconfirm
```

L'exe se place dans `dist/`. Il lit ses fichiers de travail (`.env`,
`followed_wallets.txt`, `smart_wallets.txt`, `flow_snapshots/`) **à côté de
lui** — copie-les dans `dist/` si tu déplaces l'exe ailleurs.

---

## 3. Les 5 onglets

| Onglet | Contenu |
|---|---|
| **RADAR** | Toutes les paires en liste, classées par grade. Market cap **en direct** (rafraîchi toutes les 15 s sans recharger). En haut, une section « Suivis par les smart wallets ». |
| **RECHERCHE** | Colle un contract address → grade, phase, intel, RSI, flux, smart wallets, whale flow. |
| **WALLETS** | Les smart wallets détectés automatiquement, avec **le pourquoi** : sur quels coins pumpés ils étaient early, à quel rang d'entrée, pour quel pump. |
| **FLOW** | Tous les coins classés par **accumulation whale 24h** — l'écran « où l'argent fort se positionne ». C'est la vue principale façon sun-flow. |
| **ADRESSES** | **Encadré de saisie** pour coller tes adresses (KOLs de FOMO, X…), puis les coins où chacune vient d'entrer. Un coin sur lequel **plusieurs** adresses entrent remonte en tête. |

Sur chaque ligne du radar, des icônes : **Intel** (déplie entrée/cibles/cut + RSI + flux),
**Smart wallets**, **Analyse**, **GMGN**, **DexScreener**, **Copier le contract**.

---

## 4. Le score /12

| Pilier | Critères |
|---|---|
| **Volume** | Liquidité ≥ 50k · Vol 24h ≥ 500k (monster ≥ 1M) · Turnover Vol/MC ≥ 0.5 · Vol 1h ≥ 100k |
| **Structure** | Buy pressure · Pas de dump high-volume · Tient son floor · Phase actionnable |
| **RSI** | RSI 1h favorable (room to run, ou bounce oversold) |
| **Wallets** | Distribution holders saine · Smart-money présent |
| **Survie** | Âge & survie (30 min → 30 j) |

12/12 = **A+**, 11 = **A**, 10 = **A-**, 9 = **B+**, 8 = **B**…
Tous les seuils sont dans `config.py`.

Le **RSI** est coloré en dégradé continu : blanc à 50, de plus en plus **rouge** vers 80
(zone de vente), de plus en plus **vert** vers 20 (zone d'achat).

---

## 5. Whale Flow — comment ça marche (méthode sun-flow)

**Le problème :** parser chaque swap est impossible de façon fiable — Helius ne décode
pas les AMM récents (Meteora DBC, pump AMM) : les transactions reviennent en `UNKNOWN`
sans transferts lisibles.

**La solution :** on peut lire à tout instant le **solde exact de tous les holders**
(API DAS). On prend donc des **photos régulières des soldes**, et le flux est la
**différence entre deux photos** :

```
Photo(t2) − Photo(t1) = qui a accumulé, qui a distribué, et combien
```

C'est plus robuste que le parsing de swaps : ça capture achats, ventes, transferts et
routages, quel que soit le DEX.

Sortie : **Net USD Flow 24h / 7j / 30j**, ventilé par **Whale / Shark / Dolphin / Fish** —
catégories définies en **part du supply** (Whale ≥ 1 %, Shark ≥ 0.25 %, Dolphin ≥ 0.05 %),
pas en USD fixe, pour rester pertinent quelle que soit la taille du coin.

⚠️ **Il faut de l'historique.** Une photo est prise à chaque scan (max une / 30 min).
Le flux 24h apparaît après 24h de fonctionnement, le 7j après 7 jours, etc.
Les fenêtres non couvertes affichent « pas encore d'historique » plutôt qu'un faux zéro.

**Lecture :** whales en inflow net = l'argent fort entre. Whales en outflow pendant que
le prix monte = distribution, ils vendent au retail.

---

## 6. Découverte automatique de smart wallets

```bash
python -m mmscanner.discover_wallets
```

La méthode du cours (Module 07), automatisée :
1. Prend les coins qui viennent de **percer** (≥ 3x sur 24h, jeunes, liquides)
2. Lit leurs **early buyers** on-chain
3. Garde ceux qui reviennent sur **plusieurs pumps** (récurrence ≥ 2)
4. **Mémorise le pourquoi** : quels coins, quel rang d'entrée, quel pump

Sorties : `smart_wallets_data.json` (détail, lu par l'onglet WALLETS) et
`smart_wallets.txt` (watchlist utilisée par le scoring).

## 7. Adresses suivies

Deux façons d'alimenter la liste, dans l'onglet **ADRESSES**.

### a) Par handle FOMO (@pseudo) — le plus simple

Colle les `@pseudo` dans le second encadré → **Résoudre & ajouter**.
Chaque handle est résolu en **wallet Solana vérifié** puis ajouté à ta liste.

Nécessite une clé **FomoScan** gratuite :
1. https://partner.fomoscan.sh (sans carte bancaire) — la clé arrive par Telegram
2. `FOMOSCAN_API_KEY=fsk_live_...` dans `.env`

> Pourquoi cette clé : FOMO ne publie pas les wallets. Son API interne
> (`prod-api.fomo.family/user/<handle>`) répond **401** sans token de session.
> FomoScan maintient un index public de correspondances handle ↔ wallet
> **vérifiées** et l'expose via `/v2/user/handle/{handle}` → `solanaAddress`.

### b) Par adresse — colle directement
Le parser accepte **n'importe quel format** :

```
9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM
9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM   Cupsey
7phbaH6UeyFJmjdPoyiaQAHX3B1gRtnCVZL7HZNtbonk, Euris
4Hs8VbNmQrT2yWxZcE5kLpJ9aUdFgRt3oBvMnKiPq7Xw - KOL privé
https://solscan.io/account/2Wc9WdKpLmN4rXvBqZt7yHsEjA3oUgFiRb5nMkTxQ8Pe
adresse1, adresse2          (plusieurs sur une ligne)
```

Il extrait les adresses base58 valides, ignore le reste (URLs, puces, ponctuation),
prend le texte restant comme label, et supprime les doublons.
Stocké dans `followed_wallets.txt`.

L'onglet lit leurs achats des dernières 72h et affiche les coins où elles sont
entrées. **Signal fort** : plusieurs adresses suivies sur le même coin.

---

## 8. Architecture

```
app.py                → point d'entrée APPLICATION (fenêtre native) ⭐
run.py                → point d'entrée console (--demo / --once / serveur)
mscan.ico / .png      → icône de l'application
config.py             → tous les seuils + univers + clés
smart_wallets.txt     → watchlist (générée par la découverte)
followed_wallets.txt  → TES adresses suivies (manuelles)
mmscanner/
  sources_gecko.py    → découverte pools (DEX memecoin) + OHLCV   [GeckoTerminal]
  sources_dex.py      → MC, volumes, buy/sell par timeframe        [DexScreener]
  sources_helius.py   → holders + concentration (API DAS)          [Helius]
  helius_tx.py        → transactions on-chain
  holder_flow.py      → whale flow par photos de soldes ⭐
  discover_wallets.py → découverte auto de smart wallets
  followed.py         → achats récents des adresses suivies
  checker.py          → analyse à la demande d'un mint
  indicators.py       → RSI (Wilder) + Fibonacci
  phases.py           → détection de phase + intel entrée/TP/cut
  scoring.py          → le score /12
  engine.py           → orchestration (parallélisée)
  telegram.py         → alertes RADAR / UPGRADED
web/server.py         → interface (5 onglets)
MSCAN.spec            → recette de build PyInstaller (app fenêtrée)
dist/MSCAN.exe        → application autonome
flow_snapshots/       → photos de soldes (créé automatiquement)
```

---

## 9. Performance & limites

- Un scan complet ≈ **5 à 6 min**, re-scan toutes les 10 min. Le facteur limitant est
  le rate-limit GeckoTerminal gratuit (OHLCV) — le scanner se cadence tout seul et
  met les pools en cache (`pool_cache.json`).
- **Couverture** : 4 sources combinées — DEX memecoin GeckoTerminal (pumpswap,
  raydium-launchlab, meteora-dbc), top volume, trending/nouveaux, et les endpoints
  de découverte DexScreener (token-profiles, token-boosts).
- **Liquidité adaptative** : un coin passe si liq ≥ 50 K, **ou** si liq ≥ 20 K avec
  ≥ 500 K de volume 24 h (un jeune coin très actif a peu de liquidité mais un énorme
  turnover — c'est justement le profil des plays early).
- L'univers est **100 % crypto-natif**. Découverte sur les DEX à memecoins
  (pumpswap / raydium-launchlab / meteora-dbc), puis filtre `is_crypto_native()` qui
  écarte stables, majors, et **tout ce qui trace ou imite une action, un indice, une
  matière première ou une entreprise** — y compris les memecoins nommés d'après des
  actions (TSLA, NVDA, HOOD, SpaceX, Anthropic…), qui sont bien lancés sur pumpswap
  mais ne sont pas du crypto-natif. Listes éditables dans `config.py`
  (`EXCLUDE_STOCKS`, `EXCLUDE_NAME_WORDS`).
- La concentration des holders n'est calculée que si le token a **moins de ~4000 holders**
  (au-delà, on renvoie « — » plutôt qu'un chiffre faux).
- Le scanner **ne trade pas** : il trouve, filtre, note et alerte. La décision reste
  manuelle — comme MikeMike : les bots servent le funnel, tu décides.

⚠️ Aide à la décision, pas un conseil financier. Les memecoins peuvent aller à zéro.
