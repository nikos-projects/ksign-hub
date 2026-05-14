#!/usr/bin/env python3
"""
bundle_cert.py

For EACH app in /tmp/build/apps_manifest.json ×
    EACH certificate bundle in /tmp/build/certs_manifest.json:

  - Injects the cert into a fresh copy of the app's original.ipa
  - Patches Info.plist so each (app × cert) combo gets a UNIQUE
    CFBundleIdentifier and CFBundleVersion — iOS uses these to tell
    OTA installs apart.
  - Outputs: /tmp/build/bundled/<app_name>/<cert_folder>/ksign_bundled.ipa

The patched values are written to bundled_manifest.json so
generate_assets.py can mirror them in the per-cert manifest.plist.
"""

import os
import sys
import json
import re
import plistlib
import shutil
import zipfile
import tempfile
import uuid
import subprocess
from datetime import datetime, timezone

BUILD_DIR  = "/tmp/build"
BUNDLE_DIR = os.path.join(BUILD_DIR, "bundled")

BASE_BUNDLE_ID = os.environ.get("BUNDLE_ID", "com.nyasami.ksign")


# ── helpers ───────────────────────────────────────────────────────────────────

def find_app_bundle(extract_dir):
    payload = os.path.join(extract_dir, "Payload")
    if not os.path.isdir(payload):
        sys.exit("[ERROR] No Payload/ directory found in IPA.")
    apps = [d for d in os.listdir(payload) if d.endswith(".app")]
    if not apps:
        sys.exit("[ERROR] No .app bundle found in Payload/.")
    return os.path.join(payload, apps[0]), apps[0].replace(".app", "")


def read_mobileprovision_name(mp_path):
    try:
        raw = open(mp_path, "rb").read().decode("utf-8", errors="ignore")
        m = re.search(r'<key>Name</key>\s*<string>([^<]+)</string>', raw)
        if m:
            return m.group(1)
    except Exception:
        pass
    return "Certificate"


def create_ksign_import_manifest(cert_folder_name, p12_name, mp_name, has_password):
    return json.dumps({
        "version":    1,
        "type":       "certificate_bundle",
        "name":       cert_folder_name,
        "files":      {"p12": p12_name, "mobileprovision": mp_name},
        "hasPassword": has_password,
        "autoImport": True,
    }, indent=2)


def patch_info_plist(plist_path, patches: dict):
    with open(plist_path, "rb") as f:
        data = plistlib.load(f)
    for key, value in patches.items():
        if key not in data:
            raise KeyError(f"Key '{key}' not found in Info.plist")
        data[key] = value
    with open(plist_path, "wb") as f:
        plistlib.dump(data, f, fmt=plistlib.FMT_XML)


