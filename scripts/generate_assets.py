#!/usr/bin/env python3
"""
generate_assets.py
Generates for EACH signed IPA:
  - manifest-<slug>.plist  (iOS OTA install manifest)
  - ksign_<version>_<slug>_signed.ipa (copied to deploy dir)

Plus a single index.html listing ALL certificates with individual OTA buttons.
Reads /tmp/build/signed_manifest.json produced by the sign step.

Also extracts expiry dates from .p12 and .mobileprovision files and
displays days-remaining on each cert card with colour-coded urgency.
"""

import os
import sys
import json
import shutil
import hashlib
import re
import subprocess
import plistlib
import tempfile
from datetime import datetime, timezone
from urllib.parse import quote

BUILD_DIR  = "/tmp/build"
DEPLOY_DIR = "/tmp/deploy"

REPO       = os.environ.get("GITHUB_REPOSITORY", "owner/repo")
REPO_OWNER = REPO.split("/")[0]
REPO_NAME  = REPO.split("/")[-1]
VERSION    = os.environ.get("IPA_VERSION", "unknown")
BUNDLE_ID  = os.environ.get("BUNDLE_ID",  "com.nyasami.ksign")
APP_NAME   = os.environ.get("APP_NAME",   "KSign")

# GitHub Pages MUST be https:// — iOS refuses itms-services over http
BASE_URL   = f"https://{REPO_OWNER}.github.io/{REPO_NAME}"

DNS_URL    = "https://github.com/dns-khoindvn/top-country-stats/releases/download/DNS/khoindvn.io.vn.mobileconfig"


# ── Expiry extraction ─────────────────────────────────────────────────────────

def _days_until(dt: datetime) -> int:
    """Return signed days between now (UTC) and *dt*. Negative = already expired."""
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (dt - now).days


def extract_p12_expiry(p12_path: str, password: str) -> datetime | None:
    """
    Use openssl to read the certificate expiry from a .p12 file.
    Returns a timezone-aware datetime or None on failure.

    openssl pipeline:
      pkcs12 -in <p12> -passin pass:<pw> -nokeys -clcerts
        → prints the leaf cert in PEM
      x509 -noout -enddate
        → prints "notAfter=<date string>"
    """
    try:
        # Step 1: extract cert PEM from p12
        p12_cmd = [
            "openssl", "pkcs12",
            "-in", p12_path,
            "-passin", f"pass:{password}",
            "-nokeys", "-clcerts",
            "-legacy",          # needed for older p12 files on OpenSSL 3
        ]
        p12_result = subprocess.run(
            p12_cmd,
            capture_output=True, timeout=30,
        )

        pem_data = p12_result.stdout
        if not pem_data or b"BEGIN CERTIFICATE" not in pem_data:
            # Try without -legacy (some OpenSSL builds don't support it)
            p12_cmd_nol = [c for c in p12_cmd if c != "-legacy"]
            p12_result = subprocess.run(p12_cmd_nol, capture_output=True, timeout=30)
            pem_data = p12_result.stdout

        if not pem_data or b"BEGIN CERTIFICATE" not in pem_data:
            print(f"  [WARN] Could not extract PEM from p12: {p12_path}")
            return None

        # Step 2: get notAfter from PEM
        x509_result = subprocess.run(
            ["openssl", "x509", "-noout", "-enddate"],
            input=pem_data,
            capture_output=True, timeout=15,
        )
        out = x509_result.stdout.decode("utf-8", errors="ignore").strip()
        # out looks like: notAfter=Nov 15 12:00:00 2025 GMT
        m = re.search(r"notAfter=(.+)", out)
        if not m:
            print(f"  [WARN] Could not parse notAfter from: {out!r}")
            return None

        date_str = m.group(1).strip()
        dt = datetime.strptime(date_str, "%b %d %H:%M:%S %Y %Z")
        return dt.replace(tzinfo=timezone.utc)

    except Exception as e:
        print(f"  [WARN] P12 expiry extraction failed: {e}")
        return None


