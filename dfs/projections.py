"""Projection engine — the "ladder method".

Turns sportsbook over/under props into per-batter fantasy projections.

Key idea: for a counting stat, over_prob at the 0.5 line = P(X>=1), at 1.5 =
P(X>=2), etc. Since E[X] = sum_{k>=1} P(X>=k), the expected value of a counting
stat is just the sum of over_prob across all its rungs.
"""

from __future__ import annotations

import math
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
    e_bb: float = config.BB_RATE
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
            "e_bb": self.e_bb,
            "game": self.game,
            "game_time": self.game_time,
            "flags": list(self.flags),
        }


def _lowest_rung_prob(rungs: list[dict]) -> float:
    """over_prob of the lowest point line (P(1+)). Rungs must be pre-sorted."""
    if not rungs:
        return 0.0
    return float(rungs[0]["over_prob"])


def _expected(rungs: list[dict], uplift: bool = False) -> float:
    """E[X] for a counting stat.

    With multiple rungs (0.5, 1.5, …) this is the ladder sum Σ P(X≥k). With a
    single 0.5 rung (all SGO offers today) it's P(X≥1); for common stats we then
    blend toward the Poisson estimate −ln(1−p) to recover the 2+ games the single
    line misses (config.UPLIFT_FACTOR).
    """
    if not rungs:
        return 0.0
    rungs = sorted(rungs, key=lambda r: float(r["point"]))
    probs = [float(r["over_prob"]) for r in rungs]
    ladder = sum(probs)
    if uplift and len(rungs) == 1 and abs(float(rungs[0]["point"]) - 0.5) < 1e-9:
        p = probs[0]
        if 0.0 < p < 1.0:
            return p + config.UPLIFT_FACTOR * (-math.log(1.0 - p) - p)
    return ladder


def compute_expected_components(
    market_rungs: dict[str, list[dict]], uplift: bool = False
) -> dict[str, float]:
    """Compute E[R], E[1B], E[2B], E[3B], E[HR], E[RBI], E[SB], E[BB] for a batter.

    `market_rungs` maps a normalized_market_key to its list of rung dicts (each
    with `point` and `over_prob`). With `uplift=True`, common stats (R/1B/RBI)
    that have only a single 0.5 line get the multi-hit uplift; rare stats and
    walks always use the raw ladder.
    """
    rungs = {m: sorted(rs, key=lambda r: float(r["point"])) for m, rs in market_rungs.items()}

    def get(market: str) -> list[dict]:
        return rungs.get(market, [])

    e_hr = _expected(get(config.MARKET_HR))
    e_2b = _expected(get(config.MARKET_2B))
    e_3b = _expected(get(config.MARKET_3B))
    e_sb = _expected(get(config.MARKET_SB))
    e_r = _expected(get(config.MARKET_R), uplift=uplift)
    e_rbi = _expected(get(config.MARKET_RBI), uplift=uplift)

    # Singles: prefer a direct singles market; otherwise back out from hits.
    if config.MARKET_1B in rungs:
        e_1b = _expected(get(config.MARKET_1B), uplift=uplift)
    else:
        p1_hits = _lowest_rung_prob(get(config.MARKET_HITS))
        p1_hr = _lowest_rung_prob(get(config.MARKET_HR))
        p1_2b = _lowest_rung_prob(get(config.MARKET_2B))
        p1_3b = _lowest_rung_prob(get(config.MARKET_3B))
        e_1b = max(0.0, p1_hits - p1_hr - p1_2b - p1_3b)

    # Walks: real prop if present, else the flat league baseline.
    e_bb = _expected(get(config.MARKET_BB)) if config.MARKET_BB in rungs else config.BB_RATE

    return {
        "e_r": e_r,
        "e_1b": e_1b,
        "e_2b": e_2b,
        "e_3b": e_3b,
        "e_hr": e_hr,
        "e_rbi": e_rbi,
        "e_sb": e_sb,
        "e_bb": e_bb,
    }


def project_from_components(components: dict[str, float], scoring: dict[str, float] | None = None) -> float:
    """Apply scoring weights (walks from prop, HBP flat baseline); round to 2dp."""
    s = scoring or config.DEFAULT_SCORING
    proj = (
        s["R"] * components["e_r"]
        + s["1B"] * components["e_1b"]
        + s["2B"] * components["e_2b"]
        + s["3B"] * components["e_3b"]
        + s["HR"] * components["e_hr"]
        + s["RBI"] * components["e_rbi"]
        + s["BB"] * components.get("e_bb", config.BB_RATE)
        + s["HBP"] * config.HBP_RATE
        + s["SB"] * components["e_sb"]
    )
    return round(proj, 2)


def compute_pitcher_projection(lines: dict, scoring: dict[str, float] | None = None) -> tuple[float, dict]:
    """Project a pitcher from his prop lines.

    `lines` holds expected values from the market: {outs, k, hits, er, bb} are the
    posted O/U lines (the market's expected value), `win_prob` the fair yes-prob.
    Hit-batsmen use a per-inning baseline; CG/no-hitter/perfect are ~0 and omitted.
    Returns (proj_pts, components).
    """
    s = scoring or config.DEFAULT_PITCHER_SCORING

    def g(key: str) -> float:
        v = lines.get(key)
        return float(v) if v is not None else 0.0

    ip = g("outs") / 3.0
    win = g("win_prob")
    k, hits, er, bb = g("k"), g("hits"), g("er"), g("bb")
    hb = config.HB_RATE_PER_IP * ip
    proj = (s["IP"] * ip + s["W"] * win + s["K"] * k + s["H"] * hits
            + s["ER"] * er + s["BB"] * bb + s["HB"] * hb)
    comps = {"ip": round(ip, 2), "win": round(win, 3), "k": k, "hits": hits,
             "er": er, "bb": bb, "hb": round(hb, 3)}
    return round(proj, 2), comps


def compute_projections(
    df: pd.DataFrame,
    scoring: dict[str, float] | None = None,
    uplift: bool = False,
) -> list[BatterProjection]:
    """Compute projections for every batter in a props DataFrame.

    Handles: per-(player, market) rung grouping, the ladder math, dedupe by
    name (keep highest projection), and game-level auto-flags. `uplift=True`
    applies the multi-hit correction (the app enables it; the pure ladder is the
    default). Returns a list sorted by projection descending.
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
        components = compute_expected_components(markets, uplift=uplift)
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
