#!/usr/bin/env python3
"""
fetch_cert.py
Downloads ALL certificate bundles (.p12, .mobileprovision, password.txt)
from the NovaCerts GitHub repo into /tmp/build/certs/<folder_name>/.

If CERT_FOLDERS_JSON is set (a JSON array of folder names), all listed folders
are fetched. Falls back to CERT_FOLDER (single) or auto-detect latest.

Rate-limit strategy:
  - Uses the contents API (one call per folder) to get file metadata/listing.
  - Downloads actual file content via `download_url` (raw CDN on
    objects.githubusercontent.com), which does NOT count against the
    authenticated REST API rate limit (5000 req/hr).
  - Retries on 403/429/5xx with exponential back-off; reads X-RateLimit-Reset.
"""

import os
import sys
import json
import base64
import time
import requests

CERT_REPO         = os.environ.get("CERT_REPO",         "NovaDev404/NovaCerts")
CERT_PAT          = os.environ.get("CERT_REPO_PAT",      os.environ.get("GH_TOKEN", ""))
CERT_FOLDER       = os.environ.get("CERT_FOLDER",        "")
CERT_FOLDERS_JSON = os.environ.get("CERT_FOLDERS_JSON",  "")
BUILD_DIR         = "/tmp/build"
CERTS_DIR         = os.path.join(BUILD_DIR, "certs")

API         = "https://api.github.com"
MAX_RETRIES = 5
RETRY_CODES = {403, 429, 500, 502, 503, 504}

# Root-level folders that are never cert bundles
NON_CERT_FOLDERS = {
    "scripts", ".github", ".git", "docs", "tools",
    "readme", "assets", "ci", ".vscode",
}


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def gh_headers():
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if CERT_PAT:
        h["Authorization"] = f"Bearer {CERT_PAT}"
    return h

def _rate_limit_wait(response):
    """Sleep until X-RateLimit-Reset if present, else 15 s."""
    reset_ts = response.headers.get("X-RateLimit-Reset")
    if reset_ts:
        wait = max(1, int(reset_ts) - int(time.time())) + 2
        print(f"  ⏳ Rate limit — sleeping {wait}s until reset…")
        time.sleep(wait)
    else:
        time.sleep(15)

def gh_get(url, timeout=60):
    """Authenticated GET with retry/back-off."""
    for attempt in range(1, MAX_RETRIES + 1):
        r = requests.get(url, headers=gh_headers(), timeout=timeout,
                         allow_redirects=True)
        if r.status_code == 401:
            sys.exit("[ERROR] CERT_REPO_PAT is invalid or missing.")
        if r.status_code not in RETRY_CODES:
            return r
        print(f"  [WARN] HTTP {r.status_code} attempt {attempt}/{MAX_RETRIES}: {url}")
        if r.status_code in (403, 429):
            _rate_limit_wait(r)
        else:
            time.sleep(2 ** attempt)
    r.raise_for_status()
    return r

