"""Command-line entry point. Only `sync --dry-run` is wired up so far."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import ics
from .config import Config
from .plan import build_plan
from .sink import FakeSink
from .store import Store
from .sync import ActionKind, reconcile

UTC = ZoneInfo("UTC")

_SYMBOL = {
    ActionKind.CREATE: "＋ create",
    ActionKind.UPDATE: "~ update",
    ActionKind.NOOP: "· unchanged",
    ActionKind.CANCEL: "✗ cancel",
}


def _cmd_sync(args: argparse.Namespace) -> int:
    cfg = Config.from_env()
    if args.days:
        cfg.lookahead_days = max(1, min(args.days, 14))

    now = datetime.now(ics.PARISH_TZ)
    plan = build_plan(cfg, now=now)
    win_start = now.astimezone(UTC)
    win_end = (now + timedelta(days=cfg.lookahead_days)).astimezone(UTC)

    header = "Dry run" if args.dry_run else "Sync"
    if args.dry_run:
        sink = FakeSink()
    else:
        from .youtube import YouTubeSink, build_service_from_env

        sink = YouTubeSink(build_service_from_env(), stream_key=cfg.stream_key)

    store = Store(cfg.db_path)
    summary = reconcile(
        plan, store, sink,
        window_start_utc=win_start, window_end_utc=win_end, dry_run=args.dry_run,
    )
    print(
        f"# {header} — {len(plan)} planned in next {cfg.lookahead_days}d "
        f"(as of {now:%Y-%m-%d %H:%M %Z}); state: {cfg.db_path}\n"
    )
    planned_by_key = {p.key: p for p in plan}
    for a in summary.actions:
        p = planned_by_key.get(a.key)
        when = f"{p.start_utc:%a %m-%d %H:%M}Z" if p else "(scheduled)"
        title = p.title if p else a.key
        print(f"  {_SYMBOL[a.kind]:12} {when}  {title}")
    print(
        f"\n  create={summary.created} update={summary.updated} "
        f"unchanged={summary.unchanged} cancel={summary.cancelled}"
    )
    store.close()
    return 0


def _cmd_auth(args: argparse.Namespace) -> int:
    import json

    from .youtube import run_oauth_flow

    token = run_oauth_flow(args.client_secrets)
    node = json.load(open(args.client_secrets)).get("installed") or {}
    updates = {
        "GOOGLE_OAUTH_CLIENT_ID": node.get("client_id", ""),
        "GOOGLE_OAUTH_CLIENT_SECRET": node.get("client_secret", ""),
        "GOOGLE_OAUTH_REFRESH_TOKEN": token,
    }
    _write_env(args.write_env, updates)
    print(f"\n✅ OAuth succeeded. Wrote GOOGLE_OAUTH_* credentials to {args.write_env}")
    print("   (refresh token not printed; it's a secret and now lives in that file.)")
    return 0


def _write_env(path: str, updates: dict[str, str]) -> None:
    """Create or update KEY=value lines in an env file, preserving other lines."""
    import os

    lines = []
    if os.path.exists(path):
        with open(path) as fh:
            lines = fh.read().splitlines()
    remaining = dict(updates)
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip() if "=" in line else None
        if key in remaining:
            lines[i] = f"{key}={remaining.pop(key)}"
    for key, val in remaining.items():
        lines.append(f"{key}={val}")
    with open(path, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    os.chmod(path, 0o600)


def _cmd_list_streams(args: argparse.Namespace) -> int:
    from .youtube import build_service_from_env, list_streams

    cfg = Config.from_env()
    pairs = list_streams(build_service_from_env())
    print("liveStream id\t\tingestion stream key")
    for sid, key in pairs:
        match = "  <- YOUTUBE_STREAM_KEY" if cfg.stream_key and key == cfg.stream_key else ""
        print(f"{sid}\t{key}{match}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    """Long-running deploy loop: reconcile, then sleep until 15 min before the next event."""
    import time

    from .scheduler import next_run_at
    from .youtube import YouTubeSink, build_service_from_env

    cfg = Config.from_env()
    service = build_service_from_env()
    while True:
        now = datetime.now(ics.PARISH_TZ)
        plan = build_plan(cfg, now=now)
        with Store(cfg.db_path) as store:
            summary = reconcile(
                plan, store, YouTubeSink(service, stream_key=cfg.stream_key),
                window_start_utc=now.astimezone(UTC),
                window_end_utc=(now + timedelta(days=cfg.lookahead_days)).astimezone(UTC),
            )
        nxt = next_run_at(plan, now)
        print(
            f"[{now:%Y-%m-%d %H:%M %Z}] create={summary.created} update={summary.updated} "
            f"unchanged={summary.unchanged} cancel={summary.cancelled} "
            f"→ next run {nxt:%Y-%m-%d %H:%M %Z}",
            flush=True,
        )
        if args.once:
            return 0
        time.sleep(max(60.0, (nxt - now).total_seconds()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="youtube-ics")
    sub = parser.add_subparsers(dest="command", required=True)

    p_sync = sub.add_parser("sync", help="Reconcile calendar → YouTube broadcasts")
    p_sync.add_argument("--dry-run", action="store_true", help="Print the plan; make no changes")
    p_sync.add_argument("--days", type=int, default=None, help="Look-ahead window (1–14)")
    p_sync.set_defaults(func=_cmd_sync)

    p_auth = sub.add_parser("auth", help="One-time OAuth consent → prints a refresh token")
    p_auth.add_argument(
        "--client-secrets", required=True, help="Path to the OAuth client secrets JSON (Desktop)"
    )
    p_auth.add_argument("--write-env", default=".env", help="Env file to write creds into")
    p_auth.set_defaults(func=_cmd_auth)

    p_ls = sub.add_parser("list-streams", help="List your reusable liveStream id↔key pairs")
    p_ls.set_defaults(func=_cmd_list_streams)

    p_run = sub.add_parser("run", help="Deploy loop: reconcile, sleep to 15m before next event")
    p_run.add_argument("--once", action="store_true", help="Run a single reconcile and exit")
    p_run.set_defaults(func=_cmd_run)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
