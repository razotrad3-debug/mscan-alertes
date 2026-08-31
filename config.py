"""
Configuration centrale du scanner MikeMike.
Tous les seuils viennent directement de la méthodologie (voir le cours PDF).
Modifie ici sans toucher au code.
"""
import os
import sys


def _app_dir() -> str:
    """Dossier où vivent les fichiers de travail (.env, listes, caches).

    Figé en .exe : le dossier de l'exécutable (et non le dossier temporaire
    d'extraction), pour que l'utilisateur retrouve et modifie ses fichiers.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


APP_DIR = _app_dir()


def path(name: str) -> str:
    """Chemin absolu d'un fichier de travail."""
    return os.path.join(APP_DIR, name)


try:
    from dotenv import load_dotenv
    load_dotenv(path(".env"))
except Exception:
    pass

# ── Clés / secrets (via .env) ─────────────────────────────
HELIUS_API_KEY     = os.getenv("HELIUS_API_KEY", "").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "").strip()
# FomoScan : resout un handle FOMO en wallet Solana verifie.
# Cle gratuite sur https://partner.fomoscan.sh  (format fsk_live_... / fsk_test_...)
FOMOSCAN_API_KEY   = os.getenv("FOMOSCAN_API_KEY", "").strip()

CHAIN = "solana"
# Chaines suivies. La couche wallet (holders, smart money, whale flow) reste
# Solana-only : Helius ne couvre que Solana. Le reste (scoring, RSI, intel) marche partout.
CHAINS = ["solana", "robinhood", "base", "ethereum"]
CHAIN_META = {
    "solana":   {"label": "Solana",    "color": "#9945FF"},
    "robinhood":{"label": "Robinhood", "color": "#CCFF00"},
    "ethereum": {"label": "Ethereum",  "color": "#627EEA"},
    "base":     {"label": "Base",      "color": "#3B7DFF"},
}

# ── Filtres MikeMike (ses params DexScreener) ─────────────
MIN_LIQUIDITY_USD = 50_000     # minLiq (coins établis)
# Un coin jeune très actif peut avoir moins de liquidité mais un énorme turnover :
# on l'accepte si liq >= MIN_LIQ_EARLY ET vol24h >= MIN_VOL_EARLY.
MIN_LIQ_EARLY     = 20_000
MIN_VOL_EARLY     = 500_000
MAX_AGE_HOURS     = 720        # maxAge = 30 jours
MIN_AGE_HOURS     = 0.5        # on laisse respirer 30 min après launch
MIN_VOL_H24       = 100_000    # plancher d'intérêt (tier bas)

# ── Univers memecoin (exclut majors, stables, stocks tokenisés) ──
MIN_MCAP = 30_000              # sous ça : trop illiquide / non scorable
MAX_MCAP = 80_000_000          # au-dessus : ce n'est plus un memecoin jouable
EXCLUDE_SYMBOLS = {
    "SOL", "WSOL", "USDC", "USDT", "USDS", "USD1", "EURC", "PYUSD",
    "JUP", "JTO", "JLP", "MSOL", "JITOSOL", "BSOL", "INF", "WBTC", "WETH", "CBBTC",
}
# ── Hors univers : actions, indices, matières premières, entreprises ──
# On ne veut QUE du crypto-natif. Beaucoup de ces tokens sont techniquement des
# memecoins (lancés sur pumpswap) mais tracent/imitent des actions -> exclus.
EXCLUDE_STOCKS = {
    # tech / mega caps
    "NVDA","TSLA","AAPL","MSFT","AMZN","GOOGL","GOOG","META","AMD","INTC","MU",
    "AVGO","SMCI","ARM","ORCL","CRM","ADBE","NFLX","DIS","UBER","ABNB","SHOP",
    # fintech / brokers / crypto-equities
    "HOOD","COIN","MSTR","SQ","PYPL","CRCL","SOFI","IBKR","SCHW",
    # meme stocks & autres
    "GME","AMC","BBBY","BB","NOK","PLTR","RBLX","RIVN","LCID","NIO","F","GM","BA",
    # espace / privé / pré-IPO
    "SPCX","SPACEX","STARLINK","OPENAI","ANTHROPIC","STRIPE","XAI","NEURALINK",
    # indices / ETF / matières premières / FX
    "SPY","QQQ","IWM","DIA","VOO","TQQQ","SQQQ","GLD","SLV","USO","XAU","XAG",
    "VIX","DJIA","NASDAQ","SP500","NIKKEI","EUR","GBP","JPY","CNY",
}
# Noms d'entreprises/institutions : si le nom du token les contient -> exclu
EXCLUDE_NAME_WORDS = {
    "tesla","nvidia","apple","microsoft","amazon","google","alphabet","meta",
    "robinhood","coinbase","microstrategy","spacex","starlink","openai",
    "anthropic","stripe","neuralink","gamestop","palantir","netflix","disney",
    "berkshire","blackrock","nasdaq","s&p","dow jones","ferrari","ford",
    "xiaomi","samsung","huawei","sony","toyota","honda","boeing","airbus",
    "walmart","costco","starbucks","mcdonald","nike","adidas","pepsi","coca-cola",
    "visa","mastercard","jpmorgan","goldman","morgan stanley","citigroup",
    "pfizer","moderna","johnson","intel corp","qualcomm","cisco","ibm","oracle",
}
EXCLUDE_MINTS = {
    "So11111111111111111111111111111111111111112",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
}

