#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
prune_dead_domains.py

Reads the generated uBlock filter list, checks DNS resolution for every domain
in parallel, and comments out any entry where DNS fails (i.e. the scammer's
domain has expired or been taken down).

Domains that resolve successfully are left unchanged.
Already-commented entries are re-checked and uncommented if they resolve again.

Run from inside the repo directory:
    uv run prune_dead_domains.py
"""

import socket
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

# ── Configuration ─────────────────────────────────────────────────────────────

FILTER_FILE   = Path("dropshipping-filters.txt")
MAX_WORKERS   = 50    # parallel DNS threads — safe for most systems
DNS_TIMEOUT   = 5.0   # seconds per lookup

# ── Helpers ───────────────────────────────────────────────────────────────────

def fail(message: str) -> None:
    print(f"\n❌  ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def check_dns(domain: str) -> tuple[str, bool]:
    """Return (domain, resolves) — resolves=True if at least one A/AAAA record exists."""
    try:
        socket.setdefaulttimeout(DNS_TIMEOUT)
        socket.getaddrinfo(domain, None)
        return domain, True
    except (socket.gaierror, socket.timeout, OSError):
        return domain, False


# ── Load filter file ──────────────────────────────────────────────────────────

if not FILTER_FILE.exists():
    fail(
        f"{FILTER_FILE} not found.\n"
        "Run update_dropshipping_filters.py first to generate it."
    )

lines = FILTER_FILE.read_text(encoding="utf-8").splitlines()

# ── Parse lines into (original_line, domain_or_None, is_commented_rule) ──────
#
# We handle three kinds of lines:
#   Active rule:          ||example.com^
#   Already commented:    ! ||example.com^   (commented out by a previous run)
#   Header / other:       ! Title: …  or blank
#
# We only touch lines that are active rules or previously-commented rules.

RULE_PREFIX     = "||"
COMMENT_PREFIX  = "! ||"

parsed = []   # list of dicts: {line, domain, active, is_rule}
for line in lines:
    stripped = line.strip()
    if stripped.startswith(COMMENT_PREFIX):
        domain = stripped[len("! "):].lstrip("|").rstrip("^")
        parsed.append({"line": line, "domain": domain, "active": False, "is_rule": True})
    elif stripped.startswith(RULE_PREFIX):
        domain = stripped.lstrip("|").rstrip("^")
        parsed.append({"line": line, "domain": domain, "active": True, "is_rule": True})
    else:
        parsed.append({"line": line, "domain": None, "active": None, "is_rule": False})

rule_entries = [p for p in parsed if p["is_rule"]]
domains      = [p["domain"] for p in rule_entries]

if not domains:
    fail("No domain rules found in the filter file.")

print(f"🔍  Checking DNS for {len(domains)} domains "
      f"({MAX_WORKERS} parallel workers, {DNS_TIMEOUT}s timeout) …\n")

# ── DNS checks in parallel ────────────────────────────────────────────────────

results: dict[str, bool] = {}
done = 0

with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
    futures = {executor.submit(check_dns, d): d for d in domains}
    for future in as_completed(futures):
        domain, resolves = future.result()
        results[domain] = resolves
        done += 1
        status = "✔" if resolves else "✘"
        # Simple progress indicator: overwrite the same line
        print(f"  [{done:>4}/{len(domains)}]  {status}  {domain:<50}", end="\r")

print()  # newline after progress

# ── Summarise ─────────────────────────────────────────────────────────────────

alive = sum(1 for v in results.values() if v)
dead  = len(results) - alive
print(f"\n📊  Results: {alive} alive, {dead} no DNS (will be commented out)\n")

# ── Rebuild file ──────────────────────────────────────────────────────────────

# Update the "Last modified" and "Version" header lines to reflect this run
now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
version = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")

new_lines = []
rule_iter = iter(rule_entries)

for entry in parsed:
    if not entry["is_rule"]:
        line = entry["line"]
        # Update metadata lines in-place
        if line.startswith("! Last modified:"):
            line = f"! Last modified: {now_iso}"
        elif line.startswith("! Version:"):
            line = f"! Version: {version}"
        elif line.startswith("! Entries:"):
            line = f"! Entries: {alive} active, {dead} commented out (no DNS)"
        new_lines.append(line)
        continue

    rule = next(rule_iter)
    domain   = rule["domain"]
    resolves = results.get(domain, True)  # default to keeping if somehow missing

    if resolves:
        # Domain is alive — ensure the line is active (uncomment if needed)
        new_lines.append(f"||{domain}^")
    else:
        # Domain is dead — comment it out
        new_lines.append(f"! ||{domain}^")

output = "\n".join(new_lines) + "\n"

try:
    FILTER_FILE.write_text(output, encoding="utf-8")
except OSError as exc:
    fail(f"Could not write updated filter file: {exc}")

print(f"✅  Updated {FILTER_FILE}")
print(f"    {alive} active rules, {dead} commented out.")

if dead:
    dead_list = [d for d, ok in sorted(results.items()) if not ok]
    print(f"\n   Commented-out domains:")
    for d in dead_list:
        print(f"    ✘  {d}")
