#!/usr/bin/env python3
"""
Sleeper Live Draft Agent
========================
Algorithmic pick recommender with six-team full-PPR defaults
(slot 5, snake). Scores players with the league's custom scoring,
computes value over replacement, positional scarcity, tier drop-offs, roster
need, and injury risk — then watches the live draft room and tells you what
to pick the moment you're on the clock.

Usage:
  python3 draft_agent.py board                     # pre-draft cheat sheet (also writes CSV)
  python3 draft_agent.py mock  [--slot 5]           # simulate a full draft to test the agent
  python3 draft_agent.py live  <draft_url_or_id> [--slot 5]   # draft night
  python3 draft_agent.py refresh                   # force re-download of data

Data comes live from Sleeper's public API (projections + injury statuses),
cached in ./data so a restart mid-draft is instant. No API key needed.
"""

import argparse
import csv
import json
import math
import os
import random
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta

# ----------------------------------------------------------------------------
# League configuration (override with --league or a live draft URL)
# ----------------------------------------------------------------------------
SEASON = "2026"

# Your league. With these set, every command self-configures from Sleeper:
# scoring, roster slots, team count and round count are read from the league
# itself, so the hardcoded defaults below are only a fallback.
LEAGUE_ID = None  # New league: supply --league or a live draft URL.
DRAFT_ID = None
MY_USERNAME = None  # Username lookup is opt-in with --user.

TEAMS = 6
MY_SLOT = 5
ROUNDS = 15  # 9 starters + 6 bench

STARTERS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DEF": 1}
FLEX_ELIGIBLE = ("RB", "WR", "TE")

# Roster caps for the default 15-round single-QB league.
POSITION_CAPS = {"QB": 2, "RB": 6, "WR": 7, "TE": 2, "K": 1, "DEF": 1}

# Fallback scoring, verified against the live league on 2026-08-24. When
# LEAGUE_ID is set these are replaced wholesale by the league's own settings.
SCORING = {
    # Passing
    "pass_yd": 0.04, "pass_td": 4.0, "pass_int": -2.0, "pass_2pt": 2.0,
    # Rushing
    "rush_yd": 0.1, "rush_td": 6.0, "rush_2pt": 2.0,
    # Receiving (full PPR)
    "rec": 1.0, "rec_yd": 0.1, "rec_td": 6.0, "rec_2pt": 2.0,
    # Turnovers — this league DOES penalize fumbles lost (-2). It is not
    # shown on the Passing tab, which is why it was missed originally.
    "fum_lost": -2.0,
    # Kicking. Sleeper only projects 40+ yard field goals, so the short
    # bands below rarely fire; they are here so the table stays correct.
    "xpm": 1.0, "xpmiss": -1.0, "fgmiss": -1.0,
    "fgm_0_19": 3.0, "fgm_20_29": 3.0, "fgm_30_39": 3.0,
    "fgm_40_49": 4.0, "fgm_50p": 5.0,
    # Team defense
    "sack": 1.0, "int": 2.0, "fum_rec": 2.0, "ff": 0.0, "def_td": 6.0,
    "safe": 2.0, "blk_kick": 2.0, "def_st_td": 6.0, "st_td": 6.0,
    "fum_rec_td": 6.0, "st_ff": 1.0, "st_fum_rec": 1.0,
    "def_st_ff": 1.0, "def_st_fum_rec": 1.0, "def_4_and_stop": 1.0,
    "pts_allow_0": 10.0, "pts_allow_1_6": 7.0, "pts_allow_7_13": 4.0,
    "pts_allow_14_20": 1.0, "pts_allow_21_27": 0.0, "pts_allow_28_34": -1.0,
    "pts_allow_35p": -4.0,
}

# Sleeper's PROJECTION feed uses some stat keys that its SCORING settings
# don't, so map projection keys onto the scored key that covers them.
STAT_ALIASES = {
    "def_fum_td": "fum_rec_td",
    "def_kr_td": "st_td",
    "def_pr_td": "st_td",
    "fgmiss_0_19": "fgmiss", "fgmiss_20_29": "fgmiss", "fgmiss_30_39": "fgmiss",
    "fgmiss_40_49": "fgmiss", "fgmiss_50p": "fgmiss",
}

POSITIONS = ["QB", "RB", "WR", "TE", "K", "DEF"]

# Expected missed games when no verified return date is available. These are
# planning assumptions, not diagnoses; a weekly Out tag is not a season loss.
INJURY_MISSED_GAMES = {
    "IR": 4, "Out": 1, "PUP": 4, "Sus": 2, "COV": 1,
    "Doubtful": 0.75, "DNR": 4, "NA": 0.5, "Questionable": 0.25,
}
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DEFAULT_NEWS = None  # Personal, time-sensitive news is opt-in with --news.
CACHE_HOURS = 1  # data re-fetched hourly; live mode refreshes at startup

# In a 1-QB league you can wait on QB (and to a lesser degree TE): the position
# refills every round, while the RB/WR cliffs are real.
POS_WEIGHT = {"QB": 0.80, "TE": 0.92, "RB": 1.0, "WR": 1.0, "K": 1.0, "DEF": 1.0}

# Lookahead: how many Monte Carlo draws of "who survives to my next pick",
# and how much the value of that next pick counts toward this decision.
LOOKAHEAD_SIMS = 150
LOOKAHEAD_WEIGHT = 0.85

# How much to trust the market (ADP) over Sleeper's own projection.
# 0.0 = projections only (single-source risk); 1.0 = pure ADP (no edge).
# Sleeper's projections are measurably bearish on running backs relative to
# consensus, so a modest blend hedges that without giving up the edge.
MARKET_BLEND = 0.15  # preseason ADP is a weaker signal once games have started

# ANSI colors
def _c(code, s):
    return f"\033[{code}m{s}\033[0m" if sys.stdout.isatty() else str(s)
def bold(s): return _c("1", s)
def green(s): return _c("32", s)
def yellow(s): return _c("33", s)
def red(s): return _c("31", s)
def cyan(s): return _c("36", s)
def dim(s): return _c("2", s)


