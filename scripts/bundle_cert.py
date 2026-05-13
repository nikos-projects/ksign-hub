#!/usr/bin/env python3
"""
bundle_cert.py

For EACH certificate bundle in /tmp/build/certs_manifest.json:
  - Injects the cert into a fresh copy of ksign_original.ipa
  - Patches Info.plist so each cert gets a UNIQUE CFBundleIdentifier
    and CFBundleVersion — iOS uses these to distinguish OTA installs.
    Without unique values every cert looks like the same app and iOS
    refuses with "KSign couldn't be installed, try again later".
  - Outputs /tmp/build/bundled/<folder_name>/ksign_bundled.ipa

The patched values are written to the manifest so generate_assets.py
can mirror them exactly in the per-cert manifest.plist.
"""

import os
import sys
import json
import re
import shutil
import zipfile
import tempfile

BUILD_DIR  = "/tmp/build"
INPUT_IPA  = os.path.join(BUILD_DIR, "ksign_original.ipa")
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
        "version": 1,
        "type": "certificate_bundle",
        "name": cert_folder_name,
        "files": {"p12": p12_name, "mobileprovision": mp_name},
        "hasPassword": has_password,
        "autoImport": True,
    }, indent=2)


def plist_set_string(plist_bytes, key, value):
    """
    Replace the <string> value that immediately follows <key>KEY</key>
    in a binary or text plist that has been decoded to bytes.

    Works on text XML plists only (IPA Info.plist is always XML).
    Raises ValueError if the key is not found.
    """
    text = plist_bytes.decode("utf-8", errors="replace")

    pattern = rf'(<key>{re.escape(key)}</key>\s*<string>)[^<]*(</string>)'
    replacement = rf'\g<1>{re.escape(value)}\2'
    new_text, n = re.subn(pattern, replacement, text)
    if n == 0:
        raise ValueError(f"Key '{key}' not found in Info.plist")
    return new_text.encode("utf-8")


def safe_slug(name, maxlen=24):
    """Lowercase alphanumeric slug, safe for use as a bundle-id component."""
    slug = re.sub(r"[^a-z0-9]", "", name.lower())
    return slug[:maxlen] or "cert"


# ── core injection ────────────────────────────────────────────────────────────

