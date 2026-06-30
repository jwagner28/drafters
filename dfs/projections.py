"""Projection engine — the "ladder method".

Turns sportsbook over/under props into per-batter fantasy projections.

Key idea: for a counting stat, over_prob at the 0.5 line = P(X>=1), at 1.5 =
P(X>=2), etc. Since E[X] = sum_{k>=1} P(X>=k), the expected value of a counting
stat is just the sum of over_prob across all its rungs.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import pandas as pd

from . import config

# CSV columns we expect for batter props.
REQUIRED_COLUMNS = [
    "player",
    "normalized_market_key",
    "point",
    "over_prob",
]


@dataclass
class BatterProjection:
    player: str
    proj_pts: float
    e_r: float
    e_1b: float
    e_2b: float
    e_3b: float
    e_hr: float
    e_rbi: float
    e_sb: float
    game: str | None = None
    game_time: str | None = None
    flags: list[str] = field(default_factory=list)

    def as_row(self) -> dict:
        return {
            "player": self.player,
            "proj_pts": self.proj_pts,
            "e_r": self.e_r,
            "e_1b": self.e_1b,
            "e_2b": self.e_2b,
            "e_3b": self.e_3b,
            "e_hr": self.e_hr,
            "e_rbi": self.e_rbi,
            "e_sb": self.e_sb,
            "game": self.game,
            "game_time": self.game_time,
            "flags": list(self.flags),
        }


def _sum_over(rungs: list[dict]) -> float:
    """E[X] for a counting stat = sum of over_prob across all rungs."""
    return float(sum(float(r["over_prob"]) for r in rungs))


def _lowest_rung_prob(rungs: list[dict]) -> float:
    """over_prob of the lowest point line (P(1+)). Rungs must be pre-sorted."""
    if not rungs:
        return 0.0
    return float(rungs[0]["over_prob"])


def compute_expected_components(market_rungs: dict[str, list[dict]]) -> dict[str, float]:
    """Compute E[R], E[1B], E[2B], E[3B], E[HR], E[RBI], E[SB] for one batter.

    `market_rungs` maps a normalized_market_key to its list of rung dicts
    (each with at least `point` and `over_prob`). Rungs are sorted ascending by
    point here, so callers don't have to.
    """
    rungs = {m: sorted(rs, key=lambda r: float(r["point"])) for m, rs in market_rungs.items()}

    def get(market: str) -> list[dict]:
        return rungs.get(market, [])

    e_hr = _sum_over(get(config.MARKET_HR))
    e_2b = _sum_over(get(config.MARKET_2B))
    e_3b = _sum_over(get(config.MARKET_3B))
    e_r = _sum_over(get(config.MARKET_R))
    e_rbi = _sum_over(get(config.MARKET_RBI))
    e_sb = _sum_over(get(config.MARKET_SB))

    # Singles: prefer a direct singles market; otherwise back out from hits.
    if config.MARKET_1B in rungs:
        e_1b = _lowest_rung_prob(get(config.MARKET_1B))
    else:
        p1_hits = _lowest_rung_prob(get(config.MARKET_HITS))
        p1_hr = _lowest_rung_prob(get(config.MARKET_HR))
        p1_2b = _lowest_rung_prob(get(config.MARKET_2B))
        p1_3b = _lowest_rung_prob(get(config.MARKET_3B))
        e_1b = max(0.0, p1_hits - p1_hr - p1_2b - p1_3b)

    return {
        "e_r": e_r,
        "e_1b": e_1b,
        "e_2b": e_2b,
        "e_3b": e_3b,
        "e_hr": e_hr,
        "e_rbi": e_rbi,
        "e_sb": e_sb,
    }


def project_from_components(components: dict[str, float], scoring: dict[str, float] | None = None) -> float:
    """Apply scoring weights + flat BB/HBP baselines and round to 2 decimals."""
    s = scoring or config.DEFAULT_SCORING
    proj = (
        s["R"] * components["e_r"]
        + s["1B"] * components["e_1b"]
        + s["2B"] * components["e_2b"]
        + s["3B"] * components["e_3b"]
        + s["HR"] * components["e_hr"]
        + s["RBI"] * components["e_rbi"]
        + s["BB"] * config.BB_RATE
        + s["HBP"] * config.HBP_RATE
        + s["SB"] * components["e_sb"]
    )
    return round(proj, 2)


def compute_projections(
    df: pd.DataFrame,
    scoring: dict[str, float] | None = None,
) -> list[BatterProjection]:
    """Compute projections for every batter in a props DataFrame.

    Handles: per-(player, market) rung grouping, the ladder math, dedupe by
    name (keep highest projection), and game-level auto-flags. Returns a list
    sorted by projection descending.
    """
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"CSV missing required columns: {missing}")

    scoring = scoring or config.DEFAULT_SCORING

    # Group rows: player -> market -> list of rung dicts. Also remember each
    # player's game context (first non-null seen).
    grouped: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    context: dict[str, dict] = {}
    for rec in df.to_dict("records"):
        name = str(rec["player"]).strip()
        market = str(rec["normalized_market_key"]).strip()
        if market not in config.VALID_MARKETS:
            continue
        try:
            rung = {"point": float(rec["point"]), "over_prob": float(rec["over_prob"])}
        except (TypeError, ValueError):
            continue
        grouped[name][market].append(rung)
        if name not in context:
            context[name] = {
                "game": rec.get("game"),
                "game_time": rec.get("commence_time_local"),
            }

    projections: list[BatterProjection] = []
    for name, markets in grouped.items():
        components = compute_expected_components(markets)
        proj = project_from_components(components, scoring)
        ctx = context.get(name, {})
        projections.append(
            BatterProjection(
                player=name,
                proj_pts=proj,
                game=ctx.get("game"),
                game_time=ctx.get("game_time"),
                **components,
            )
        )

    projections = _dedupe_keep_highest(projections)
    _apply_game_flags(projections)
    projections.sort(key=lambda p: p.proj_pts, reverse=True)
    return projections


def _dedupe_keep_highest(projections: list[BatterProjection]) -> list[BatterProjection]:
    """Dedupe batters by (case-insensitive) name, keeping the highest projection."""
    best: dict[str, BatterProjection] = {}
    for p in projections:
        key = " ".join(p.player.strip().lower().split())
        cur = best.get(key)
        if cur is None or p.proj_pts > cur.proj_pts:
            best[key] = p
    return list(best.values())


def _apply_game_flags(projections: list[BatterProjection]) -> None:
    """Flag games that look in-progress (mostly ~0) or inflated (many 9+)."""
    by_game: dict[str, list[BatterProjection]] = defaultdict(list)
    for p in projections:
        if p.game:
            by_game[p.game].append(p)

    for game, players in by_game.items():
        if not players:
            continue
        near_zero = sum(1 for p in players if p.proj_pts < config.NEAR_ZERO_THRESHOLD)
        inflated = sum(1 for p in players if p.proj_pts >= config.INFLATED_THRESHOLD)
        if len(players) >= 2 and near_zero / len(players) >= config.NEAR_ZERO_SHARE:
            for p in players:
                p.flags.append("in_progress")
        if inflated >= config.INFLATED_MIN_COUNT:
            for p in players:
                p.flags.append("inflated")


def projections_to_dataframe(projections: list[BatterProjection]) -> pd.DataFrame:
    """Render projections as a tidy DataFrame for the UI / export."""
    rows = [p.as_row() for p in projections]
    df = pd.DataFrame(rows)
    if not df.empty:
        df["flags"] = df["flags"].apply(lambda fs: ", ".join(fs))
    return df