# ── Étendue de la découverte ──────────────────────────────
DISCOVERY_PAGES   = 2          # pages GeckoTerminal top-volume (~20 pools/page)
# DEX à memecoins interrogés directement (univers bien plus pur que le top global)
DISCOVERY_DEXES   = ["pumpswap", "raydium-launchlab", "meteora-dbc"]
DEX_PAGES         = 2
USE_DEXSCREENER_DISCOVERY = True   # sources DexScreener en complément
INCLUDE_NEW_POOLS = True       # les trenches : nouveaux pools
INCLUDE_TRENDING  = True
ENRICH_TOP_N      = 28         # enrichissement lourd (OHLCV/RSI/wallets) sur les N meilleurs
WATCHLIST_MAX     = 60         # wallets auto-decouverts gardes (les mieux notes)
SMARTMONEY_TOP_N  = 25         # check smart-wallets sur les N meilleurs (coûteux en requêtes)

# ── Seuils de scoring (les piliers de la méthode) ─────────
VOL24_MONSTER   = 1_000_000    # "volume monster"
VOL24_GOOD      = 500_000
VOLH1_ATTENTION = 100_000      # "100-250k/h = pay attention"
VOLM5_BREAKOUT  = 5_000        # "5k-15k/5min = breakout"
TURNOVER_MIN    = 0.5          # vol24h / marketcap (attention = rotation)
TOP_HOLDER_MAX  = 0.15         # un holder > 15% = risque de dump
TOP10_MAX       = 0.40         # top10 > 40% = distribution dangereuse
RSI_OVERBOUGHT  = 70           # au-dessus = plus de "room to run"
RSI_OVERSOLD    = 40           # zone de bounce

# ── Cadence & alertes ─────────────────────────────────────
SCAN_INTERVAL_SEC = 600        # re-scan toutes les 10 min (un scan complet dure ~5-6 min
                               # — limite du rate-limit GeckoTerminal gratuit)
ALERT_MIN_GRADE   = "A-"       # Telegram : alerte seulement >= cette grade

# ── Decouverte automatique des smart wallets ──────────────
# Le scanner relance tout seul la chasse aux smart wallets sur les coins qui
# viennent de percer : la watchlist se met a jour sans intervention.
AUTO_DISCOVER_WALLETS   = True
DISCOVER_INTERVAL_H     = 4          # relance toutes les 4 h
DISCOVER_STATE_FILE     = path("discover_state.json")
WEB_HOST = "127.0.0.1"
WEB_PORT = 8787

# ── Fichiers ──────────────────────────────────────────────
SMART_WALLETS_FILE = path("smart_wallets.txt")
STATE_FILE         = path("scanner_state.json")
FOLLOWED_FILE      = path("followed_wallets.txt")
WALLET_STORE_FILE  = path("smart_wallets_data.json")
POOL_CACHE_FILE    = path("pool_cache.json")
SNAPSHOT_DIR       = path("flow_snapshots")

# ── Grade ladder (calquée sur prntwrx : 12/12=A+, 11=A, 10=A-) ──
GRADE_LADDER = [
    (12, "A+"), (11, "A"), (10, "A-"),
    (9, "B+"),  (8, "B"),  (7, "B-"),
    (6, "C+"),  (5, "C"),  (4, "C-"),
    (0, "D"),
]
GRADE_ORDER = ["D", "C-", "C", "C+", "B-", "B", "B+", "A-", "A", "A+"]


def grade_from_score(score: int) -> str:
    for threshold, g in GRADE_LADDER:
        if score >= threshold:
            return g
    return "D"


def grade_rank(g: str) -> int:
    return GRADE_ORDER.index(g) if g in GRADE_ORDER else -1
