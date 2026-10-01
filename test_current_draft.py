"""Offline regressions for the six-team in-season draft."""
import contextlib
import io
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import draft_agent as da
from test_agent import build_fake_universe, run_mock


class CurrentDraftTests(unittest.TestCase):
    def player(self):
        p = da.Player("1", "Example Player", "RB", "BUF")
        p.points, p.games, p.bye = 170, 17, 12
        return p

    def test_six_team_defaults_and_short_long_turns(self):
        self.assertIsNone(da.LEAGUE_ID)
        self.assertIsNone(da.DRAFT_ID)
        self.assertIsNone(da.MY_USERNAME)
        self.assertIsNone(da.DEFAULT_NEWS)
        st = da.DraftState({})
        self.assertEqual(st.my_pick_numbers(), [5, 8, 17, 20, 29, 32, 41, 44, 53, 56, 65, 68, 77, 80, 89])
        self.assertEqual(st.following_my_pick(5), 8)
        self.assertEqual(st.following_my_pick(8), 17)
        for pick in st.my_pick_numbers():
            self.assertEqual(st.slot_for_pick(pick), 5)

    def test_replacement_uses_state_teams_and_actual_slots(self):
        db, projections = build_fake_universe()
        pool = da.build_player_pool(projections, db)
        six, _ = da.compute_replacement_levels(pool, 6, 15)
        twelve, _ = da.compute_replacement_levels(pool, 12, 15)
        for pos in da.POSITIONS:
            self.assertGreaterEqual(six[pos], twelve[pos])
        with patch.dict(da.STARTERS, {"RB": 1, "FLEX": 0}):
            fewer_rb, _ = da.compute_replacement_levels(pool, 6, 15)
            self.assertGreater(fewer_rb["RB"], six["RB"])
        state = da.DraftState(pool, teams=12, my_slot=10)
        self.assertEqual(state.repl, twelve)

    def test_runtime_defaults_follow_loaded_settings(self):
        with patch.object(da, "TEAMS", 10), patch.object(da, "ROUNDS", 12):
            st = da.DraftState({}, my_slot=10)
            self.assertEqual((st.teams, st.rounds), (10, 12))
        with self.assertRaises(ValueError):
            da.DraftState({}, teams=6, my_slot=10)

    def test_complete_six_team_rosters(self):
        db, projections = build_fake_universe()
        for seed in (1, 42, 2026):
            with self.subTest(seed=seed):
                st = da.DraftState(da.build_player_pool(projections, db))
                log = run_mock(st, seed)
                self.assertEqual(len(st.my_roster()), 15)
                self.assertEqual(sum(st.starters_unfilled(st.my_roster()).values()), 0)
                self.assertTrue(all(rnd >= 14 for rnd, _, p, _ in log if p.pos in ("K", "DEF")))
                self.assertEqual(len(st.taken), 90)

    def test_one_game_is_bounded_and_past_points_not_added(self):
        p = self.player()
        da.apply_current_form({"1": p}, {"1": [{"rush_yd": 10000}]}, 2)
        self.assertEqual(p.remaining_games, 16)
        self.assertAlmostEqual(p.points, (10 * 6 / 7 + 15 / 7) * 16)
        self.assertEqual(p.recent_games, 1)
        self.assertLess(p.points, 180)

    def test_zero_game_counts_and_missing_game_does_not(self):
        p = self.player()
        da.apply_current_form({"1": p}, {"1": [{"gp": 1}]}, 2)
        self.assertLess(p.points, 160)
        p = self.player()
        da.apply_current_form({"1": p}, {}, 2)
        self.assertEqual(p.points, 160)
        self.assertEqual(p.recent_games, 0)

    def test_future_bye_and_18_game_feed(self):
        p = self.player()
        p.games = 18
        da.apply_current_form({"1": p}, {}, 13)
        self.assertEqual(p.remaining_games, 6)  # bye already happened
        self.assertEqual(p.points, 60)
        p = self.player()
        da.apply_current_form({"1": p}, {}, 12)
        self.assertEqual(p.remaining_games, 6)

    def test_defense_season_totals_with_gp_one(self):
        p = da.Player("LAR", "Rams", "DEF", "LAR")
        p.points, p.games, p.bye = 170, 1, 10
        da.apply_current_form({p.pid: p}, {}, 2)
        self.assertEqual(p.points, 160)

    def test_newer_clear_status_beats_old_injury(self):
        projections = [{"player_id": "1", "stats": {"rush_yd": 100},
                        "player": {"position": "RB", "injury_status": None, "news_updated": 20}}]
        db = {"1": {"injury_status": "Out", "news_updated": 10}}
        self.assertIsNone(da.build_player_pool(projections, db)["1"].injury)
        db["1"]["news_updated"] = 30
        self.assertEqual(da.build_player_pool(projections, db)["1"].injury, "Out")

    def test_weekly_out_and_confirmed_season_ending(self):
        p = self.player()
        p.injury = "Out"
        self.assertEqual(p.adj_points, 160)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "news.json"
            path.write_text(json.dumps({"1": {"out_for_season": True, "source": "https://example.com/report",
                                               "as_of": date.today().isoformat(), "note": "confirmed"}}))
            da.apply_news_overrides({"1": p}, path)
            self.assertEqual(p.adj_points, 0)
            st = da.DraftState({"1": p})
            self.assertEqual(st.recommend(5)[0], [])

    def test_public_startup_does_not_load_personal_news(self):
        db, projections = build_fake_universe()
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(da, "DATA_DIR", tmp), \
             patch.object(da, "fetch_players", return_value=db), \
             patch.object(da, "fetch_projections", return_value=projections), \
             patch.object(da, "fetch_recent_results", return_value=(2, {})), \
             patch.object(da, "fetch_byes", return_value={}):
            personal_news = Path(tmp) / "news_2026.json"
            personal_news.write_text("invalid historical personal file")
            pool = da.load_data(quiet=True)
            self.assertGreater(len(pool), 100)
            with self.assertRaises(json.JSONDecodeError):
                da.load_data(quiet=True, news=str(personal_news))

    def test_return_week_excludes_bye_and_stale_news_rejected(self):
        p = self.player()
        p.start_week, p.bye, p.remaining_games = 2, 3, 16
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "news.json"
            info = {"return_week": 5, "source": "https://example.com/report",
                    "as_of": date.today().isoformat(), "note": "estimated return"}
            path.write_text(json.dumps({"1": info}))
            da.apply_news_overrides({"1": p}, path)
            self.assertEqual(p.missed_games, 2)
            info["as_of"] = "2020-01-01"
            path.write_text(json.dumps({"1": info}))
            with self.assertRaises(ValueError):
                da.apply_news_overrides({"1": p}, path)

    def test_stats_only_completed_same_season_games(self):
        rows = [{"player_id": "1", "week": 1, "season": "2026", "stats": {"gp": 1, "rec": 3}},
                {"player_id": "2", "week": 1, "season": "2026", "stats": {"gp": 0}},
                {"player_id": "3", "week": 2, "season": "2026", "stats": {"gp": 1}},
                {"player_id": "4", "week": 1, "season": "2025", "stats": {"gp": 1}}]
        with patch.object(da, "_http_get_json", return_value=rows) as http, patch.object(da, "_save_cache"):
            week, results = da.fetch_recent_results(force=True, start_week=2)
            self.assertEqual(week, 2)
            self.assertEqual(set(results), {"1"})
            self.assertIn("/2026/1?", http.call_args.args[0])
        with patch.object(da, "_http_get_json", return_value=[]):
            with self.assertRaises(RuntimeError):
                da.fetch_recent_results(force=True, start_week=2)

    def test_settings_without_slots_still_update_size(self):
        with patch.object(da, "TEAMS", 6), patch.object(da, "ROUNDS", 15):
            da.apply_draft_settings({"settings": {"teams": 8, "rounds": 16}}, quiet=True)
            self.assertEqual((da.TEAMS, da.ROUNDS), (8, 16))
        with self.assertRaises(ValueError):
            da.apply_draft_settings({"settings": {"slots_super_flex": 1}}, quiet=True)

    def test_live_requires_new_link_before_network(self):
        from argparse import Namespace
        with patch.object(da, "_http_get_json") as http:
            with self.assertRaises(ValueError):
                da.cmd_live(Namespace(draft=None))
            http.assert_not_called()

    def test_live_pick_order_and_rollback(self):
        pool = {str(i): da.Player(str(i), f"Player {i}", "RB", "BUF") for i in range(1, 7)}
        st = da.DraftState(pool)
        picks = [{"pick_no": i, "player_id": str(i)} for i in range(1, 7)]
        da.sync_live_picks(st, list(reversed(picks)))
        self.assertEqual([p.pid for p in st.my_roster()], ["5"])
        da.sync_live_picks(st, picks[:4])
        self.assertEqual(st.my_roster(), [])
        self.assertNotIn("5", st.taken)
        with self.assertRaises(ValueError):
            da.sync_live_picks(st, [picks[1]])

    def test_missing_projection_still_counts_as_drafted_starter(self):
        st = da.DraftState({})
        picks = [{"pick_no": i, "player_id": str(i),
                  "metadata": {"position": "QB", "first_name": "Test", "last_name": str(i)}}
                 for i in range(1, 6)]
        da.sync_live_picks(st, picks)
        self.assertEqual(st.starters_unfilled(st.my_roster())["QB"], 0)
        self.assertEqual(st.my_roster()[0].points, 0)
        del picks[-1]["metadata"]
        with self.assertRaisesRegex(ValueError, "Cannot identify"):
            da.sync_live_picks(da.DraftState({}), picks)

    def test_live_without_verified_scoring_stops(self):
        from argparse import Namespace
        draft = {"type": "snake", "season": "2026", "league_id": None, "settings": {}}
        with patch.object(da, "_http_get_json", return_value=draft), patch.object(da, "load_data") as load:
            with self.assertRaisesRegex(ValueError, "no linked league"):
                da.cmd_live(Namespace(draft="1234567890123456", league=None))
            load.assert_not_called()

    def test_live_configuration_precedes_pool_and_picks(self):
        from argparse import Namespace
        draft = {"type": "snake", "season": "2026", "status": "drafting", "league_id": "new",
                 "settings": {"teams": 6, "rounds": 15, "slots_qb": 1, "slots_rb": 2,
                              "slots_wr": 2, "slots_te": 1, "slots_flex": 1, "slots_k": 1, "slots_def": 1}}
        # Completed room lets the integration exercise startup and exit safely.
        picks = [{"player_id": str(i), "pick_no": i, "metadata": {"position": "RB"}}
                 for i in range(1, 91)]
        events = []
        def league(*args, **kwargs):
            events.append("league")
        def pool(*args, **kwargs):
            events.append("pool")
            self.assertEqual((da.TEAMS, da.ROUNDS), (6, 15))
            self.assertTrue(kwargs["force"])
            return {}
        args = Namespace(draft="1234567890123456", league=None, teams=None, rounds=None,
                         slot=5, user=None, blend=.15, start_week=2, news=None, poll=3, warn=3)
        with patch.object(da, "_http_get_json", side_effect=[draft, picks]), \
             patch.object(da, "apply_league_config", side_effect=league), \
             patch.object(da, "load_data", side_effect=pool), \
             patch.object(da, "resolve_slot") as resolve, contextlib.redirect_stdout(io.StringIO()):
            da.cmd_live(args)
            resolve.assert_not_called()
        self.assertEqual(events, ["league", "pool"])
        self.assertEqual(args.slot, 5)


if __name__ == "__main__":
    unittest.main()
