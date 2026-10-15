import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import install_cron
import wake
import check


class Results(unittest.TestCase):
    def claude(self, **changes):
        result = {"type": "result", "subtype": "success", "result": "ok", "is_error": False,
                  "usage": {"input_tokens": 12, "output_tokens": 1}}
        result.update(changes)
        return json.dumps(result)

    def codex(self, extra=None):
        events = [{"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}},
                  {"type": "turn.completed", "usage": {"input_tokens": 12, "output_tokens": 1}}]
        return "\n".join(json.dumps(event) for event in events + (extra or []))

    def test_claude_success(self):
        ok, _, usage = wake.evaluate("claude", 0, self.claude())
        self.assertTrue(ok)
        self.assertEqual(usage["input_tokens"], 12)

    def test_old_false_positive_nonzero(self):
        self.assertFalse(wake.evaluate("claude", 1, self.claude())[0])

    def test_claude_json_error(self):
        self.assertFalse(wake.evaluate("claude", 0, self.claude(is_error=True))[0])

    def test_claude_error_subtype(self):
        self.assertFalse(wake.evaluate("claude", 0, self.claude(subtype="error_during_execution"))[0])

    def test_unexpected_reply(self):
        self.assertFalse(wake.evaluate("claude", 0, self.claude(result="cannot authenticate"))[0])

    def test_codex_success(self):
        self.assertTrue(wake.evaluate("codex", 0, self.codex())[0])

    def test_codex_error_even_after_reply(self):
        self.assertFalse(wake.evaluate("codex", 0, self.codex([{"type": "error"}]))[0])

    def test_codex_failed_turn(self):
        self.assertFalse(wake.evaluate("codex", 0, self.codex([{"type": "turn.failed"}]))[0])

    def test_codex_incomplete(self):
        self.assertFalse(wake.evaluate("codex", 0, '{"type":"turn.started"}')[0])

    def test_invalid_output(self):
        for provider in ("claude", "codex"):
            for output in ("not JSON", "[]", "null", ""):
                self.assertFalse(wake.evaluate(provider, 0, output)[0])

    def test_api_keys_removed(self):
        with patch.dict(wake.os.environ, {"OPENAI_API_KEY": "fake", "ANTHROPIC_API_KEY": "fake"}):
            env = wake.subscription_env()
            self.assertNotIn("OPENAI_API_KEY", env)
            self.assertNotIn("ANTHROPIC_API_KEY", env)

    def test_real_subprocess_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, output = wake.invoke([sys.executable, "-c", "import time; time.sleep(10)"], tmp, 0.05)
        self.assertEqual((rc, output), (124, ""))

    def test_second_provider_attempted_after_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(wake, "ROOT", Path(tmp)), patch.object(wake, "config", return_value={}), \
                 patch.object(wake, "wake_one", side_effect=[False, True]) as run, \
                 patch.object(sys, "argv", ["wake.py"]):
                self.assertEqual(wake.main(), 1)
                self.assertEqual([call.args[0] for call in run.call_args_list], ["claude", "codex"])

    def test_missing_dependencies_are_reported(self):
        with patch.object(check, "config", return_value={"timezone": "Asia/Taipei"}), \
             patch.object(check, "run", return_value=None), \
             patch.object(check.shutil, "which", return_value=None), \
             patch("builtins.print") as printed:
            self.assertEqual(check.check(), 1)
            self.assertTrue(any("FAIL codex CLI" in call.args[0] for call in printed.call_args_list))


class Cron(unittest.TestCase):
    root = Path("/home/test/ai-waker")

    def cfg(self, start):
        return {"start_time": start, "runs_per_day": 4, "interval_minutes": 301}

    def test_public_schedule(self):
        jobs = install_cron.schedule(self.cfg("08:00"), self.root)
        self.assertEqual([line.split()[:2] for line in jobs],
                         [["0", "8"], ["1", "13"], ["2", "18"], ["3", "23"]])

    def test_personal_schedule(self):
        jobs = install_cron.schedule(self.cfg("06:30"), self.root)
        self.assertEqual([line.split()[:2] for line in jobs],
                         [["30", "6"], ["31", "11"], ["32", "16"], ["33", "21"]])

    def test_midnight_wrap(self):
        jobs = install_cron.schedule(self.cfg("21:33"), self.root)
        self.assertEqual([line.split()[:2] for line in jobs],
                         [["33", "21"], ["34", "2"], ["35", "7"], ["36", "12"]])

    def test_invalid_times(self):
        for value in ("24:00", "8:0", "-1:00", "08:61"):
            with self.assertRaises(ValueError):
                install_cron.schedule(self.cfg(value), self.root)

    def test_preserve_report_and_events(self):
        unrelated = '# Report\nMAILTO=""\n30 8 * * * /home/ubuntu/earnings/run_report.sh\n20 * * * * /home/ubuntu/earnings/push_events.sh\n'
        legacy = "".join(f"0 {hour} * * * /home/ubuntu/earnings/wake.sh >> /tmp/log 2>&1\n" for hour in (7, 12, 17, 22))
        new = install_cron.merge(unrelated + legacy, ["new"] * 4, "/home/ubuntu/earnings/wake.sh")
        self.assertTrue(new.startswith(unrelated))
        self.assertNotIn("/home/ubuntu/earnings/wake.sh", new)

    def test_reinstall_is_idempotent(self):
        jobs = install_cron.schedule(self.cfg("08:00"), self.root)
        first = install_cron.merge("MAILTO=\"\"\n", jobs)
        self.assertEqual(install_cron.merge(first, jobs), first)

    def test_partial_legacy_refused(self):
        with self.assertRaises(ValueError):
            install_cron.merge("0 7 * * * /old/wake.sh\n", ["new"] * 4, "/old/wake.sh")

    def test_unclosed_block_refused(self):
        with self.assertRaises(ValueError):
            install_cron.merge(install_cron.BEGIN + "\nunfinished", ["new"] * 4)

    def test_installer_on_isolated_crontab(self):
        # Exercise the actual --apply path without touching the host's real crontab.
        import os
        with tempfile.TemporaryDirectory(prefix="ai-waker-test-") as tmp:
            root = Path(tmp)
            for name in ("wake.py", "install_cron.py", "config.example.json"):
                shutil_source = Path(wake.__file__).parent / name
                (root / name).write_bytes(shutil_source.read_bytes())
            fixture = root / "fixture"
            fixture.mkdir()
            table = root / "fake-crontab.txt"
            unrelated = 'MAILTO=""\n30 8 * * * /some/report.sh\n'
            table.write_text(unrelated)
            fake_cron = fixture / "crontab"
            fake_cron.write_text('#!/usr/bin/env python3\nimport os,sys\nfrom pathlib import Path\np=Path(os.environ["TEST_CRON_FILE"])\nif sys.argv[1]=="-l": sys.stdout.write(p.read_text())\nelse: p.write_text(sys.stdin.read())\n')
            fake_cron.chmod(0o700)
            fake_zone = fixture / "timedatectl"
            fake_zone.write_text('#!/bin/sh\nprintf "Asia/Taipei\\n"\n')
            fake_zone.chmod(0o700)
            env = os.environ.copy()
            env["PATH"] = str(fixture) + os.pathsep + env["PATH"]
            env["TEST_CRON_FILE"] = str(table)
            args = [sys.executable, str(root / "install_cron.py")]
            subprocess.run(args, check=True, capture_output=True, env=env)
            self.assertEqual(table.read_text(), unrelated)  # preview is read-only
            subprocess.run(args + ["--apply"], check=True, capture_output=True, env=env)
            installed = table.read_text()
            self.assertTrue(installed.startswith(unrelated))
            self.assertIn("0 8 * * *", installed)
            self.assertIn("3 23 * * *", installed)
            subprocess.run(args + ["--apply"], check=True, capture_output=True, env=env)
            self.assertEqual(table.read_text(), installed)
            backups = list((root / "backups").glob("crontab-*.txt"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(), unrelated)


if __name__ == "__main__":
    unittest.main()