def extract_mp_expiry(mp_path: str) -> datetime | None:
    """
    Parse ExpirationDate from a .mobileprovision file.
    A mobileprovision is a CMS-signed blob; the embedded plist is between
    the two PEM-like headers. We strip the binary wrapper and parse with plistlib.
    Returns a timezone-aware datetime or None on failure.
    """
    try:
        with open(mp_path, "rb") as f:
            raw = f.read()

        # The embedded plist starts at "<?xml" or the binary plist magic b'\x62\x70\x6c\x69\x73\x74'
        # Most provisioning profiles contain an XML plist — find it.
        start = raw.find(b"<?xml")
        if start == -1:
            # Try binary plist
            start = raw.find(b"bplist")
        if start == -1:
            print(f"  [WARN] Could not locate embedded plist in {mp_path}")
            return None

        end = raw.find(b"</plist>", start)
        if end == -1:
            print(f"  [WARN] Could not find end of plist in {mp_path}")
            return None

        plist_bytes = raw[start: end + len(b"</plist>")]
        data = plistlib.loads(plist_bytes)

        exp = data.get("ExpirationDate")
        if not isinstance(exp, datetime):
            print(f"  [WARN] ExpirationDate missing or wrong type in {mp_path}")
            return None

        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return exp

    except Exception as e:
        print(f"  [WARN] Mobileprovision expiry extraction failed: {e}")
        return None


def expiry_info(p12_path: str, mp_path: str, password: str) -> dict:
    """
    Returns a dict with expiry info for both the p12 cert and mobileprovision.
    {
      "p12_expiry":  "2025-11-15" | None,
      "p12_days":    42 | None,
      "mp_expiry":   "2025-10-01" | None,
      "mp_days":     -3 | None,     # negative = already expired
      "worst_days":  -3 | None,     # the more urgent of the two
    }
    """
    p12_dt  = extract_p12_expiry(p12_path, password)
    mp_dt   = extract_mp_expiry(mp_path)

    p12_days = _days_until(p12_dt) if p12_dt else None
    mp_days  = _days_until(mp_dt)  if mp_dt  else None

    p12_expiry = p12_dt.strftime("%Y-%m-%d") if p12_dt else None
    mp_expiry  = mp_dt.strftime("%Y-%m-%d")  if mp_dt  else None

    known_days = [d for d in [p12_days, mp_days] if d is not None]
    worst_days = min(known_days) if known_days else None

    return {
        "p12_expiry":  p12_expiry,
        "p12_days":    p12_days,
        "mp_expiry":   mp_expiry,
        "mp_days":     mp_days,
        "worst_days":  worst_days,
    }


# ── Expiry chip renderer ──────────────────────────────────────────────────────

def _expiry_chip_style(days: int | None) -> tuple[str, str, str]:
    """
    Returns (label, color_var, bg_rgba) based on days remaining.
    Thresholds: >30 green, 8-30 yellow, 1-7 orange, <=0 red.
    """
    if days is None:
        return "Unknown", "#5a6180", "rgba(90,97,128,0.12)"
    if days <= 0:
        return "Expired", "#f87171", "rgba(248,113,113,0.12)"
    if days <= 7:
        return f"{days}d left", "#fb923c", "rgba(251,146,60,0.12)"
    if days <= 30:
        return f"{days}d left", "#fbbf24", "rgba(251,191,36,0.12)"
    return f"{days}d left", "#34d399", "rgba(52,211,153,0.10)"


def expiry_chips_html(info: dict) -> str:
    """Render two expiry chips: one for P12, one for mobileprovision."""
    chips = []
    for label, key_days, key_date, icon in [
        ("Cert",  "p12_days", "p12_expiry", "🔑"),
        ("Prov",  "mp_days",  "mp_expiry",  "📋"),
    ]:
        days = info.get(key_days)
        date = info.get(key_date) or "—"
        text, color, bg = _expiry_chip_style(days)
        border_color = color.replace(")", ", 0.35)").replace("rgb(", "rgba(") if "rgb" in color else color

        chips.append(
            f'<div class="expiry-chip" style="background:{bg};border-color:{color}33;" '
            f'title="{icon} {label} expires {date}">'
            f'  <span class="expiry-icon">{icon}</span>'
            f'  <div class="expiry-body">'
            f'    <span class="expiry-label">{label}</span>'
            f'    <span class="expiry-val" style="color:{color}">{text}</span>'
            f'  </div>'
            f'</div>'
        )

    return '<div class="expiry-row">' + "".join(chips) + "</div>"


# ── Helpers ───────────────────────────────────────────────────────────────────

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sanitize_slug(name):
    """Turn a cert folder name into a URL-safe slug."""
    return re.sub(r"_+", "_", re.sub(r"[^a-zA-Z0-9\-]", "_", name)).strip("_") or "cert"


