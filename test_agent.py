#!/usr/bin/env python3
"""
Offline test harness. Builds a synthetic-but-realistic player universe (with
real positional shapes: a TE cliff, RB attrition, injury flags) and runs unit
tests + full mock drafts against it, without touching Sleeper's API.

    python3 test_agent.py            # run all checks
    python3 test_agent.py --show     # also print a sample on-the-clock board
"""
import argparse
import json
import os
import random
import sys

import draft_agent as da

rng = random.Random(7)


def build_fake_universe():
    players_db, projections = {}, []
    pid = [1000]

    def add(pos, i, stats, injury=None, team="DAL"):
        p = str(pid[0]); pid[0] += 1
        players_db[p] = {"full_name": f"{pos}{i} Test", "position": pos, "team": team,
                         "status": "Active", "injury_status": injury,
                         "age": rng.randint(22, 31), "years_exp": rng.randint(0, 9),
                         "fantasy_positions": [pos]}
        projections.append({"player_id": p, "stats": stats,
                            "player": {"position": pos, "team": team,
                                       "first_name": f"{pos}{i}", "last_name": "Test"}})

    for i in range(1, 33):
        b = max(0.35, 1 - (i - 1) * 0.035)
        add("QB", i, {"pass_yd": 4600 * b, "pass_td": 36 * b, "pass_int": 10 + i * 0.2,
                      "rush_yd": max(0, 550 - i * 25), "rush_td": max(0, 5 - i * 0.2),
                      "gp": 17, "adp_ppr": (i * 10 + 15) if i <= 10 else 100 + i * 8})
    for i in range(1, 71):
        b = max(0.12, 1 - (i - 1) * 0.022)
        inj = {5: "Questionable", 9: "Out", 14: "IR"}.get(i)
        add("RB", i, {"rush_yd": 1350 * b, "rush_td": 11 * b, "rec": 55 * b,
                      "rec_yd": 420 * b, "rec_td": 2.5 * b, "gp": 16,
                      "adp_ppr": max(1.5, i * 4.4 - 2.5 + rng.uniform(-1, 1))}, injury=inj)
    for i in range(1, 91):
        b = max(0.12, 1 - (i - 1) * 0.018)
        inj = {7: "Questionable", 12: "Doubtful"}.get(i)
        add("WR", i, {"rec": 105 * b, "rec_yd": 1450 * b, "rec_td": 9.5 * b,
                      "rush_yd": 30 * b, "gp": 17,
                      "adp_ppr": max(1.8, i * 3.6 - 1.5 + rng.uniform(-1, 1))}, injury=inj)
    for i in range(1, 33):  # steep cliff after the top 3
        b = max(0.2, (1 if i <= 3 else 0.62) - (i - 1) * 0.02)
        add("TE", i, {"rec": 92 * b, "rec_yd": 1050 * b, "rec_td": 8 * b, "gp": 17,
                      "adp_ppr": (i * 11 + 8) if i <= 12 else 150 + i * 5})
    for i in range(1, 25):
        b = max(0.6, 1 - (i - 1) * 0.02)
        add("K", i, {"xpm": 35 * b, "fgm_0_19": 1, "fgm_20_29": 8 * b, "fgm_30_39": 9 * b,
                     "fgm_40_49": 8 * b, "fgm_50p": 4 * b, "fgmiss": 3, "xpmiss": 1,
                     "gp": 17, "adp_ppr": 155 + i * 3})
    for i, t in enumerate(["PHI","BAL","KC","SF","DAL","BUF","NYJ","PIT","CLE","MIA",
                           "DET","GB","HOU","SEA","LAR","DEN","TB","NO","CIN","LAC",
                           "MIN","ATL","IND","WAS"], 1):
        b = max(0.6, 1 - (i - 1) * 0.02)
        players_db[t] = {"first_name": t, "last_name": "D/ST", "position": "DEF",
                         "team": t, "status": "Active", "injury_status": None,
                         "fantasy_positions": ["DEF"]}
        projections.append({"player_id": t,
                            "stats": {"sack": 45 * b, "int": 14 * b, "fum_rec": 9 * b,
                                      "def_td": 3 * b, "pts_allow_14_20": 6,
                                      "pts_allow_7_13": 4 * b, "gp": 17,
                                      "adp_ppr": 150 + i * 3.2},
                            "player": {"position": "DEF", "team": t,
                                       "first_name": t, "last_name": "D/ST"}})
    return players_db, projections


