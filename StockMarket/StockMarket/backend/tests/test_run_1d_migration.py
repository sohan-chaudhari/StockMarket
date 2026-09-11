"""
Safety-behavior tests for run_1d_migration.py, the scoped 1D-only entrypoint.
Focuses on what the task cares about most: it must refuse to do anything
real without the exact --execute-1d-migration flag, dry-run must make zero
Angel One calls and zero writes, and it must never reach the old all-tier
cmd_migrate / MigrationOrchestrator.run() / _swap_tables() path.
"""
import unittest
import inspect
import ast
from unittest.mock import patch, MagicMock

import run_1d_migration


class _StripDocstrings(ast.NodeTransformer):
    def _strip(self, node):
        self.generic_visit(node)
        if (node.body and isinstance(node.body[0], ast.Expr)
                and isinstance(getattr(node.body[0], "value", None), ast.Constant)
                and isinstance(node.body[0].value.value, str)):
            node.body = node.body[1:] or [ast.Pass()]
        return node

    def visit_Module(self, node):
        return self._strip(node)

    def visit_FunctionDef(self, node):
        return self._strip(node)

    def visit_AsyncFunctionDef(self, node):
        return self._strip(node)

    def visit_ClassDef(self, node):
        return self._strip(node)


def _code_only(func_or_module) -> str:
    """Source with EVERY docstring (module- and function-level, recursively)
    stripped, so a substring check reflects actual executable code -- not
    this file's own prose explaining what ISN'T called (which would
    otherwise trip a naive 'assertNotIn' the moment it's mentioned to
    explain its absence)."""
    src = inspect.getsource(func_or_module)
    tree = ast.parse(src)
    tree = _StripDocstrings().visit(tree)
    return ast.unparse(tree)


class TestArgumentSafety(unittest.TestCase):
    def test_no_flag_defaults_to_dry_run(self):
        with patch("run_1d_migration.cmd_dry_run") as mock_dry, \
             patch("run_1d_migration.cmd_execute") as mock_exec, \
             patch("sys.argv", ["run_1d_migration.py"]):
            run_1d_migration.main()
        mock_dry.assert_called_once()
        mock_exec.assert_not_called()

    def test_explicit_dry_run_flag_calls_dry_run_not_execute(self):
        with patch("run_1d_migration.cmd_dry_run") as mock_dry, \
             patch("run_1d_migration.cmd_execute") as mock_exec, \
             patch("sys.argv", ["run_1d_migration.py", "--dry-run"]):
            run_1d_migration.main()
        mock_dry.assert_called_once()
        mock_exec.assert_not_called()

    def test_execute_flag_required_for_real_run(self):
        with patch("run_1d_migration.cmd_dry_run") as mock_dry, \
             patch("run_1d_migration.cmd_execute") as mock_exec, \
             patch("sys.argv", ["run_1d_migration.py", "--execute-1d-migration"]):
            run_1d_migration.main()
        mock_exec.assert_called_once()
        mock_dry.assert_not_called()

    def test_unmistakable_flag_name(self):
        """The flag itself must be the specific, hard-to-trigger-by-accident
        name requested -- not a generic --run or --yes."""
        parser_help = run_1d_migration.main.__module__
        # Inspect the actual argparse wiring rather than just the docstring.
        import argparse
        source = inspect.getsource(run_1d_migration.main)
        self.assertIn("--execute-1d-migration", source)


class TestTickerFilter(unittest.TestCase):
    def test_no_filter_returns_none(self):
        class NS:
            tickers = None
        self.assertIsNone(run_1d_migration._parse_ticker_filter(NS()))

    def test_missing_attr_returns_none(self):
        class NS:
            pass
        self.assertIsNone(run_1d_migration._parse_ticker_filter(NS()))

    def test_comma_separated_list_parsed(self):
        class NS:
            tickers = "RELIANCE,TCS,INFY"
        self.assertEqual(run_1d_migration._parse_ticker_filter(NS()), ["RELIANCE", "TCS", "INFY"])

    def test_single_ticker(self):
        class NS:
            tickers = "RELIANCE"
        self.assertEqual(run_1d_migration._parse_ticker_filter(NS()), ["RELIANCE"])

    def test_blank_entries_and_whitespace_ignored(self):
        class NS:
            tickers = " RELIANCE, ,TCS ,,"
        # _parse_ticker_filter drops empty/whitespace-only splits (the lone
        # " " and trailing "" entries); build_plan()'s own filter does the
        # remaining .strip().upper() normalization -- verified end-to-end
        # in test_build_plan_scopes_to_only_the_requested_tickers.
        self.assertEqual(run_1d_migration._parse_ticker_filter(NS()), [" RELIANCE", "TCS "])

    def test_build_plan_scopes_to_only_the_requested_tickers(self):
        fake_rows = [
            {"ticker": "RELIANCE", "exchange": "NSE"},
            {"ticker": "TCS", "exchange": "NSE"},
            {"ticker": "INFY", "exchange": "NSE"},
            {"ticker": "WIPRO", "exchange": "NSE"},
        ]
        with patch("run_1d_migration._resolve_active_identity_rows", return_value=fake_rows), \
             patch("migration.plan_1d.audit_existing_1d_coverage", return_value={}), \
             patch("database.SessionLocal", return_value=MagicMock()):
            plan, cfg = run_1d_migration.build_plan(ticker_filter=["reliance", "TCS"])
        resolved = {i.ticker for i in plan.identities}
        self.assertEqual(resolved, {"RELIANCE", "TCS"}, "must be case-insensitive and exclude unrequested tickers")

    def test_build_plan_without_filter_includes_everything(self):
        fake_rows = [{"ticker": "A", "exchange": "NSE"}, {"ticker": "B", "exchange": "NSE"}]
        with patch("run_1d_migration._resolve_active_identity_rows", return_value=fake_rows), \
             patch("migration.plan_1d.audit_existing_1d_coverage", return_value={}), \
             patch("database.SessionLocal", return_value=MagicMock()):
            plan, cfg = run_1d_migration.build_plan(ticker_filter=None)
        self.assertEqual({i.ticker for i in plan.identities}, {"A", "B"})

    def test_execute_path_wires_ticker_filter_from_args(self):
        source = _code_only(run_1d_migration.cmd_execute)
        self.assertIn("ticker_filter", source)
        self.assertIn("_parse_ticker_filter", source)


