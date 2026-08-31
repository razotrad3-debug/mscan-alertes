"""Modèle de données d'une paire scannée."""
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any


@dataclass
class Pair:
    # identité
    chain: str
    name: str
    symbol: str
    mint: str
    pair_address: str
    gecko_pool: Optional[str] = None

    # métriques de marché (DexScreener)
    price_usd: float = 0.0
    market_cap: float = 0.0
    fdv: float = 0.0
    liquidity_usd: float = 0.0
    vol_h24: float = 0.0
    vol_h6: float = 0.0
    vol_h1: float = 0.0
    vol_m5: float = 0.0
    chg_m5: float = 0.0
    chg_h1: float = 0.0
    chg_h6: float = 0.0
    chg_h24: float = 0.0
    buys_h1: int = 0
    sells_h1: int = 0
    buys_m5: int = 0
    sells_m5: int = 0
    buys_h6: int = 0
    sells_h6: int = 0
    buys_h24: int = 0
    sells_h24: int = 0
    age_hours: float = 0.0

    # analyse technique (GeckoTerminal OHLCV)
    rsi_15m: Optional[float] = None
    rsi_1h: Optional[float] = None
    rsi_4h: Optional[float] = None
    swing_low: Optional[float] = None    # en prix
    swing_high: Optional[float] = None
    rsi_note: str = ""

    # couche wallet (Helius)
    holders: Optional[int] = None
    top_holder_pct: Optional[float] = None
    top10_pct: Optional[float] = None
    smart_holders: int = 0
    smart_names: List[str] = field(default_factory=list)
    smart_detail: List[Dict[str, Any]] = field(default_factory=list)  # [{short,label,coins:[...]}]
    smart_accumulating: int = 0        # wallets suivis ayant acheté < 6h
    sources: List[Dict[str, Any]] = field(default_factory=list)  # d'ou vient ce coin
    wallets_available: bool = False
    from_wallet: bool = False        # remonte par une adresse suivie, pas par la decouverte

    # résultats
    score: int = 0
    max_score: int = 12
    grade: str = "D"
    phase: str = "-"
    criteria: List[Dict[str, Any]] = field(default_factory=list)
    intel: Dict[str, Any] = field(default_factory=dict)
    updated_at: float = 0.0

    # liens
    # slugs de chaine pour construire les liens externes
    _GMGN = {"solana": "sol", "ethereum": "eth", "base": "base"}

    @property
    def gmgn_url(self) -> str:
        slug = self._GMGN.get((self.chain or "solana").lower())
        if not slug:      # chaine non couverte par GMGN (robinhood) -> DexScreener
            return self.dex_url
        return f"https://gmgn.ai/{slug}/token/{self.mint}"

    @property
    def dex_url(self) -> str:
        base = self.pair_address or self.mint
        return f"https://dexscreener.com/{(self.chain or 'solana').lower()}/{base}"

    def pct_move_leg(self) -> Optional[float]:
        if self.swing_low and self.swing_high and self.swing_low > 0:
            return (self.swing_high - self.swing_low) / self.swing_low * 100
        return None

    def to_dict(self) -> Dict[str, Any]:
        d = {k: getattr(self, k) for k in self.__dataclass_fields__}
        d["gmgn_url"] = self.gmgn_url
        d["dex_url"] = self.dex_url
        return d