# ----------------------------------------------------------------------------
# Data fetching (Sleeper public API) with on-disk cache
# ----------------------------------------------------------------------------
def _http_get_json(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "draft-agent/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _cache_path(name):
    os.makedirs(DATA_DIR, exist_ok=True)
    return os.path.join(DATA_DIR, name)


def _load_cache(name, max_age_hours=CACHE_HOURS):
    path = _cache_path(name)
    if os.path.exists(path):
        age = time.time() - os.path.getmtime(path)
        if age < max_age_hours * 3600:
            try:
                with open(path) as f:
                    return json.load(f)
            except Exception:
                return None
    return None


def _save_cache(name, obj):
    with open(_cache_path(name), "w") as f:
        json.dump(obj, f)


def fetch_league(league_id, force=False):
    """League config straight from Sleeper: scoring, roster slots, size."""
    name = f"league_{league_id}.json"
    if not force:
        cached = _load_cache(name)
        if cached:
            return cached
    data = _http_get_json(f"https://api.sleeper.app/v1/league/{league_id}", timeout=30)
    _save_cache(name, data)
    return data


def apply_league_config(league_id, force=False, quiet=False):
    """Overwrite the hardcoded league settings with the real ones.

    This is the single most important correctness feature in the tool: it
    removes any chance of the scoring table drifting from the actual league.
    Explicit league requests fail if their scoring cannot be verified."""
    global SCORING, STARTERS, ROUNDS, TEAMS
    try:
        lg = fetch_league(league_id, force)
    except Exception as e:
        if not quiet:
            print(yellow(f"Could not read league {league_id} ({e})."))
        raise RuntimeError(f"Cannot verify scoring for league {league_id}") from e

    if not isinstance(lg, dict) or not lg.get("scoring_settings") or not lg.get("roster_positions"):
        raise ValueError(f"League {league_id} has no usable scoring/roster settings")
    unsupported = set(lg["roster_positions"]) - set(POSITIONS + ["FLEX", "BN"])
    if unsupported:
        raise ValueError(f"Unsupported roster slots: {sorted(unsupported)}")

    sc = lg.get("scoring_settings") or {}
    if sc:
        SCORING = {k: float(v) for k, v in sc.items()
                   if isinstance(v, (int, float))}

    rp = lg.get("roster_positions") or []
    if rp:
        starters = {"QB": 0, "RB": 0, "WR": 0, "TE": 0, "FLEX": 0, "K": 0, "DEF": 0}
        for slot in rp:
            if slot == "BN":
                continue
            key = {"SUPER_FLEX": "FLEX", "REC_FLEX": "FLEX",
                   "WRRB_FLEX": "FLEX", "IDP_FLEX": "FLEX"}.get(slot, slot)
            if key in starters:
                starters[key] += 1
        if sum(starters.values()) > 0:
            STARTERS = {k: v for k, v in starters.items()}
        ROUNDS = len(rp)
        if "SUPER_FLEX" in rp:
            POS_WEIGHT["QB"] = 1.15  # QBs become scarce in superflex

    if lg.get("total_rosters"):
        TEAMS = int(lg["total_rosters"])

    if not quiet:
        line = ", ".join(f"{k}{v}" for k, v in STARTERS.items() if v)
        print(dim(f"Loaded league '{lg.get('name','?')}': {TEAMS} teams, "
                  f"{ROUNDS} rounds, {line}, {len(SCORING)} scoring rules"))
    return True


def fetch_players(force=False):
    """All NFL players: injury_status, team, position, age, depth chart."""
    if not force:
        cached = _load_cache("players.json")
        if cached:
            return cached
    try:
        print(dim("Fetching NFL player database from Sleeper (~5MB, one time)..."))
        data = _http_get_json("https://api.sleeper.app/v1/players/nfl", timeout=120)
        _save_cache("players.json", data)
        return data
    except Exception as e:
        # Not fatal: the projection feed carries name, team, position and
        # injury status for every player it returns.
        print(yellow(f"Player database unavailable ({e}); "
                     "continuing with projection data only."))
        return {}


def fetch_byes(season=SEASON, force=False):
    """Bye week per NFL team = the week that team has no scheduled game.

    Sleeper has no bye endpoint, so it is derived from the schedule."""
    if not force:
        cached = _load_cache(f"byes_{season}.json", 24 * 14)
        if cached:
            return cached
    try:
        sched = _http_get_json(
            f"https://api.sleeper.app/schedule/nfl/regular/{season}", timeout=30)
    except Exception as e:
        print(yellow(f"Could not load the NFL schedule ({e}); "
                     "bye weeks will be ignored."))
        return {}
    weeks = {}
    for gm in sched:
        for t in (gm.get("home"), gm.get("away")):
            if t:
                weeks.setdefault(t, set()).add(gm.get("week"))
    byes = {}
    for t, played in weeks.items():
        for w in range(1, 19):
            if w not in played:
                byes[t] = w
                break
    _save_cache(f"byes_{season}.json", byes)
    return byes


def fetch_projections(force=False):
    """Season projections with stats + ADP. Returns list of records."""
    if not force:
        cached = _load_cache(f"projections_{SEASON}.json")
        if cached:
            return cached
    pos_q = "&".join(f"position[]={p}" for p in POSITIONS)
    urls = [
        f"https://api.sleeper.com/projections/nfl/{SEASON}?season_type=regular&{pos_q}&order_by=adp_ppr",
        f"https://api.sleeper.app/projections/nfl/{SEASON}?season_type=regular&{pos_q}&order_by=adp_ppr",
    ]
    last_err = None
    for url in urls:
        try:
            print(dim(f"Fetching {SEASON} season projections..."))
            data = _http_get_json(url, timeout=60)
            if isinstance(data, list) and data:
                _save_cache(f"projections_{SEASON}.json", data)
                return data
        except Exception as e:
            last_err = e
    # Fallback: old-style endpoint returns {player_id: {stat: val}}
    try:
        url = f"https://api.sleeper.app/v1/projections/nfl/regular/{SEASON}"
        print(dim("Primary projections endpoint failed; trying legacy endpoint..."))
        raw = _http_get_json(url, timeout=120)
        data = [{"player_id": pid, "stats": st} for pid, st in raw.items()
                if isinstance(st, dict)]
        if data:
            _save_cache(f"projections_{SEASON}.json", data)
            return data
    except Exception as e:
        last_err = e
    raise RuntimeError(f"Could not fetch projections from Sleeper: {last_err}")


def fetch_recent_results(force=False, start_week=None):
    """Only use weeks before the scoring horizon, never partial current games."""
    if start_week is None:
        state = _http_get_json("https://api.sleeper.app/v1/state/nfl", timeout=20)
        if str(state.get("season")) != SEASON:
            raise RuntimeError(f"Sleeper season differs from {SEASON}; use --start-week explicitly")
        start_week = int(state.get("week") or 1) if state.get("season_type") == "regular" else 1
    if not 1 <= start_week <= 18:
        raise ValueError("start week must be between 1 and 18")
    results = {}
    # Last four completed weeks capture recent roles; older weeks add little
    # to this intentionally conservative form adjustment.
    for week in range(max(1, start_week - 4), start_week):
        name = f"stats_{SEASON}_{week}.json"
        data = None if force else _load_cache(name)
        if data is None:
            data = _http_get_json(
                f"https://api.sleeper.app/stats/nfl/{SEASON}/{week}?season_type=regular", timeout=30)
            if not isinstance(data, list) or not data:
                raise RuntimeError(f"Week {week} stats missing; refusing a silently preseason-only board")
            _save_cache(name, data)
        for rec in data:
            if str(rec.get("season")) != SEASON or rec.get("week") != week:
                continue
            stats = rec.get("stats") or {}
            if (stats.get("gp") or stats.get("gms_active") or 0) > 0:
                results.setdefault(str(rec.get("player_id")), []).append(stats)
    return start_week, results


def apply_current_form(pool, results, start_week):
    """Shrink recent PPG toward the season prior, then value future games only.

    At one game, form gets 1/7 weight. Even an extreme box score can change
    the prior by at most 7.1%. At four games, its weight reaches 40%.
    """
    for p in pool.values():
        # Team-defense season records carry gp=1 despite season-long totals.
        prior_games = 17.0 if p.pos == "DEF" else min(17.0, p.games or 17.0)
        prior_ppg = p.points / prior_games
        rows = results.get(p.pid, [])
        p.recent_games = len(rows)
        if rows:
            p.recent_ppg = sum(score_stats(row, p.pos) for row in rows) / len(rows)
            weight = min(0.4, len(rows) / (len(rows) + 6.0))
            bounded = max(prior_ppg * 0.5, min(prior_ppg * 1.5, p.recent_ppg))
            prior_ppg = (1 - weight) * prior_ppg + weight * bounded
            touches = sum(row.get("rush_att", 0) + row.get("rec_tgt", 0) for row in rows)
            if p.pos in ("RB", "WR", "TE"):
                p.form_note = f"{len(rows)} recent game(s): {p.recent_ppg:.1f} PPG, {touches / len(rows):.1f} carries+targets/game"
            else:
                p.form_note = f"{len(rows)} recent game(s): {p.recent_ppg:.1f} PPG"
        p.start_week = start_week
        future_bye = p.bye is not None and p.bye >= start_week
        # If schedule is unavailable, conservatively reserve a bye.
        p.remaining_games = max(0, 19 - start_week - int(future_bye or p.bye is None))
        p.points = prior_ppg * p.remaining_games
    return pool


def apply_news_overrides(pool, path):
    """Verified manual news can override generic availability assumptions."""
    if not path:
        return
    with open(path) as f:
        overrides = json.load(f)
    for pid, info in overrides.items():
        if pid not in pool:
            raise ValueError(f"News override player ID {pid} not on this board")
        if not info.get("source") or not info.get("as_of") or not info.get("note"):
            raise ValueError(f"News override {pid} needs source, as_of, and note")
        age = (datetime.now().date() - datetime.fromisoformat(info["as_of"]).date()).days
        if age < 0 or age > 7:
            raise ValueError(f"News override {pid} must be reviewed within the last 7 days")
        p = pool[pid]
        if info.get("out_for_season") is True:
            p.missed_games = p.remaining_games
        elif "missed_games" in info:
            missed = info["missed_games"]
            if isinstance(missed, bool) or not isinstance(missed, (int, float)) or not math.isfinite(missed) or missed < 0:
                raise ValueError(f"News override {pid}: missed_games must be nonnegative")
            p.missed_games = min(p.remaining_games, missed)
        elif "return_week" in info:
            return_week = info["return_week"]
            if isinstance(return_week, bool) or not isinstance(return_week, int) or not 1 <= return_week <= 19:
                raise ValueError(f"News override {pid}: return_week must be 1–19")
            missed = max(0, return_week - p.start_week)
            if p.bye is not None and p.start_week <= p.bye < return_week:
                missed -= 1
            p.missed_games = min(p.remaining_games, missed)
        else:
            raise ValueError(f"News override {pid} needs out_for_season=true, return_week, or missed_games")
        p.news_note = f"{info['note']} ({info['as_of']}; {info['source']})"


# ----------------------------------------------------------------------------
# Player model
# ----------------------------------------------------------------------------
class Player:
    __slots__ = ("pid", "name", "pos", "team", "points", "adp", "injury",
                 "age", "years_exp", "status", "tier", "vorp", "games", "bye",
                 "remaining_games", "start_week", "missed_games", "recent_games",
                 "recent_ppg", "form_note", "news_note")

    def __init__(self, pid, name, pos, team):
        self.pid = pid
        self.name = name
        self.pos = pos
        self.team = team or "FA"
        self.points = 0.0
        self.adp = 999.0
        self.injury = None
        self.age = None
        self.years_exp = None
        self.status = None
        self.tier = None
        self.vorp = 0.0
        self.games = None
        self.bye = None
        self.remaining_games = 17
        self.start_week = 1
        self.missed_games = None
        self.recent_games = 0
        self.recent_ppg = None
        self.form_note = ""
        self.news_note = ""

    @property
    def adj_points(self):
        """Expected future points with explicit availability assumptions."""
        missed = self.missed_games
        if missed is None:
            missed = INJURY_MISSED_GAMES.get(self.injury or "", 0)
        return self.points * max(0.0, 1 - missed / max(1, self.remaining_games))

    def injury_note(self):
        if self.news_note:
            return yellow(f"[NEWS: assumes {self.missed_games:g} missed games; {self.news_note}]")
        if not self.injury:
            return ""
        sev = {"IR": red, "Out": red, "PUP": red, "DNR": red,
               "Doubtful": red, "Sus": yellow, "COV": yellow,
               "Questionable": yellow, "NA": yellow}
        color = sev.get(self.injury, yellow)
        missed = INJURY_MISSED_GAMES.get(self.injury, 0)
        return color(f"[{self.injury.upper()}: assumes {missed:g} missed games]")


def score_stats(stats, pos):
    """Apply the league's exact custom scoring to a projected stat line.

    Any projection key with no scoring entry contributes zero, so an unknown
    stat is ignored rather than guessed at."""
    pts = 0.0
    for stat, val in (stats or {}).items():
        if not isinstance(val, (int, float)):
            continue
        w = SCORING.get(stat)
        if w is None:
            w = SCORING.get(STAT_ALIASES.get(stat, ""), None)
        if w:
            pts += w * val
    return pts


def apply_market_blend(pool, weight=MARKET_BLEND):
    """Blend each projection toward what the market implies for that player.

    Within a position, the player taken k-th by ADP is nudged toward the
    points of the k-th-best projected player at that position. If projections
    and the market agree on the ordering, nothing moves. Where they disagree
    the player is pulled toward consensus, which limits the damage when the
    single projection source is wrong.

    This matters here: Sleeper's 2026 projections rank Ashton Jeanty 59th
    while the market drafts him 14th — and that pattern repeats across RBs,
    which is a large part of why an unblended board drafts so few of them."""
    if weight <= 0:
        return pool
    for pos in POSITIONS:
        ps = [p for p in pool.values() if p.pos == pos and p.adp < 900]
        if len(ps) < 8:
            continue
        by_adp = sorted(ps, key=lambda x: x.adp)
        by_pts = sorted(ps, key=lambda x: -x.points)
        implied = [q.points for q in by_pts]
        for i, p in enumerate(by_adp):
            p.points = (1.0 - weight) * p.points + weight * implied[i]
    return pool


def build_player_pool(projections, players_db):
    pool = {}
    for rec in projections:
        pid = str(rec.get("player_id"))
        stats = rec.get("stats") or {}
        meta = rec.get("player") or {}
        db = players_db.get(pid, {}) if isinstance(players_db, dict) else {}
        pos = (meta.get("position") or db.get("position") or
               (db.get("fantasy_positions") or [None])[0])
        if pos not in POSITIONS:
            continue
        if pos == "DEF" or (pid and not pid.isdigit()):
            name = db.get("full_name") or f"{meta.get('first_name','')} {meta.get('last_name','')}".strip() or pid
            if pos == "DEF":
                name = f"{db.get('first_name', pid)} {db.get('last_name', 'D/ST')}".strip()
        else:
            name = (db.get("full_name") or
                    f"{meta.get('first_name','')} {meta.get('last_name','')}".strip() or pid)
        team = meta.get("team") or db.get("team")
        p = Player(pid, name, pos, team)
        p.points = score_stats(stats, pos)
        adp = (stats.get("adp_ppr") or stats.get("adp_half_ppr") or
               stats.get("adp_std") or stats.get("adp_2qb"))
        if isinstance(adp, (int, float)) and adp > 0:
            p.adp = float(adp)
        gp = stats.get("gp") or stats.get("gms_active")
        if isinstance(gp, (int, float)) and gp > 0:
            p.games = 17.0 if pos == "DEF" else float(gp)
        # A null designation in the newer record explicitly clears an old
        # injury. Using `new or old` incorrectly resurrects cleared injuries.
        sources = sorted((meta, db), key=lambda x: x.get("news_updated") or 0, reverse=True)
        p.injury = next((src["injury_status"] for src in sources if "injury_status" in src), None)
        p.status = db.get("status")
        p.age = db.get("age")
        p.years_exp = db.get("years_exp")
        # Skip players who are clearly not draftable
        if p.points <= 0:
            continue
        if p.status in ("Inactive",) and pos != "DEF":
            continue
        pool[pid] = p

    # Players with no ADP: synthesize one from custom-scored rank so sorting works
    ranked = sorted(pool.values(), key=lambda x: -x.adj_points)
    for i, p in enumerate(ranked):
        if p.adp >= 999.0:
            p.adp = 300.0 + i
    return pool


# ----------------------------------------------------------------------------
# Value model: replacement levels, VORP, tiers
# ----------------------------------------------------------------------------
def compute_replacement_levels(pool, teams=None, rounds=None):
    """Replacement = the player you could get off waivers / late bench.
    Starter demand follows the configured lineup and actual team count.
    A scaled bench cushion reflects additional RB/WR demand."""
    teams = TEAMS if teams is None else teams
    rounds = ROUNDS if rounds is None else rounds
    by_pos = {pos: sorted([p for p in pool.values() if p.pos == pos],
                          key=lambda x: -x.adj_points) for pos in POSITIONS}

    demand = {pos: STARTERS.get(pos, 0) * teams for pos in POSITIONS}
    # Allocate flex demand using this league's roster slots and team count.
    flex_pool = []
    for pos in FLEX_ELIGIBLE:
        flex_pool.extend(by_pos[pos][demand[pos]:])
    flex_pool.sort(key=lambda x: -x.adj_points)
    for p in flex_pool[:STARTERS["FLEX"] * teams]:
        demand[p.pos] += 1

    # Bench cushion: RB/WR get drafted well past starter demand
    bench = max(0, rounds - sum(STARTERS.values()))
    scale = teams / 12 * bench / 6
    cushion = {pos: round(n * scale) for pos, n in
               {"QB": 2, "RB": 8, "WR": 8, "TE": 2, "K": 0, "DEF": 0}.items()}
    repl = {}
    for pos in POSITIONS:
        idx = min(demand[pos] + cushion[pos], len(by_pos[pos]) - 1)
        repl[pos] = by_pos[pos][idx].adj_points if by_pos[pos] else 0.0
    return repl, by_pos


def assign_tiers(by_pos):
    """Gap-based tiers within each position (a big projected-points drop
    starts a new tier)."""
    thresholds = {"QB": 14, "RB": 12, "WR": 11, "TE": 14, "K": 6, "DEF": 6}
    for pos, players in by_pos.items():
        tier = 1
        for i, p in enumerate(players[:60]):
            if i > 0:
                gap = players[i - 1].adj_points - p.adj_points
                if gap >= thresholds.get(pos, 12):
                    tier += 1
            p.tier = tier
        for p in players[60:]:
            p.tier = 99


# ----------------------------------------------------------------------------
# Draft state + recommendation engine
# ----------------------------------------------------------------------------
class DraftState:
    def __init__(self, pool, teams=None, rounds=None, my_slot=None):
        teams = TEAMS if teams is None else teams
        rounds = ROUNDS if rounds is None else rounds
        my_slot = MY_SLOT if my_slot is None else my_slot
        if teams < 2 or not 1 <= my_slot <= teams:
            raise ValueError(f"slot must be between 1 and {teams}; got {my_slot}")
        if rounds < sum(STARTERS.values()):
            raise ValueError("draft has fewer rounds than required starters")
        self.pool = pool
        self.teams = teams
        self.rounds = rounds
        self.my_slot = my_slot
        self.taken = {}          # pid -> pick_no
        self.rosters = {s: [] for s in range(1, teams + 1)}  # slot -> [Player]
        self.repl, self.by_pos = compute_replacement_levels(pool, teams, rounds)
        assign_tiers(self.by_pos)
        for p in pool.values():
            p.vorp = p.adj_points - self.repl.get(p.pos, 0.0)

    # --- snake math ---
    def slot_for_pick(self, pick_no):
        rnd = (pick_no - 1) // self.teams + 1
        idx = (pick_no - 1) % self.teams
        return (idx + 1) if rnd % 2 == 1 else (self.teams - idx)

    def my_pick_numbers(self):
        picks = []
        for rnd in range(1, self.rounds + 1):
            if rnd % 2 == 1:
                picks.append((rnd - 1) * self.teams + self.my_slot)
            else:
                picks.append(rnd * self.teams - self.my_slot + 1)
        return picks

    def next_my_pick(self, current_pick):
        for pk in self.my_pick_numbers():
            if pk >= current_pick:
                return pk
        return None

    def following_my_pick(self, current_pick):
        """My next pick strictly after this one (the lookahead target)."""
        mine = [pk for pk in self.my_pick_numbers() if pk > current_pick]
        return mine[0] if mine else None

    # --- state updates ---
    def record_pick(self, pid, pick_no):
        pid = str(pid)
        if pid in self.taken:
            return
        self.taken[pid] = pick_no
        slot = self.slot_for_pick(pick_no)
        p = self.pool.get(pid)
        if p:
            self.rosters[slot].append(p)

    def available(self):
        return [p for p in self.pool.values() if p.pid not in self.taken]

    def my_roster(self):
        return self.rosters[self.my_slot]

    # --- roster accounting ---
    def roster_counts(self, roster):
        c = {pos: 0 for pos in POSITIONS}
        for p in roster:
            c[p.pos] += 1
        return c

    def starters_unfilled(self, roster):
        c = self.roster_counts(roster)
        need = {}
        need["QB"] = max(0, STARTERS["QB"] - c["QB"])
        need["TE"] = max(0, STARTERS["TE"] - c["TE"])
        need["K"] = max(0, STARTERS["K"] - c["K"])
        need["DEF"] = max(0, STARTERS["DEF"] - c["DEF"])
        rb_extra = max(0, c["RB"] - STARTERS["RB"])
        wr_extra = max(0, c["WR"] - STARTERS["WR"])
        te_extra = max(0, c["TE"] - STARTERS["TE"])
        flex_filled = min(STARTERS["FLEX"], rb_extra + wr_extra + te_extra)
        need["RB"] = max(0, STARTERS["RB"] - c["RB"])
        need["WR"] = max(0, STARTERS["WR"] - c["WR"])
        need["FLEX"] = STARTERS["FLEX"] - flex_filled
        return need

    # ------------------------------------------------------------------
    # Survival model: will this player still be there at my next pick?
    # ------------------------------------------------------------------
    @staticmethod
    def _phi(x):
        """Logistic CDF, used as the 'has he been drafted yet' curve.

        Deliberately heavier-tailed than a normal: real drafts produce
        occasional big slides and big reaches, and a Gaussian tail collapses
        to exactly 0/1 a few picks out, which makes the conditional
        probability below degenerate."""
        z = max(-40.0, min(40.0, x * 1.7))  # 1.7 ≈ logistic-to-normal scale
        return 1.0 / (1.0 + math.exp(-z))

    @staticmethod
    def _adp_sigma(adp):
        """How noisy is the market around a player's ADP? Early picks are
        tightly clustered; later picks vary a lot between drafters."""
        return max(4.0, min(4.0 + 0.18 * adp, 22.0))

    def survival_prob(self, player, target_pick, current_pick):
        """P(player is still on the board at target_pick | on the board now).
        Conditioning on 'available now' matters: a guy who has already slid
        past his ADP is more likely to keep sliding than his raw ADP says."""
        s = self._adp_sigma(player.adp)
        p_gone_by_target = self._phi((target_pick - player.adp) / s)
        p_gone_by_now = self._phi((current_pick - player.adp) / s)
        surv_target = 1.0 - p_gone_by_target
        surv_now = max(1.0 - p_gone_by_now, 1e-12)
        return max(0.0, min(surv_target / surv_now, 1.0))

    def _simulate_survivors(self, avail, target_pick, current_pick,
                            n_sims=LOOKAHEAD_SIMS, pool_size=90, rng=None):
        """Monte Carlo: draw which players survive to target_pick, and record
        the best 2 survivors at each position in each simulation."""
        rng = rng or random.Random(1234)
        pool = avail[:pool_size]
        probs = [(p, self.survival_prob(p, target_pick, current_pick)) for p in pool]
        sims = []
        for _ in range(n_sims):
            best = {pos: [] for pos in POSITIONS}
            for p, pr in probs:
                if rng.random() < pr and len(best[p.pos]) < 2:
                    best[p.pos].append(p)
            sims.append(best)
        return sims

    # ------------------------------------------------------------------
    # Roster-fit scoring, factored out so the lookahead can reuse it
    # ------------------------------------------------------------------
    def _depth_discount(self, pos, n_after):
        """How much of a player's value you will actually collect.

        VORP measures what a player is worth *when he starts*. Your 6th WR
        starts almost never, so most of his VORP is unrealised. This decays
        value once you pass the number of lineup slots the position can fill —
        which is what stops the board hoarding one position."""
        slots = STARTERS.get(pos, 0)
        if pos in FLEX_ELIGIBLE:
            slots += STARTERS.get("FLEX", 0)
        if n_after <= slots:
            return 1.0
        return max(0.15, 0.6 ** (n_after - slots))

    def _fit_adjust(self, p, counts, need):
        """Bonus/penalty for how a player fits the roster. Returns (delta, reason)."""
        pos_need = need.get(p.pos, 0)
        flex_need = need.get("FLEX", 0) if p.pos in FLEX_ELIGIBLE else 0
        if pos_need > 0:
            return 12.0, f"fills {p.pos} starter"
        if flex_need > 0 and p.pos in FLEX_ELIGIBLE:
            return 6.0, "fills FLEX"
        if p.pos in ("QB", "TE") and counts[p.pos] >= 1:
            return -30.0, f"already have {p.pos}"
        return 0.0, None

    def _bye_penalty(self, p, my_roster):
        """Discourage stacking bye weeks.

        Two distinct failures, weighted differently:
          * Same position, same bye — the week arrives and you have nobody to
            start there. This is what produced a week with no startable QB.
          * Roster-wide pile-up — one week where half your lineup is out.
        K and DEF skip the same-position term (you only roster one, so its bye
        is an unavoidable stream), but they still count toward the pile-up:
        a kicker sharing a bye with your RB1, WR1 and defense is what turns one
        week into a guaranteed loss."""
        if not p.bye:
            return 0.0, None
        same_bye = [q for q in my_roster if q.bye == p.bye]
        same_pos = [q for q in same_bye if q.pos == p.pos]
        pen, reason = 0.0, None
        if same_pos and p.pos not in ("K", "DEF"):
            # Worse when the position is thin: no cover at all that week.
            thin = 14.0 if STARTERS.get(p.pos, 0) >= 1 and len(
                [q for q in my_roster if q.pos == p.pos]) <= STARTERS.get(p.pos, 1) else 7.0
            pen -= thin * len(same_pos)
            reason = (f"bye {p.bye} clashes with your "
                      f"{same_pos[0].pos} {same_pos[0].name}")
        if len(same_bye) >= 2:
            pen -= 4.0 * (len(same_bye) - 1)
            reason = reason or (f"{len(same_bye) + 1} of your players would "
                                f"share bye week {p.bye}")
        return pen, reason

    def _eligible(self, p, counts, rnd, forced=False):
        """Hard roster-construction rules.

        `forced` is set when the only thing left to do is fill required
        starters — at that point the normal 'don't draft a K yet' rules must
        step aside, or the draft ends with an illegal lineup."""
        if p.adj_points <= 0:
            return False
        if STARTERS.get(p.pos, 0) == 0 and not (p.pos in FLEX_ELIGIBLE and STARTERS.get("FLEX", 0)):
            return False
        if p.pos in ("K", "DEF") and rnd < self.rounds - 1 and not forced:
            return False
        if counts.get(p.pos, 0) >= POSITION_CAPS.get(p.pos, 9):
            return False
        if p.pos in ("QB", "TE") and counts.get(p.pos, 0) >= max(1, STARTERS.get(p.pos, 0)) and rnd < self.rounds - 3 and not forced:
            return False
        return True

    # --- the core: score every available player for THIS pick ---
    def recommend(self, current_pick, n=5):
        avail = sorted(self.available(), key=lambda x: -x.vorp)
        my_roster = self.my_roster()
        counts = self.roster_counts(my_roster)
        need = self.starters_unfilled(my_roster)
        rnd = (current_pick - 1) // self.teams + 1
        next_pick = self.following_my_pick(current_pick)
        picks_until_next = (next_pick - current_pick) if next_pick else 0

        # Candidate set: top of the VORP board, plus K/DEF explicitly in the
        # last two rounds (they'd never crack the top-150 VORP slice).
        candidates = avail[:150]
        if rnd >= self.rounds - 1:
            for pos in ("K", "DEF"):
                extra = sorted([q for q in avail if q.pos == pos],
                               key=lambda x: -x.adj_points)[:5]
                candidates = candidates + [q for q in extra if q not in candidates]

        # Safety net: if the picks I have left only just cover the starting
        # slots I still have empty, draft nothing but those. Without this the
        # value model can happily end the draft with no quarterback.
        my_left = [pk for pk in self.my_pick_numbers() if pk >= current_pick]
        required_left = sum(need.get(k, 0) for k in
                            ("QB", "RB", "WR", "TE", "K", "DEF", "FLEX"))
        forced = required_left > 0 and len(my_left) <= required_left
        if forced:
            def fills(q):
                if need.get(q.pos, 0) > 0:
                    return True
                return need.get("FLEX", 0) > 0 and q.pos in FLEX_ELIGIBLE
            candidates = sorted([q for q in avail if fills(q)],
                                key=lambda x: -x.adj_points)[:60]

        # ---- Stage 1: base value for every candidate ----
        base = []
        for p in candidates:
            if not self._eligible(p, counts, rnd, forced):
                continue

            depth = self._depth_discount(p.pos, counts[p.pos] + 1)
            pos_weight = 1.0 if self.teams <= 6 else POS_WEIGHT[p.pos]
            score = p.vorp * pos_weight * depth
            reasons = []
            if depth < 0.7:
                reasons.append(f"would be your {counts[p.pos] + 1}th {p.pos} "
                               f"(bench value only, x{depth:.2f})")

            fit, fit_reason = self._fit_adjust(p, counts, need)
            score += fit
            if fit_reason:
                reasons.append(fit_reason)

            bye_pen, bye_reason = self._bye_penalty(p, my_roster)
            score += bye_pen
            if bye_reason:
                reasons.append(bye_reason)

            # Tier urgency: last man in a tier is worth reaching for
            same_tier_left = [q for q in avail if q.pos == p.pos and q.tier == p.tier]
            if p.tier and p.tier < 90 and len(same_tier_left) <= 2:
                score += 5.0
                reasons.append(f"last of {p.pos} tier {p.tier}")

            # Value vs ADP (falling player)
            if p.adp + 8 < current_pick:
                score += min((current_pick - p.adp) * 0.35, 10.0)
                reasons.append(f"value: ADP {p.adp:.0f} at pick {current_pick}")

            # Injury flags (already priced into adj_points; explain them)
            if p.injury:
                reasons.append(p.injury_note())
            if p.form_note:
                reasons.append(p.form_note)
            if p.news_note:
                reasons.append(p.news_note)
            if p.games is not None and p.games < 14:
                reasons.append(f"projected only {p.games:.0f} games")

            # Don't reach absurdly early vs market
            if p.adp > current_pick + 2.2 * self.teams:
                score -= 8.0

            base.append([score, p, reasons])

        base.sort(key=lambda t: -t[0])

        # ---- Stage 2: one-step lookahead to my NEXT pick ----
        # For each realistic candidate, ask: if I take him now, what is the
        # expected value of the best player I can still get at my next turn?
        # This is what makes the snake position matter: from slot 9 the
        # 16 -> 33 gap swallows 17 players, so the agent front-loads the
        # positions that will be gutted and waits on the ones that won't.
        if picks_until_next > 0 and len(base) > 1:
            sims = self._simulate_survivors(avail, next_pick, current_pick)
            next_rnd = (next_pick - 1) // self.teams + 1
            shortlist = base[:40]

            for entry in shortlist:
                p = entry[1]
                counts_after = dict(counts)
                counts_after[p.pos] += 1
                need_after = self.starters_unfilled(my_roster + [p])

                total = 0.0
                for sim in sims:
                    best_next = 0.0
                    for pos in POSITIONS:
                        for q in sim[pos]:
                            if q.pid == p.pid:
                                continue
                            if not self._eligible(q, counts_after, next_rnd):
                                continue
                            fit_q, _ = self._fit_adjust(q, counts_after, need_after)
                            dq = self._depth_discount(q.pos, counts_after[q.pos] + 1)
                            qw = 1.0 if self.teams <= 6 else POS_WEIGHT[q.pos]
                            v = q.vorp * qw * dq + fit_q
                            if v > best_next:
                                best_next = v
                            break  # only the best survivor per position matters
                    total += best_next
                expected_next = total / len(sims)
                entry[0] += LOOKAHEAD_WEIGHT * expected_next
                entry.append(expected_next)

            # Explain the cliff: what does this position look like when I'm back?
            for entry in shortlist:
                p = entry[1]
                surv_pts = []
                for sim in sims:
                    others = [q for q in sim[p.pos] if q.pid != p.pid]
                    if others:
                        surv_pts.append(others[0].adj_points)
                if surv_pts:
                    exp_pts = sum(surv_pts) / len(surv_pts)
                    dropoff = p.adj_points - exp_pts
                    if dropoff >= 15:
                        entry[2].append(
                            f"cliff: best {p.pos} left at pick {next_pick} ≈ "
                            f"{exp_pts:.0f} pts (-{dropoff:.0f})")
                # How likely is he to make it back to me?
                surv = self.survival_prob(p, next_pick, current_pick)
                if surv >= 0.55:
                    entry[2].append(f"{surv*100:.0f}% likely to last to pick {next_pick}"
                                    " — consider waiting")

            base = shortlist + base[40:]
            base.sort(key=lambda t: -t[0])

        scored = [(e[0], e[1], e[2]) for e in base]
        return scored[:n], need, rnd


# ----------------------------------------------------------------------------
# Display
# ----------------------------------------------------------------------------
def fmt_player(p, width=24):
    return f"{p.name[:width]:<{width}} {p.pos:<4}{(p.team or ''):<4}"


def print_recommendations(state, current_pick, header=True):
    recs, need, rnd = state.recommend(current_pick, n=5)
    if header:
        needs_str = ", ".join(f"{k}:{v}" for k, v in need.items() if v > 0) or "starters filled"
        nxt = state.following_my_pick(current_pick)
        print()
        print(bold(cyan(f"=== YOU'RE UP — Pick {current_pick} (Round {rnd}) ===")))
        print(dim(f"   Roster needs: {needs_str}"))
        if nxt:
            gap = nxt - current_pick - 1
            note = "  ← longer gap, consider positional scarcity" if gap >= state.teams else ""
            print(dim(f"   Next pick: {nxt} ({gap} players come off the board first)"
                      f"{note}"))
    for i, (score, p, reasons) in enumerate(recs, 1):
        tag = green("► PICK") if i == 1 else f"  {i}."
        bye_s = f"bye{p.bye}" if p.bye else "  -  "
        line = (f"{tag}  {fmt_player(p)} "
                f"proj {p.adj_points:5.1f}  VORP {p.vorp:5.1f}  "
                f"ADP {p.adp:5.1f}  T{p.tier} {bye_s} {p.injury_note()}")
        print(line)
        if reasons:
            print(dim(f"        {'; '.join(reasons[:3])}"))
        if p.form_note and p.form_note not in reasons[:3]:
            print(dim(f"        {p.form_note}"))
    return recs


def print_board_snapshot(state, current_pick, k=3):
    """Best available at each position — quick glance while others pick."""
    avail = state.available()
    print(dim(f"\n-- Best available (pick {current_pick}) --"))
    for pos in ["RB", "WR", "TE", "QB"]:
        top = sorted([p for p in avail if p.pos == pos], key=lambda x: -x.vorp)[:k]
        names = ", ".join(f"{p.name} ({p.adj_points:.0f})" + (p.injury_note() or "")
                          for p in top)
        print(dim(f"   {pos}: {names}"))


# ----------------------------------------------------------------------------
# Modes
# ----------------------------------------------------------------------------
def load_data(force=False, league_id=LEAGUE_ID, quiet=False, blend=None,
              start_week=None, news=DEFAULT_NEWS):
    if league_id:
        apply_league_config(league_id, force, quiet)
    players_db = fetch_players(force)
    projections = fetch_projections(force)
    start_week, results = fetch_recent_results(force, start_week)
    pool = build_player_pool(projections, players_db)
    apply_market_blend(pool, MARKET_BLEND if blend is None else blend)
    byes = fetch_byes(force=force)
    for p in pool.values():
        p.bye = byes.get(p.team)
    apply_current_form(pool, results, start_week)
    apply_news_overrides(pool, news)
    if not quiet:
        covered = sum(p.recent_games > 0 for p in pool.values())
        print(dim(f"Scoring future weeks {start_week}–18; recent results for {covered} players. "
                  "Missed-game estimates are assumptions; NEWS shows source context."))
        print(dim(f"Data loaded {datetime.now().astimezone().isoformat(timespec='seconds')}; "
                  f"cache limit {CACHE_HOURS} hour. PPR={SCORING.get('rec', 0):g}."))
    if len(pool) < 100:
        print(red(f"Warning: only {len(pool)} scored players — data may be incomplete."))
    return pool


def load_for_args(args, force=None):
    global TEAMS, ROUNDS
    if args.league:
        apply_league_config(args.league, args.refresh)
    if args.teams is not None:
        TEAMS = args.teams
    if args.rounds is not None:
        ROUNDS = args.rounds
    # Validate the draft setup before downloading large feeds.
    DraftState({}, my_slot=args.slot)
    return load_data(args.refresh if force is None else force, league_id=None,
                     blend=args.blend, start_week=args.start_week, news=args.news)


def cmd_board(args):
    pool = load_for_args(args)
    state = DraftState(pool, my_slot=args.slot)
    print(bold(f"\n{SEASON} Remaining-Season Draft Board — {state.teams} teams, PPR={SCORING.get('rec', 0):g}, slot {args.slot}"))
    print(dim("Your picks: " + ", ".join(map(str, state.my_pick_numbers()))))
    print(dim(f"Replacement levels: " + ", ".join(f"{k} {v:.0f}" for k, v in state.repl.items())))
    ranked = sorted(pool.values(), key=lambda x: -x.vorp)
    print(f"\n{'#':>3} {'Player':<24} {'Pos':<4}{'Tm':<4}{'Proj':>7}{'VORP':>7}{'ADP':>7}  Tier")
    for i, p in enumerate(ranked[:160], 1):
        print(f"{i:>3} {p.name[:24]:<24} {p.pos:<4}{p.team:<4}"
              f"{p.adj_points:>7.1f}{p.vorp:>7.1f}{p.adp:>7.1f}   T{p.tier} {p.injury_note()}")
    # CSV export
    csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cheatsheet.csv")
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["rank", "player", "pos", "team", "remaining_pts_custom", "vorp", "adp", "tier", "injury", "recent_games", "recent_ppg", "start_week", "availability_note"])
        for i, p in enumerate(ranked, 1):
            writer.writerow([i, p.name, p.pos, p.team, round(p.adj_points, 1),
                             round(p.vorp, 1), round(p.adp, 1), p.tier, p.injury or "",
                             p.recent_games, p.recent_ppg, p.start_week, p.injury_note()])
    print(green(f"\nSaved full cheat sheet to {csv_path}"))


