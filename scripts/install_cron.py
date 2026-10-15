#!/usr/bin/env python3
"""Preview or install a configurable daily schedule; preserve unrelated cron jobs."""
import argparse
from datetime import datetime, timezone
import difflib
import fcntl
from pathlib import Path
import re
import shlex
import subprocess
from wake import ROOT, config

BEGIN = "# BEGIN ai-waker managed schedule"
END = "# END ai-waker managed schedule"


def schedule(cfg, root):
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", cfg["start_time"]):
        raise ValueError("start_time must be HH:MM")
    count, interval = cfg["runs_per_day"], cfg["interval_minutes"]
    if not isinstance(count, int) or not isinstance(interval, int):
        raise ValueError("Schedule counts must be integers")
    if count < 1 or interval < 1 or count * interval > 1440:
        raise ValueError("Schedule must fit in a daily repeating cycle")
    path = str(root)
    if not re.fullmatch(r"/[A-Za-z0-9_./-]+", path):
        raise ValueError("Install path must be absolute and use cron-safe characters")
    hour, minute = map(int, cfg["start_time"].split(":"))
    start = hour * 60 + minute
    result = []
    for i in range(count):
        when = (start + i * interval) % 1440
        result.append(f"{when % 60} {when // 60} * * * {path}/wake.sh >> {path}/logs/cron.log 2>&1")
    return result


def merge(old, jobs, replace_legacy=None):
    kept, in_block, found_block, legacy_count = [], False, False, 0
    for line in old.splitlines():
        if line == BEGIN:
            if in_block or found_block:
                raise ValueError("Duplicate managed schedule block")
            in_block, found_block = True, True
            continue
        if line == END:
            if not in_block:
                raise ValueError("Unmatched managed schedule block")
            in_block = False
            continue
        if in_block:
            continue
        stripped = line.strip()
        if replace_legacy and stripped and not stripped.startswith("#"):
            fields = stripped.split(None, 5)
            tokens = shlex.split(fields[5]) if len(fields) == 6 else []
            if tokens and tokens[0] == replace_legacy:
                legacy_count += 1
                continue
            if replace_legacy in stripped:
                raise ValueError("Unexpected wrapped legacy wake job")
        kept.append(line)
    if in_block:
        raise ValueError("Unclosed managed schedule block")
    if replace_legacy and legacy_count not in (0, 4):
        raise ValueError("Expected zero or four legacy wake jobs")
    return "\n".join(kept + [BEGIN] + jobs + [END]) + "\n"


def read_cron():
    result = subprocess.run(["crontab", "-l"], text=True, capture_output=True)
    if result.returncode and "no crontab" not in result.stderr.lower():
        raise RuntimeError("Unable to read current crontab")
    return result.stdout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Default: preview only")
    parser.add_argument("--replace-legacy", help="Exact legacy wake.sh path, if replacing an older schedule")
    args = parser.parse_args()
    cfg = config()
    zone = subprocess.run(["timedatectl", "show", "-p", "Timezone", "--value"],
                          check=True, text=True, capture_output=True).stdout.strip()
    if zone != cfg["timezone"]:
        raise SystemExit("VPS timezone differs from config; no changes made")
    for name in ("logs", "backups"):
        (ROOT / name).mkdir(exist_ok=True)
    with (ROOT / "backups/install.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        old = read_cron()
        new = merge(old, schedule(cfg, ROOT), args.replace_legacy)
        print("".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                           fromfile="current-crontab", tofile="new-crontab")))
        if not args.apply:
            print("PREVIEW ONLY; pass --apply to install")
            return
        if old == new:
            print("Already installed; no changes needed")
            return
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = ROOT / "backups" / ("crontab-" + stamp + ".txt")
        backup.write_text(old, encoding="utf-8")
        backup.chmod(0o600)
        if read_cron() != old:
            raise SystemExit("Crontab changed while preparing; refusing to overwrite")
        subprocess.run(["crontab", "-"], input=new, check=True, text=True)
        if read_cron() != new:
            raise SystemExit("Verification failed; inspect crontab and backup")
        print("INSTALLED; backup=" + str(backup))


if __name__ == "__main__":
    main()