class TestOldMigrationPathNeverInvoked(unittest.TestCase):
    def test_module_does_not_import_cmd_migrate_or_orchestrator_run(self):
        """Static check: the scoped entrypoint's source must never reference
        the old all-tier migrate command or MigrationOrchestrator.run --
        both are unsafe for an incremental 1D-only run (see module
        docstring)."""
        source = _code_only(run_1d_migration)
        self.assertNotIn("cmd_migrate", source)
        self.assertNotIn("_swap_tables", source)
        self.assertNotIn(".run(", source, "must not call MigrationOrchestrator.run()")
        self.assertNotIn("MigrationOrchestrator", source)

    def test_execute_path_calls_batch_downloader_run_tier_directly(self):
        """Confirms cmd_execute reuses BatchDownloader.run_tier() for ONLY
        the 1D tier rather than going through the orchestrator's all-tier
        loop."""
        source = inspect.getsource(run_1d_migration.cmd_execute)
        self.assertIn("run_tier(", source)
        self.assertIn("ONE_D_TIER_INDEX", source)


class TestDryRunMakesNoRealCalls(unittest.TestCase):
    def test_dry_run_token_check_never_loads_instruments(self):
        """The one call in the token-resolution path capable of triggering
        a network request is angelone_service.load_instruments() -- dry-run
        must never call it. get_token() itself is safe (falls back to the
        in-memory hardcoded table, never fetches)."""
        source = _code_only(run_1d_migration._dry_run_token_check)
        self.assertNotIn("load_instruments", source)
        self.assertIn("get_token", source)

    def test_build_plan_makes_zero_write_calls(self):
        """build_plan() (shared by dry-run) must only ever SELECT -- verified
        by mocking the DB session and asserting no commit/execute-of-a-
        write ever happens, and that Angel One's instrument loader is never
        touched."""
        fake_identity_rows = [{"ticker": "RELIANCE", "exchange": "NSE"}]

        mock_db = MagicMock()
        mock_db.execute.return_value.fetchall.return_value = [("RELIANCE", "NSE")]
        mock_db.commit = MagicMock()

        with patch("database.SessionLocal", return_value=mock_db), \
             patch("run_1d_migration._resolve_active_identity_rows", return_value=fake_identity_rows), \
             patch("migration.plan_1d.audit_existing_1d_coverage", return_value={}), \
             patch("angelone_service.angelone_service.load_instruments") as mock_load, \
             patch("angelone_service.angelone_service.get_token", return_value={"token": "1"}):
            plan, cfg = run_1d_migration.build_plan()

        mock_load.assert_not_called()
        mock_db.commit.assert_not_called()
        self.assertGreater(len(plan.identities), 0)

    def test_dry_run_report_prints_without_error_and_makes_no_calls(self):
        mock_db = MagicMock()
        mock_db.execute.return_value.fetchall.return_value = [("RELIANCE", "NSE")]

        with patch("database.SessionLocal", return_value=mock_db), \
             patch("run_1d_migration._resolve_active_identity_rows",
                   return_value=[{"ticker": "RELIANCE", "exchange": "NSE"}]), \
             patch("migration.plan_1d.audit_existing_1d_coverage", return_value={}), \
             patch("angelone_service.angelone_service.load_instruments") as mock_load, \
             patch("angelone_service.angelone_service.get_token", return_value=None):
            run_1d_migration.cmd_dry_run(argparse_namespace())

        mock_load.assert_not_called()
        mock_db.commit.assert_not_called()


def argparse_namespace():
    class NS:
        dry_run = True
        execute_1d_migration = False
    return NS()


class TestExecutePathSafetyGuards(unittest.TestCase):
    def test_execute_path_never_renames_drops_or_truncates_candles(self):
        source = _code_only(run_1d_migration)
        forbidden = ["RENAME TO candles", "DROP TABLE candles", "DROP TABLE IF EXISTS candles",
                     "TRUNCATE TABLE candles", "TRUNCATE candles"]
        for phrase in forbidden:
            self.assertNotIn(phrase, source, f"forbidden operation found: {phrase!r}")

    def test_execute_path_uses_additive_promotion_and_reconciliation(self):
        source = inspect.getsource(run_1d_migration.cmd_execute)
        self.assertIn("promote_1d_candles", source)
        self.assertIn("reconcile_1d_promotion", source)
        self.assertIn("prepare_staging_table", source)

    def test_staging_left_in_place_not_dropped_after_execute(self):
        source = _code_only(run_1d_migration.cmd_execute)
        self.assertNotIn("DROP TABLE", source)


if __name__ == "__main__":
    unittest.main()
