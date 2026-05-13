#!/usr/bin/env python3
"""
generate_assets.py

Reads /tmp/build/signed_manifest.json and produces:
  /tmp/deploy/
    index.html                        ← landing page with ALL apps & certs
    <app_name>/
      <cert_slug>/
        manifest.plist                ← OTA manifest
        app.ipa                       ← signed IPA (copied here)

The website groups installs by app, then by cert, making it easy to
pick "KSign signed with GlobalTakeoff" vs "PlayBox signed with AcmeCorp".
"""

import json
import os
import re
import shutil
import sys
from collections import defaultdict
from datetime import datetime, timezone

BUILD_DIR   = "/tmp/build"
DEPLOY_DIR  = "/tmp/deploy"
SIGNED_MANIFEST = os.path.join(BUILD_DIR, "signed_manifest.json")

GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "owner/repo")


def slug(name: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-zA-Z0-9-]", "-", name)).strip("-").lower()


def make_manifest_plist(title, bundle_id, bundle_version, ipa_url):
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
          <string>{ipa_url}</string>
        </dict>
      </array>
      <key>metadata</key>
      <dict>
        <key>bundle-identifier</key>
        <string>{bundle_id}</string>
        <key>bundle-version</key>
        <string>{bundle_version}</string>
        <key>kind</key>
        <string>software</string>
        <key>title</key>
        <string>{title}</string>
      </dict>
    </dict>
  </array>
