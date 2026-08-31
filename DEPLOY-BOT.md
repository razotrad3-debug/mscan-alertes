# Alertes Telegram — même ordinateur éteint

Trois étapes. La première est obligatoire, les deux autres se font une fois.

---

## 1. Créer le bot (2 minutes, sur ton téléphone)

1. Sur Telegram, écris à **@BotFather** → `/newbot`
2. Choisis un nom, puis un identifiant finissant par `bot`
3. Il te donne un **token** du type `8123456789:AAH...`
4. **Envoie `/start` à ton nouveau bot** — sans ça il n'a pas le droit de t'écrire

Puis, dans MSCAN : onglet **Wallets → Alertes**, colle le token, clique
**Enregistrer**. Le numéro de conversation est trouvé tout seul. Clique
**Envoyer un test** pour vérifier.

À partir de là tu reçois les A+ **dès qu'ils sont notés**, sans attendre la fin
du scan — mais seulement quand MSCAN tourne.

---

## 2. Pour que ça marche PC éteint

Le scan doit tourner ailleurs. L'option gratuite est **GitHub Actions** : le
fichier `.github/workflows/alertes.yml` est déjà prêt, il scanne toutes les
30 minutes et pousse les A+ sur Telegram.

### Mise en place

1. Crée un dépôt sur github.com (**privé**, tes listes de wallets restent à toi)
2. Depuis le dossier du projet :

```bash
git init && git add . && git commit -m "MSCAN" && git branch -M main
```

3. Relie-le à ton dépôt et pousse :

```bash
git remote add origin https://github.com/TON-COMPTE/mscan.git && git push -u origin main
```

4. Sur GitHub : **Settings → Secrets and variables → Actions → New repository secret**.
   Ajoute ces quatre secrets :

| Nom | Valeur |
|---|---|
| `HELIUS_API_KEY` | ta clé Helius |
| `TELEGRAM_BOT_TOKEN` | le token de BotFather |
| `TELEGRAM_CHAT_ID` | le numéro affiché dans l'onglet Alertes |
| `FOMOSCAN_API_KEYS` | tes clés FomoScan, séparées par une virgule |

5. Onglet **Actions** → *Alertes MSCAN* → **Run workflow** pour tester tout de suite.

`.env` est dans `.gitignore` : tes clés ne partent jamais dans le dépôt, elles
vivent uniquement dans les secrets GitHub, qui sont chiffrés.

### Quota

Un dépôt privé donne **2 000 minutes gratuites par mois**. Un scan dure 4 à
6 minutes, donc toutes les 30 minutes ça consomme environ 5 800 minutes — au-dessus.
Deux façons de rester dans le gratuit :

- **Passer à une exécution par heure** — remplace `*/30 * * * *` par `0 * * * *`
  dans `alertes.yml` (≈ 2 900 min/mois, encore un peu au-dessus : `0 */2 * * *`
  pour toutes les 2 h reste largement dedans)
- **Mettre le dépôt en public** — minutes illimitées. Le code et tes listes de
  wallets deviennent visibles ; les clés restent secrètes.

GitHub désactive les workflows planifiés après 60 jours sans activité sur le
dépôt : un commit de temps en temps suffit à les garder actifs.

---

## 3. Autres hébergements (payants, plus réactifs)

Si tu veux un scan continu toutes les 10 minutes sans limite :

**Railway** — le plus simple, ~5 $/mois
1. [railway.app](https://railway.app) → *New Project* → *Deploy from GitHub repo*
2. Il détecte le `Dockerfile` tout seul
3. Onglet **Variables** : les mêmes quatre clés que ci-dessus

**VPS** (Hetzner, OVH…)

```bash
docker build -t mscan-bot . && docker run -d --restart=always --env-file .env --name mscan-bot mscan-bot
```

Sur un serveur, `bot_server.py` tourne en continu et répond aussi aux commandes.

---

## Interroger le bot depuis le téléphone

Une fois le bot en place, tu peux lui demander à tout moment — la réponse est
immédiate, il lit le dernier scan, il n'en relance pas :

| Commande | Effet |
|---|---|
| `/top` | les 10 meilleurs coins |
| `/aplus` | uniquement les A+ |
| `/solana` `/base` `/eth` `/robinhood` | filtré par chaîne |
| `/wallets` | les smart wallets les mieux notés |
| `/coin <adresse>` | analyse d'un token |
| `/etat` | date du dernier scan |
| `/scan` | force un nouveau scan |

Ces commandes ne répondent que si `bot_server.py` tourne en continu (Railway,
VPS). En mode GitHub Actions, tu reçois les alertes mais le bot ne répond pas
entre deux exécutions.

---

## Réglages

| Variable | Défaut | Rôle |
|---|---|---|
| `SCAN_INTERVAL_SEC` | 600 | délai entre deux scans |
| `HELIUS_API_KEY` | — | couche wallet ; sans elle, pas de smart money |

Pour alerter aussi les A, ouvre `mmscanner/telegram_alerts.py` et remplace
`ALERT_GRADES = ("A+",)` par `("A+", "A")`.