def cmd_mock(args):
    pool = load_for_args(args)
    state = DraftState(pool, my_slot=args.slot)
    rng = random.Random(args.seed)
    total_picks = state.teams * state.rounds
    print(bold(f"\nSimulating a {state.teams}-team draft — you are slot {args.slot}."))
    print(dim("Opponents pick near ADP with realistic randomness.\n"))

    for pick_no in range(1, total_picks + 1):
        slot = state.slot_for_pick(pick_no)
        if slot == state.my_slot:
            recs, _, rnd = state.recommend(pick_no, n=5)
            if not recs:
                break
            choice = recs[0][1]
            state.record_pick(choice.pid, pick_no)
            print(green(f"R{rnd:>2} P{pick_no:>3}  YOU  -> {fmt_player(choice)} "
                        f"proj {choice.adj_points:6.1f} VORP {choice.vorp:5.1f} "
                        f"ADP {choice.adp:5.1f} {choice.injury_note()}"))
            if args.verbose:
                for i, (s, p, r) in enumerate(recs[1:4], 2):
                    print(dim(f"          alt{i}: {p.name} ({'; '.join(r[:2])})"))
        else:
            # Opponent AI: mostly ADP order with noise; late rounds fill K/DEF
            rnd_num = (pick_no - 1) // state.teams + 1
            avail = state.available()
            opp_roster = state.rosters[slot]
            opp_counts = state.roster_counts(opp_roster)
            cand = sorted(avail, key=lambda x: x.adp)
            cand = [p for p in cand if not (
                p.pos in ("K", "DEF") and rnd_num < state.rounds - 3)]
            cand = [p for p in cand if opp_counts[p.pos] < POSITION_CAPS.get(p.pos, 9) + 1]
            if rnd_num >= state.rounds - 1:
                for pos in ("K", "DEF"):
                    if opp_counts[pos] == 0:
                        pos_c = [p for p in avail if p.pos == pos]
                        if pos_c:
                            cand = sorted(pos_c, key=lambda x: x.adp)
                            break
            if not cand:
                cand = sorted(avail, key=lambda x: x.adp)
            pick = cand[min(int(abs(rng.gauss(0, 2.2))), len(cand) - 1)]
            state.record_pick(pick.pid, pick_no)

    print(bold("\nYour final roster:"))
    order = {"QB": 0, "RB": 1, "WR": 2, "TE": 3, "K": 4, "DEF": 5}
    for p in sorted(state.my_roster(), key=lambda x: (order[x.pos], -x.adj_points)):
        print(f"   {fmt_player(p)} proj {p.adj_points:6.1f}  ADP {p.adp:5.1f} {p.injury_note()}")
    total = sum(p.adj_points for p in state.my_roster())
    print(dim(f"   Total projected (all {state.rounds}, including bench): {total:.0f} remaining custom pts"))


