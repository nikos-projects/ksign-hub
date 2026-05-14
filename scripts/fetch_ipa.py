#!/usr/bin/env python3
"""
fetch_ipa.py
Downloads .ipa files for ALL apps listed in the APPS_JSON env var
(set by check_updates.py).  Each IPA is saved as:
  /tmp/build/<app_name>/original.ipa

Falls back to the legacy IPA_URL / IPA_VERSION env vars for single-app
configs so old workflows keep working.
"""

import os
import sys
import re
import json
import zipfile
import requests

GH_TOKEN  = os.environ.get("GH_TOKEN",  "")
APPS_JSON = os.environ.get("APPS_JSON", "")

# Legacy single-app fallback
IPA_URL     = os.environ.get("IPA_URL",     "")
IPA_VERSION = os.environ.get("IPA_VERSION", "unknown")
IPA_REPO    = os.environ.get("IPA_REPO",    "nyasami/ksign")

BUILD_DIR = "/tmp/build"
API       = "https://api.github.com"


def gh_headers():
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if GH_TOKEN:
        h["Authorization"] = f"Bearer {GH_TOKEN}"
    return h


def download_ipa(url, dest):
    print(f"  Downloading: {url}")
    headers = {**gh_headers(), "Accept": "application/octet-stream"}

    with requests.get(url, headers=headers, stream=True,
                      allow_redirects=True, timeout=300) as r:
        r.raise_for_status()
        total      = int(r.headers.get("content-length", 0))
        downloaded = 0
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=256 * 1024):
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    print(f"\r  {downloaded/total*100:.1f}%  "
                          f"({downloaded:,}/{total:,} bytes)", end="")
    print(f"\n  ✅ Saved {downloaded:,} bytes → {dest}")
    return downloaded


def validate_ipa(path):
    if not zipfile.is_zipfile(path):
        sys.exit(f"[ERROR] Not a valid IPA/ZIP: {path}")
    print(f"  ✅ IPA integrity OK (valid ZIP)")


def resolve_apps():
    """
    Return a list of dicts: [{app_name, version, ipa_url}, ...].
    Tries APPS_JSON first, then the legacy single-app env vars.
    """
    if APPS_JSON:
        try:
            apps = json.loads(APPS_JSON)
            if apps:
                return apps
        except json.JSONDecodeError:
            print("[WARN] APPS_JSON is not valid JSON — falling back to single-app mode.")

    # Legacy: single-app via IPA_URL / IPA_VERSION
    if IPA_URL and IPA_URL.lower().endswith(".ipa"):
        app_name = IPA_REPO.split("/")[-1]
        return [{"app_name": app_name, "version": IPA_VERSION, "ipa_url": IPA_URL,
                 "repo": IPA_REPO}]

    # Last resort: probe the IPA_REPO releases
    print("APPS_JSON not set and IPA_URL invalid — probing IPA_REPO releases…")
    app_name = IPA_REPO.split("/")[-1]
    r = requests.get(f"{API}/repos/{IPA_REPO}/releases/latest",
                     headers=gh_headers(), timeout=30)
    if r.ok:
        rel = r.json()
        ver = rel.get("tag_name", "unknown")
        for asset in rel.get("assets", []):
            if asset["name"].lower().endswith(".ipa"):
                return [{"app_name": app_name, "version": ver,
                         "ipa_url": asset["browser_download_url"], "repo": IPA_REPO}]
        for url in re.findall(r'https?://\S+\.ipa', rel.get("body", "")):
            return [{"app_name": app_name, "version": ver, "ipa_url": url, "repo": IPA_REPO}]

    sys.exit(f"[ERROR] Could not resolve any IPA to download.")


def main():
    os.makedirs(BUILD_DIR, exist_ok=True)
    apps = resolve_apps()

    if not apps:
        sys.exit("[ERROR] No apps to download.")

    print(f"Downloading IPAs for {len(apps)} app(s)…")
    downloaded_apps = []

    for app in apps:
        app_name = app.get("app_name") or app.get("repo", "unknown").split("/")[-1]
        version  = app.get("version", "unknown")
        ipa_url  = app.get("ipa_url", "")

        print(f"\n── {app_name} @ {version}")

        if not ipa_url or not ipa_url.lower().endswith(".ipa"):
            print(f"  [WARN] No valid IPA URL for {app_name} — skipping.")
            continue

        app_dir  = os.path.join(BUILD_DIR, app_name)
        dest     = os.path.join(app_dir, "original.ipa")
        os.makedirs(app_dir, exist_ok=True)

        try:
            download_ipa(ipa_url, dest)
            validate_ipa(dest)
        except Exception as e:
            print(f"  [ERROR] Failed to download {app_name}: {e} — skipping.")
            continue

        # Persist version for downstream scripts
        with open(os.path.join(app_dir, "ipa_version.txt"), "w") as f:
            f.write(version)

        downloaded_apps.append({
            "app_name":    app_name,
            "version":     version,
            "ipa_path":    dest,
            "repo":        app.get("repo", ""),
            "comment":     app.get("comment", ""),
            "display_name": app.get("display_name", ""),
        })

    if not downloaded_apps:
        sys.exit("[ERROR] No IPAs were successfully downloaded.")

    # Write a manifest so downstream scripts know what was fetched
    manifest_path = os.path.join(BUILD_DIR, "apps_manifest.json")
    with open(manifest_path, "w") as f:
        json.dump(downloaded_apps, f, indent=2)

    print(f"\n✅ {len(downloaded_apps)} IPA(s) downloaded:")
    for a in downloaded_apps:
        print(f"  • {a['app_name']} {a['version']} → {a['ipa_path']}")

    # Legacy single-app compat
    first = downloaded_apps[0]
    with open(os.path.join(BUILD_DIR, "ipa_version.txt"), "w") as f:
        f.write(first["version"])


if __name__ == "__main__":
    main()
