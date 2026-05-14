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
            entries     = apps_data[app_name]
            app_version = entries[0]["app_version"] if entries else "?"
            app_comment = entries[0].get("comment", "")
            # display_name from manifest; fall back to a humanised app_name
            _raw_display = entries[0].get("display_name", "")
            display_name = _raw_display or app_name.replace("_", " ").replace("-", " ").title()
            app_slug    = slug(app_name)

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
            <td class="bundle-id-cell"><code>{e.get('bundle_id', '')}</code></td>
          </tr>"""

            # Hide meaningless version strings
            show_version = app_version not in ("direct", "unknown", "", None)
            version_html = (
                f'<span class="app-version">'
                f'{app_version if app_version.startswith("v") else "v" + app_version}'
                f'</span>'
            ) if show_version else ""

            comment_html = f'\n        <p class="app-comment">{app_comment}</p>' if app_comment else ""

            parts.append(f"""
      <section class="app-card" id="{app_slug}">
        <div class="app-header">
          <h2 class="app-title">{display_name}</h2>
          {version_html}
        </div>{comment_html}
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
        f'<a href="#{slug(a)}">{apps_data[a][0].get("display_name", "") or a}</a>'
        for a in sorted(apps_data.keys())
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
    .app-comment {{
      color: #9090b0;
      font-size: .85rem;
      font-style: italic;
      margin: .25rem 0 .75rem;
      line-height: 1.5;
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
      padding: .4rem .75rem;
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
      .cert-table {{ table-layout: fixed; width: 100%; }}
      .cert-name  {{ width: 45%; word-break: break-word; }}
      .expiry-badge {{ font-size: .7rem; padding: .2rem .4rem; }}
      .install-btn  {{ padding: .35rem .55rem; font-size: .8rem; }}
      .app-card {{ padding: 1rem; }}
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

  <nav>{nav_links} · <a href="dns-instructions/">🛡 DNS Guide</a></nav>

  {app_cards()}

  <footer>
    Auto-deployed from <code>{GITHUB_REPOSITORY}</code> · {build_time}
  </footer>
</body>
</html>"""


def make_dns_html(base_url, build_time):
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>DNS Setup Guide — Block Apple Revocation</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #0f0f13;
      color: #e0e0e8;
      min-height: 100vh;
      padding: 2rem 1rem 4rem;
    }}
    .page {{
      max-width: 720px;
      margin: 0 auto;
    }}
    a {{ color: #a78bfa; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .back {{
      display: inline-block;
      margin-bottom: 2rem;
      font-size: .9rem;
      color: #7c7c99;
    }}
    .back:hover {{ color: #a78bfa; }}
    h1 {{
      font-size: 1.8rem;
      font-weight: 700;
      background: linear-gradient(135deg, #a78bfa, #60a5fa);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      margin-bottom: .5rem;
    }}
    .subtitle {{
      color: #7c7c99;
      font-size: .95rem;
      margin-bottom: 2rem;
      line-height: 1.6;
    }}
    .warning {{
      background: #1f1507;
      border: 1px solid #713f12;
      border-radius: 10px;
      padding: 1rem 1.2rem;
      font-size: .88rem;
      color: #fde68a;
      margin-bottom: 2rem;
      line-height: 1.6;
    }}
    .warning strong {{ color: #fbbf24; }}
    h2 {{
      font-size: 1.15rem;
      font-weight: 700;
      color: #c4b5fd;
      margin: 2rem 0 .75rem;
      padding-bottom: .4rem;
      border-bottom: 1px solid #2a2a3a;
    }}
    h3 {{
      font-size: 1rem;
      font-weight: 600;
      color: #a78bfa;
      margin: 1.5rem 0 .5rem;
    }}
    p {{
      font-size: .92rem;
      line-height: 1.7;
      color: #c0c0d8;
      margin-bottom: .75rem;
    }}
    ol, ul {{
      padding-left: 1.4rem;
      margin-bottom: .75rem;
    }}
    li {{
      font-size: .92rem;
      line-height: 1.7;
      color: #c0c0d8;
      margin-bottom: .3rem;
    }}
    .domain-list {{
      background: #1a1a24;
      border: 1px solid #2a2a3a;
      border-radius: 10px;
      padding: .85rem 1.1rem;
      margin: .75rem 0 1rem;
      list-style: none;
      padding-left: 1.1rem;
    }}
    .domain-list li {{
      font-family: monospace;
      font-size: .9rem;
      color: #86efac;
      margin-bottom: .2rem;
    }}
    .domain-list li::before {{
      content: "⊘ ";
      color: #4ade80;
    }}
    pre {{
      background: #13131c;
      border: 1px solid #2a2a3a;
      border-radius: 10px;
      padding: 1rem 1.1rem;
      overflow-x: auto;
      font-size: .8rem;
      line-height: 1.6;
      color: #a0e0c0;
      margin: .75rem 0 1rem;
    }}
    .step {{
      background: #1a1a24;
      border: 1px solid #2a2a3a;
      border-radius: 10px;
      padding: 1rem 1.2rem;
      margin-bottom: .75rem;
    }}
    .step-num {{
      display: inline-block;
      background: linear-gradient(135deg, #7c3aed, #2563eb);
      color: #fff;
      font-size: .75rem;
      font-weight: 700;
      width: 1.5rem;
      height: 1.5rem;
      line-height: 1.5rem;
      text-align: center;
      border-radius: 50%;
      margin-right: .5rem;
      flex-shrink: 0;
    }}
    .step p {{ margin-bottom: 0; }}
    .tab-bar {{
      display: flex;
      gap: .5rem;
      margin-bottom: 1.25rem;
      flex-wrap: wrap;
    }}
    .tab {{
      background: #1a1a24;
      border: 1px solid #2a2a3a;
      border-radius: 8px;
      padding: .45rem 1rem;
      font-size: .85rem;
      font-weight: 600;
      color: #a0a0c0;
      cursor: pointer;
      transition: all .15s;
    }}
    .tab.active, .tab:hover {{
      background: #2a1a4a;
      border-color: #7c3aed;
      color: #c4b5fd;
    }}
    .tab-content {{ display: none; }}
    .tab-content.active {{ display: block; }}
    footer {{
      text-align: center;
      color: #3a3a55;
      font-size: .8rem;
      margin-top: 3rem;
    }}
    @media (max-width: 500px) {{
      h1 {{ font-size: 1.4rem; }}
      pre {{ font-size: .72rem; }}
    }}
  </style>
</head>
<body>
  <div class="page">
    <a class="back" href="../">← Back to installer</a>

    <h1>🛡 Block Apple Certificate Revocation</h1>
    <p class="subtitle">
      When iOS installs a sideloaded app it contacts Apple's servers to check
      whether the signing certificate has been revoked. Blocking those domains
      at the DNS level prevents that check from completing, keeping your signed
      apps working even after a cert is flagged.
    </p>

    <div class="warning">
      <strong>Heads up:</strong> Blocking these domains affects your whole device.
      Apple Pay, App Store cert validation, and MDM enrollment may behave differently.
      To undo everything, just remove the DNS profile from
      <strong>Settings → General → VPN &amp; Device Management</strong>.
    </div>

    <h2>Domains to Block</h2>
    <p>Add all of these to your blocklist — they are all subdomains of <code>apple.com</code>:</p>
    <ul class="domain-list">
      <li>certs.apple.com</li>
      <li>valid.apple.com</li>
      <li>crl.apple.com</li>
      <li>ocsp2.apple.com</li>
      <li>apptest.apple.com</li>
      <li>vpp.itunes.apple.com</li>
    </ul>

    <h2>Choose Your Method</h2>

    <div class="tab-bar">
      <div class="tab active" onclick="switchTab('nextdns')">NextDNS</div>
      <div class="tab" onclick="switchTab('pihole')">Pi-hole</div>
      <div class="tab" onclick="switchTab('profile')">Install Profile</div>
    </div>

    <!-- NextDNS -->
    <div id="tab-nextdns" class="tab-content active">
      <h3>Step 1 — Create a NextDNS account</h3>
      <p>Go to <a href="https://nextdns.io" target="_blank" rel="noopener">nextdns.io</a>, sign up for free, and copy your <strong>Configuration ID</strong> from the top of the dashboard (it looks like <code>abc123</code>).</p>

      <h3>Step 2 — Block the domains</h3>
      <p>In your NextDNS dashboard go to the <strong>Denylist</strong> tab and add each domain above one by one. Make sure <strong>Exact match</strong> is selected — not wildcard — so you don't accidentally block all of <code>apple.com</code>.</p>

      <h3>Step 3 — Download the DNS profile</h3>
      <p>Go to <strong>Setup → Apple → Download Profile</strong> in your NextDNS dashboard. It generates a signed <code>.mobileconfig</code> with your config ID baked in. Download it and skip to the <strong>Install Profile</strong> tab to finish.</p>
    </div>

    <!-- Pi-hole -->
    <div id="tab-pihole" class="tab-content">
      <h3>Step 1 — Add domains to the blocklist</h3>
      <p>In your Pi-hole admin panel go to <strong>Blacklist → Domains</strong> (not Regex). Add each domain exactly as listed above. Leave <strong>Add domain as wildcard</strong> unchecked.</p>

      <h3>Step 2 — Reach Pi-hole outside your home network</h3>
      <p>You have two options:</p>
      <ul>
        <li><strong>WireGuard / OpenVPN</strong> back to your home — most reliable, routes all DNS through Pi-hole from anywhere.</li>
        <li><strong>Manual DNS profile</strong> — point a <code>.mobileconfig</code> at your Pi-hole's public IP (requires your Pi-hole to be publicly reachable on port 53, which is a security risk unless firewalled carefully).</li>
      </ul>

      <h3>Step 3 — Create a DNS profile for your Pi-hole</h3>
      <p>Paste the XML below into a text editor, replace <code>YOUR.PIHOLE.IP.HERE</code> with your Pi-hole's IP, save it as <code>pihole.mobileconfig</code>, then host it on any HTTPS URL and open it on your iPhone. Then follow the <strong>Install Profile</strong> tab.</p>
      <pre><?xml version="1.0" encoding="UTF-8"?>
&lt;!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd"&gt;
&lt;plist version="1.0"&gt;
&lt;dict&gt;
  &lt;key&gt;PayloadContent&lt;/key&gt;
  &lt;array&gt;
    &lt;dict&gt;
      &lt;key&gt;DNSSettings&lt;/key&gt;
      &lt;dict&gt;
        &lt;key&gt;DNSProtocol&lt;/key&gt;
        &lt;string&gt;Plain&lt;/string&gt;
        &lt;key&gt;Servers&lt;/key&gt;
        &lt;array&gt;
          &lt;string&gt;YOUR.PIHOLE.IP.HERE&lt;/string&gt;
        &lt;/array&gt;
      &lt;/dict&gt;
      &lt;key&gt;PayloadType&lt;/key&gt;
      &lt;string&gt;com.apple.dnsSettings.managed&lt;/string&gt;
      &lt;key&gt;PayloadIdentifier&lt;/key&gt;
      &lt;string&gt;com.pihole.dns&lt;/string&gt;
      &lt;key&gt;PayloadUUID&lt;/key&gt;
      &lt;string&gt;A1B2C3D4-E5F6-7890-ABCD-EF1234567890&lt;/string&gt;
      &lt;key&gt;PayloadVersion&lt;/key&gt;
      &lt;integer&gt;1&lt;/integer&gt;
      &lt;key&gt;PayloadDisplayName&lt;/key&gt;
      &lt;string&gt;Pi-hole DNS&lt;/string&gt;
    &lt;/dict&gt;
  &lt;/array&gt;
  &lt;key&gt;PayloadDisplayName&lt;/key&gt;
  &lt;string&gt;Pi-hole DNS&lt;/string&gt;
  &lt;key&gt;PayloadIdentifier&lt;/key&gt;
  &lt;string&gt;com.pihole.dns.profile&lt;/string&gt;
  &lt;key&gt;PayloadType&lt;/key&gt;
  &lt;string&gt;Configuration&lt;/string&gt;
  &lt;key&gt;PayloadUUID&lt;/key&gt;
  &lt;string&gt;B2C3D4E5-F6A7-8901-BCDE-F12345678901&lt;/string&gt;
  &lt;key&gt;PayloadVersion&lt;/key&gt;
  &lt;integer&gt;1&lt;/integer&gt;
  &lt;key&gt;PayloadRemovalDisallowed&lt;/key&gt;
  &lt;false/&gt;
&lt;/dict&gt;
&lt;/plist&gt;</pre>
    </div>

    <!-- Install Profile -->
    <div id="tab-profile" class="tab-content">
      <h3>Installing the profile on your iPhone</h3>
      <p>This works the same whether you used NextDNS or Pi-hole.</p>

      <div class="step"><span class="step-num">1</span> <p>Open the <code>.mobileconfig</code> file on your iPhone — via AirDrop, Safari, or any HTTPS link. iOS will say <em>"Profile Downloaded"</em>.</p></div>
      <div class="step"><span class="step-num">2</span> <p>Go to <strong>Settings → General → VPN &amp; Device Management</strong> and tap the profile listed under <em>Downloaded Profile</em>.</p></div>
      <div class="step"><span class="step-num">3</span> <p>Tap <strong>Install</strong> in the top-right, enter your passcode, and confirm twice.</p></div>
      <div class="step"><span class="step-num">4</span> <p>The profile is now active system-wide — no per-network Wi-Fi changes needed.</p></div>

      <h3>Verify it's working</h3>
      <p>On your iPhone open Safari and navigate to <code>http://crl.apple.com</code> — it should fail to load or time out. If it loads normally, double-check your blocklist entries and that the profile is installed and not paused.</p>

      <h3>To remove</h3>
      <p>Go to <strong>Settings → General → VPN &amp; Device Management</strong>, tap the profile, and tap <strong>Remove Profile</strong>. Normal DNS resumes immediately.</p>
    </div>

    <footer>
      Auto-deployed from <code>{GITHUB_REPOSITORY}</code> · {build_time}
    </footer>
  </div>

  <script>
    function switchTab(id) {{
      document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
      document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
      document.querySelector('[onclick="switchTab(\\''+id+'\\')"]').classList.add('active');
      document.getElementById('tab-' + id).classList.add('active');
    }}
  </script>
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
            "cert_folder":    cert_folder,
            "cert_slug":      cert_slug,
            "app_version":    app_version,
            "manifest_url":   manifest_url,
            "bundle_id":      bundle_id,
            "cert_expiry":    entry.get("cert_expiry",    "unknown"),
            "cert_days_left": entry.get("cert_days_left", None),
            "comment":        entry.get("comment",        ""),
            "display_name":   entry.get("display_name",   ""),
        })

    build_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    html       = make_index_html(dict(apps_data), base_url, build_time)
    index_path = os.path.join(DEPLOY_DIR, "index.html")
    with open(index_path, "w") as f:
        f.write(html)
    print(f"\n✅ index.html written with {len(apps_data)} app(s).")

    dns_dir  = os.path.join(DEPLOY_DIR, "dns-instructions")
    os.makedirs(dns_dir, exist_ok=True)
    dns_path = os.path.join(dns_dir, "index.html")
    with open(dns_path, "w") as f:
        f.write(make_dns_html(base_url, build_time))
    print(f"✅ dns-instructions/index.html written.")

    print(f"\nDeploy tree summary:")
    for app_name, entries in sorted(apps_data.items()):
        print(f"  {app_name}/  ({len(entries)} cert(s))")
        for e in entries:
            print(f"    {e['cert_slug']}/manifest.plist")


if __name__ == "__main__":
    main()