def parse_draft_id(s):
    m = re.search(r"(\d{10,})", s)
    if not m:
        raise SystemExit(red(f"Could not find a draft ID in '{s}'. "
                             "Paste the draft room URL or the numeric draft ID."))
    return m.group(1)


def apply_draft_settings(draft, quiet=False):
    """Derive roster slots from the DRAFT itself.

    A mock draft usually has no league attached and may use different roster
    settings than your league (different team count, two flex spots, and so
    on). Reading the draft's own slots_* fields keeps recommendations correct
    when you are testing in a mock."""
    global STARTERS, ROUNDS, TEAMS
    s = draft.get("settings") or {}
    if any(s.get(k) for k in ("slots_super_flex", "slots_rec_flex", "slots_wrrb_flex", "slots_idp_flex", "slots_dl", "slots_lb", "slots_db")):
        raise ValueError("This agent currently supports QB/RB/WR/TE/FLEX/K/DEF roster slots only")
    if s.get("rounds"):
        ROUNDS = int(s["rounds"])
    if s.get("teams"):
        TEAMS = int(s["teams"])
    slots = {"QB": s.get("slots_qb"), "RB": s.get("slots_rb"),
             "WR": s.get("slots_wr"), "TE": s.get("slots_te"),
             "FLEX": (s.get("slots_flex") or 0) + (s.get("slots_super_flex") or 0),
             "K": s.get("slots_k"), "DEF": s.get("slots_def")}
    if not any(v for v in slots.values()):
        return False
    STARTERS = {k: int(v or 0) for k, v in slots.items()}
    if s.get("rounds"):
        ROUNDS = int(s["rounds"])
    if s.get("teams"):
        TEAMS = int(s["teams"])
    if s.get("slots_super_flex"):
        POS_WEIGHT["QB"] = 1.15
    if not quiet:
        line = ", ".join(f"{k}{v}" for k, v in STARTERS.items() if v)
        print(dim(f"Roster slots from the draft: {line} "
                  f"({TEAMS} teams, {ROUNDS} rounds)"))
    return True


