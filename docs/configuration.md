# Configuration and optional injury notes

## Choose your league settings

The example defaults in `draft_agent.py` are six teams, pick 5, 15 rounds,
full PPR, and QB / 2 RB / 2 WR / TE / FLEX / K / DEF plus six bench spots.
They are an example, not universal fantasy rules.

For an actual league board or mock, use `--league YOUR_LEAGUE_ID` and your
`--slot`. The league supplies scoring, roster slots, team count, and rounds.
Explicit `--teams` and `--rounds` override size and length for board/mock mode.

For `live`, the draft's linked league supplies scoring. A room without a linked
league requires `--league YOUR_LEAGUE_ID` for a league with matching scoring.
The draft supplies
its own size and roster settings. An explicit live `--teams` or `--rounds`
that conflicts with the room is rejected. `--user` opts into username lookup
and can override `--slot`; otherwise your supplied slot stays selected.

Roster construction is tuned to standard single-QB redraft. Nonstandard
lineups may require changes beyond flags, including position caps in the
source. Standard snake math does not support traded picks or keeper value.
The fixed `SEASON` constant is `2026`; moving to a later season requires a
code/configuration update and verification of the data feeds.

## Injury estimates

The engine uses the newer available Sleeper injury metadata. A cleared
status can remove an older flag. In the absence of a specific estimate,
it assumes these missed games from the remaining schedule:

- Out: 1.
- IR or PUP: 4.
- Doubtful: 0.75.
- Questionable: 0.25.

Fractional games represent uncertainty in expected availability; they do
not mean a player will literally miss that fraction of a game. IR/PUP does
not automatically mean season-ending. These rough assumptions can be wrong;
read current reporting for a player you are considering.

## Add a sourced override

Personal news is **off by default**. To add it, create a JSON object keyed
by the player's Sleeper ID and supply `--news data/my-news.json`.
The following is a schema example: replace every placeholder before use.

```json
{
  "SLEEPER_PLAYER_ID": {
    "return_week": 6,
    "source": "URL_OF_THE_REPORT_YOU_VERIFIED",
    "as_of": "YYYY-MM-DD",
    "note": "Explain what was reported and whether this return is an estimate."
  }
}
```

Each entry needs `source`, `as_of`, and `note`, plus **one** of:

- `return_week`: an integer from 1 to 19. Week 19 means no regular-season return.
- `missed_games`: a nonnegative number, allowing fractional uncertainty.
- `out_for_season`: `true`, which removes expected remaining value and prevents
  a recommendation.

Use the date you actually reviewed the report, not a future date. Entries
older than seven days, unknown player IDs, and invalid estimates are rejected.
The code records your source but does not read or verify the article for you.

```sh
python3 draft_agent.py board --refresh --news data/my-news.json
python3 draft_agent.py live YOUR_DRAFT_ROOM_URL --slot 5 --news data/my-news.json
```

News files under `data/` stay local and are excluded from Git. Historical
September notes from the maintainer's machine are not published or
implicitly loaded by this public version.

[Back to the README](../README.md)