</dict>
</plist>"""


def make_index_html(apps_data, base_url, build_time):
    """
    apps_data: { app_name: [ {cert_folder, cert_slug, app_version,
                               manifest_url, bundle_id}, ... ] }
    """

    def app_cards():
        parts = []
        for app_name in sorted(apps_data.keys()):
            entries    = apps_data[app_name]
            app_version = entries[0]["app_version"] if entries else "?"
            app_slug   = slug(app_name)

            cert_rows = ""
            for e in sorted(entries, key=lambda x: x["cert_folder"]):
                install_url = f"itms-services://?action=download-manifest&url={e['manifest_url']}"

                days  = e.get("cert_days_left")
                expiry = e.get("cert_expiry", "unknown")

                if days is None:
                    badge_cls  = "expiry-unknown"
                    badge_text = "Expiry unknown"
                elif days < 0:
                    badge_cls  = "expiry-dead"
                    badge_text = f"Expired {abs(days)}d ago"
                elif days <= 7:
                    badge_cls  = "expiry-critical"
                    badge_text = f"{days}d left"
                elif days <= 30:
                    badge_cls  = "expiry-warn"
                    badge_text = f"{days}d left"
                else:
                    badge_cls  = "expiry-ok"
                    badge_text = f"{days}d left"

                cert_rows += f"""
          <tr>
            <td class="cert-name">{e['cert_folder']}</td>
            <td><span class="expiry-badge {badge_cls}" title="Expires {expiry}">{badge_text}</span></td>
            <td><a class="install-btn" href="{install_url}">⬇ Install</a></td>
            <td class="bundle-id">{e['bundle_id']}</td>
          </tr>"""

            parts.append(f"""
      <section class="app-card" id="{app_slug}">
        <div class="app-header">
          <h2 class="app-title">{app_name}</h2>
          <span class="app-version">v{app_version}</span>
        </div>
        <p class="app-subtitle">Choose a certificate to install with:</p>
        <table class="cert-table">
          <thead>
            <tr>
              <th>Certificate</th>
              <th>Expiry</th>
              <th>Install</th>
              <th>Bundle ID</th>
            </tr>
          </thead>
          <tbody>{cert_rows}
          </tbody>
        </table>
      </section>""")
        return "\n".join(parts)

    nav_links = " · ".join(
        f'<a href="#{slug(a)}">{a}</a>' for a in sorted(apps_data.keys())
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>OTA App Installer</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #0f0f13;
      color: #e0e0e8;
      min-height: 100vh;
      padding: 2rem 1rem 4rem;
    }}

    .credit-banner {{
      max-width: 780px;
      margin: 0 auto 2rem;
      background: linear-gradient(135deg, #1a1025, #0f1a25);
      border: 1px solid #2e2040;
      border-radius: 12px;
      padding: .85rem 1.25rem;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 1rem;
      flex-wrap: wrap;
    }}
    .credit-banner p {{
      font-size: .85rem;
      color: #a0a0c0;
      line-height: 1.5;
    }}
    .credit-banner p a {{
      color: #a78bfa;
      text-decoration: none;
      font-weight: 600;
    }}
    .credit-banner p a:hover {{ text-decoration: underline; }}
    .donate-btn {{
      display: inline-flex;
      align-items: center;
      gap: .4rem;
      background: #FFDD00;
      color: #000;
      text-decoration: none;
      font-size: .82rem;
      font-weight: 700;
      padding: .45rem 1rem;
      border-radius: 8px;
      white-space: nowrap;
      flex-shrink: 0;
      transition: opacity .15s;
    }}
    .donate-btn:hover {{ opacity: .85; }}

    header {{
      text-align: center;
      margin-bottom: 2.5rem;
    }}
    header h1 {{
      font-size: 2rem;
      font-weight: 700;
      background: linear-gradient(135deg, #a78bfa, #60a5fa);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }}
    header p {{
      color: #7c7c99;
      margin-top: .4rem;
      font-size: .9rem;
    }}

    nav {{
      text-align: center;
      margin-bottom: 2rem;
      font-size: .9rem;
    }}
    nav a {{ color: #a78bfa; text-decoration: none; }}
    nav a:hover {{ text-decoration: underline; }}

    .app-card {{
      background: #1a1a24;
      border: 1px solid #2a2a3a;
      border-radius: 14px;
      max-width: 780px;
      margin: 0 auto 2rem;
      padding: 1.5rem 1.75rem;
    }}

    .app-header {{
      display: flex;
      align-items: baseline;
      gap: .75rem;
      margin-bottom: .35rem;
    }}
    .app-title  {{ font-size: 1.35rem; font-weight: 700; }}
    .app-version {{
      font-size: .8rem;
      background: #2a2a3a;
      padding: .2rem .55rem;
      border-radius: 99px;
      color: #a0a0c0;
    }}
    .app-subtitle {{
      color: #7c7c99;
      font-size: .85rem;
      margin-bottom: 1rem;
    }}

    .cert-table {{
      width: 100%;
      border-collapse: collapse;
    }}
    .cert-table th {{
      text-align: left;
      font-size: .75rem;
      text-transform: uppercase;
      letter-spacing: .06em;
      color: #5c5c78;
      padding: .4rem .6rem;
      border-bottom: 1px solid #2a2a3a;
    }}
    .cert-table td {{
      padding: .6rem .6rem;
      border-bottom: 1px solid #1e1e2a;
      vertical-align: middle;
    }}
    .cert-table tr:last-child td {{ border-bottom: none; }}

    .cert-name  {{ font-weight: 500; }}
    .bundle-id  {{ font-size: .78rem; color: #5a5a78; font-family: monospace; }}

    .install-btn {{
      display: inline-block;
      background: linear-gradient(135deg, #7c3aed, #2563eb);
      color: #fff;
      text-decoration: none;
      padding: .4rem 1rem;
      border-radius: 8px;
      font-size: .85rem;
      font-weight: 600;
      white-space: nowrap;
      transition: opacity .15s;
    }}
    .install-btn:hover {{ opacity: .85; }}

    .expiry-badge {{
      display: inline-block;
      padding: .25rem .6rem;
      border-radius: 99px;
      font-size: .78rem;
      font-weight: 600;
      white-space: nowrap;
    }}
    .expiry-ok       {{ background: #14532d; color: #86efac; }}
    .expiry-warn     {{ background: #713f12; color: #fde68a; }}
    .expiry-critical {{ background: #7f1d1d; color: #fca5a5; animation: pulse 1.5s infinite; }}
    .expiry-dead     {{ background: #1f1f2e; color: #6b7280; text-decoration: line-through; }}
    .expiry-unknown  {{ background: #1f1f2e; color: #6b7280; }}

    @keyframes pulse {{
      0%, 100% {{ opacity: 1; }}
      50%       {{ opacity: .55; }}
    }}

    footer {{
      text-align: center;
      color: #3a3a55;
      font-size: .8rem;
      margin-top: 3rem;
    }}

    @media (max-width: 500px) {{
      .bundle-id {{ display: none; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>📲 OTA App Installer</h1>
    <p>Open this page on your iPhone or iPad, then tap Install.</p>
  </header>

  <div class="credit-banner">
    <p>KSign is made with ❤️ by <a href="https://github.com/nyasami" target="_blank" rel="noopener">Asami</a> — a free, open-source iOS app signer. If it's been useful to you, consider buying her a coffee.</p>
    <a class="donate-btn" href="https://buymeacoffee.com/nyasami" target="_blank" rel="noopener">☕ Donate</a>
  </div>

  <nav>{nav_links}</nav>

  {app_cards()}

  <footer>
    Auto-deployed from <code>{GITHUB_REPOSITORY}</code> · {build_time}
  </footer>
</body>
</html>"""


