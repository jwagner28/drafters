"""Template scouting reports (no LLM).

Turns an opponent's tendency numbers into plain-English sentences: how they open,
their value discipline, favorite players/teams, stack-vs-diversify, and one
exploitable weakness.
"""

from __future__ import annotations


def _pct(x: float) -> str:
    return f"{round(x * 100)}%"


def _opening_sentence(t: dict) -> str:
    r12 = t.get("round_buckets", {}).get("R1-2")
    pitchers = t.get("pitchers", {})
    fp = pitchers.get("first_pitcher_avg_round")
    if not r12:
        return "Not enough early-round data to read their opening yet."
    hitters = r12.get("IF", 0) + r12.get("OF", 0) + r12.get("HT", 0)
    p_share = r12.get("P", 0)
    if p_share >= 0.34:
        open_txt = f"opens with pitching ({_pct(p_share)} of their first two picks are arms)"
    elif hitters >= 0.8:
        open_txt = "opens with bats almost exclusively"
    else:
        open_txt = f"leans hitter early ({_pct(hitters)} bats in rounds 1–2)"
    if fp is not None:
        open_txt += f", taking their first pitcher around round {fp:g}"
    return "They " + open_txt + "."


def _value_sentence(t: dict) -> str:
    v = t.get("value")
    if not v:
        return "No value-discipline read yet."
    pos_pct = v.get("avg_pos_pct")
    overall = v.get("avg_overall_rank")
    if pos_pct is None:
        return "No value-discipline read yet."
    if pos_pct <= 0.12:
        disc = "very disciplined — they almost always take one of the best available at the position"
    elif pos_pct <= 0.3:
        disc = "fairly disciplined — usually near the top of the board at the position"
    else:
        disc = "a reacher — they routinely pass over higher-projected players"
    return (f"On value they're {disc} "
            f"(avg in-position rank ≈ {v.get('avg_pos_rank')}, overall ≈ {overall}).")


def _players_sentence(t: dict) -> str:
    aff = [a for a in t.get("player_affinity", []) if a["affinity"] >= 0.4 and a["drafted"] >= 1]
    if not aff:
        return "No standout player loyalties."
    names = ", ".join(a["name"] for a in aff[:4])
    return f"They gravitate to specific players: {names}."


def _teams_sentence(t: dict) -> str:
    teams = [x for x in t.get("team_affinity", []) if x.get("ratio") and x["ratio"] >= 1.5 and x["picks"] >= 2]
    if not teams:
        return "No strong team bias."
    parts = [f"{x['team']} ({x['ratio']:g}× the league rate)" for x in teams[:3]]
    return "Team bias toward " + ", ".join(parts) + "."


def _stacking_sentence(t: dict) -> str:
    s = t.get("stacking")
    if not s or s.get("coefficient") is None:
        return "No clear stacking signal yet."
    c = s["coefficient"]
    if c >= 1.3:
        return f"They stack teammates aggressively ({c:g}× the baseline rate of pairing same-team players)."
    if c <= 0.7:
        return f"They deliberately diversify ({c:g}× baseline — they spread across teams)."
    return f"They stack about as often as chance ({c:g}× baseline)."


def _weakness_sentence(t: dict) -> str:
    v = t.get("value")
    s = t.get("stacking")
    r12 = t.get("round_buckets", {}).get("R1-2", {})
    teams = [x for x in t.get("team_affinity", []) if x.get("ratio") and x["ratio"] >= 1.8 and x["picks"] >= 2]

    if v and v.get("avg_pos_pct") is not None and v["avg_pos_pct"] >= 0.3:
        return ("Exploit: they reach, so premium value slides — hold your board and let "
                "top-projected players fall to you instead of reaching back.")
    if s and s.get("coefficient") and s["coefficient"] >= 1.3 and teams:
        t0 = teams[0]["team"]
        return (f"Exploit: they over-stack — grabbing a {t0} player or two before them can "
                "blow up the stack they're chasing.")
    if r12 and r12.get("P", 0) >= 0.34:
        return ("Exploit: they spend early capital on pitching — the top hitters will be there "
                "for you a little longer than usual.")
    if r12 and (r12.get("IF", 0) + r12.get("OF", 0) + r12.get("HT", 0)) >= 0.8:
        return ("Exploit: they ignore pitching early — the scarce ace tier can run dry, so you "
                "can corner one elite arm without much competition.")
    return "Exploit: no glaring hole yet — keep drafting best-available and revisit after more data."


def scouting_report(tendencies: dict | None) -> dict:
    """Return {'summary': [sentences...], 'has_data': bool} for an opponent."""
    if not tendencies or tendencies.get("n_picks", 0) == 0:
        return {"has_data": False, "summary": ["No draft history for this opponent yet."]}
    return {
        "has_data": True,
        "n_contests": tendencies.get("n_contests", 0),
        "summary": [
            _opening_sentence(tendencies),
            _value_sentence(tendencies),
            _players_sentence(tendencies),
            _teams_sentence(tendencies),
            _stacking_sentence(tendencies),
            _weakness_sentence(tendencies),
        ],
    }