def resolve_slot(draft, username):
    """Look up which draft slot a Sleeper username holds in this draft.

    Works for mock drafts too, so you never have to guess your slot.
    Returns None if the user isn't in this draft's order."""
    if not username:
        return None
    try:
        user = _http_get_json(
            f"https://api.sleeper.app/v1/user/{username}", timeout=20)
    except Exception as e:
        print(yellow(f"Could not look up Sleeper user '{username}': {e}"))
        return None
    uid = str((user or {}).get("user_id") or "")
    order = draft.get("draft_order") or {}
    slot = order.get(uid)
    if slot:
        return int(slot)
    print(yellow(f"User '{username}' is not in this draft's order "
                 f"(draft order has {len(order)} entries)."))
    return None


def sync_live_picks(state, picks):
    """Replay the authoritative snapshot, including commissioner rollbacks."""
    ordered = sorted(picks, key=lambda pk: int(pk["pick_no"]))
    numbers = [int(pk["pick_no"]) for pk in ordered]
    if numbers != list(range(1, len(ordered) + 1)):
        raise ValueError("Draft pick snapshot has missing or duplicate pick numbers")
    # Projections can omit injured, inactive, or newly added players. They
    # still occupy a roster spot when selected in the actual room.
    missing = {}
    for pk in ordered:
        pid = str(pk["player_id"])
        if pid in state.pool:
            continue
        meta = pk.get("metadata") or {}
        pos = meta.get("position")
        if pos in POSITIONS:
            name = f"{meta.get('first_name', '')} {meta.get('last_name', '')}".strip() or pid
            missing[pid] = Player(pid, name, pos, meta.get("team"))
        elif state.slot_for_pick(int(pk["pick_no"])) == state.my_slot:
            raise ValueError(f"Cannot identify the position of your pick {pk['pick_no']} "
                             f"(player {pid}); refusing incomplete roster advice")
    state.pool.update(missing)
    state.taken.clear()
    state.rosters = {slot: [] for slot in range(1, state.teams + 1)}
    for pk in ordered:
        state.record_pick(pk["player_id"], int(pk["pick_no"]))
    return ordered


