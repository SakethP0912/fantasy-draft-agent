# Fantasy Draft Agent

**Join the league. Understand your picks. Build a team you can root for.**

A free, local draft assistant for [Sleeper](https://sleeper.com/) fantasy football.
It follows the draft, compares the players still available, and explains who to
consider next based on your scoring, roster, draft position, recent performance,
and injury information.

You do not need to know every NFL player to join your friends' league. This
project is for people new to fantasy—or football altogether—who want a starting
point, a second opinion, and a way to learn while playing. The goal is to help
you make informed picks and compete with more experienced friends, without
feeling like you are getting fleeced because everyone else knows more.

**You stay in control:** it recommends picks; you select them in Sleeper.
It does not evaluate trades or guarantee wins.

## A real example

The maintainer's September 15, 2026 draft started from **5th in the draft order
out of 6 teams**, in a full-PPR snake league. The actual draft had 14 rounds.

As of **October 1, 2026**, after two scored matchups:

- **2–0:** tied for the best record.
- **333.28 points:** the highest points total among the six teams.
- Opening selections: **Amon-Ra St. Brown at pick 5**, **Jonathan Taylor at 8**,
  **Josh Allen at 17**, and **Trey McBride at 20**.

These are actual selections and league totals retrieved from Sleeper, not a
mock draft. They are one early-season example, not evidence of a guaranteed
edge or a claim that every selection followed the agent. See the
[full result snapshot and its limits](docs/results.md) and
[sanitized source data](examples/personal-results-2026.json).

## New to fantasy? Start here

In fantasy football, you draft real NFL players onto a virtual team. Their
on-field production earns points under your league's rules. Each week, you
choose a starting lineup to compete against another manager.

A few terms you will see:

- **PPR:** points per reception. Full PPR awards one point for each catch.
- **Snake draft:** the order reverses each round. In a six-team league, the
  person picking fifth gets overall picks 5, 8, 17, 20, and so on.
- **QB / RB / WR / TE:** quarterback, running back, wide receiver, tight end.
- **FLEX:** an extra starting spot that can hold an RB, WR, or TE.
- **K / DEF:** kicker and a whole NFL team's defense/special teams.
- **Bench:** reserve players who do not score for you unless you start them.
- **Bye:** the week an NFL team does not play.
- **ADP:** average draft position—roughly when other managers select a player.
- **VORP:** value over replacement—the projected advantage over another player
  you could reasonably get later at the same position. Higher is better, but
  your roster needs matter too.

You can start with the top recommendation, read its reasons, and compare the
alternatives. This helps you spot a missing position, understand an injury
flag, and decide whether a player is worth taking before your next turn.

## Get started

You need **Python 3.9 or newer**, a terminal, and an internet connection for
live data. There are **no pip dependencies, API keys, paid AI subscriptions,
or Sleeper login credentials** required. This is a Python scoring and
simulation engine; it makes no LLM calls.

```sh
git clone https://github.com/SakethP0912/fantasy-draft-agent.git
cd fantasy-draft-agent
python3 --version
```

On Windows, use `py` instead of `python3` if that is how Python is installed.

### 1. Preview the player board

```sh
python3 draft_agent.py board --refresh
```

This downloads current data, prints a ranked board, and writes `cheatsheet.csv`.
The built-in example setup is **six teams, slot 5, 15 rounds, full PPR**:
QB, 2 RB, 2 WR, TE, FLEX, K, DEF, and six bench spots.

**For your actual league**, use its league ID so scoring and roster settings
come from Sleeper. Replace `YOUR_LEAGUE_ID` and `5` with your own values:

```sh
python3 draft_agent.py board --league YOUR_LEAGUE_ID --slot 5 --refresh
```

The league ID is the long number in your Sleeper league URL. Use the league
ID for `--league`; use the draft-room URL for `live` below.

### 2. Practice before the real draft

```sh
python3 draft_agent.py mock --teams 6 --slot 5 --rounds 14 --seed 42
```

This simulates opponents drafting around ADP and shows the resulting roster.
The example matches the maintainer's actual 14-round league. Change the team
count, slot, and rounds to match yours. Keeping the same seed makes the
opponent simulation repeatable with the same data and configuration.

### 3. Connect on draft day

Open your Sleeper draft room, copy its URL, and replace `YOUR_DRAFT_ROOM_URL`:

```sh
python3 draft_agent.py live YOUR_DRAFT_ROOM_URL --slot 5
```

The agent refreshes data at startup, loads the linked league's settings, and
checks new picks every three seconds. It prints recommendations when your
turn arrives. **Make the pick yourself in Sleeper.** Press `Ctrl+C` to stop;
rerunning the command reconstructs the draft from its current picks.

To look up your draft slot by username instead:

```sh
python3 draft_agent.py live YOUR_DRAFT_ROOM_URL --user YOUR_SLEEPER_USERNAME
```

If username lookup fails, the slot argument (default 5) remains in use.
Always check the connection message confirms your slot, team count, and rounds.
A live room without a linked league needs `--league YOUR_LEAGUE_ID` for a
league with matching scoring; otherwise it stops rather than guessing.

## What the recommendations consider

1. **Your scoring.** Re-scores projected NFL statistics under the league's
   scoring rules, rather than relying only on a generic ranking.
2. **Useful players for your roster.** Compares positions against replacement
   options, accounts for league size, and protects required starting spots.
3. **Your next turn.** Simulates who might still be available when the snake
   comes back to you. A short wait and a long wait can justify different picks.
4. **Recent games.** Blends up to four completed weeks into the season prior.
   One game has limited influence, so a single huge performance does not take
   over the rankings. Targets and carries appear as extra context.
5. **Future availability.** Values remaining games, accounts for byes, and
   discounts injury designations using explicit missed-game assumptions.
6. **Market expectations.** Uses a modest positional ADP blend and ADP-based
   opponent simulations. Kicker and defense are generally left until late.

The terminal's `proj` value means modeled **remaining-season points**, not a
promise of points next week. The numbered alternatives and explanations are
more useful during the draft than blindly following the static board.

## Options and practical limits

- `--refresh` downloads fresh data. Most cached data expires after one hour;
  bye schedules are cached longer. Live mode refreshes once at startup,
  **not continuously throughout the draft**.
- `--start-week 4` manually sets the first future scoring week. Normally it is
  detected from Sleeper. Only earlier weeks enter the recent-form sample.
- `--blend 0.15` controls the influence of ADP on projections, from 0 to 1.
- `--news PATH` adds your own recently verified availability estimates.
  See [configuration and injury notes](docs/configuration.md).
- `python3 draft_agent.py --help` and `python3 draft_agent.py live --help`
  list the commands and options.

**Best-supported setup:** standard single-QB redraft snake leagues with
QB/RB/WR/TE/FLEX/K/DEF slots. The code is currently configured for the **2026 NFL
season**. It rejects unsupported roster types and third-round reversal.
It is not designed for auctions, superflex, IDP, keeper/dynasty valuation,
or traded draft picks. Only fields supplied by Sleeper can be scored;
unknown or unprojected statistics cannot be invented.

The model uses Weeks 1–18 as its regular-season horizon, even if your fantasy
championship ends earlier. It does not model playoff matchups, trade value,
or individual injury recovery. Opponents may ignore ADP. Injuries and roles
can change faster than the feeds update. Use the explanations and current
news to make the final call.

## Troubleshooting

- **Cannot load data:** check your connection and retry. Sleeper's projection
  and stats endpoints may change or be unavailable. Missing required results
  stop the run rather than silently substituting old preseason rankings.
- **Stale injury note:** update its source/date and estimate, or omit `--news`.
  Personal notes are opt-in; none are bundled into the default board.
- **Wrong roster or league size:** supply the correct `--league`, or connect
  to the correct draft URL. Explicit `--teams`/`--rounds` can override local
  board/mock settings; conflicting live overrides are rejected.
- **Unsupported roster:** this release does not translate unusual lineup
  types into supported ones. Use it only with a supported format.

## Development and validation

Run the offline checks without a Sleeper connection:

```sh
python3 test_agent.py
python3 -m unittest -v test_current_draft.py
```

They exercise scoring, snake order, six-team roster construction, injury
handling, recent-form bounds, live-pick reconstruction, and configuration.
GitHub Actions runs these checks on Python 3.9 and 3.13.

`draft_agent.py` is the main CLI. `backtest.py` runs **simulated drafts against
today's projections**, despite its name; it is not a historical outcome
backtest. `lineup_optimizer.py` is a basic bye-week helper using a fixed
standard lineup and season averages, not a current matchup or injury-aware
start/sit model. These helpers are secondary to the draft assistant.

Contributions that improve data validation, supported scoring, explanations,
or meaningful evaluation are welcome. Include a reproducible example and
run both test commands before submitting changes.

## Data and privacy

Data comes from [Sleeper's public API](https://docs.sleeper.com/). The agent
reads public information and makes no draft picks, roster moves, or trades.
Downloads stay in local `data/`; generated cheat sheets, personal news files,
league caches, and environment files are excluded from Git. Only the sanitized
personal-result example is included here. This project is not affiliated with
Sleeper or the NFL.