def get_p12_expiry(p12_path, password=""):
    """
    Extract the certificate expiry date from a .p12 file using openssl.
    Returns (datetime_utc, days_remaining) or (None, None) on failure.
    """
    try:
        # Step 1: extract the PEM cert from the p12
        cmd_pem = [
            "openssl", "pkcs12",
            "-in", p12_path,
            "-nokeys", "-nomacver",
            "-passin", f"pass:{password}",
        ]
        pem_result = subprocess.run(cmd_pem, capture_output=True)
        if pem_result.returncode != 0:
            # Try legacy mode (older p12 formats)
            cmd_pem += ["-legacy"]
            pem_result = subprocess.run(cmd_pem, capture_output=True)
        if pem_result.returncode != 0:
            print(f"  [WARN] openssl pkcs12 failed: {pem_result.stderr.decode(errors='ignore')[:200]}")
            return None, None

        pem_data = pem_result.stdout

        # Step 2: read NotAfter from the PEM
        cmd_dates = ["openssl", "x509", "-noout", "-enddate"]
        dates_result = subprocess.run(cmd_dates, input=pem_data, capture_output=True)
        if dates_result.returncode != 0:
            print(f"  [WARN] openssl x509 failed: {dates_result.stderr.decode(errors='ignore')[:200]}")
            return None, None

        # Output looks like: notAfter=Nov 12 23:59:59 2025 GMT
        line = dates_result.stdout.decode(errors="ignore").strip()
        m = re.search(r"notAfter=(.+)", line)
        if not m:
            return None, None

        date_str = m.group(1).strip()
        expiry   = datetime.strptime(date_str, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        now      = datetime.now(timezone.utc)
        days_left = (expiry - now).days

        print(f"  ✓ Cert expiry: {expiry.strftime('%Y-%m-%d')} ({days_left} days left)")
        return expiry, days_left

    except Exception as e:
        print(f"  [WARN] Could not read p12 expiry: {e}")
        return None, None


def safe_slug(name, maxlen=24):
    slug = re.sub(r"[^a-z0-9]", "", name.lower())
    return slug[:maxlen] or "cert"


# ── core injection ────────────────────────────────────────────────────────────

def inject_certs_into_ipa(input_ipa, output_ipa, p12_path, mp_path, password,
                          unique_bundle_id, unique_bundle_version):
    print(f"  Input IPA  : {input_ipa}")
    print(f"  Output IPA : {output_ipa}")
    print(f"  Bundle ID  : {unique_bundle_id}")
    print(f"  Bundle ver : {unique_bundle_version}")

    with tempfile.TemporaryDirectory() as tmpdir:
        with zipfile.ZipFile(input_ipa, "r") as zf:
            zf.extractall(tmpdir)

        app_path, app_name = find_app_bundle(tmpdir)
        print(f"  App bundle : {app_name}.app")

        # ── Patch Info.plist ─────────────────────────────────────────────────
        info_plist_path = os.path.join(app_path, "Info.plist")
        if not os.path.exists(info_plist_path):
            raise FileNotFoundError("Info.plist not found inside .app bundle")

        with open(info_plist_path, "rb") as _f:
            _data = plistlib.load(_f)

        patches = {
            "CFBundleIdentifier": unique_bundle_id,
            "CFBundleVersion":    unique_bundle_version,
        }
        if "CFBundleShortVersionString" in _data:
            patches["CFBundleShortVersionString"] = unique_bundle_version

        patch_info_plist(info_plist_path, patches)
        print(f"  ✓ Info.plist patched ({len(patches)} keys)")

        # ── Cert injection ───────────────────────────────────────────────────
        p12_name         = "cert.p12"
        mp_name          = "cert.mobileprovision"
        cert_folder_name = read_mobileprovision_name(mp_path)
        cert_folder_name = "".join(
            c for c in cert_folder_name if c.isalnum() or c in "._- "
        )[:40].strip() or "BundledCert"

        # Correct injection path per Asami's v1.5 release notes:
        # Ksign.app/signing-assets/<folder>/cert.p12 + cert.mobileprovision + cert.txt
        signing_assets_dir = os.path.join(app_path, "signing-assets", cert_folder_name)
        os.makedirs(signing_assets_dir, exist_ok=True)
        shutil.copy2(p12_path, os.path.join(signing_assets_dir, "cert.p12"))
        shutil.copy2(mp_path,  os.path.join(signing_assets_dir, "cert.mobileprovision"))
        if password:
            open(os.path.join(signing_assets_dir, "cert.txt"), "w").write(password)
        print(f"  ✓ Injected → signing-assets/{cert_folder_name}/")

        # ── Repack ───────────────────────────────────────────────────────────
        os.makedirs(os.path.dirname(output_ipa), exist_ok=True)
        with zipfile.ZipFile(output_ipa, "w",
                             compression=zipfile.ZIP_DEFLATED,
                             compresslevel=6) as zout:
            for root, dirs, files in os.walk(tmpdir):
                for fname in files:
                    fpath   = os.path.join(root, fname)
                    arcname = os.path.relpath(fpath, tmpdir)
                    zout.write(fpath, arcname)

    size = os.path.getsize(output_ipa)
    print(f"  ✅ Done: {output_ipa} ({size:,} bytes)")
    return output_ipa


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    certs_manifest_path = os.path.join(BUILD_DIR, "certs_manifest.json")
    apps_manifest_path  = os.path.join(BUILD_DIR, "apps_manifest.json")

    if not os.path.exists(certs_manifest_path):
        sys.exit(f"[ERROR] certs_manifest.json not found. Run fetch_cert.py first.")
    if not os.path.exists(apps_manifest_path):
        sys.exit(f"[ERROR] apps_manifest.json not found. Run fetch_ipa.py first.")

    with open(certs_manifest_path) as f:
        cert_bundles = json.load(f)
    with open(apps_manifest_path) as f:
        apps = json.load(f)

    if not cert_bundles:
        sys.exit("[ERROR] certs_manifest.json is empty.")
    if not apps:
        sys.exit("[ERROR] apps_manifest.json is empty.")

    os.makedirs(BUNDLE_DIR, exist_ok=True)
    output_manifest = []
    total = len(apps) * len(cert_bundles)
    n     = 0

    for app in apps:
        app_name  = app["app_name"]
        input_ipa = app["ipa_path"]
        version   = app["version"]

        if not os.path.exists(input_ipa):
            print(f"[WARN] IPA not found for {app_name}: {input_ipa} — skipping.")
            continue

        print(f"\n══ App: {app_name} @ {version} ({len(cert_bundles)} cert(s))")

        for i, bundle in enumerate(cert_bundles):
            n    += 1
            folder   = bundle["folder"]
            p12_path = bundle["p12_path"]
            mp_path  = bundle["mp_path"]
            password = bundle.get("password", "")

            print(f"\n  [{n}/{total}] Cert: {folder}")

            app_slug  = safe_slug(app_name)
            cert_slug = safe_slug(folder)

            # Read the real bundle ID from the IPA's Info.plist BEFORE we patch it
            original_bundle_id = BASE_BUNDLE_ID  # fallback
            try:
                with zipfile.ZipFile(input_ipa, "r") as zf:
                    plist_candidates = [
                        n for n in zf.namelist()
                        if n.endswith(".app/Info.plist") and n.startswith("Payload/")
                    ]
                    if plist_candidates:
                        with zf.open(plist_candidates[0]) as pf:
                            pdata = plistlib.load(pf)
                            original_bundle_id = pdata.get("CFBundleIdentifier", BASE_BUNDLE_ID)
                            print(f"  ✓ Original bundle ID: {original_bundle_id}")
            except Exception as e:
                print(f"  [WARN] Could not read original bundle ID: {e}")

            # Unique patched ID: needed so iOS treats each cert variant as a distinct app
            unique_bundle_id      = f"{original_bundle_id}.{cert_slug}"
            unique_bundle_version = f"1.{apps.index(app)}.{i}"

            # Extract expiry from .p12 before bundling
            expiry_dt, days_left = get_p12_expiry(p12_path, password)
            expiry_str = expiry_dt.strftime("%Y-%m-%d") if expiry_dt else "unknown"

            out_dir    = os.path.join(BUNDLE_DIR, app_name, folder)
            os.makedirs(out_dir, exist_ok=True)
            output_ipa = os.path.join(out_dir, "ksign_bundled.ipa")

            try:
                inject_certs_into_ipa(
                    input_ipa, output_ipa,
                    p12_path, mp_path, password,
                    unique_bundle_id,
                    unique_bundle_version,
                )
                output_manifest.append({
                    "app_name":          app_name,
                    "app_version":       version,
                    "folder":            folder,
                    "p12_path":          p12_path,
                    "mp_path":           mp_path,
                    "password":          password,
                    "bundled_ipa":       output_ipa,
                    "bundle_id":         unique_bundle_id,       # used in manifest.plist
                    "original_bundle_id": original_bundle_id,    # displayed on website
                    "bundle_version":    unique_bundle_version,
                    "cert_expiry":       expiry_str,
                    "cert_days_left":    days_left,
                    "comment":           app.get("comment", ""),
                    "display_name":      app.get("display_name", ""),
                })
            except Exception as e:
                print(f"  [ERROR] Failed to bundle '{app_name}' × '{folder}': {e}")
                continue

    if not output_manifest:
        sys.exit("[ERROR] No IPAs were successfully bundled.")

    out_manifest_path = os.path.join(BUILD_DIR, "bundled_manifest.json")
    with open(out_manifest_path, "w") as f:
        json.dump(output_manifest, f, indent=2)

    print(f"\n✅ {len(output_manifest)}/{total} bundled IPA(s) ready.")
    for item in output_manifest:
        print(f"  • {item['app_name']} × {item['folder']}")
        print(f"      bundle_id : {item['bundle_id']}")
        print(f"      version   : {item['bundle_version']}")

    # Legacy single-cert compat
    first = output_manifest[0]
    open(os.path.join(BUILD_DIR, "p12_path.txt"),      "w").write(first["p12_path"])
    open(os.path.join(BUILD_DIR, "mp_path.txt"),       "w").write(first["mp_path"])
    open(os.path.join(BUILD_DIR, "cert_password.txt"), "w").write(first["password"])


if __name__ == "__main__":
    main()
