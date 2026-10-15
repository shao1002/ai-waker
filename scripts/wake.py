#!/usr/bin/env python3
"""Linux VPS subscription wake runner. No third-party Python dependencies."""
import argparse
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
PROMPT = "Reply with only: ok. Do not use any tools."


def evaluate(provider, rc, output):
    if rc != 0:
        return False, "cli_exit_" + str(rc), {}
    try:
        if provider == "claude":
            data = json.loads(output)
            if data.get("is_error") or data.get("type") != "result":
                return False, "provider_error", {}
            if data.get("subtype") != "success":
                return False, "incomplete_result", {}
            reply, usage = data.get("result", ""), data.get("usage") or {}
        else:
            events = [json.loads(line) for line in output.splitlines() if line.strip()]
            if any(e.get("type") in ("error", "turn.failed") for e in events):
                return False, "provider_error", {}
            done = [e for e in events if e.get("type") == "turn.completed"]
            replies = [e.get("item", {}).get("text", "") for e in events
                       if e.get("type") == "item.completed"
                       and e.get("item", {}).get("type") == "agent_message"]
            if not done or not replies:
                return False, "incomplete_result", {}
            reply, usage = replies[-1], done[-1].get("usage") or {}
        if reply.strip().lower() != "ok":
            return False, "unexpected_reply", {}
        counts = {k: v for k, v in usage.items()
                  if k.endswith("tokens") and isinstance(v, (int, float))}
        return True, "reply_ok", counts
    except (ValueError, TypeError, AttributeError):
        return False, "invalid_json", {}


def config():
    path = ROOT / "config.local.json"
    if not path.exists():
        path = ROOT / "config.example.json"
    return json.loads(path.read_text(encoding="utf-8"))


def locate(name, cfg):
    return (cfg.get(name + "_bin") or shutil.which(name)
            or str(Path.home() / ".npm-global/bin" / name))


def command(provider, cfg):
    if provider == "claude":
        return [locate("claude", cfg), "-p", "--output-format", "json",
                "--model", cfg.get("claude_model", "haiku"), "--tools", "",
                "--strict-mcp-config", "--no-session-persistence", "--setting-sources", ""]
    cmd = [locate("codex", cfg), "exec", "--skip-git-repo-check", "--ephemeral",
           "--ignore-user-config", "--sandbox", "read-only", "--json", "--color", "never",
           "-c", 'forced_login_method="chatgpt"', "-c", 'model_provider="openai"',
           "-c", 'model_reasoning_effort="low"']
    if cfg.get("codex_model"):
        cmd += ["--model", cfg["codex_model"]]
    return cmd + ["-"]


def subscription_env():
    env = os.environ.copy()
    for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL",
                "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
                "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY"):
        env.pop(key, None)
    return env


def invoke(cmd, cwd, seconds):
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, encoding="utf-8",
                            cwd=cwd, env=subscription_env(), start_new_session=True)
    try:
        output, _ = proc.communicate(PROMPT + "\n", timeout=seconds)
        return proc.returncode, output
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        return 124, ""


def record(provider, ok, reason, usage, elapsed, cfg):
    entry = {"time": datetime.now(ZoneInfo(cfg["timezone"])).isoformat(timespec="seconds"),
             "provider": provider, "status": "OK" if ok else "FAIL", "reason": reason,
             "seconds": round(elapsed, 2), "usage": usage}
    line = json.dumps(entry, ensure_ascii=False)
    # Never persist raw responses, stderr, prompts with user data or credentials.
    with (ROOT / "logs/wake.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    print(line, flush=True)


def wake_one(provider, work, cfg):
    started = time.monotonic()
    try:
        if provider == "codex":
            login = subprocess.run([locate("codex", cfg), "login", "status"],
                                   capture_output=True, text=True, timeout=20,
                                   env=subscription_env())
            if login.returncode or "chatgpt" not in (login.stdout + login.stderr).lower():
                record(provider, False, "chatgpt_login_required", {}, time.monotonic() - started, cfg)
                return False
        rc, output = invoke(command(provider, cfg), work, cfg.get("timeout_seconds", 180))
        ok, reason, usage = evaluate(provider, rc, output)
    except (OSError, subprocess.SubprocessError):
        ok, reason, usage = False, "launch_error", {}
    record(provider, ok, reason, usage, time.monotonic() - started, cfg)
    return ok


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=("both", "claude", "codex"), default="both")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    cfg = config()
    providers = ("claude", "codex") if args.provider == "both" else (args.provider,)
    if args.dry_run:
        for provider in providers:
            print(json.dumps({"provider": provider, "command": command(provider, cfg)}, ensure_ascii=False))
        return 0
    (ROOT / "logs").mkdir(exist_ok=True)
    work = ROOT / "work"
    work.mkdir(exist_ok=True)
    with (ROOT / "logs/wake.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("SKIP another wake is still running", flush=True)
            return 0
        # All providers are attempted even when the first one fails.
        results = [wake_one(provider, work, cfg) for provider in providers]
        return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