def inject_certs_into_ipa(input_ipa, output_ipa, p12_path, mp_path, password,
                          unique_bundle_id, unique_bundle_version):
    """
    Extract IPA → inject certs → patch Info.plist identifiers → repack.

    unique_bundle_id      — written to CFBundleIdentifier in Info.plist
    unique_bundle_version — written to CFBundleVersion AND CFBundleShortVersionString
    """
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

        plist_bytes = open(info_plist_path, "rb").read()

        # CFBundleIdentifier — iOS uses this as the app's unique identity.
        # Two IPAs with the same value = iOS refuses the second install.
        plist_bytes = plist_set_string(plist_bytes, "CFBundleIdentifier", unique_bundle_id)

        # CFBundleVersion — must also be unique; iOS caches (id, version) pairs.
        plist_bytes = plist_set_string(plist_bytes, "CFBundleVersion", unique_bundle_version)

        # CFBundleShortVersionString — what users see; keep consistent.
        try:
            plist_bytes = plist_set_string(plist_bytes, "CFBundleShortVersionString",
                                           unique_bundle_version)
        except ValueError:
            pass  # optional key, skip if absent

        with open(info_plist_path, "wb") as f:
            f.write(plist_bytes)
        print(f"  ✓ Info.plist patched")

        # ── Cert injection ───────────────────────────────────────────────────
        p12_name = "cert.p12"
        mp_name  = "cert.mobileprovision"
        cert_folder_name = read_mobileprovision_name(mp_path)
        cert_folder_name = "".join(
            c for c in cert_folder_name if c.isalnum() or c in "._- "
        )[:40].strip() or "BundledCert"

        # Injection 1: Documents/Certificates/
        cert_dest_dir = os.path.join(app_path, "Documents", "Certificates", cert_folder_name)
        os.makedirs(cert_dest_dir, exist_ok=True)
        shutil.copy2(p12_path, os.path.join(cert_dest_dir, p12_name))
        shutil.copy2(mp_path,  os.path.join(cert_dest_dir, mp_name))
        if password:
            open(os.path.join(cert_dest_dir, "password.txt"), "w").write(password)
        print(f"  ✓ Injected → Documents/Certificates/{cert_folder_name}/")

        # Injection 2: import.ksign manifest
        manifest_json = create_ksign_import_manifest(
            cert_folder_name, p12_name, mp_name, bool(password)
        )
        open(os.path.join(app_path, "import.ksign"), "w").write(manifest_json)
        print(f"  ✓ import.ksign written")

        # Injection 3: Library/Application Support/
        lib_cert_dir = os.path.join(
            app_path, "Library", "Application Support",
            "KSign", "Certificates", cert_folder_name
        )
        os.makedirs(lib_cert_dir, exist_ok=True)
        shutil.copy2(p12_path, os.path.join(lib_cert_dir, p12_name))
        shutil.copy2(mp_path,  os.path.join(lib_cert_dir, mp_name))
        if password:
            open(os.path.join(lib_cert_dir, "password.txt"), "w").write(password)
        print(f"  ✓ Injected → Library/Application Support/KSign/Certificates/")

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
    manifest_path = os.path.join(BUILD_DIR, "certs_manifest.json")
    if not os.path.exists(manifest_path):
        sys.exit(f"[ERROR] certs_manifest.json not found. Run fetch_cert.py first.")

    with open(manifest_path) as f:
        cert_bundles = json.load(f)

    if not cert_bundles:
        sys.exit("[ERROR] certs_manifest.json is empty.")

    if not os.path.exists(INPUT_IPA):
        sys.exit(f"[ERROR] Original IPA not found at {INPUT_IPA}. Run fetch_ipa.py first.")

    os.makedirs(BUNDLE_DIR, exist_ok=True)
    output_manifest = []

    for i, bundle in enumerate(cert_bundles):
        folder   = bundle["folder"]
        p12_path = bundle["p12_path"]
        mp_path  = bundle["mp_path"]
        password = bundle.get("password", "")

        print(f"\n[{i+1}/{len(cert_bundles)}] Bundling cert: {folder}")

        # Build unique bundle-id: com.nyasami.ksign.globaltakeoff
        # safe_slug strips everything non-alphanumeric so it's a valid
        # bundle-id component regardless of what's in the folder name.
        id_suffix        = safe_slug(folder)
        unique_bundle_id = f"{BASE_BUNDLE_ID}.{id_suffix}"

        # Unique version: 1.0.<index> — simple, always distinct
        unique_bundle_version = f"1.0.{i}"

        out_dir    = os.path.join(BUNDLE_DIR, folder)
        os.makedirs(out_dir, exist_ok=True)
        output_ipa = os.path.join(out_dir, "ksign_bundled.ipa")

        try:
            inject_certs_into_ipa(
                INPUT_IPA, output_ipa,
                p12_path, mp_path, password,
                unique_bundle_id,
                unique_bundle_version,
            )
            output_manifest.append({
                "folder":         folder,
                "p12_path":       p12_path,
                "mp_path":        mp_path,
                "password":       password,
                "bundled_ipa":    output_ipa,
                "bundle_id":      unique_bundle_id,      # ← passed to generate_assets
                "bundle_version": unique_bundle_version, # ← passed to generate_assets
            })
        except Exception as e:
            print(f"  [ERROR] Failed to bundle cert '{folder}': {e}")
            continue

    if not output_manifest:
        sys.exit("[ERROR] No IPAs were successfully bundled.")

    out_manifest_path = os.path.join(BUILD_DIR, "bundled_manifest.json")
    with open(out_manifest_path, "w") as f:
        json.dump(output_manifest, f, indent=2)

    print(f"\n✅ {len(output_manifest)} bundled IPA(s) ready.")
    for item in output_manifest:
        print(f"  • {item['folder']}")
        print(f"      bundle_id  : {item['bundle_id']}")
        print(f"      version    : {item['bundle_version']}")
        print(f"      ipa        : {item['bundled_ipa']}")

    # Legacy single-cert compat
    first = output_manifest[0]
    open(os.path.join(BUILD_DIR, "p12_path.txt"),      "w").write(first["p12_path"])
    open(os.path.join(BUILD_DIR, "mp_path.txt"),       "w").write(first["mp_path"])
    open(os.path.join(BUILD_DIR, "cert_password.txt"), "w").write(first["password"])


if __name__ == "__main__":
    main()