def xml_escape(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def generate_plist(ipa_url, title, bundle_id, bundle_version):
    """
    iOS OTA manifest.

    Critical rules that cause 'try again later' when violated:
      1. bundle-identifier must exactly match the signed IPA's CFBundleIdentifier.
      2. bundle-version must be UNIQUE per cert — iOS uses (bundle-id, bundle-version)
         as a cache key; duplicates cause it to abort with a generic error.
      3. The IPA url AND the plist url referenced in itms-services:// must both be
         reachable over HTTPS. HTTP is blocked on iOS 17+.
      4. title should be unique and human-readable so users know what they installed.
    """
    safe_url   = xml_escape(ipa_url)
    safe_title = xml_escape(title)
    safe_bid   = xml_escape(bundle_id)
    safe_ver   = xml_escape(bundle_version)

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>items</key>
  <array>
    <dict>
      <key>assets</key>
      <array>
        <dict>
          <key>kind</key>
          <string>software-package</string>
          <key>url</key>
          <string>{safe_url}</string>
        </dict>
      </array>
      <key>metadata</key>
      <dict>
        <key>bundle-identifier</key>
        <string>{safe_bid}</string>
        <key>bundle-version</key>
        <string>{safe_ver}</string>
        <key>kind</key>
        <string>software</string>
        <key>title</key>
        <string>{safe_title}</string>
      </dict>
    </dict>
  </array>
</dict>
</plist>"""


def cert_card_html(idx, cert):
    folder    = cert["folder"]
    ipa_url   = cert["ipa_url"]
    plist_url = cert["plist_url"]
    sha       = cert["sha256"]
    size_mb   = cert["size_mb"]
    exp_info  = cert.get("expiry", {})

    # plist URL MUST be percent-encoded inside the itms-services query string
    itms_url  = f"itms-services://?action=download-manifest&url={quote(plist_url, safe='')}"
    short_sha = sha[:16]
    display   = folder.replace("_", " ").replace("-", " ").title()

    # Badge colour: reflect worst expiry state
    worst = exp_info.get("worst_days")
    if worst is None:
        badge_color = ""
        badge_label = "Signed"
    elif worst <= 0:
        badge_color = 'style="background:rgba(248,113,113,0.12);color:#f87171;border-color:#f8717133;"'
        badge_label = "Expired"
    elif worst <= 7:
        badge_color = 'style="background:rgba(251,146,60,0.12);color:#fb923c;border-color:#fb923c33;"'
        badge_label = "Expiring"
    elif worst <= 30:
        badge_color = 'style="background:rgba(251,191,36,0.10);color:#fbbf24;border-color:#fbbf2433;"'
        badge_label = "Valid"
    else:
        badge_color = ""
        badge_label = "Valid"

    expiry_html = expiry_chips_html(exp_info) if exp_info else ""

    return f"""
      <div class="cert-card" style="--card-index:{idx}">
        <div class="cert-header">
          <div class="cert-icon">🔐</div>
          <div class="cert-meta">
            <div class="cert-name">{display}</div>
            <div class="cert-folder-raw">{folder}</div>
          </div>
          <span class="badge" {badge_color}>{badge_label}</span>
        </div>

        {expiry_html}

        <div class="cert-details">
          <div class="detail-item">
            <span class="detail-label">Size</span>
            <span class="detail-val">{size_mb:.1f} MB</span>
          </div>
          <div class="detail-item">
            <span class="detail-label">SHA-256</span>
            <span class="detail-val mono">{short_sha}…</span>
          </div>
        </div>

        <div class="cert-actions">
          <a href="{itms_url}" class="install-btn">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" stroke-width="2.2"
                 stroke-linecap="round" stroke-linejoin="round">
              <path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20z"/>
              <path d="M8 12l4 4 4-4M12 8v8"/>
            </svg>
            Install via OTA
          </a>
          <a href="{ipa_url}" class="direct-btn" title="Download .ipa">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" stroke-width="2"
                 stroke-linecap="round" stroke-linejoin="round">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/>
            </svg>
            Download .ipa
          </a>
        </div>

        <details class="sha-details">
          <summary>Full SHA-256</summary>
          <div class="sha-box">{sha}</div>
        </details>
      </div>"""


def generate_html(certs, version, build_time):
    cert_count = len(certs)
    cards_html = "\n".join(cert_card_html(i, c) for i, c in enumerate(certs))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
  <title>{APP_NAME} — OTA Installer</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Syne:wght@400;600;700;800&display=swap" rel="stylesheet">
  <style>
    :root {{
      --bg:      #07070d;
      --surface: #0e0e18;
      --surf2:   #13131f;
      --border:  #1a1a2e;
      --border2: #252540;
      --accent:  #6c63ff;
      --accent2: #b06fff;
      --accent3: #00e5c0;
      --text:    #dde3f0;
      --muted:   #5a6180;
      --muted2:  #7a85a8;
      --r:       14px;
    }}

    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    html {{ scroll-behavior: smooth; }}

    body {{
      background: var(--bg);
      color: var(--text);
      font-family: 'Syne', sans-serif;
      min-height: 100vh;
      overflow-x: hidden;
    }}

    /* subtle grid overlay */
    body::after {{
      content: '';
      position: fixed; inset: 0;
      background-image:
        linear-gradient(var(--border) 1px, transparent 1px),
        linear-gradient(90deg, var(--border) 1px, transparent 1px);
      background-size: 48px 48px;
      opacity: 0.18;
      pointer-events: none;
      z-index: 0;
    }}

    .orb {{
      position: fixed; border-radius: 50%;
      filter: blur(90px); pointer-events: none; z-index: 0;
    }}
    .orb-1 {{
      width: 400px; height: 400px;
      background: radial-gradient(circle, rgba(108,99,255,0.13), transparent 70%);
      top: -120px; left: -120px;
    }}
    .orb-2 {{
      width: 320px; height: 320px;
      background: radial-gradient(circle, rgba(0,229,192,0.07), transparent 70%);
      bottom: 5%; right: -80px;
    }}

    /* ── Layout ── */
    .page {{
      position: relative; z-index: 1;
      max-width: 860px;
      margin: 0 auto;
      padding-top: 0;
      padding-bottom: calc(40px + env(safe-area-inset-bottom));
      padding-left:  max(16px, env(safe-area-inset-left));
      padding-right: max(16px, env(safe-area-inset-right));
    }}

    /* ── Hero ── */
    .hero {{
      padding: 3.5rem 0 2.5rem;
      text-align: center;
    }}

    .logo {{
      display: inline-flex; align-items: center; justify-content: center;
      width: 76px; height: 76px;
      background: linear-gradient(135deg, var(--accent), var(--accent2));
      border-radius: 22px;
      font-size: 2.2rem;
      margin-bottom: 1.4rem;
      box-shadow: 0 0 40px rgba(108,99,255,0.25);
      animation: logoFloat 4s ease-in-out infinite;
    }}
    @keyframes logoFloat {{
      0%,100% {{ transform: translateY(0); }}
      50%      {{ transform: translateY(-6px); }}
    }}

    .hero h1 {{
      font-size: clamp(1.55rem, 5vw, 2.8rem);
      font-weight: 800;
      letter-spacing: -0.03em;
      line-height: 1.1;
      background: linear-gradient(135deg, #fff 20%, var(--accent2) 65%, var(--accent3));
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
      margin-bottom: 0.5rem;
    }}

    .hero-sub {{
      color: var(--muted2);
      font-size: clamp(0.8rem, 2.5vw, 1rem);
      font-weight: 400;
    }}

    /* ── Stats bar ── */
    .stats-bar {{
      display: flex;
      justify-content: center;
      gap: 0.45rem;
      flex-wrap: wrap;
      margin: 1.75rem 0 2.25rem;
    }}

    .stat-pill {{
      display: inline-flex; align-items: center; gap: 0.4rem;
      padding: 0.38rem 0.85rem;
      background: var(--surface);
      border: 1px solid var(--border2);
      border-radius: 99px;
      font-family: 'Space Mono', monospace;
      font-size: 0.68rem;
      color: var(--muted2);
    }}
    .stat-pill .dot {{
      width: 6px; height: 6px; border-radius: 50%;
      background: var(--accent3); box-shadow: 0 0 6px var(--accent3);
    }}
    .stat-pill strong {{ color: var(--text); }}

    /* ── DNS banner ── */
    .dns-banner {{
      display: flex;
      align-items: center;
      gap: 0.9rem;
      background: linear-gradient(135deg, rgba(0,229,192,0.07), rgba(108,99,255,0.07));
      border: 1px solid rgba(0,229,192,0.25);
      border-radius: var(--r);
      padding: 0.9rem 1rem;
      margin-bottom: 2rem;
    }}

    .dns-icon {{
      font-size: 1.5rem;
      flex-shrink: 0;
      width: 40px; height: 40px;
      display: flex; align-items: center; justify-content: center;
      background: rgba(0,229,192,0.1);
      border: 1px solid rgba(0,229,192,0.2);
      border-radius: 10px;
    }}

    .dns-text {{ flex: 1; min-width: 0; }}
    .dns-title {{
      font-size: 0.85rem; font-weight: 700; color: var(--text);
      margin-bottom: 0.15rem;
    }}
    .dns-sub {{
      font-size: 0.72rem; color: var(--muted2); line-height: 1.4;
    }}

    .dns-btn {{
      flex-shrink: 0;
      display: inline-flex; align-items: center; justify-content: center;
      gap: 0.4rem;
      padding: 0.6rem 1rem;
      min-height: 44px;
      background: rgba(0,229,192,0.12);
      color: var(--accent3);
      border: 1px solid rgba(0,229,192,0.35);
      border-radius: 9px;
      font-family: 'Syne', sans-serif;
      font-size: 0.8rem; font-weight: 700;
      text-decoration: none;
      white-space: nowrap;
      transition: background 0.18s, box-shadow 0.18s;
      -webkit-tap-highlight-color: transparent;
    }}
    .dns-btn:active {{
      background: rgba(0,229,192,0.2);
      box-shadow: 0 0 14px rgba(0,229,192,0.2);
    }}

    /* ── Section heading ── */
    .section-head {{
      display: flex; align-items: center; gap: 0.75rem;
      margin-bottom: 1.1rem;
    }}
    .section-label {{
      font-family: 'Space Mono', monospace;
      font-size: 0.66rem;
      text-transform: uppercase; letter-spacing: 0.14em;
      color: var(--muted);
      white-space: nowrap;
    }}
    .section-line {{ flex: 1; height: 1px; background: var(--border2); }}

    /* ── Cert grid ── */
    .cert-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(min(100%, 300px), 1fr));
      gap: 0.9rem;
      margin-bottom: 2rem;
    }}

    /* ── Cert card ── */
    .cert-card {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--r);
      padding: 1.2rem;
      display: flex; flex-direction: column; gap: 0.9rem;
      opacity: 0; transform: translateY(14px);
      animation: cardIn 0.35s ease forwards;
      animation-delay: calc(var(--card-index) * 0.06s);
    }}
    @keyframes cardIn {{ to {{ opacity: 1; transform: translateY(0); }} }}
    .cert-card:hover {{
      border-color: var(--border2);
      box-shadow: 0 0 24px rgba(108,99,255,0.08);
    }}

    .cert-header {{ display: flex; align-items: center; gap: 0.75rem; }}

    .cert-icon {{
      font-size: 1.4rem; flex-shrink: 0;
      width: 38px; height: 38px;
      display: flex; align-items: center; justify-content: center;
      background: var(--surf2);
      border: 1px solid var(--border2);
      border-radius: 9px;
    }}

    .cert-meta {{ flex: 1; min-width: 0; }}
    .cert-name {{
      font-size: 0.88rem; font-weight: 700; color: var(--text);
      white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }}
    .cert-folder-raw {{
      font-family: 'Space Mono', monospace;
      font-size: 0.58rem; color: var(--muted);
      margin-top: 0.12rem;
      white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }}

    .badge {{
      flex-shrink: 0;
      font-size: 0.6rem; font-family: 'Space Mono', monospace;
      padding: 0.18rem 0.5rem; border-radius: 99px;
      background: rgba(0,229,192,0.08); color: var(--accent3);
      border: 1px solid rgba(0,229,192,0.2);
    }}
    .badge::before {{ content: '● '; font-size: 0.45rem; }}

    /* ── Expiry row ── */
    .expiry-row {{
      display: flex;
      gap: 0.45rem;
    }}

    .expiry-chip {{
      flex: 1;
      display: flex;
      align-items: center;
      gap: 0.4rem;
      padding: 0.42rem 0.6rem;
      border-radius: 8px;
      border: 1px solid transparent;
    }}

    .expiry-icon {{
      font-size: 0.8rem;
      flex-shrink: 0;
      line-height: 1;
    }}

    .expiry-body {{
      display: flex;
      flex-direction: column;
      gap: 0.05rem;
      min-width: 0;
    }}

    .expiry-label {{
      font-size: 0.52rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--muted);
      font-family: 'Space Mono', monospace;
    }}

    .expiry-val {{
      font-size: 0.72rem;
      font-weight: 700;
      font-family: 'Space Mono', monospace;
      white-space: nowrap;
    }}

    /* ── Detail chips ── */
    .cert-details {{ display: flex; gap: 0.45rem; }}
    .detail-item {{
      flex: 1;
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 0.5rem 0.65rem;
    }}
    .detail-label {{
      font-size: 0.58rem; text-transform: uppercase;
      letter-spacing: 0.08em; color: var(--muted);
      display: block; margin-bottom: 0.18rem;
    }}
    .detail-val {{ font-size: 0.8rem; color: var(--text); font-weight: 600; }}
    .detail-val.mono {{
      font-family: 'Space Mono', monospace;
      font-size: 0.65rem; font-weight: 400;
    }}

    /* ── Action buttons ── */
    .cert-actions {{
      display: flex;
      flex-direction: column;
      gap: 0.5rem;
    }}

    .install-btn {{
      width: 100%;
      display: flex; align-items: center; justify-content: center;
      gap: 0.45rem;
      padding: 0.82rem 1rem;
      min-height: 50px;
      background: linear-gradient(135deg, var(--accent), var(--accent2));
      color: #fff;
      font-family: 'Syne', sans-serif;
      font-size: 0.92rem; font-weight: 700;
      border-radius: 10px;
      text-decoration: none;
      box-shadow: 0 0 20px rgba(108,99,255,0.28);
      transition: opacity 0.15s, transform 0.12s;
      -webkit-tap-highlight-color: transparent;
    }}
    .install-btn:active {{ opacity: 0.78; transform: scale(0.98); }}

    .direct-btn {{
      width: 100%;
      display: flex; align-items: center; justify-content: center;
      gap: 0.35rem;
      padding: 0.7rem 1rem;
      min-height: 44px;
      background: var(--surf2);
      color: var(--muted2);
      font-size: 0.78rem; font-family: 'Space Mono', monospace;
      border: 1px solid var(--border2);
      border-radius: 10px;
      text-decoration: none;
      transition: color 0.15s, border-color 0.15s;
      -webkit-tap-highlight-color: transparent;
    }}
    .direct-btn:active {{ color: var(--text); border-color: var(--accent); }}

    /* ── SHA details ── */
    .sha-details {{ font-size: 0.7rem; }}
    .sha-details summary {{
      cursor: pointer; color: var(--muted);
      font-family: 'Space Mono', monospace; font-size: 0.62rem;
      user-select: none; list-style: none; outline: none;
    }}
    .sha-details summary::-webkit-details-marker {{ display: none; }}
    .sha-details summary::before {{ content: '▸ '; }}
    .sha-details[open] summary::before {{ content: '▾ '; }}
    .sha-box {{
      margin-top: 0.45rem;
      background: var(--bg); border: 1px solid var(--border);
      border-radius: 6px; padding: 0.45rem 0.65rem;
      font-family: 'Space Mono', monospace; font-size: 0.56rem;
      color: var(--muted); word-break: break-all; line-height: 1.6;
    }}

    /* ── How-to card ── */
    .info-card {{
      background: var(--surface); border: 1px solid var(--border);
      border-radius: var(--r); padding: 1.2rem; margin-bottom: 1rem;
    }}
    .steps {{ list-style: none; }}
    .steps li {{
      display: flex; gap: 0.8rem; align-items: flex-start;
      padding: 0.55rem 0; border-bottom: 1px solid var(--border);
      font-size: 0.84rem; color: var(--muted2); line-height: 1.5;
    }}
    .steps li:last-child {{ border-bottom: none; }}
    .step-num {{
      flex-shrink: 0; width: 22px; height: 22px;
      background: var(--border2); border-radius: 6px;
      display: flex; align-items: center; justify-content: center;
      font-family: 'Space Mono', monospace; font-size: 0.65rem;
      color: var(--accent2); margin-top: 2px;
    }}

    .warning {{
      background: rgba(255,180,0,0.05);
      border: 1px solid rgba(255,180,0,0.18);
      border-radius: 9px; padding: 0.8rem 0.95rem;
      font-size: 0.8rem; color: #fbbf24;
      margin-top: 0.9rem; line-height: 1.5;
    }}

    /* ── Footer ── */
    footer {{
      margin-top: 2.5rem; text-align: center;
      font-family: 'Space Mono', monospace;
      font-size: 0.65rem; color: var(--muted); line-height: 2;
    }}
    footer a {{ color: var(--accent2); text-decoration: none; }}

    @media (max-width: 480px) {{
      .hero {{ padding: 2.2rem 0 1.8rem; }}
      .logo {{ width: 64px; height: 64px; font-size: 1.9rem; border-radius: 18px; }}
      .stats-bar {{ margin: 1.25rem 0 1.75rem; }}
      .cert-card {{ padding: 1rem; gap: 0.75rem; }}
      .dns-banner {{ gap: 0.65rem; padding: 0.75rem 0.85rem; }}
      .dns-sub {{ display: none; }}
    }}
  </style>
</head>
<body>
  <div class="orb orb-1"></div>
  <div class="orb orb-2"></div>

  <div class="page">

    <div class="hero">
      <div class="logo">✍️</div>
      <h1>{APP_NAME} OTA Installer</h1>
      <p class="hero-sub">Certificate-bundled builds · Install directly on iOS</p>
    </div>

    <div class="stats-bar">
      <div class="stat-pill">
        <span class="dot"></span>
        <strong>{cert_count}</strong>&nbsp;cert{'' if cert_count == 1 else 's'}
      </div>
      <div class="stat-pill">
        <span class="dot"></span>
        v<strong>{version}</strong>
      </div>
      <div class="stat-pill">
        <span class="dot"></span>
        <strong>{build_time} UTC</strong>
      </div>
    </div>

    <!-- DNS install banner -->
    <div class="dns-banner">
      <div class="dns-icon">🌐</div>
      <div class="dns-text">
        <div class="dns-title">Install DNS Profile</div>
        <div class="dns-sub">Recommended — install khoindvn DNS before signing.</div>
      </div>
      <a href="{DNS_URL}" class="dns-btn">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none"
             stroke="currentColor" stroke-width="2.2"
             stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="10"/>
          <line x1="2" y1="12" x2="22" y2="12"/>
          <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>
        </svg>
        Install DNS
      </a>
    </div>

    <div class="section-head">
      <span class="section-label">Available Certificates</span>
      <div class="section-line"></div>
    </div>

    <div class="cert-grid">
{cards_html}
    </div>

    <div class="section-head">
      <span class="section-label">How to Install</span>
      <div class="section-line"></div>
    </div>

    <div class="info-card">
      <ol class="steps">
        <li><span class="step-num">1</span>Tap <strong>Install DNS</strong> above and allow the profile in Settings.</li>
        <li><span class="step-num">2</span>Pick a certificate and tap <strong>Install via OTA</strong> on your iPhone.</li>
        <li><span class="step-num">3</span>Follow the iOS prompts — allow installation when asked.</li>
        <li><span class="step-num">4</span>Go to <strong>Settings → General → VPN &amp; Device Management</strong> and trust the developer.</li>
        <li><span class="step-num">5</span>Open {APP_NAME} — your certificate is pre-loaded automatically.</li>
      </ol>
      <div class="warning">
        ⚠️ Tap <em>Install via OTA</em> only on an iOS device. Each card installs {APP_NAME} pre-bundled with that specific certificate.
      </div>
    </div>

    <footer>
      Auto-built by <a href="https://github.com/{REPO}">GitHub Actions</a> ·
      {build_time} UTC<br>
      {APP_NAME} by <a href="https://github.com/nyasami/ksign">nyasami</a>
    </footer>

  </div>
</body>
</html>"""


def main():
    os.makedirs(DEPLOY_DIR, exist_ok=True)

    signed_manifest_path = os.path.join(BUILD_DIR, "signed_manifest.json")
    if not os.path.exists(signed_manifest_path):
        sys.exit(f"[ERROR] signed_manifest.json not found at {signed_manifest_path}. "
                 "Run the sign step first.")

    with open(signed_manifest_path) as f:
        signed_items = json.load(f)

    if not signed_items:
        sys.exit("[ERROR] signed_manifest.json is empty.")

    ver_file   = os.path.join(BUILD_DIR, "ipa_version.txt")
    version    = open(ver_file).read().strip() if os.path.exists(ver_file) else VERSION
    build_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

    certs_meta = []

    for idx, item in enumerate(signed_items):
        folder     = item["folder"]
        signed_ipa = item["signed_ipa"]
        p12_path   = item.get("p12_path", "")
        mp_path    = item.get("mp_path",  "")
        password   = item.get("password", "")  # not in signed_manifest — may be absent
        slug       = sanitize_slug(folder)

        if not os.path.exists(signed_ipa):
            print(f"[WARN] Signed IPA missing for '{folder}': {signed_ipa} — skipping.")
            continue

        ipa_size    = os.path.getsize(signed_ipa)
        ipa_size_mb = ipa_size / (1024 * 1024)
        ipa_sha     = sha256(signed_ipa)

        ipa_filename = f"ksign_{version}_{slug}_signed.ipa"
        ipa_deploy   = os.path.join(DEPLOY_DIR, ipa_filename)
        print(f"Copying IPA [{folder}] → {ipa_deploy}")
        shutil.copy2(signed_ipa, ipa_deploy)

        ipa_url    = f"{BASE_URL}/{ipa_filename}"
        plist_name = f"manifest-{slug}.plist"
        plist_url  = f"{BASE_URL}/{plist_name}"

        # ── Plist MUST mirror the patched Info.plist exactly ───────────────
        plist_bundle_id = item.get("bundle_id",     f"{BUNDLE_ID}.{slug}")
        plist_version   = item.get("bundle_version", f"1.0.{idx}")
        plist_title     = f"{APP_NAME} — {folder}"

        plist_content = generate_plist(ipa_url, plist_title, plist_bundle_id, plist_version)
        plist_path_out = os.path.join(DEPLOY_DIR, plist_name)
        with open(plist_path_out, "w") as f:
            f.write(plist_content)
        print(f"  ✓ {plist_name}  id='{plist_bundle_id}'  version='{plist_version}'")

        # ── Expiry info ────────────────────────────────────────────────────
        exp = {}
        if p12_path and mp_path and os.path.exists(p12_path) and os.path.exists(mp_path):
            print(f"  Extracting expiry dates…")
            # password may not be carried through signed_manifest; fall back to reading
            # the cert_password.txt written by fetch_cert.py / bundle_cert.py if absent
            if not password:
                pw_file = os.path.join(BUILD_DIR, "cert_password.txt")
                if os.path.exists(pw_file):
                    password = open(pw_file).read().strip()
            exp = expiry_info(p12_path, mp_path, password)
            print(f"    P12 expires : {exp['p12_expiry']} ({exp['p12_days']}d)")
            print(f"    MP  expires : {exp['mp_expiry']} ({exp['mp_days']}d)")
        else:
            print(f"  [WARN] Cert paths missing/not found for '{folder}' — skipping expiry check.")

        certs_meta.append({
            "folder":    folder,
            "ipa_url":   ipa_url,
            "plist_url": plist_url,
            "sha256":    ipa_sha,
            "size_mb":   ipa_size_mb,
            "expiry":    exp,
        })

    if not certs_meta:
        sys.exit("[ERROR] No valid signed IPAs to deploy.")

    html_content = generate_html(certs_meta, version, build_time)
    html_path    = os.path.join(DEPLOY_DIR, "index.html")
    with open(html_path, "w") as f:
        f.write(html_content)
    print(f"✓ index.html written ({len(certs_meta)} cert card(s))")

    meta = {
        "version":    version,
        "build_time": build_time,
        "bundle_id":  BUNDLE_ID,
        "app_name":   APP_NAME,
        "certs":      certs_meta,
    }
    with open(os.path.join(DEPLOY_DIR, "meta.json"), "w") as f:
        json.dump(meta, f, indent=2)
    print("✓ meta.json written")

    print(f"\n✅ Deploy assets ready in {DEPLOY_DIR} ({len(certs_meta)} cert(s))")
    for c in certs_meta:
        exp = c.get("expiry", {})
        print(f"  • {c['folder']}")
        print(f"      IPA      : {c['ipa_url']}")
        print(f"      OTA      : itms-services://?action=download-manifest&url={quote(c['plist_url'], safe='')}")
        print(f"      P12 exp  : {exp.get('p12_expiry','?')} ({exp.get('p12_days','?')}d remaining)")
        print(f"      MP  exp  : {exp.get('mp_expiry','?')} ({exp.get('mp_days','?')}d remaining)")


if __name__ == "__main__":
    main()
