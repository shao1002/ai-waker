#!/usr/bin/env python3
"""Check prerequisites and subscription logins without making model requests."""
import json
import shutil
import subprocess
import sys
from wake import config, locate, subscription_env


def run(args):
    try:
        return subprocess.run(args, capture_output=True, text=True,
                              timeout=30, env=subscription_env())
    except (OSError, subprocess.SubprocessError):
        return None


def check():
    cfg = config()
    checks = []

    def add(label, ok, hint=""):
        checks.append(bool(ok))
        print(("OK   " if ok else "FAIL ") + label + (" — " + hint if not ok and hint else ""))

    add("Linux + Python 3.9+", sys.platform == "linux" and sys.version_info >= (3, 9))
    for name in ("crontab", "timedatectl"):
        add(name, bool(shutil.which(name)), "Install cron/systemd tools on the VPS")
    zone = run(["timedatectl", "show", "-p", "Timezone", "--value"])
    add("System timezone matches " + cfg["timezone"],
        zone is not None and zone.returncode == 0 and zone.stdout.strip() == cfg["timezone"],
        "Choose a config timezone matching this VPS; setup does not change the system clock")
    cron = run(["systemctl", "is-active", "cron"])
    if cron is None or cron.returncode:
        cron = run(["systemctl", "is-active", "crond"])
    add("cron service active", cron is not None and cron.returncode == 0,
        "Start the cron/crond service before enabling the schedule")
    for provider in ("claude", "codex"):
        executable = locate(provider, cfg)
        version = run([executable, "--version"])
        available = version is not None and version.returncode == 0
        add(provider + " CLI", available,
            "Install the CLI or set " + provider + "_bin in config.local.json")
        if not available:
            continue
        help_args = [executable, "--help"] if provider == "claude" else [executable, "exec", "--help"]
        help_result = run(help_args)
        flags = ("--tools", "--setting-sources", "--no-session-persistence", "--strict-mcp-config") \
            if provider == "claude" else ("--ephemeral", "--ignore-user-config", "--sandbox", "--json")
        add(provider + " required CLI flags",
            help_result is not None and help_result.returncode == 0
            and all(flag in help_result.stdout for flag in flags), "Update to a compatible CLI version")
        auth_args = [executable, "auth", "status"] if provider == "claude" else [executable, "login", "status"]
        auth = run(auth_args)
        ok = auth is not None and auth.returncode == 0
        if provider == "claude" and ok:
            try:
                status = json.loads(auth.stdout)
                ok = status.get("loggedIn") and status.get("authMethod") == "claude.ai"
            except (ValueError, AttributeError):
                ok = False
        elif ok:
            ok = "chatgpt" in (auth.stdout + auth.stderr).lower()
        add(provider + " subscription login", ok,
            "Run claude then /login" if provider == "claude" else "Run codex login --device-auth")
    return 0 if all(checks) else 1


if __name__ == "__main__":
    raise SystemExit(check())
