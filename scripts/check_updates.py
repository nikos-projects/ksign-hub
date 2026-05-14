#!/usr/bin/env python3
"""
check_updates.py
Reads apps from <repo_root>/appstosign.txt — one entry per line, either:
  - owner/repo          → looks up latest GitHub release with a .ipa asset
  - https://…/foo.ipa  → treats the URL as a pinned direct download (always
                          re-downloads if the cert changed; no version tracking)

Lines starting with # and blank lines are ignored.
"""

import os
import sys
import json
import re
import requests

CERT_REPO    = os.environ.get("CERT_REPO",      "NovaDev404/NovaCerts")
GH_TOKEN     = os.environ.get("GH_TOKEN",        "")
CERT_PAT     = os.environ.get("CERT_REPO_PAT",   GH_TOKEN)
FORCE        = os.environ.get("FORCE_REBUILD",   "false").lower() == "true"
STATE_DIR    = ".state"
APPS_FILE    = os.path.join(os.path.dirname(__file__), "..", "appstosign.txt")

API = "https://api.github.com"

# Root-level folders that are never cert bundles
NON_CERT_FOLDERS = {
    "scripts", ".github", ".git", "docs", "tools",
    "readme", "assets", "ci", ".vscode",
}


def gh_headers(token=""):
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    tok = token or GH_TOKEN
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def set_output(name, value):
    env_file = os.environ.get("GITHUB_OUTPUT", "")
    if env_file:
        with open(env_file, "a") as f:
            f.write(f"{name}={value}\n")
    else:
        print(f"::set-output name={name}::{value}")


def read_state(filename):
    path = os.path.join(STATE_DIR, filename)
    if os.path.exists(path):
        return open(path).read().strip()
    return ""


# ── Load apps list ────────────────────────────────────────────────────────────

def load_apps():
    """
    Read appstosign.txt. Each line is either:
      owner/repo
      https://…/foo.ipa
      owner/repo //optional comment shown on the website
      https://…/foo.ipa //name=My App  optional comment
    Lines starting with # and blank lines are ignored.
    Returns a list of dicts: {entry, comment, display_name}
    """
    candidates = [
        APPS_FILE,
        "appstosign.txt",
        os.path.join(os.getcwd(), "appstosign.txt"),
    ]
    for path in candidates:
        if os.path.exists(path):
            apps = []
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    comment      = ""
                    display_name = ""
                    if " //" in line:
                        parts   = line.split(" //", 1)
                        line    = parts[0].strip()
                        comment = parts[1].strip()
                        # Pull out name=Foo if present, leave rest as comment
                        name_m = re.search(r'\bname=([^\s,]+)', comment, re.I)
                        if name_m:
                            display_name = name_m.group(1).strip()
                            comment = re.sub(r'\bname=[^\s,]+\s*,?\s*', '', comment, flags=re.I).strip()
                    apps.append({"entry": line, "comment": comment, "display_name": display_name})
            if apps:
                print(f"Loaded {len(apps)} app(s) from {path}")
                return apps

    # Legacy fallback
    fallback = os.environ.get("IPA_REPO", "nyasami/ksign")
    print(f"[WARN] appstosign.txt not found — falling back to IPA_REPO={fallback}")
    return [{"entry": fallback, "comment": "", "display_name": ""}]


# ── Cert repo helpers ─────────────────────────────────────────────────────────

def get_all_cert_folders():
    url = f"{API}/repos/{CERT_REPO}/contents/"
    r = requests.get(url, headers=gh_headers(CERT_PAT), timeout=30)
    if r.status_code == 404:
        print(f"[WARN] Cert repo {CERT_REPO} not found — check CERT_REPO_PAT.")
        return []
    r.raise_for_status()
    items = r.json()

    folders = []
    for i in items:
        if i["type"] != "dir":
            continue
        name = i["name"]
        if name.lower() in NON_CERT_FOLDERS or name.startswith("."):
            continue
        folders.append(name)

    folders.sort(reverse=True)
    print(f"Discovered {len(folders)} cert folder(s).")
    return folders


# ── Per-app IPA release fetch ─────────────────────────────────────────────────

