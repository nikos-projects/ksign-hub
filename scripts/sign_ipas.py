#!/usr/bin/env python3
"""
sign_ipas.py
Reads /tmp/build/bundled_manifest.json, calls zsign for each entry,
and writes /tmp/build/signed_manifest.json.

No bash string interpolation → no issues with folder names that contain
parentheses, commas, or other shell-special characters.
"""

import json
import os
import re
import subprocess
import sys

BUILD_DIR       = "/tmp/build"
BUNDLED_MANIFEST = os.path.join(BUILD_DIR, "bundled_manifest.json")
SIGNED_DIR       = os.path.join(BUILD_DIR, "signed")
SIGNED_MANIFEST  = os.path.join(BUILD_DIR, "signed_manifest.json")


def slug(name: str) -> str:
    """Return a filesystem-safe slug for *name*."""
    return re.sub(r"_+", "_", re.sub(r"[^a-zA-Z0-9_-]", "_", name)).strip("_")


def main():
    if not os.path.exists(BUNDLED_MANIFEST):
        sys.exit(f"[ERROR] {BUNDLED_MANIFEST} not found — run bundle_cert.py first.")

    with open(BUNDLED_MANIFEST) as f:
        bundles = json.load(f)

    if not bundles:
        sys.exit("[ERROR] bundled_manifest.json is empty.")

    os.makedirs(SIGNED_DIR, exist_ok=True)
    signed_entries = []
    count = len(bundles)

    for i, entry in enumerate(bundles, 1):
        folder  = entry["folder"]
        p12     = entry["p12_path"]
        mp      = entry["mp_path"]
        passwd  = entry.get("password", "")
        bundled = entry["bundled_ipa"]

        output = os.path.join(SIGNED_DIR, f"{slug(folder)}_signed.ipa")

        print(f"\n[{i}/{count}] Signing: {folder}")
        print(f"  P12:     {p12}")
        print(f"  MP:      {mp}")
        print(f"  Bundled: {bundled}")
        print(f"  Output:  {output}")

        cmd = [
            "zsign",
            "-k", p12,
            "-p", passwd,
            "-m", mp,
            "-o", output,
            "-z", "9",
            bundled,
        ]

        result = subprocess.run(cmd, capture_output=False)
        if result.returncode != 0:
            print(f"  [ERROR] zsign exited {result.returncode} for '{folder}' — skipping.")
            continue

        size = os.path.getsize(output)
        print(f"  ✅ Signed: {size:,} bytes → {output}")

        signed_entries.append({
            "folder":     folder,
            "signed_ipa": output,
            "p12_path":   p12,
            "mp_path":    mp,
        })

    if not signed_entries:
        sys.exit("[ERROR] No IPAs were successfully signed.")

    with open(SIGNED_MANIFEST, "w") as f:
        json.dump(signed_entries, f, indent=2)

    print(f"\n✅ {len(signed_entries)}/{count} IPA(s) signed.")
    for e in signed_entries:
        print(f"  • {e['folder']} → {e['signed_ipa']}")


if __name__ == "__main__":
    main()