def cmd_live(args):
    if not (args.draft or DRAFT_ID):
        raise ValueError("Supply the new Sleeper draft URL: python3 draft_agent.py live <draft_url>")
    draft_id = parse_draft_id(args.draft or DRAFT_ID)

    # Pull draft config FIRST so the league's own scoring drives the board
    try:
        draft = _http_get_json(f"https://api.sleeper.app/v1/draft/{draft_id}")
    except Exception as e:
        raise SystemExit(red(f"Could not load draft {draft_id}: {e}"))

    if draft.get("type") != "snake" or (draft.get("settings") or {}).get("reversal_round", 0):
        raise ValueError("This agent requires a standard snake draft without third-round reversal")
    if str(draft.get("season")) != SEASON:
        raise ValueError(f"Draft season does not match {SEASON}")
    league_id = draft.get("league_id") or args.league
    if not league_id:
        raise ValueError("This room has no linked league to verify scoring. "
                         "Pass --league YOUR_LEAGUE_ID with matching scoring.")
    apply_league_config(league_id, force=True)

    # A mock draft carries no league, so its own roster settings win.
    apply_draft_settings(draft)
    if args.teams is not None and args.teams != TEAMS:
        raise ValueError("--teams conflicts with the live draft")
    if args.rounds is not None and args.rounds != ROUNDS:
        raise ValueError("--rounds conflicts with the live draft")

    # Auto-detect your slot from the draft order when possible. In mock
    # drafts the slot is random, so this is the difference between the agent
    # advising the right team and quietly advising someone else's.
    detected = resolve_slot(draft, args.user) if args.user else None
    if detected and detected != args.slot:
        print(green(f"Detected your draft slot as {detected} "
                    f"(overriding --slot {args.slot})."))
        args.slot = detected
    elif detected:
        print(dim(f"Confirmed your draft slot: {detected}"))
    teams = (draft.get("settings") or {}).get("teams") or TEAMS
    rounds = (draft.get("settings") or {}).get("rounds") or ROUNDS
    DraftState({}, teams=teams, rounds=rounds, my_slot=args.slot)
    pool = load_data(force=True, league_id=None, blend=args.blend,
                     start_week=args.start_week, news=args.news)
    state = DraftState(pool, teams=teams, rounds=rounds, my_slot=args.slot)
    status = draft.get("status")
    print(bold(f"\nConnected to draft {draft_id} — {teams} teams, {rounds} rounds, "
               f"you are slot {args.slot}. Status: {status}"))
    my_picks = state.my_pick_numbers()
    print(dim(f"Your picks: {', '.join(map(str, my_picks))}"))
    print(dim("Watching the draft room. Ctrl-C to stop.\n"))

    seen = 0
    last_snapshot = None
    was_on_clock = False
    while True:
        try:
            picks = _http_get_json(
                f"https://api.sleeper.app/v1/draft/{draft_id}/picks", timeout=20)
        except Exception as e:
            print(yellow(f"  (fetch hiccup: {e}; retrying)"))
            time.sleep(args.poll)
            continue

        snapshot = sorted((int(pk["pick_no"]), str(pk["player_id"])) for pk in picks)
        if snapshot != last_snapshot:
            old = dict(last_snapshot or [])
            picks = sync_live_picks(state, picks)
            if last_snapshot and not all(item in snapshot for item in last_snapshot):
                print(yellow("Draft picks corrected or rolled back; rebuilt rosters."))
            for pk in picks:
                if old.get(int(pk["pick_no"])) == str(pk["player_id"]):
                    continue
                pid = str(pk.get("player_id"))
                pick_no = pk.get("pick_no") or (len(state.taken) + 1)
                p = state.pool.get(pid)
                meta = pk.get("metadata") or {}
                nm = p.name if p else f"{meta.get('first_name','?')} {meta.get('last_name','')}"
                slot = pk.get("draft_slot") or state.slot_for_pick(pick_no)
                who = green("YOU") if slot == state.my_slot else f"slot {slot}"
                print(f"  Pick {pick_no:>3} ({who}): {nm} "
                      f"{dim('(' + (p.pos if p else meta.get('position','?')) + ')')}")
            seen = len(picks)
            last_snapshot = snapshot
            was_on_clock = False

        current_pick = seen + 1
        if current_pick > teams * rounds:
            print(bold(green("\nDraft complete. Good luck this season!")))
            break

        on_clock_slot = state.slot_for_pick(current_pick)
        if on_clock_slot == state.my_slot:
            if not was_on_clock:
                print_recommendations(state, current_pick)
                was_on_clock = True
        else:
            nxt = state.next_my_pick(current_pick)
            if nxt is not None and (nxt - current_pick) == args.warn and not was_on_clock:
                print(yellow(f"\n  Heads up — you pick in {args.warn}. Likely board:"))
                print_recommendations(state, nxt, header=False)
                print_board_snapshot(state, current_pick)
                was_on_clock = True  # suppress repeat until next pick lands

        time.sleep(args.poll)


