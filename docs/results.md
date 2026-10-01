# Personal draft and early-season results

This is the maintainer's real six-team Sleeper draft and an early-season
snapshot, retrieved from Sleeper on **October 1, 2026**. The draft slot is the
**place in the draft queue**, not a claim about final league placement.

## Draft context

- Draft date: **September 15, 2026**, America/Los_Angeles.
- Position in the draft order: **5th of 6**.
- Format: **snake, full PPR, 14 rounds**.
- Starters: QB, 2 RB, 2 WR, TE, FLEX, K, DEF; **five bench spots**.
- League scoring began in Week 2. The snapshot includes Weeks 2 and 3.

The agent's local example defaults have 15 rounds and six bench spots.
This actual league had 14 rounds; live mode reads the room's actual settings.

## Actual draft selections

1. Overall pick **5** — Amon-Ra St. Brown, WR.
2. Overall pick **8** — Jonathan Taylor, RB.
3. Overall pick **17** — Josh Allen, QB.
4. Overall pick **20** — Trey McBride, TE.
5. Overall pick **29** — Chris Olave, WR.
6. Overall pick **32** — Kyren Williams, RB.
7. Overall pick **41** — David Montgomery, RB.
8. Overall pick **44** — Garrett Wilson, WR.
9. Overall pick **53** — D'Andre Swift, RB.
10. Overall pick **56** — Christian Watson, WR.
11. Overall pick **65** — Rome Odunze, WR.
12. Overall pick **68** — Jalen Coker, WR.
13. Overall pick **77** — Houston Texans, DEF.
14. Overall pick **80** — Evan McPherson, K.

## Observed result

**2 wins, 0 losses, 0 ties; 333.28 points scored.**

The record was tied for best in the six-team league. The points total ranked
**1st of 6** when comparing each roster's points-for total. This is a
points-for ranking calculated from the API response, not an official Sleeper
draft grade or a final-season finish.

## What this shows—and what it cannot establish

This documents a completed draft from a late first-round position and a
successful two-matchup start. It does not establish how much the software
contributed. The draft API records the picks made, not whether each pick
followed a recommendation. There is no control team, baseline strategy,
or full-season sample here. Do not read the result as a win-rate forecast.

For stronger evidence, future evaluations should record recommendations
before picks, measure outcomes across multiple drafts and seasons, and compare
against simple ADP-based drafting under the same league rules.

## Evidence and privacy

The [sanitized JSON snapshot](../examples/personal-results-2026.json) contains
all the reported picks, settings, totals, and limitations. The release process
matched the user-confirmed draft slot 5 to the draft order, then matched its
owner to the league roster. Points combine Sleeper's integer and decimal
fields. The ranking compares points-for totals across six rosters.

Only this team's selections and summary are published. League IDs, account
IDs, usernames, opponent identities, and raw league responses are omitted.
The snapshot is maintainer-verified against Sleeper; it is not independently
auditable from a public league link in this repository.

[Back to the README](../README.md)
