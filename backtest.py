#!/usr/bin/env python3
"""
Draft Backtest
==============
Simulates N full drafts from your slot against the REAL current board, then
reports the roster shapes you should expect, your projected starting lineup,
and how much bye weeks will cost you.

    python3 backtest.py                 # 12 drafts from your slot
    python3 backtest.py --n 30 --slot 4
"""
import argparse, collections, random, copy
import draft_agent as da
import lineup_optimizer as lo


def simulate(pool_src, byes, slot, seed, teams=None, rounds=None):
    teams = da.TEAMS if teams is None else teams
    rounds = da.ROUNDS if rounds is None else rounds
    pool = {k: copy.copy(v) for k, v in pool_src.items()}
    st = da.DraftState(pool, teams=teams, rounds=rounds, my_slot=slot)
    r = random.Random(seed)
    for pick in range(1, teams * rounds + 1):
        s = st.slot_for_pick(pick)
        if s == slot:
            recs, _, _ = st.recommend(pick, n=3)
            if not recs:
                break
            ch = recs[0][1]
        else:
            av = st.available()
            rn = (pick - 1) // teams + 1
            cnt = st.roster_counts(st.rosters[s])
            c = [x for x in sorted(av, key=lambda z: z.adp)
                 if not (x.pos in ("K", "DEF") and rn < rounds - 2)
                 and cnt[x.pos] < da.POSITION_CAPS.get(x.pos, 9)]
            if rn >= rounds - 1:
                for need in ("K", "DEF"):
                    if cnt[need] == 0:
                        cc = sorted([x for x in av if x.pos == need],
                                    key=lambda z: z.adp)
                        if cc:
                            c = cc
                        break
            if not c:
                c = sorted(av, key=lambda z: z.adp)
            ch = c[min(int(abs(r.gauss(0, 2.0))), len(c) - 1)]
        st.record_pick(ch.pid, pick)
    return st


def lineup_total(st):
    r = [{"pos": p.pos, "tm": f"B{p.bye}", "pts": p.adj_points, "n": p.name}
         for p in st.my_roster()]
    byes = {f"B{b}": b for b in set(p.bye for p in st.my_roster())}
    weekly = []
    holes = collections.Counter()
    for wk in range(1, 19):
        starters, unf = lo.optimal_lineup(r, wk, byes)
        weekly.append(sum(p["pts"] for p in starters if p))
        for u in unf:
            holes[u] += 1
    return max(weekly), max(weekly) - min(weekly), holes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=12)
    ap.add_argument("--slot", type=int, default=da.MY_SLOT)
    ap.add_argument("--league", default=da.LEAGUE_ID)
    args = ap.parse_args()

    pool = da.load_data(league_id=args.league)
    byes = da.fetch_byes()
    shapes, totals, drops = collections.Counter(), [], []
    allholes = collections.Counter()
    for seed in range(1, args.n + 1):
        st = simulate(pool, byes, args.slot, seed)
        c = st.roster_counts(st.my_roster())
        shapes[f"{c['QB']}QB/{c['RB']}RB/{c['WR']}WR/{c['TE']}TE"] += 1
        best, drop, holes = lineup_total(st)
        totals.append(best); drops.append(drop)
        allholes.update(holes)

    print(da.bold(f"\n{args.n} simulated drafts from slot {args.slot}\n"))
    print("  Roster shapes you should expect:")
    for k, v in shapes.most_common():
        print(f"    {k}   {v}/{args.n}")
    print(f"\n  Full-strength starting lineup: mean {sum(totals)/len(totals):.0f} pts "
          f"({min(totals):.0f}–{max(totals):.0f})")
    print(f"  Worst bye week costs you: {sum(drops)/len(drops)/17:.1f} pts/week on average")
    if allholes:
        print("\n  Slots you will have to stream on their bye "
              "(unavoidable when you roster only one):")
        for slot_name, n in allholes.most_common():
            print(f"    {slot_name}: {n/args.n:.1f} week(s) per season")


if __name__ == "__main__":
    main()
