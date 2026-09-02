# Tes trendlines dans MSCAN

Tu traces sur DexScreener comme d'habitude. MSCAN reprend tes lignes et
t'envoie un Telegram quand le prix vient les toucher.

## Installation (une fois)

1. Installe **Tampermonkey** dans Chrome
   (chrome.google.com/webstore, cherche « Tampermonkey »).
2. Ouvre le tableau de bord Tampermonkey, onglet **Utilitaires**,
   « Importer depuis un fichier » — ou clique sur **+** et colle le contenu de
   `outils/dexscreener-mscan.user.js`.
3. Enregistre. C'est tout.

Le script ne s'active que sur `dexscreener.com`. Il lit les coordonnees de tes
traces et les envoie a MSCAN sur ta machine, sur `127.0.0.1`. Aucun compte,
aucun mot de passe, aucun cookie, et rien ne sort de ton ordinateur.

## Utilisation

Ouvre une paire, trace ta ligne. Deux secondes plus tard, une petite etiquette
apparait en bas a droite :

    MSCAN · 2 lignes sur DICKCAT — sous surveillance

MSCAN garde la ligne meme quand tu fermes l'onglet. Pour qu'elle soit
surveillee, il faut que **l'application MSCAN soit ouverte** — c'est elle qui
regarde les prix.

- Tu deplaces une ligne : elle remplace l'ancienne au prochain passage.
- Tu effaces une ligne : elle disparait de MSCAN aussi.
- Tu ne fais rien : elle reste, et se surveille toute seule.

## Ce qui declenche l'alerte

Le prix **vient** toucher la ligne, a 1,5 % pres. Pas quand il traine dessus :
une ligne doit d'abord s'eloigner de 4,5 % pour se rearmer. Et jamais deux
fois en moins de six heures sur la meme ligne.

Le message dit si la ligne fait office de support (le prix descend dessus) ou
de resistance (il remonte dessous), et si elle monte ou descend.

## Ce que le script sait lire

Trendline, demi-droite, droite prolongee, ligne horizontale, demi-droite
horizontale. Les autres outils — Fibonacci, rectangles, textes — sont ignores
en silence.

## Duree de vie

Une trendline est suivie trois fois la duree de son trace, avec un minimum de
48 h et un maximum de 15 jours. Un niveau horizontal, lui, tient 15 jours :
il ne se deforme pas avec le temps.

## Prix ou market cap

Peu importe. DexScreener a un bouton `Price / Mcap` ; MSCAN deduit tout seul,
**ligne par ligne**, dans quelle unite tu as trace, et convertit en market cap
pour que l'alerte parle comme les autres.

Attention, ca vient de DexScreener et non de MSCAN : **une ligne tracee en
Mcap est invisible quand tu repasses en Price**, et inversement. Elle n'est
pas perdue — elle est juste hors de l'ecran. MSCAN, lui, la suit correctement
dans les deux cas.

## Si ca ne marche pas

- « MSCAN introuvable » : l'application n'est pas ouverte. Lance `MSCAN.exe`.
- Rien ne s'affiche : recharge la page, le chart met une seconde a se remplir.
- Tout est casse d'un coup : DexScreener a change son chart. Le script lit
  ses rouages internes, qui ne sont pas publics. Ca se repare, dis-le moi.

## Verifier ce qui est enregistre

    http://127.0.0.1:8787/api/trendlines

La liste des lignes suivies, avec le market cap ou chacune passe en ce moment.