def main():
    if not os.path.exists(SIGNED_MANIFEST):
        sys.exit(f"[ERROR] {SIGNED_MANIFEST} not found — run sign_ipas.py first.")

    with open(SIGNED_MANIFEST) as f:
        signed = json.load(f)

    if not signed:
        sys.exit("[ERROR] signed_manifest.json is empty.")

    os.makedirs(DEPLOY_DIR, exist_ok=True)

    owner, repo_name = GITHUB_REPOSITORY.split("/", 1) if "/" in GITHUB_REPOSITORY else ("owner", GITHUB_REPOSITORY)
    base_url = f"https://{owner}.github.io/{repo_name}"

    apps_data = defaultdict(list)  # app_name → [cert entries]

    for entry in signed:
        app_name       = entry.get("app_name", "app")
        app_version    = entry.get("app_version", "unknown")
        cert_folder    = entry["folder"]
        bundle_id      = entry.get("bundle_id",      "")
        bundle_version = entry.get("bundle_version", "1.0.0")
        signed_ipa     = entry["signed_ipa"]

        app_slug  = slug(app_name)
        cert_slug = slug(cert_folder)

        # Copy IPA into deploy tree
        ipa_dir = os.path.join(DEPLOY_DIR, app_slug, cert_slug)
        os.makedirs(ipa_dir, exist_ok=True)
        dest_ipa = os.path.join(ipa_dir, "app.ipa")
        shutil.copy2(signed_ipa, dest_ipa)
        print(f"  Copied: {signed_ipa} → {dest_ipa}")

        # Write manifest.plist
        ipa_url      = f"{base_url}/{app_slug}/{cert_slug}/app.ipa"
        manifest_url = f"{base_url}/{app_slug}/{cert_slug}/manifest.plist"
        title        = f"{app_name} ({cert_folder})"

        plist_content = make_manifest_plist(title, bundle_id, bundle_version, ipa_url)
        plist_path    = os.path.join(ipa_dir, "manifest.plist")
        with open(plist_path, "w") as f:
            f.write(plist_content)
        print(f"  Wrote: {plist_path}")

        apps_data[app_name].append({
            "cert_folder":   cert_folder,
            "cert_slug":     cert_slug,
            "app_version":   app_version,
            "manifest_url":  manifest_url,
            "bundle_id":     bundle_id,
            "cert_expiry":   entry.get("cert_expiry",    "unknown"),
            "cert_days_left": entry.get("cert_days_left", None),
        })

    build_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    html       = make_index_html(dict(apps_data), base_url, build_time)
    index_path = os.path.join(DEPLOY_DIR, "index.html")
    with open(index_path, "w") as f:
        f.write(html)
    print(f"\n✅ index.html written with {len(apps_data)} app(s).")

    print(f"\nDeploy tree summary:")
    for app_name, entries in sorted(apps_data.items()):
        print(f"  {app_name}/  ({len(entries)} cert(s))")
        for e in entries:
            print(f"    {e['cert_slug']}/manifest.plist")


if __name__ == "__main__":
    main()