def get_latest_ipa_for_repo(repo):
    """
    Return (version, ipa_url, app_name) for the latest release of *repo*
    that has a .ipa asset.  .apk and other non-iOS files are skipped.
    Returns (None, None, app_name) if nothing is found.
    """
    app_name = repo.split("/")[-1]
    headers  = gh_headers(GH_TOKEN)

    # Try /releases/latest first, then paginated list
    for url in [
        f"{API}/repos/{repo}/releases/latest",
        f"{API}/repos/{repo}/releases",
    ]:
        r = requests.get(url, headers=headers, timeout=30)
        if not r.ok:
            continue

        payload = r.json()
        releases = payload if isinstance(payload, list) else [payload]

        for release in releases:
            version = release.get("tag_name", "unknown")
            assets  = release.get("assets", [])

            # Only .ipa — explicitly skip .apk and anything else
            ipa_assets = [
                a for a in assets
                if a["name"].lower().endswith(".ipa")
            ]

            if ipa_assets:
                url_dl = ipa_assets[0]["browser_download_url"]
                print(f"  [{repo}] Found IPA: {ipa_assets[0]['name']} @ {version}")
                return version, url_dl, app_name

            # Fallback: scrape .ipa URLs from release body (skip .apk)
            body = release.get("body", "")
            ipa_urls = [
                u for u in re.findall(r'https?://\S+', body)
                if u.lower().endswith(".ipa")
            ]
            if ipa_urls:
                print(f"  [{repo}] Found IPA URL in release body @ {version}")
                return version, ipa_urls[0], app_name

        break  # if /releases/latest returned 200 (even with no IPA), stop here

    print(f"  [WARN] No .ipa found for {repo}.")
    return None, None, app_name


# ── Entry-type detection & resolution ────────────────────────────────────────

def is_direct_url(entry):
    """True if the entry looks like a direct https:// link to a .ipa file."""
    return entry.lower().startswith("http") and entry.lower().endswith(".ipa")


def app_name_from_url(url):
    """Derive a slug app name from a direct URL, e.g. foo from /path/foo.ipa"""
    base = url.rstrip("/").split("/")[-1]           # foo.ipa
    name = re.sub(r"\.ipa$", "", base, flags=re.I) # foo
    name = re.sub(r"[^a-zA-Z0-9_-]", "_", name)   # sanitise
    return name or "app"


def resolve_entry(entry):
    """
    Given one line from appstosign.txt, return (version, ipa_url, app_name).

    Direct URL  → version is always "direct" (no version tracking; the URL
                  itself is assumed static — user updates the line to change).
    owner/repo  → queries GitHub releases API, picks the first .ipa asset.
    """
    if is_direct_url(entry):
        app_name = app_name_from_url(entry)
        print(f"  [direct URL] {app_name} → {entry}")
        return "direct", entry, app_name

    # Treat as owner/repo
    return get_latest_ipa_for_repo(entry)




def main():
    print(f"Checking cert repo : {CERT_REPO}")

    all_folders   = get_all_cert_folders()
    if not all_folders:
        sys.exit("[ERROR] No certificate folders found in cert repo.")

    latest_folder = all_folders[0]
    folders_json  = json.dumps(all_folders)
    last_cert     = read_state("last_cert")

    app_repos = load_apps()

    apps_info    = []
    should_build = FORCE or (latest_folder != last_cert)

    for item in app_repos:
        entry        = item["entry"]
        comment      = item.get("comment", "")
        display_name = item.get("display_name", "")
        print(f"\nChecking entry     : {entry}")
        if comment:
            print(f"  Comment          : {comment}")
        version, ipa_url, app_name = resolve_entry(entry)

        # For direct URLs, prefer display_name > url-derived slug
        if display_name:
            app_name = display_name

        # Direct URLs have no trackable version — cert change alone triggers rebuild
        if version == "direct":
            print(f"  Direct URL — version tracking skipped, cert change triggers rebuild")
        else:
            last_version = read_state(f"last_ipa_version_{app_name}")
            print(f"  Cached version   : {last_version}")
            print(f"  Latest version   : {version}")
            if version and version != last_version:
                should_build = True
                print(f"  → NEW version detected for {app_name}")

        apps_info.append({
            "repo":         entry,
            "app_name":     app_name,
            "version":      version  or "unknown",
            "ipa_url":      ipa_url  or "",
            "comment":      comment,
            "display_name": display_name,
        })

    apps_json = json.dumps(apps_info)

    print(f"\nAll cert folders   : {all_folders}")
    print(f"Latest cert folder : {latest_folder}")
    print(f"Apps               : {apps_json}")
    print(f"cert_changed       : {latest_folder != last_cert}")
    print(f"force              : {FORCE}")
    print(f"should_build       : {should_build}")

    set_output("should_build",      str(should_build).lower())
    set_output("cert_folder",       latest_folder)
    set_output("cert_folders_json", folders_json)
    set_output("apps_json",         apps_json)

    # Legacy single-app outputs (first app) for backwards compat
    if apps_info:
        first = apps_info[0]
        set_output("ipa_url",     first["ipa_url"])
        set_output("ipa_version", first["version"])

    if not should_build:
        print("\n✅ Nothing new — skipping build.")
    else:
        print(f"\n🔨 Changes detected — triggering build "
              f"({len(all_folders)} cert(s) × {len(apps_info)} app(s)).")


if __name__ == "__main__":
    main()