def opponent_pick(state, slot, pick_no, r):
    """Opponents draft near ADP with noise, and fill K/DEF at the end."""
    rnd_num = (pick_no - 1) // state.teams + 1
    avail = state.available()
    counts = state.roster_counts(state.rosters[slot])
    if rnd_num >= state.rounds - 1:
        for pos in ("K", "DEF"):
            if counts[pos] == 0:
                c = sorted([p for p in avail if p.pos == pos], key=lambda x: x.adp)
                if c:
                    return c[0]
    cand = [p for p in sorted(avail, key=lambda x: x.adp)
            if not (p.pos in ("K", "DEF") and rnd_num < state.rounds - 3)
            and counts[p.pos] < da.POSITION_CAPS.get(p.pos, 9) + 1]
    if not cand:
        cand = sorted(avail, key=lambda x: x.adp)
    return cand[min(int(abs(r.gauss(0, 2.2))), len(cand) - 1)]


def run_mock(state, seed, verbose=False):
    r = random.Random(seed)
    log = []
    for pick_no in range(1, state.teams * state.rounds + 1):
        slot = state.slot_for_pick(pick_no)
        if slot == state.my_slot:
            recs, _, rnd = state.recommend(pick_no, n=5)
            if not recs:
                break
            choice = recs[0][1]
            log.append((rnd, pick_no, choice, recs))
        else:
            choice = opponent_pick(state, slot, pick_no, r)
        state.record_pick(choice.pid, pick_no)
    return log


def fresh_state(pool_src, slot=9):
    players_db, projections = pool_src
    pool = da.build_player_pool(projections, players_db)
    return da.DraftState(pool, teams=12, rounds=15, my_slot=slot)


FAILURES = []


