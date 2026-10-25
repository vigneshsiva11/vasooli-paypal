#!/usr/bin/env python3
"""Standalone runner for Phase 1 / Demo insufficient-funds scenario.

Guarantees:
- Standalone: zero imports from `app.*` (standard library only).
- Reads admin token from `VASOOLI_ADMIN_TOKEN` env var or via hidden `getpass`.
- Never prints or leaks the admin token.
- POSTs scenario with a long timeout (handles waking services).
- Polls GET /demo/runs/{id} every 5s.
- Formats step progress table: (step, status, seconds, source).
- Prints final status.
- Exits 0 only if final status is 'recovered', else exits 1.
- Supports `--reset`, requiring explicit confirmation ('RESET') before POST /demo/reset.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import getpass
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any


def _parse_iso(ts_str: Any) -> datetime | None:
    if not ts_str or not isinstance(ts_str, str):
        return None
    try:
        clean = ts_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _format_seconds(started: str | None, finished: str | None, status: str) -> str:
    start_dt = _parse_iso(started)
    if not start_dt:
        return "-"
    end_dt = _parse_iso(finished)
    if not end_dt:
        if status == "running":
            now_dt = datetime.now(timezone.utc)
            delta = (now_dt - start_dt).total_seconds()
            return f"{delta:.1f}s"
        return "-"
    delta = (end_dt - start_dt).total_seconds()
    return f"{max(0.0, delta):.1f}s"


def _http_request(
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: dict[str, Any] | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    req_headers = {"Accept": "application/json"}
    if headers:
        req_headers.update(headers)

    body_bytes = None
    if data is not None:
        body_bytes = json.dumps(data).encode("utf-8")
        req_headers["Content-Type"] = "application/json"

    req = urllib.request.Request(
        url=url,
        data=body_bytes,
        headers=req_headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read().decode("utf-8")
            return json.loads(content) if content else {}
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(err_body)
            err_msg = parsed.get("detail", err_body)
        except Exception:
            err_msg = err_body
        raise RuntimeError(f"HTTP {exc.code} {exc.reason}: {err_msg}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Connection error to {url}: {exc.reason}") from None


def _print_table(steps: list[dict[str, Any]]) -> None:
    headers = ("step", "status", "seconds", "source")
    rows: list[tuple[str, str, str, str]] = []

    for s in steps:
        name = str(s.get("name") or "")
        st = str(s.get("status") or "")
        secs = _format_seconds(s.get("started_at"), s.get("finished_at"), st)
        src = str(s.get("source") or "-")
        rows.append((name, st, secs, src))

    col_widths = [len(h) for h in headers]
    for row in rows:
        for idx, val in enumerate(row):
            col_widths[idx] = max(col_widths[idx], len(val))

    fmt = "  ".join(f"{{:<{w}}}" for w in col_widths)
    separator = "  ".join("-" * w for w in col_widths)

    print("\n" + fmt.format(*headers))
    print(separator)
    for row in rows:
        print(fmt.format(*row))
    print()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Vasooli insufficient-funds scenario against a running API."
    )
    parser.add_argument(
        "--base",
        default="http://127.0.0.1:8123",
        help="Base URL of the Vasooli API (default: http://127.0.0.1:8123)",
    )
    parser.add_argument(
        "--customer",
        default="demo-001",
        help="Customer reference starting with 'demo-' (default: demo-001)",
    )
    parser.add_argument(
        "--amount",
        type=float,
        default=49.0,
        help="Scenario transaction amount (default: 49.0)",
    )
    parser.add_argument(
        "--advance-hours",
        type=float,
        default=72.0,
        help="Simulated hours to advance clock for retry (default: 72.0)",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Trigger database reset before running scenario (prompts for typed confirmation).",
    )

    args = parser.parse_args()
    base_url = args.base.rstrip("/")

    # Secure token retrieval: never logged or printed
    admin_token = os.environ.get("VASOOLI_ADMIN_TOKEN")
    if not admin_token:
        # Prompt securely if stdin is a terminal
        if sys.stdin.isatty():
            admin_token = getpass.getpass("Enter VASOOLI_ADMIN_TOKEN (input hidden): ").strip()
        else:
            admin_token = ""

    headers: dict[str, str] = {}
    if admin_token:
        headers["X-Admin-Token"] = admin_token

    # Handle --reset confirmation
    if args.reset:
        print("=" * 60)
        print("WARNING: You requested a reset of the demo database.")
        print("This will drop operational collections on the allowlisted demo DB.")
        print("=" * 60)
        try:
            confirm = input("Type 'RESET' to confirm demo database reset: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nReset aborted.")
            return 1

        if confirm != "RESET":
            print("Reset aborted: typed confirmation did not match 'RESET'.")
            return 1

        print("Sending reset request to /demo/reset...")
        try:
            reset_resp = _http_request(
                f"{base_url}/demo/reset",
                method="POST",
                headers=headers,
                timeout=60.0,
            )
            print(f"Reset successful. Status: {reset_resp.get('status')}, Dropped collections: {len(reset_resp.get('dropped_collections', []))}")
        except Exception as exc:
            print(f"Reset failed: {exc}", file=sys.stderr)
            return 1

    # Start scenario run
    print(f"\nTriggering scenario 'insufficient-funds' on {base_url}...")
    print(f"Customer: {args.customer} | Amount: ${args.amount:.2f} | Advance: {args.advance_hours:.1f}h")

    payload = {
        "customer_ref": args.customer,
        "amount": args.amount,
        "currency": "USD",
        "advance_hours": args.advance_hours,
    }

    try:
        launch_resp = _http_request(
            f"{base_url}/demo/scenarios/insufficient-funds",
            method="POST",
            headers=headers,
            data=payload,
            timeout=180.0,
        )
    except Exception as exc:
        print(f"Failed to launch scenario: {exc}", file=sys.stderr)
        return 1

    run_id = launch_resp.get("run_id")
    if not run_id:
        print(f"Invalid launch response (no run_id): {launch_resp}", file=sys.stderr)
        return 1

    print(f"Scenario launched successfully. Run ID: {run_id}")
    print("Polling /demo/runs/{id} every 5s...")

    last_step_count = -1
    last_status = ""

    while True:
        try:
            run_data = _http_request(
                f"{base_url}/demo/runs/{run_id}",
                method="GET",
                headers=headers,
                timeout=15.0,
            )
        except Exception as exc:
            print(f"[Polling error: {exc}] Retrying in 5s...", file=sys.stderr)
            time.sleep(5.0)
            continue

        run_status = run_data.get("status")
        final_status = run_data.get("final_status")
        steps = run_data.get("steps", [])

        # Print table whenever status changes or steps update
        _print_table(steps)
        print(f"Overall Run Status: {run_status} (final: {final_status or 'in_progress'})")

        if run_status != "running":
            print("=" * 60)
            print(f"SCENARIO COMPLETED. Final status: {final_status}")
            print("=" * 60)
            if final_status == "recovered":
                return 0
            return 1

        time.sleep(5.0)


if __name__ == "__main__":
    sys.exit(main())
