#!/usr/bin/env python3
"""
Weekly Lineup Optimizer
=======================
Takes a drafted roster and works out, week by week, who you should start —
accounting for bye weeks. This is a basic season-projection helper, not a
matchup-aware weekly forecast.

    python3 lineup_optimizer.py --draft <draft_id> --slot 10
    python3 lineup_optimizer.py --draft <draft_id> --user YOUR_SLEEPER_USERNAME
    python3 lineup_optimizer.py --fixture roster.json      # offline, for testing

It reports three things:
  1. Your optimal starting lineup for each week of the regular season.
  2. Weeks where you CANNOT legally fill a slot (everyone at that position is
     on bye) — these are the weeks that quietly lose you a matchup.
  3. Which bye weeks are most expensive, so you know when to plan ahead.
"""
import argparse
import json
import sys

import draft_agent as da

REG_SEASON_WEEKS = 18
STARTER_SLOTS = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF"]
FLEX_OK = ("RB", "WR", "TE")


fetch_byes = da.fetch_byes  # shared with the draft agent


def optimal_lineup(roster, week, byes):
    """Best legal lineup for one week. Returns (starters, unfilled_slots)."""
    active = [p for p in roster if byes.get(p["tm"]) != week]
    used, starters, unfilled = set(), [], []
    for slot in STARTER_SLOTS:
        pool = [p for p in active if id(p) not in used and
                (p["pos"] in FLEX_OK if slot == "FLEX" else p["pos"] == slot)]
        if not pool:
            unfilled.append(slot)
            starters.append(None)
            continue
        pick = max(pool, key=lambda p: p["pts"])
        used.add(id(pick))
        starters.append(pick)
    return starters, unfilled


def analyse(roster, byes, label="Your team"):
    per_game = lambda p: p["pts"] / 17.0
    print(da.bold(f"\n{label}: week-by-week optimal lineup"))
    print(da.dim("  (projected points are season totals / 17 — relative, not exact)"))

    problem_weeks, weekly = [], []
    for wk in range(1, REG_SEASON_WEEKS + 1):
        starters, unfilled = optimal_lineup(roster, wk, byes)
        total = sum(per_game(p) for p in starters if p)
        weekly.append((wk, total, starters, unfilled))
        if unfilled:
            problem_weeks.append((wk, unfilled))

    baseline = max(t for _, t, _, _ in weekly)
    for wk, total, starters, unfilled in weekly:
        on_bye = [p["n"] for p in roster if byes.get(p["tm"]) == wk]
        drop = baseline - total
        flag = ""
        if unfilled:
            flag = da.red(f"  ** CANNOT FILL: {', '.join(unfilled)} **")
        elif drop > 12:
            flag = da.yellow(f"  (-{drop:.0f} pts vs your best week)")
        bye_note = da.dim(f"  bye: {', '.join(on_bye)}") if on_bye else ""
        print(f"  Week {wk:>2}: {total:5.1f} pts{flag}{bye_note}")

    print(da.bold("\nWeeks that need action:"))
    if not problem_weeks:
        print(da.green("  None — every slot is fillable all season."))
    for wk, unfilled in problem_weeks:
        print(da.red(f"  Week {wk}: no available {', '.join(unfilled)} on your roster. "
                     f"You must stream one from waivers."))

    # Bye concentration
    counts = {}
    for p in roster:
        b = byes.get(p["tm"])
        if b:
            counts.setdefault(b, []).append(p)
    print(da.bold("\nBye-week concentration:"))
    for wk in sorted(counts, key=lambda w: -len(counts[w])):
        grp = counts[wk]
        if len(grp) < 2:
            continue
        lost = sum(per_game(p) for p in grp)
        names = ", ".join(f"{p['n']} ({p['pos']})" for p in grp)
        sev = da.red if lost > 45 else (da.yellow if lost > 25 else da.dim)
        print(sev(f"  Week {wk:>2}: {len(grp)} players, ~{lost:.0f} pts of "
                  f"weekly production — {names}"))
    return weekly, problem_weeks


def roster_from_draft(draft_id, slot=None, user=None):
    draft = da._http_get_json(f"https://api.sleeper.app/v1/draft/{draft_id}")
    if user:
        slot = da.resolve_slot(draft, user) or slot
    if not slot:
        raise SystemExit(da.red("Pass --slot or --user."))
    league_id = draft.get("league_id") or da.LEAGUE_ID
    if league_id:
        da.apply_league_config(league_id, quiet=True)
    picks = da._http_get_json(
        f"https://api.sleeper.app/v1/draft/{draft_id}/picks")
    proj = {str(r.get("player_id")): r for r in da.fetch_projections()}
    roster = []
    for pk in picks:
        if pk.get("draft_slot") != slot:
            continue
        m = pk.get("metadata") or {}
        rec = proj.get(str(pk.get("player_id")), {})
        roster.append({"n": f"{m.get('first_name','')} {m.get('last_name','')}".strip(),
                       "pos": m.get("position"), "tm": m.get("team"),
                       "rd": pk.get("round"),
                       "pts": da.score_stats(rec.get("stats") or {}, m.get("position"))})
    return roster, slot


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--draft", default=da.DRAFT_ID)
    ap.add_argument("--slot", type=int, default=None)
    ap.add_argument("--user", default=da.MY_USERNAME)
    ap.add_argument("--fixture", default=None,
                    help="JSON file with {roster:[...], byes:{...}} for offline use")
    args = ap.parse_args()

    if args.fixture:
        blob = json.load(open(args.fixture))
        roster, byes = blob["roster"], blob["byes"]
        label = blob.get("label", "Your team")
    else:
        if not args.draft:
            ap.error("pass --draft DRAFT_ID or --fixture PATH")
        roster, slot = roster_from_draft(args.draft, args.slot, args.user)
        byes = fetch_byes()
        label = f"Slot {slot}"
    if not roster:
        raise SystemExit(da.red("No players found for that slot."))
    analyse(roster, byes, label)


if __name__ == "__main__":
    main()
