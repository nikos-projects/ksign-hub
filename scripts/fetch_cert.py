#!/usr/bin/env python3
"""
fetch_cert.py
Downloads ALL certificate bundles (.p12, .mobileprovision, password.txt)
from the NovaCerts GitHub repo into /tmp/build/certs/<folder_name>/.

If CERT_FOLDERS_JSON is set (a JSON array of folder names), all listed folders
are fetched. Falls back to CERT_FOLDER (single) or auto-detect latest.
"""

import os
import sys
import json
import base64
import requests

CERT_REPO          = os.environ.get("CERT_REPO",          "NovaDev404/NovaCerts")
CERT_PAT           = os.environ.get("CERT_REPO_PAT",       os.environ.get("GH_TOKEN", ""))
CERT_FOLDER        = os.environ.get("CERT_FOLDER",         "")
CERT_FOLDERS_JSON  = os.environ.get("CERT_FOLDERS_JSON",   "")
BUILD_DIR          = "/tmp/build"
CERTS_DIR          = os.path.join(BUILD_DIR, "certs")

API = "https://api.github.com"

def gh_headers(token):
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h

def list_folder(path):
    url = f"{API}/repos/{CERT_REPO}/contents/{path}"
    r = requests.get(url, headers=gh_headers(CERT_PAT), timeout=30)
    if r.status_code == 401:
        sys.exit("[ERROR] CERT_REPO_PAT is missing or invalid. Add it as a repo secret.")
    r.raise_for_status()
    return r.json()

def download_file(api_url, dest_path):
    """Download via GitHub contents API (handles files up to 100MB)."""
    r = requests.get(api_url, headers=gh_headers(CERT_PAT), timeout=60)
    r.raise_for_status()
    data = r.json()

    if data.get("encoding") == "base64":
        content = base64.b64decode(data["content"].replace("\n", ""))
    else:
        dl = requests.get(data["download_url"], headers=gh_headers(CERT_PAT), timeout=120)
        dl.raise_for_status()
        content = dl.content

    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with open(dest_path, "wb") as f:
        f.write(content)
    print(f"  ✓ {os.path.basename(dest_path)} ({len(content):,} bytes)")
    return dest_path

def find_file_by_ext(items, ext):
    return next((i for i in items if i["name"].lower().endswith(ext.lower())), None)

def fetch_one_folder(folder):
    """
    Download p12 + mobileprovision + optional password from a single cert folder.
    Returns a dict with paths and metadata, or None on failure.
    """
    print(f"\n── Fetching cert folder: {folder}")
    dest_dir = os.path.join(CERTS_DIR, folder)
    os.makedirs(dest_dir, exist_ok=True)

    try:
        items = list_folder(folder)
    except Exception as e:
        print(f"  [WARN] Could not list folder '{folder}': {e}")
        return None

    # Dive into a sub-folder if no p12 at top level
    subfolders = [i for i in items if i["type"] == "dir"]
    if subfolders and not any(i["name"].lower().endswith(".p12") for i in items):
        print(f"  Found sub-folder: {subfolders[0]['name']} — diving in")
        try:
            items = list_folder(f"{folder}/{subfolders[0]['name']}")
        except Exception as e:
            print(f"  [WARN] Could not list sub-folder: {e}")
            return None

    files = [i for i in items if i["type"] == "file"]
    print(f"  Files found: {[f['name'] for f in files]}")

    p12_item = find_file_by_ext(files, ".p12")
    mp_item  = find_file_by_ext(files, ".mobileprovision")
    pw_item  = (
        next((f for f in files if f["name"].lower() in ("password.txt", "pass.txt")), None)
        or find_file_by_ext(files, ".txt")
    )

    if not p12_item:
        print(f"  [WARN] No .p12 found in '{folder}' — skipping.")
        return None
    if not mp_item:
        print(f"  [WARN] No .mobileprovision found in '{folder}' — skipping.")
        return None

    p12_path = download_file(p12_item["url"], os.path.join(dest_dir, p12_item["name"]))
    mp_path  = download_file(mp_item["url"],  os.path.join(dest_dir, mp_item["name"]))

    password = ""
    if pw_item:
        pw_path  = download_file(pw_item["url"], os.path.join(dest_dir, pw_item["name"]))
        with open(pw_path, "r", errors="ignore") as f:
            password = f.read().strip()
        print(f"  Password loaded ({len(password)} chars)")
    else:
        print("  [WARN] No password file — using empty password")

    return {
        "folder":   folder,
        "p12_path": p12_path,
        "mp_path":  mp_path,
        "password": password,
    }

def resolve_folders():
    """Return the list of cert folder names to process."""
    if CERT_FOLDERS_JSON:
        try:
            folders = json.loads(CERT_FOLDERS_JSON)
            if isinstance(folders, list) and folders:
                print(f"Using CERT_FOLDERS_JSON: {folders}")
                return folders
        except json.JSONDecodeError:
            print("[WARN] CERT_FOLDERS_JSON is not valid JSON — falling back.")

    if CERT_FOLDER:
        print(f"Using CERT_FOLDER: {CERT_FOLDER}")
        return [CERT_FOLDER]

    print(f"No folder specified — auto-detecting from {CERT_REPO}...")
    root = list_folder("")
    folders = sorted(
        [i["name"] for i in root if i["type"] == "dir"],
        reverse=True
    )
    if not folders:
        sys.exit("[ERROR] No certificate folders found in cert repo.")
    print(f"Auto-detected folders: {folders}")
    return folders

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
        sys.exit("[ERROR] No valid certificate bundles could be downloaded.")

    # Write a manifest for downstream scripts
    manifest_path = os.path.join(BUILD_DIR, "certs_manifest.json")
    with open(manifest_path, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\n✅ {len(results)} cert bundle(s) ready.")
    for r in results:
        print(f"  • {r['folder']}: {r['p12_path']}")

    # Legacy single-cert compatibility (use first / latest cert)
    first = results[0]
    with open(os.path.join(BUILD_DIR, "p12_path.txt"),      "w") as f: f.write(first["p12_path"])
    with open(os.path.join(BUILD_DIR, "mp_path.txt"),       "w") as f: f.write(first["mp_path"])
    with open(os.path.join(BUILD_DIR, "cert_password.txt"), "w") as f: f.write(first["password"])

if __name__ == "__main__":
    main()