def check(cond, msg):
    if cond:
        print(f"  PASS  {msg}")
    else:
        print(f"  FAIL  {msg}")
        FAILURES.append(msg)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args()

    universe = build_fake_universe()
    st = fresh_state(universe)

    print("\n[1] Snake-draft math (12 teams, slot 9)")
    check(st.slot_for_pick(9) == 9, "pick 9 belongs to slot 9")
    check(st.slot_for_pick(16) == 9, "pick 16 belongs to slot 9 (round 2 reverses)")
    check(st.my_pick_numbers()[:5] == [9, 16, 33, 40, 57],
          f"pick sequence starts 9,16,33,40,57 (got {st.my_pick_numbers()[:5]})")
    check(st.following_my_pick(9) == 16, "next pick after 9 is 16")
    check(da.parse_draft_id("https://sleeper.com/draft/nfl/1234567890123456789")
          == "1234567890123456789", "draft ID parsed from a Sleeper URL")

    print("\n[2] Custom scoring matches the league settings")
    pts = da.score_stats({"pass_yd": 5000, "pass_td": 40, "pass_int": 10}, "QB")
    check(abs(pts - (200 + 160 - 20)) < 0.01, f"QB 5000/40/10 = 340.0 (got {pts:.1f})")
    pts = da.score_stats({"rec": 100, "rec_yd": 1400, "rec_td": 10}, "WR")
    check(abs(pts - (100 + 140 + 60)) < 0.01, f"WR 100/1400/10 PPR = 300.0 (got {pts:.1f})")
    pts = da.score_stats({"fgm_50p": 5, "xpm": 30}, "K")
    check(abs(pts - (25 + 30)) < 0.01, f"K five 50+ FG + 30 XP = 55.0 (got {pts:.1f})")

    print("\n[3] Injury handling")
    ir = [p for p in st.pool.values() if p.injury == "IR"][0]
    out = [p for p in st.pool.values() if p.injury == "Out"][0]
    check(abs(ir.adj_points / ir.points - 13 / 17) < 0.001, "IR assumes four missed games, not automatically season-ending")
    check(abs(out.adj_points / out.points - 16 / 17) < 0.001, "Out assumes one missed game, not a 65% season penalty")
    adp_order = sorted(st.pool.values(), key=lambda x: x.adp)
    for i, p in enumerate(adp_order[:8], 1):
        st.record_pick(p.pid, i)
    recs, need, _ = st.recommend(9, n=8)
    check(all(p.adj_points > 0 for _, p, _ in recs),
          "recommendations have positive expected future value")

    print("\n[4] Survival model (probabilistic lookahead)")
    clean = fresh_state(universe)
    top = sorted(clean.pool.values(), key=lambda x: x.adp)[0]          # ADP ~1.5
    mid = [p for p in clean.pool.values() if 14 < p.adp < 20][0]        # ADP ~17
    late = [p for p in clean.pool.values() if 60 < p.adp < 70][0]
    check(clean.survival_prob(top, 9, 1) < 0.15,
          f"ADP ~{top.adp:.0f} player almost surely gone by pick 9 "
          f"({clean.survival_prob(top, 9, 1):.2f})")
    check(0.3 < clean.survival_prob(mid, 16, 9) < 0.85,
          f"ADP ~{mid.adp:.0f} player is a coin flip to reach pick 16 "
          f"({clean.survival_prob(mid, 16, 9):.2f})")
    check(clean.survival_prob(late, 16, 9) > 0.9,
          f"ADP ~{late.adp:.0f} player very likely to last to pick 16 "
          f"({clean.survival_prob(late, 16, 9):.2f})")
    # Conditioning on "still available" must not degenerate to exactly 0.
    # A top-ADP player who somehow slid to pick 33 is still very unlikely to
    # last another 7 picks (everyone can see him) — but the model has to say
    # that with a real number, not a numerical 0/0.
    naive = 1 - clean._phi((40 - top.adp) / clean._adp_sigma(top.adp))
    cond = clean.survival_prob(top, 40, 33)
    check(cond > naive and 0.0 < cond < 0.5,
          f"faller at pick 33: conditional survival is small but well-defined "
          f"({cond:.3f} vs unconditional {naive:.6f})")
    # Monotonicity: survival must never increase with a later target pick
    mono = all(clean.survival_prob(mid, t, 9) >= clean.survival_prob(mid, t + 1, 9) - 1e-9
               for t in range(10, 60))
    check(mono, "survival probability decreases monotonically with pick number")

    print("\n[5] Full mock drafts (5 seeds) — roster legality")
    for seed in (1, 7, 42, 99, 2026):
        s = fresh_state(universe)
        log = run_mock(s, seed)
        roster = s.my_roster()
        c = s.roster_counts(roster)
        ok_len = len(roster) == s.rounds
        ok_start = c["QB"] >= 1 and c["RB"] >= 2 and c["WR"] >= 2 and c["TE"] >= 1 \
            and c["K"] == 1 and c["DEF"] == 1
        kdef_rounds = [rnd for rnd, _, pl, _ in log if pl.pos in ("K", "DEF")]
        ok_kdef = all(r >= s.rounds - 1 for r in kdef_rounds)
        ok_qb = c["QB"] <= 2 and c["TE"] <= 2
        early_qb = [rnd for rnd, _, pl, _ in log if pl.pos == "QB" and rnd <= 3]
        check(ok_len and ok_start and ok_kdef and ok_qb,
              f"seed {seed}: 15 picks, all starters filled, K/DEF only in R{s.rounds-1}-{s.rounds}, "
              f"no 3rd QB/TE  [QB{c['QB']} RB{c['RB']} WR{c['WR']} TE{c['TE']}]")
        if seed == 42 or args.show:
            print("        " + "  ".join(
                f"R{rnd}:{pl.pos}" for rnd, _, pl, _ in log))

    print("\n[6] Slot sensitivity — the agent should behave differently from"
          " slot 1 vs slot 9 vs slot 12")
    shapes = {}
    for slot in (1, 9, 12):
        s = fresh_state(universe, slot=slot)
        log = run_mock(s, 42)
        shapes[slot] = [pl.pos for _, _, pl, _ in log[:6]]
        print(f"        slot {slot:>2} first six picks: {' '.join(shapes[slot])}")
    check(len(set(tuple(v) for v in shapes.values())) > 1,
          "draft slot changes the recommended sequence")

    print("\n[7] Live-league config handling")
    pts = da.score_stats({"rush_yd": 1000, "rush_td": 8, "rec": 50,
                          "rec_yd": 400, "fum_lost": 3}, "RB")
    expected = 100 + 48 + 50 + 40 - 6
    check(abs(pts - expected) == 0 or abs(pts - expected) < 0.01,
          f"fumbles lost are penalized: RB line = {expected} (got {pts:.1f})")
    kpts = da.score_stats({"fgm_40_49": 9, "fgm_50p": 8, "fgmiss_40_49": 1,
                           "fgmiss_50p": 3, "xpm": 42, "xpmiss": 2}, "K")
    kexp = 36 + 40 - 4 + 42 - 2
    check(abs(kpts - kexp) < 0.01,
          f"kicker miss aliases (fgmiss_40_49/50p -> fgmiss) score: "
          f"{kexp} (got {kpts:.1f})")
    unknown = da.score_stats({"totally_made_up_stat": 999}, "RB")
    check(unknown == 0.0, "an unscored projection key contributes zero, not garbage")

    saved = (dict(da.SCORING), dict(da.STARTERS), da.ROUNDS, da.TEAMS)
    try:
        da.SCORING, da.STARTERS, da.ROUNDS, da.TEAMS = {}, {}, 0, 0
        fake = {"name": "test", "total_rosters": 10,
                "roster_positions": ["QB","RB","RB","WR","WR","TE","FLEX",
                                     "K","DEF","BN","BN","BN"],
                "scoring_settings": {"rec": 0.5, "pass_td": 6}}
        os.makedirs(da.DATA_DIR, exist_ok=True)
        with open(da._cache_path("league_TESTONLY.json"), "w") as f:
            json.dump(fake, f)
        ok = da.apply_league_config("TESTONLY", quiet=True)
        check(ok and da.TEAMS == 10 and da.ROUNDS == 12
              and da.STARTERS["RB"] == 2 and da.STARTERS["FLEX"] == 1
              and da.SCORING.get("rec") == 0.5,
              f"league settings override the defaults "
              f"(teams={da.TEAMS}, rounds={da.ROUNDS}, rec={da.SCORING.get('rec')})")
    finally:
        da.SCORING, da.STARTERS, da.ROUNDS, da.TEAMS = saved
        try:
            os.remove(da._cache_path("league_TESTONLY.json"))
        except OSError:
            pass

    print("\n[8] Bye-week awareness")
    s = fresh_state(universe, slot=10)
    byes = {"DAL": 6}
    for p in s.pool.values():
        p.bye = byes.get(p.team)
    te = sorted([p for p in s.pool.values() if p.pos == "TE"],
                key=lambda x: -x.adj_points)
    s.rosters[10].append(te[0])                       # rostered TE, bye 6
    pen, reason = s._bye_penalty(te[1], s.my_roster())  # another TE, also bye 6
    check(pen < 0 and reason is not None,
          f"second TE on the same bye is penalized ({pen:.0f} pts: {reason})")
    k = [p for p in s.pool.values() if p.pos == "K"][0]
    kpen, _ = s._bye_penalty(k, s.my_roster())
    check(kpen == 0.0, "kickers are exempt from the bye penalty (you stream them)")
    clean_p = te[1]
    clean_p.bye = 9
    pen2, _ = s._bye_penalty(clean_p, s.my_roster())
    check(pen2 == 0.0, "a TE on a different bye is not penalized")
    s.rosters[10].extend([p for p in s.pool.values() if p.pos == "WR"][:3])
    for q in s.my_roster():
        q.bye = 6
    pileup, pr = s._bye_penalty(
        [p for p in s.pool.values() if p.pos == "RB"][0], s.my_roster())
    check(pileup < 0, f"roster-wide bye pile-up is penalized ({pileup:.0f}: {pr})")

    print("\n[9] Never finishes with an illegal lineup")
    # A pathological board where one position is so deep that the value model
    # would never choose it on merit. The force-fill safety net must still
    # produce a legal starting lineup.
    pdb, pproj = build_fake_universe()
    for rec in pproj:                      # make QBs look worthless
        if rec["player"]["position"] == "QB":
            rec["stats"] = {k: (v * 0.05 if k not in ("adp_ppr", "gp") else v)
                            for k, v in rec["stats"].items()}
    illegal = []
    for slot in (1, 6, 10, 12):
        s = fresh_state((pdb, pproj), slot=slot)
        run_mock(s, 42)
        c = s.roster_counts(s.my_roster())
        if not (c["QB"] >= 1 and c["RB"] >= 2 and c["WR"] >= 2 and
                c["TE"] >= 1 and c["K"] == 1 and c["DEF"] == 1):
            illegal.append((slot, dict(c)))
    check(not illegal,
          f"legal lineup from every slot even when a position is worthless "
          f"({'all OK' if not illegal else illegal})")

    print("\n[10] Market blend")
    mk = {}
    for i, (pts, adp) in enumerate([(300, 1), (280, 2), (260, 3), (200, 4)], 1):
        p = da.Player(str(i), f"RB{i}", "RB", "X"); p.points = pts; p.adp = adp
        mk[p.pid] = p
    for i, (pts, adp) in enumerate([(250, 5), (240, 6), (230, 7), (220, 8)], 5):
        p = da.Player(str(i), f"WR{i}", "WR", "X"); p.points = pts; p.adp = adp
        mk[p.pid] = p
    before = {k: v.points for k, v in mk.items()}
    da.apply_market_blend(mk, 0.0)
    check(all(mk[k].points == before[k] for k in mk),
          "weight 0 leaves every projection untouched")
    # RB4 is the market's 4th RB but the projection's 4th too -> agreement,
    # so nothing should move when projection order matches ADP order.
    da.apply_market_blend(mk, 0.5)
    check(all(abs(mk[k].points - before[k]) < 0.01 for k in mk),
          "when projections and ADP agree on order, the blend is a no-op")
    # Now disagree: make the market's first RB the projection's worst.
    mk2 = {}
    vals = [(150, 1), (300, 2), (280, 3), (260, 4)]
    for i, (pts, adp) in enumerate(vals, 1):
        p = da.Player(str(i), f"RB{i}", "RB", "X"); p.points = pts; p.adp = adp
        mk2[p.pid] = p
    for i in range(5, 13):   # pad so the position clears the 8-player minimum
        p = da.Player(str(i), f"RB{i}", "RB", "X"); p.points = 100 - i; p.adp = 10 + i
        mk2[p.pid] = p
    da.apply_market_blend(mk2, 0.5)
    lifted = mk2["1"].points
    check(lifted > 150,
          f"a player the market rates far higher than the projection is lifted "
          f"(150 -> {lifted:.0f})")
    check(mk2["2"].points < 300,
          f"and the one the projection over-rates is pulled down "
          f"(300 -> {mk2['2'].points:.0f})")

    if args.show:
        print("\n[11] Sample on-the-clock output at pick 9")
        s = fresh_state(universe)
        for i, p in enumerate(sorted(s.pool.values(), key=lambda x: x.adp)[:8], 1):
            s.record_pick(p.pid, i)
        da.print_recommendations(s, 9)
        da.print_board_snapshot(s, 9)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} CHECK(S) FAILED")
        for f in FAILURES:
            print(f"   - {f}")
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    main()