def cmd_refresh(args):
    load_for_args(args, force=True)
    print(green("Data refreshed."))


def main():
    ap = argparse.ArgumentParser(description="Sleeper live draft agent")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--slot", type=int, default=MY_SLOT,
                       help=f"your draft slot (default {MY_SLOT}); --user opts into auto-detection")
        p.add_argument("--teams", type=int, default=None, help=f"team count (default {TEAMS})")
        p.add_argument("--rounds", type=int, default=None, help=f"round count (default {ROUNDS})")
        p.add_argument("--start-week", type=int, default=None,
                       help="first future scoring week (default: Sleeper current regular-season week)")
        p.add_argument("--news", default=DEFAULT_NEWS,
                       help="optional JSON file of recently verified availability notes")
        p.add_argument("--refresh", action="store_true",
                       help="force re-download of projections/injuries")
        p.add_argument("--blend", type=float, default=MARKET_BLEND,
                       help="how much to trust ADP over Sleeper's projection "
                            "(0=projections only, 1=pure market)")
        p.add_argument("--league", default=LEAGUE_ID,
                       help="Sleeper league ID to read scoring/roster settings "
                            "from (pass '' to use the built-in settings)")

    b = sub.add_parser("board", help="print custom-scored cheat sheet + CSV")
    common(b)
    b.set_defaults(func=cmd_board)

    m = sub.add_parser("mock", help="simulate a full draft")
    common(m)
    m.add_argument("--seed", type=int, default=None)
    m.add_argument("--verbose", action="store_true")
    m.set_defaults(func=cmd_mock)

    l = sub.add_parser("live", help="watch a live Sleeper draft")
    l.add_argument("draft", nargs="?", default=None,
                   help="new draft room URL or draft ID (required)")
    common(l)
    l.add_argument("--poll", type=float, default=3.0, help="seconds between polls")
    l.add_argument("--user", default=None,
                   help="your Sleeper username; used to auto-detect your slot")
    l.add_argument("--warn", type=int, default=3,
                   help="show prep board when N picks away (default 3)")
    l.set_defaults(func=cmd_live)

    r = sub.add_parser("refresh", help="force re-download all data")
    r.set_defaults(func=cmd_refresh)
    common(r)

    args = ap.parse_args()
    if not 0 <= args.blend <= 1:
        ap.error("--blend must be between 0 and 1")
    if getattr(args, "poll", 3) <= 0:
        ap.error("--poll must be positive")
    try:
        args.func(args)
    except (ValueError, RuntimeError, OSError) as e:
        ap.exit(1, f"Error: {e}\n")


if __name__ == "__main__":
    main()
