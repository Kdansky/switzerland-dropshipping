#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "requests",
#   "beautifulsoup4",
# ]
# ///
"""
update_dropshipping_filters.py

1. Scrapes the dropshipping table from konsumentenschutz.ch
2. Saves the raw data as a CSV file
3. Generates a uBlock Origin filter list from the URLs
4. Writes the filter file to /github/switzerland-dropshipping/
5. Commits and pushes the change to GitHub
"""

import csv
import io
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ── Configuration ────────────────────────────────────────────────────────────

SOURCE_URL = (
    "https://www.konsumentenschutz.ch/online-ratgeber/"
    "dropshipping-die-stolpersteine-beim-onlinehandel-mit-billigware-aus-china/"
)
REPO_DIR     = Path(".")
FILTER_FILE  = REPO_DIR / "dropshipping-filters.txt"
CSV_FILE     = REPO_DIR / "dropshipping-shops.csv"

# ── Helpers ───────────────────────────────────────────────────────────────────

def fail(message: str) -> None:
    """Print a clear error message and exit with a non-zero status."""
    print(f"\n❌  ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def run(cmd: list[str], cwd: Path | None = None) -> str:
    """Run a shell command; raise a descriptive error on failure."""
    result = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Command {' '.join(cmd)!r} failed "
            f"(exit {result.returncode}):\n"
            f"stdout: {result.stdout.strip()}\n"
            f"stderr: {result.stderr.strip()}"
        )
    return result.stdout.strip()


def normalise_domain(raw: str) -> str:
    """Strip protocol, www prefix, and trailing paths/slashes from a URL."""
    domain = raw.strip().lower()
    for prefix in ("https://", "http://"):
        if domain.startswith(prefix):
            domain = domain[len(prefix):]
    if domain.startswith("www."):
        domain = domain[4:]
    domain = domain.split("/")[0]   # drop any path component
    domain = domain.split("?")[0]   # drop query strings
    return domain


# ── Step 1: Fetch and parse the page ─────────────────────────────────────────

print("🌐  Fetching source page …")
try:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }
    response = requests.get(SOURCE_URL, headers=headers, timeout=30)
    response.raise_for_status()
except requests.RequestException as exc:
    fail(f"Could not fetch {SOURCE_URL}: {exc}")

print(f"   HTTP {response.status_code}  ({len(response.content):,} bytes)")

soup = BeautifulSoup(response.text, "html.parser")

# Find the table that contains the shop list.
# The table has headers: Website, Unternehmen, Land, Handelsregister
target_table = None
for table in soup.find_all("table"):
    headers_row = table.find("tr")
    if headers_row:
        cell_texts = [th.get_text(strip=True) for th in headers_row.find_all(["th", "td"])]
        # Match on the key columns (language-independent minimum)
        if any("Website" in t for t in cell_texts):
            target_table = table
            break

if target_table is None:
    fail(
        "Could not find the dropshipping shop table on the page.\n"
        "The page structure may have changed — please inspect the source at:\n"
        f"  {SOURCE_URL}"
    )

# Parse header row
header_cells = target_table.find("tr").find_all(["th", "td"])
column_names  = [c.get_text(strip=True) for c in header_cells]
print(f"   Table columns: {column_names}")

# Parse data rows (skip header)
rows = []
for tr in target_table.find_all("tr")[1:]:
    cells = tr.find_all(["td", "th"])
    if not cells:
        continue
    row = [c.get_text(strip=True) for c in cells]
    # Pad row if it has fewer columns than the header
    while len(row) < len(column_names):
        row.append("")
    rows.append(row[:len(column_names)])

if not rows:
    fail("The table was found but contained no data rows.")

print(f"   Parsed {len(rows)} shop entries.")


# ── Step 2: Save CSV ──────────────────────────────────────────────────────────

print(f"\n📄  Saving CSV → {CSV_FILE}")
REPO_DIR.mkdir(parents=True, exist_ok=True)

try:
    with open(CSV_FILE, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(column_names)
        writer.writerows(rows)
    print(f"   Written {len(rows)} rows.")
except OSError as exc:
    fail(f"Could not write CSV to {CSV_FILE}: {exc}")


# ── Step 3: Build uBlock Origin filter list ───────────────────────────────────

# Identify the Website column index
website_col = next(
    (i for i, name in enumerate(column_names) if "website" in name.lower()),
    0,   # fall back to first column
)

domains = []
for row in rows:
    raw = row[website_col]
    if not raw:
        continue
    domain = normalise_domain(raw)
    if domain and "." in domain:      # rough sanity check
        domains.append(domain)

# Deduplicate while preserving order
seen = set()
unique_domains = []
for d in domains:
    if d not in seen:
        seen.add(d)
        unique_domains.append(d)

if not unique_domains:
    fail("No valid domains could be extracted from the Website column.")

print(f"\n🛡  Building uBlock filter list ({len(unique_domains)} domains) …")

# uBlock Origin filter list format (EasyList-compatible):
#   ! Title:        shown in the uBO dashboard
#   ! Expires:      how often uBO re-fetches the list
#   ! Version:      numeric, used by uBO to detect updates (YYYYMMDDHHmm)
#   ||example.com^  blocks all requests to example.com and subdomains
now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
version = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
lines = [
    "! Title: Switzerland Dropshipping Block List",
    "! Description: Problematic dropshipping shops identified by Konsumentenschutz.ch",
    f"! Homepage: https://github.com/kdansky/switzerland-dropshipping",
    f"! Source: {SOURCE_URL}",
    f"! Last modified: {now_iso}",
    f"! Version: {version}",
    "! Expires: 1 day (update frequency)",
    f"! Entries: {len(unique_domains)}",
    "!",
]
for domain in sorted(unique_domains):
    lines.append(f"||{domain}^")

filter_content = "\n".join(lines) + "\n"

try:
    FILTER_FILE.write_text(filter_content, encoding="utf-8")
    print(f"   Written to {FILTER_FILE}")
except OSError as exc:
    fail(f"Could not write filter file to {FILTER_FILE}: {exc}")


# ── Step 4 & 5: Git commit and push ──────────────────────────────────────────

print(f"\n🔀  Committing and pushing to GitHub …")

if not (REPO_DIR / ".git").exists():
    fail(
        f"{REPO_DIR} is not a git repository.\n"
        "Please initialise it first:\n"
        f"  cd {REPO_DIR} && git init && git remote add origin <your-repo-url>"
    )

commit_message = f"Update dropshipping filter list — {now_iso}"

try:
    # Stage both files
    run(["git", "add", str(FILTER_FILE), str(CSV_FILE)], cwd=REPO_DIR)

    # Check if there is actually anything to commit
    status = run(["git", "status", "--porcelain"], cwd=REPO_DIR)
    if not status:
        print("   Nothing changed since last commit — skipping commit and push.")
        print("\n✅  Done (no changes).")
        sys.exit(0)

    run(["git", "commit", "-m", commit_message], cwd=REPO_DIR)
    print(f"   Committed: {commit_message!r}")

    run(["git", "push"], cwd=REPO_DIR)
    print("   Pushed to remote.")

except RuntimeError as exc:
    fail(str(exc))

print("\n✅  All steps completed successfully.")