def raw_get(url, timeout=120):
    """
    Download raw bytes from CDN (objects.githubusercontent.com).
    These don't consume the authenticated API quota.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        r = requests.get(url, timeout=timeout, allow_redirects=True)
        if r.status_code not in RETRY_CODES:
            r.raise_for_status()
            return r.content
        print(f"  [WARN] Raw CDN HTTP {r.status_code} attempt {attempt}/{MAX_RETRIES}")
        time.sleep(2 ** attempt)
    r.raise_for_status()
    return b""


# ── GitHub repo helpers ───────────────────────────────────────────────────────

def list_folder(path):
    url = f"{API}/repos/{CERT_REPO}/contents/{path}"
    r   = gh_get(url)
    if r.status_code == 404:
        raise FileNotFoundError(f"Folder not found: {path}")
    r.raise_for_status()
    return r.json()

def download_file(item, dest_path):
    """
    Download one file described by a GitHub contents API item dict.
    Uses download_url (raw CDN) when available — no API quota cost.
    Falls back to fetching the full contents entry (base64 body).
    """
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)

    dl_url = item.get("download_url")
    if dl_url:
        content = raw_get(dl_url)
    else:
        r = gh_get(item["url"])
        r.raise_for_status()
        data = r.json()
        if data.get("encoding") == "base64":
            content = base64.b64decode(data["content"].replace("\n", ""))
        else:
            content = raw_get(data["download_url"])

    with open(dest_path, "wb") as f:
        f.write(content)
    print(f"  ✓ {os.path.basename(dest_path)} ({len(content):,} bytes)")
    return dest_path

def find_by_ext(items, ext):
    return next((i for i in items if i["name"].lower().endswith(ext.lower())), None)


# ── Folder helpers ────────────────────────────────────────────────────────────

def is_cert_folder(name):
    return name.lower() not in NON_CERT_FOLDERS and not name.startswith(".")


# ── Per-folder fetch ──────────────────────────────────────────────────────────

def fetch_one_folder(folder):
    """
    Download p12 + mobileprovision + optional password from one cert folder.
    Returns a result dict or None on failure (non-fatal).
    """
    print(f"\n── Fetching: {folder}")
    dest_dir = os.path.join(CERTS_DIR, folder)
    os.makedirs(dest_dir, exist_ok=True)

    try:
        items = list_folder(folder)
    except Exception as e:
        print(f"  [WARN] Cannot list '{folder}': {e} — skipping.")
        return None

    # Dive into a sub-folder if no p12 at top level
    subfolders = [i for i in items if i["type"] == "dir"]
    if subfolders and not any(i["name"].lower().endswith(".p12") for i in items):
        sub = subfolders[0]["name"]
        print(f"  Found sub-folder: {sub} — diving in")
        try:
            items = list_folder(f"{folder}/{sub}")
        except Exception as e:
            print(f"  [WARN] Cannot list sub-folder: {e} — skipping.")
            return None

    files = [i for i in items if i["type"] == "file"]
    print(f"  Files: {[f['name'] for f in files]}")

    p12_item = find_by_ext(files, ".p12")
    mp_item  = find_by_ext(files, ".mobileprovision")
    pw_item  = (
        next((f for f in files if f["name"].lower() in ("password.txt", "pass.txt")), None)
        or find_by_ext(files, ".txt")
    )

    if not p12_item:
        print(f"  [WARN] No .p12 — skipping.")
        return None
    if not mp_item:
        print(f"  [WARN] No .mobileprovision — skipping.")
        return None

    try:
        p12_path = download_file(p12_item, os.path.join(dest_dir, p12_item["name"]))
        mp_path  = download_file(mp_item,  os.path.join(dest_dir, mp_item["name"]))
    except Exception as e:
        print(f"  [ERROR] Download failed for '{folder}': {e} — skipping.")
        return None

    password = ""
    if pw_item:
        try:
            pw_path = download_file(pw_item, os.path.join(dest_dir, pw_item["name"]))
            with open(pw_path, "r", errors="ignore") as f:
                password = f.read().strip()
            print(f"  Password loaded ({len(password)} chars)")
        except Exception as e:
            print(f"  [WARN] Password download failed: {e} — using empty")
    else:
        print("  [WARN] No password file — using empty")

    return {"folder": folder, "p12_path": p12_path, "mp_path": mp_path, "password": password}


# ── Folder list resolution ────────────────────────────────────────────────────

def resolve_folders():
    if CERT_FOLDERS_JSON:
        try:
            raw = json.loads(CERT_FOLDERS_JSON)
            if isinstance(raw, list) and raw:
                clean   = [f for f in raw if is_cert_folder(f)]
                skipped = len(raw) - len(clean)
                if skipped:
                    print(f"Filtered {skipped} non-cert folder(s) from CERT_FOLDERS_JSON.")
                print(f"Using CERT_FOLDERS_JSON: {len(clean)} folder(s)")
                return clean
        except json.JSONDecodeError:
            print("[WARN] CERT_FOLDERS_JSON is not valid JSON — falling back.")

    if CERT_FOLDER:
        print(f"Using CERT_FOLDER: {CERT_FOLDER}")
        return [CERT_FOLDER]

    print(f"Auto-detecting cert folders from {CERT_REPO}…")
    root    = list_folder("")
    folders = sorted(
        [i["name"] for i in root if i["type"] == "dir" and is_cert_folder(i["name"])],
        reverse=True,
    )
    if not folders:
        sys.exit("[ERROR] No cert folders found in cert repo.")
    print(f"Auto-detected {len(folders)} folder(s).")
    return folders


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    os.makedirs(CERTS_DIR, exist_ok=True)
    os.makedirs(BUILD_DIR, exist_ok=True)

    folders = resolve_folders()
    results = []

    for folder in folders:
        info = fetch_one_folder(folder)
        if info:
            results.append(info)

    if not results:
        sys.exit("[ERROR] No valid certificate bundles downloaded.")

    manifest_path = os.path.join(BUILD_DIR, "certs_manifest.json")
    with open(manifest_path, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\n✅ {len(results)} cert bundle(s) ready.")
    for r in results:
        print(f"  • {r['folder']}")

    # Legacy single-cert compat (downstream scripts that read these files)
    first = results[0]
    with open(os.path.join(BUILD_DIR, "p12_path.txt"),      "w") as f: f.write(first["p12_path"])
    with open(os.path.join(BUILD_DIR, "mp_path.txt"),       "w") as f: f.write(first["mp_path"])
    with open(os.path.join(BUILD_DIR, "cert_password.txt"), "w") as f: f.write(first["password"])


if __name__ == "__main__":
    main()
