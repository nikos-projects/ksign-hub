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

    def _badge(days, expiry):
        if days is None:
            return "expiry-unknown", "Expiry unknown"
        if days < 0:
            return "expiry-dead", f"Expired {{abs(days)}}d ago".replace("{abs(days)}", str(abs(days)))
        if days <= 7:
            return "expiry-critical", f"{days}d left"
        if days <= 30:
            return "expiry-warn", f"{days}d left"
        return "expiry-ok", f"{days}d left"

    def app_cards():
        parts = []
        for app_name in sorted(apps_data.keys()):
            entries      = apps_data[app_name]
            app_version  = entries[0]["app_version"] if entries else "?"
            app_comment  = entries[0].get("comment", "")
            display_name = entries[0].get("display_name", "") or app_name
            app_slug     = slug(app_name)

            cert_rows = ""
            for e in sorted(entries, key=lambda x: x["cert_folder"]):
                install_url = f"itms-services://?action=download-manifest&url={{e['manifest_url']}}".replace("{e['manifest_url']}", e['manifest_url'])
                days   = e.get("cert_days_left")
                expiry = e.get("cert_expiry", "unknown")
                badge_cls, badge_text = _badge(days, expiry)
                cert_rows += f"""
            <tr>
              <td class="cert-name">{e['cert_folder']}</td>
              <td><span class="expiry-badge {badge_cls}" title="Expires {expiry}">{badge_text}</span></td>
              <td><a class="install-btn" href="{install_url}">⬇ Install</a></td>
              <td class="bundle-id">{e.get('bundle_id', '')}</td>
            </tr>"""

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
        <div class="app-controls">
          <input class="cert-filter" type="search" placeholder="Filter certificates…" aria-label="Filter certificates" />
          <button class="row-toggle" type="button"></button>
          <div class="quality-filter">
            <button class="quality-chip active" type="button" data-quality="healthy" aria-pressed="true">Healthy (30d+)</button>
            <button class="quality-chip active" type="button" data-quality="expiring" aria-pressed="true">Expiring Soon</button>
            <button class="quality-chip" type="button" data-quality="expired" aria-pressed="false">Expired</button>
          </div>
        </div>
        <div class="filter-empty">No certificates match this filter.</div>
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

    nav_items = "".join(
        f'<a href="#{slug(a)}">{apps_data[a][0].get("display_name", "") or a}</a>'
        for a in sorted(apps_data.keys())
    )
    nav_items += '<a href="https://novadev.vip/resources/dns/novadns.mobileconfig">🛡 NovaDNS</a>'

    app_ids_js = ", ".join(
        f'"{slug(a)}"' for a in sorted(apps_data.keys())
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Niko's Projects | OTA App Installer</title>
  <meta name="description" content="Install sideloaded iOS apps from Niko's Projects. Pick a certificate, check expiry health, and install directly on iPhone or iPad." />
  <meta name="theme-color" content="#000000" />
  <meta property="og:title" content="Niko's Projects" />
  <meta property="og:description" content="Direct IPA installation with certificate expiry tracking." />
  <meta property="og:type" content="website" />
  <meta property="og:url" content="{base_url}/" />
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet" />
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap');

    :root {{
      --bg-0: #000000;
      --bg-1: #040404;
      --bg-2: #0b0b0b;
      --text-0: #f5f5f5;
      --text-1: #c4c4c4;
      --text-2: #9d9d9d;
      --line: #323232;
      --line-soft: #232323;
      --card: linear-gradient(165deg,rgba(16,16,16,.94) 0%,rgba(10,10,10,.94) 58%,rgba(14,14,14,.92) 100%);
      --ok-bg: #151515; --ok-text: #e8e8e8;
      --warn-bg: #171717; --warn-text: #d9d9d9;
      --critical-bg: #141414; --critical-text: #f2f2f2;
      --dead-bg: #0f0f0f; --dead-text: #8b8b8b;
      --shadow-lg: 0 28px 70px rgba(0,0,0,.65);
      --radius-xl: 20px; --radius-lg: 14px; --radius-md: 10px;
      --fast: 170ms ease; --med: 280ms cubic-bezier(.2,.6,.2,1);
    }}

    *,*::before,*::after {{ box-sizing:border-box; margin:0; padding:0; }}

    body {{
      font-family: 'Space Grotesk','Avenir Next','Segoe UI',sans-serif;
      color: var(--text-0);
      min-height: 100vh;
      overflow-x: hidden;
      padding: 1.2rem .8rem 2.4rem;
      background:
        radial-gradient(1000px 520px at -10% -18%,rgba(200,200,200,.09),transparent 62%),
        radial-gradient(760px 440px at 108% -20%,rgba(140,140,140,.08),transparent 60%),
        linear-gradient(185deg,#050505,#000 55%,#000 100%);
    }}

    body::before, body::after {{
      content:""; position:fixed; pointer-events:none; z-index:-1;
    }}
    body::before {{
      inset:-20% auto auto -14%; width:38rem; height:38rem; border-radius:50%;
      background:radial-gradient(circle at 45% 45%,rgba(170,170,170,.18),rgba(170,170,170,0) 70%);
      filter:blur(10px); animation:driftA 16s ease-in-out infinite alternate;
    }}
    body::after {{
      right:-12rem; bottom:-12rem; width:34rem; height:34rem; border-radius:50%;
      background:radial-gradient(circle at 55% 55%,rgba(120,120,120,.15),rgba(120,120,120,0) 72%);
      filter:blur(8px); animation:driftB 20s ease-in-out infinite alternate;
    }}
    @keyframes driftA {{ 0% {{ transform:translate(0,0) scale(1); }} 100% {{ transform:translate(44px,32px) scale(1.08); }} }}
    @keyframes driftB {{ 0% {{ transform:translate(0,0) scale(1); }} 100% {{ transform:translate(-36px,-28px) scale(1.1); }} }}
    @keyframes riseIn {{ from {{ opacity:0; transform:translateY(12px); }} to {{ opacity:1; transform:translateY(0); }} }}
    @keyframes cardIn {{ from {{ opacity:0; transform:translateY(14px) scale(.99); }} to {{ opacity:1; transform:translateY(0) scale(1); }} }}
    @keyframes pulse {{ 0%,100% {{ opacity:1; }} 50% {{ opacity:.58; }} }}

    header, .credit-banner, nav, .quickstart-card, .status-panel,
    .app-card, .faq-card, .safety-note, footer {{
      max-width: 980px; margin-left:auto; margin-right:auto;
    }}

    /* ── HEADER ── */
    header {{
      text-align:center; margin-bottom:.9rem;
      animation:riseIn .55s var(--med) both;
    }}
    header h1 {{
      font-size:clamp(1.7rem,2.4vw + .95rem,3rem);
      font-weight:700; line-height:1.05; letter-spacing:.02em;
      background:linear-gradient(130deg,#fff 0%,#e6e6e6 46%,#b6b6b6 100%);
      -webkit-background-clip:text; -webkit-text-fill-color:transparent;
      text-shadow:0 0 16px rgba(255,255,255,.15);
    }}
    header p {{ color:var(--text-1); margin-top:.42rem; font-size:.92rem; }}

    /* ── CREDIT BANNER ── */
    .credit-banner {{
      margin-bottom:.85rem; padding:.8rem 1rem;
      display:flex; align-items:center; justify-content:space-between;
      gap:.9rem; flex-wrap:wrap;
      background:linear-gradient(138deg,rgba(14,14,14,.9),rgba(16,16,16,.84),rgba(20,20,20,.84));
      border:1px solid #2f2f2f; border-radius:var(--radius-xl);
      box-shadow:var(--shadow-lg); backdrop-filter:blur(10px);
      animation:riseIn .6s .08s var(--med) both;
    }}
    .credit-banner p {{ font-size:.81rem; color:#d1d1d1; line-height:1.42; }}
    .credit-banner p a, footer a {{ color:#fff; font-weight:600; text-decoration:none; transition:color var(--fast); }}
    .credit-banner p a:hover, footer a:hover {{ color:#ccc; text-decoration:underline; }}

    .donate-btn {{
      position:relative; overflow:hidden;
      display:inline-flex; align-items:center; gap:.35rem;
      border-radius:999px; text-decoration:none; white-space:nowrap;
      background:linear-gradient(135deg,#f0f0f0,#cfcfcf);
      color:#050505; font-size:.76rem; font-weight:700; padding:.42rem .86rem;
      box-shadow:0 8px 22px rgba(255,255,255,.14);
      transition:transform var(--fast),filter var(--fast);
    }}
    .donate-btn:hover {{ transform:translateY(-1px); filter:brightness(1.05); }}

    /* ── NAV ── */
    nav {{
      position:sticky; top:.45rem; z-index:20; margin-bottom:.85rem;
      display:flex; flex-wrap:wrap; justify-content:center; gap:.42rem;
      padding:.48rem; border:1px solid var(--line); border-radius:999px;
      background:rgba(7,7,7,.82); backdrop-filter:blur(12px);
      animation:riseIn .6s .14s var(--med) both;
    }}
    nav a {{
      display:inline-flex; align-items:center; border-radius:999px;
      border:1px solid #404040;
      background:linear-gradient(180deg,rgba(20,20,20,.86),rgba(13,13,13,.92));
      color:#ebebeb; font-size:.77rem; font-weight:600; letter-spacing:.02em;
      padding:.32rem .66rem; text-decoration:none; transition:all var(--fast);
    }}
    nav a:hover {{ transform:translateY(-1px); border-color:#6f6f6f; color:#fff; }}
    nav a.active {{
      border-color:transparent;
      background:linear-gradient(132deg,#fff,#d0d0d0 62%,#9c9c9c);
      color:#000; box-shadow:0 7px 18px rgba(255,255,255,.2);
    }}

    /* ── QUICKSTART ── */
    .quickstart-card {{
      margin-bottom:.9rem; padding:.9rem 1rem;
      background:linear-gradient(140deg,rgba(12,12,12,.92),rgba(9,9,9,.95));
      border:1px solid #313131; border-radius:var(--radius-xl);
      box-shadow:var(--shadow-lg); backdrop-filter:blur(10px);
      animation:riseIn .62s .18s var(--med) both;
    }}
    .quickstart-card h2 {{ font-size:.98rem; margin-bottom:.52rem; color:#f3f3f3; letter-spacing:.01em; }}
    .quickstart-note {{ color:var(--text-1); font-size:.78rem; line-height:1.45; }}
    .quickstart-steps {{
      list-style:none; display:grid; gap:.46rem; margin:.5rem 0 .6rem;
      counter-reset:quickstep;
    }}
    .quickstart-steps li {{
      display:flex; align-items:baseline; gap:.48rem;
      color:#e5e5e5; font-size:.78rem; line-height:1.4;
      counter-increment:quickstep;
    }}
    .quickstart-steps li::before {{
      content:counter(quickstep); display:inline-flex; align-items:center;
      justify-content:center; width:1.2rem; height:1.2rem; border-radius:999px;
      background:linear-gradient(135deg,#ececec,#bdbdbd); color:#090909;
      font-size:.67rem; font-weight:700; flex-shrink:0; margin-top:.05rem;
    }}

    /* ── DNS BANNER ── */
    .novadns-banner {{
      margin-bottom:.9rem; padding:.88rem 1rem;
      display:flex; align-items:center; gap:.95rem; flex-wrap:wrap;
      background:linear-gradient(138deg,rgba(12,12,12,.92),rgba(18,18,18,.84));
      border:1px solid #4a4a4a; border-radius:var(--radius-xl);
      box-shadow:var(--shadow-lg); backdrop-filter:blur(10px);
      animation:riseIn .64s .2s var(--med) both;
    }}
    .novadns-banner-icon {{ font-size:1.6rem; flex-shrink:0; }}
    .novadns-banner-body {{ flex:1; min-width:180px; }}
    .novadns-banner-body strong {{ display:block; font-size:.92rem; color:#f1f1f1; margin-bottom:.16rem; }}
    .novadns-banner-body p {{ font-size:.79rem; color:#cccccc; line-height:1.4; margin:0; }}
    .novadns-btn {{
      position:relative; overflow:hidden;
      display:inline-flex; align-items:center; gap:.34rem; border-radius:999px;
      text-decoration:none; white-space:nowrap;
      background:linear-gradient(132deg,#efefef,#bebebe); color:#080808;
      font-size:.77rem; font-weight:700; padding:.45rem .92rem;
      box-shadow:0 8px 24px rgba(255,255,255,.13);
      transition:transform var(--fast),filter var(--fast);
    }}
    .novadns-btn:hover {{ transform:translateY(-1px); filter:brightness(1.07); }}

    /* ── APP CARDS ── */
    .app-card {{
      display:none; margin-bottom:.9rem; padding:.95rem 1rem 1.05rem;
      background:var(--card); border:1px solid var(--line);
      border-radius:var(--radius-xl); box-shadow:var(--shadow-lg);
      backdrop-filter:blur(10px); position:relative; isolation:isolate;
    }}
    .app-card::before {{
      content:""; position:absolute; inset:0; border-radius:inherit; padding:1px;
      background:linear-gradient(125deg,rgba(222,222,222,.2),rgba(156,156,156,.18),rgba(216,216,216,.15));
      mask:linear-gradient(#fff 0 0) content-box,linear-gradient(#fff 0 0);
      mask-composite:exclude; -webkit-mask:linear-gradient(#fff 0 0) content-box,linear-gradient(#fff 0 0);
      -webkit-mask-composite:xor; pointer-events:none;
    }}
    .app-card.active {{ display:block; animation:cardIn .42s var(--med) both; }}

    .app-header {{ display:flex; align-items:center; flex-wrap:wrap; gap:.46rem; margin-bottom:.18rem; }}
    .app-title {{ font-size:1.08rem; font-weight:700; letter-spacing:.01em; }}
    .app-version {{
      display:inline-flex; align-items:center; border-radius:999px;
      font-size:.7rem; font-weight:600; letter-spacing:.03em; text-transform:uppercase;
      background:rgba(24,24,24,.95); color:#d8d8d8; border:1px solid #4b4b4b; padding:.16rem .48rem;
    }}
    .app-comment {{ color:var(--text-1); font-size:.78rem; line-height:1.4; margin:.12rem 0 .5rem; }}
    .app-subtitle {{ color:var(--text-2); font-size:.78rem; margin-bottom:.42rem; }}

    /* ── CONTROLS ── */
    .app-controls {{
      display:flex; align-items:center; flex-wrap:wrap; gap:.4rem; margin:.36rem 0 .55rem;
    }}
    .quality-filter {{ display:flex; flex-wrap:wrap; gap:.32rem; width:100%; margin-top:.08rem; }}
    .quality-chip {{
      border:1px solid #555; background:rgba(11,11,11,.9); color:#ddd;
      border-radius:999px; font-size:.68rem; font-weight:600; padding:.24rem .54rem;
      cursor:pointer; transition:transform var(--fast),border-color var(--fast),background var(--fast),color var(--fast);
    }}
    .quality-chip:hover {{ transform:translateY(-1px); border-color:#7c7c7c; }}
    .quality-chip.active {{ border-color:#9f9f9f; background:#f1f1f1; color:#050505; }}

    .cert-filter {{
      flex:1 1 210px; min-width:185px; border-radius:var(--radius-md);
      border:1px solid #474747; background:rgba(6,6,6,.88); color:#ececec;
      font-size:.77rem; padding:.38rem .56rem; outline:none;
      transition:border-color var(--fast),box-shadow var(--fast),background var(--fast);
      font-family:inherit;
    }}
    .cert-filter:focus {{
      border-color:#9d9d9d; box-shadow:0 0 0 3px rgba(200,200,200,.18);
      background:rgba(8,8,8,.95);
    }}
    .row-toggle {{
      border:1px solid #4d4d4d;
      background:linear-gradient(180deg,rgba(21,21,21,.92),rgba(12,12,12,.96));
      color:#ececec; border-radius:var(--radius-md);
      font-size:.73rem; font-weight:600; letter-spacing:.01em;
      padding:.36rem .58rem; cursor:pointer; font-family:inherit;
      transition:transform var(--fast),border-color var(--fast),filter var(--fast);
    }}
    .row-toggle:hover {{ transform:translateY(-1px); border-color:#7d7d7d; filter:brightness(1.08); }}
    .filter-empty {{ color:#9fb5d2; font-size:.75rem; padding:.52rem .18rem .06rem; display:none; }}

    /* ── TABLE ── */
    .cert-table {{
      width:100%; border-collapse:separate; border-spacing:0;
      overflow:hidden; border-radius:12px; border:1px solid var(--line-soft);
    }}
    .cert-table th {{
      text-align:left; font-size:.66rem; text-transform:uppercase;
      letter-spacing:.08em; color:#bababa; background:rgba(14,14,14,.92);
      padding:.36rem .46rem; border-bottom:1px solid #3a3a3a; white-space:nowrap;
    }}
    .cert-table td {{
      padding:.42rem .46rem; border-bottom:1px solid #252525;
      vertical-align:middle; background:rgba(10,10,10,.56);
      transition:background var(--fast);
    }}
    .cert-table tbody tr:hover td {{ background:rgba(22,22,22,.7); }}
    .cert-table tr:last-child td {{ border-bottom:none; }}

    .cert-name {{ font-weight:500; font-size:.78rem; line-height:1.2; }}
    .bundle-id {{
      font-size:.72rem; color:#87a2c4;
      font-family:'IBM Plex Mono',ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
    }}

    .install-btn {{
      position:relative; overflow:hidden;
      display:inline-flex; align-items:center; justify-content:center;
      border-radius:9px; text-decoration:none; font-size:.73rem; font-weight:700;
      letter-spacing:.01em; color:#060606; white-space:nowrap; padding:.31rem .58rem;
      background:linear-gradient(132deg,#f2f2f2,#d0d0d0 62%,#9f9f9f 100%);
      box-shadow:0 7px 15px rgba(255,255,255,.16);
      transition:transform var(--fast),filter var(--fast);
    }}
    .install-btn:hover {{ transform:translateY(-1px); filter:brightness(1.06); }}

    /* ── EXPIRY BADGES ── */
    .expiry-badge {{
      display:inline-flex; align-items:center; border-radius:999px;
      font-size:.66rem; font-weight:700; letter-spacing:.01em; white-space:nowrap;
      padding:.2rem .47rem; border:1px solid transparent;
    }}
    .expiry-ok,.expiry-warn,.expiry-critical {{
      background:#141414; color:#f1f1f1; border-color:rgba(230,230,230,.22);
    }}
    .expiry-dead,.expiry-unknown {{
      background:#0d0d0d; color:#888; border-color:rgba(135,135,135,.22);
    }}
    .expiry-dead {{ text-decoration:line-through; }}
    .expiry-critical {{ animation:pulse 1.6s ease-in-out infinite; }}

    .best-cert-row td {{ background:rgba(28,28,28,.9) !important; }}
    .recommended-badge {{
      display:inline-flex; align-items:center; border-radius:999px;
      border:1px solid rgba(201,201,201,.5); background:rgba(244,244,244,.95);
      color:#090909; font-size:.6rem; font-weight:700; letter-spacing:.02em;
      text-transform:uppercase; margin-left:.38rem; padding:.11rem .34rem; vertical-align:middle;
    }}
    .row-hidden {{ display:none; }}

    /* ── Tried-cert styles ── */
    .tried-row td {{ opacity:.45; }}
    .tried-btn {{
      background: rgba(30,30,30,.9) !important;
      color: #666 !important;
      box-shadow: none !important;
      border: 1px solid #3a3a3a !important;
      cursor: default;
    }}
    .tried-chip {{ border-color:#444; }}
    .tried-chip.active {{ border-color:#666; background:rgba(40,40,40,.9); color:#aaa; }}
    .tried-reset-btn {{
      display:none;
      align-items:center;
      border:1px solid #4a3030;
      background:rgba(40,14,14,.8);
      color:#cc8888;
      border-radius:999px;
      font-size:.65rem;
      font-weight:600;
      padding:.22rem .5rem;
      cursor:pointer;
      font-family:inherit;
      transition:border-color var(--fast),color var(--fast),background var(--fast);
    }}
    .tried-reset-btn:hover {{ border-color:#884444; color:#ffaaaa; background:rgba(60,16,16,.9); }}

    /* ── STATUS PANEL ── */
    .status-panel {{
      margin-bottom:.9rem; padding:.9rem 1rem;
      background:linear-gradient(140deg,rgba(12,12,12,.92),rgba(9,9,9,.95));
      border:1px solid #313131; border-radius:var(--radius-xl);
      box-shadow:var(--shadow-lg); backdrop-filter:blur(10px);
    }}
    .status-panel h2 {{ font-size:.98rem; margin-bottom:.52rem; color:#f3f3f3; }}
    .status-topline {{ display:flex; flex-wrap:wrap; gap:.45rem .75rem; align-items:center; margin-bottom:.5rem; }}
    .status-updated {{
      display:inline-flex; align-items:center; gap:.34rem; font-size:.74rem;
      color:#d6d6d6; border-radius:999px; border:1px solid #4c4c4c;
      background:rgba(9,9,9,.9); padding:.24rem .56rem; white-space:nowrap;
    }}
    .status-updated time {{ color:#fff; font-weight:600; }}
    .health-list {{ list-style:none; display:grid; gap:.38rem; }}
    .health-item {{
      display:flex; align-items:center; justify-content:space-between; gap:.6rem;
      border:1px solid #2e2e2e; border-radius:10px; background:rgba(8,8,8,.72); padding:.36rem .52rem;
    }}
    .health-name {{ color:#e0e0e0; font-size:.74rem; line-height:1.35; }}
    .health-pill {{
      display:inline-flex; align-items:center; border-radius:999px;
      padding:.16rem .45rem; font-size:.67rem; font-weight:700;
      border:1px solid #515151; background:rgba(14,14,14,.88); color:#dedede; white-space:nowrap;
    }}
    .health-pill.is-up {{ color:#dff7e9; border-color:#4b8f6a; background:#173026; }}
    .health-pill.is-warn {{ color:#ffe8c0; border-color:#8e6a3a; background:#2e2012; }}
    .status-note {{ color:var(--text-1); font-size:.78rem; line-height:1.45; margin-top:.5rem; }}

    /* ── FAQ ── */
    .faq-card {{
      margin-bottom:.9rem; padding:.9rem 1rem;
      background:linear-gradient(140deg,rgba(12,12,12,.92),rgba(9,9,9,.95));
      border:1px solid #313131; border-radius:var(--radius-xl);
      box-shadow:var(--shadow-lg); backdrop-filter:blur(10px);
    }}
    .faq-card h2 {{ font-size:.98rem; margin-bottom:.52rem; color:#f3f3f3; }}
    .faq-list details {{
      border:1px solid #323232; border-radius:10px; background:rgba(10,10,10,.55);
      padding:.42rem .52rem; margin-bottom:.38rem;
    }}
    .faq-list summary {{
      cursor:pointer; color:#ededed; font-size:.76rem; font-weight:600; list-style:none;
    }}
    .faq-list summary::-webkit-details-marker {{ display:none; }}
    .faq-list summary::before {{ content:"+"; display:inline-block; width:.85rem; color:#c4c4c4; }}
    .faq-list details[open] summary::before {{ content:"-"; }}
    .faq-list p {{ margin-top:.32rem; color:#c8c8c8; font-size:.74rem; line-height:1.42; }}

    /* ── SAFETY ── */
    .safety-note {{
      margin-bottom:.9rem; padding:.9rem 1rem;
      border:1px solid #4a4a4a; border-radius:var(--radius-xl);
      background:linear-gradient(138deg,rgba(17,17,17,.92),rgba(13,13,13,.88));
      box-shadow:var(--shadow-lg); backdrop-filter:blur(10px);
    }}
    .safety-note h2 {{ font-size:.98rem; margin-bottom:.52rem; color:#f3f3f3; }}
    .safety-note p {{ color:var(--text-1); font-size:.78rem; line-height:1.45; }}
    .safety-note p + p {{ margin-top:.36rem; }}

    footer {{
      margin-top:1.1rem; text-align:center; color:#adadad; font-size:.78rem; line-height:1.45;
      animation:riseIn .55s .25s var(--med) both;
    }}

    #particle-canvas {{
      position:fixed; inset:0; width:100%; height:100%;
      z-index:-3; pointer-events:none; opacity:.7;
    }}

    @media (max-width:900px) {{
      body {{ padding:1rem .72rem 2rem; }}
      .cert-table th:nth-child(4), .cert-table td:nth-child(4) {{ display:none; }}
    }}
    @media (max-width:680px) {{
      nav {{ top:.25rem; gap:.3rem; padding:.35rem; }}
      nav a {{ font-size:.73rem; padding:.26rem .5rem; }}
      .app-card,.quickstart-card,.status-panel,.faq-card,.safety-note {{ padding:.76rem .78rem; }}
      .bundle-id {{ display:none; }}
    }}
    @media (max-width:500px) {{
      .novadns-banner {{ flex-direction:column; text-align:center; }}
      .novadns-btn {{ width:100%; justify-content:center; }}
      .cert-filter {{ min-width:100%; }}
      .row-toggle {{ width:100%; }}
      .quality-chip {{ flex:1 1 auto; text-align:center; }}
    }}

    /* Mobile-UI adaptive mode */
    body.mobile-ui .credit-banner {{ display:none; }}
    body.mobile-ui .app-card {{ margin-bottom:.55rem; padding:.68rem .7rem .78rem; }}

    @media (prefers-reduced-motion:reduce) {{
      *,*::before,*::after {{
        animation-duration:.01ms !important; animation-iteration-count:1 !important;
        transition-duration:.01ms !important; scroll-behavior:auto !important;
      }}
      #particle-canvas {{ display:none; }}
      body::before, body::after {{ display:none; }}
    }}
  </style>
</head>
<body>
  <canvas id="particle-canvas" aria-hidden="true"></canvas>

  <header>
    <h1>Niko's Projects</h1>
    <p>Open this page on your iPhone or iPad, then tap Install.</p>
  </header>

  <div class="credit-banner">
    <p>KSign is made with ❤️ by <a href="https://github.com/nyasami" target="_blank" rel="noopener">Asami</a> — a free, open-source iOS app signer. If it's been useful to you, consider buying her a coffee.</p>
    <a class="donate-btn" href="https://buymeacoffee.com/nyasami" target="_blank" rel="noopener">☕ Donate</a>
  </div>

  <div class="credit-banner" style="margin-top:-.48rem; padding:.5rem 1rem;">
    <p style="font-size:.73rem; color:#888;">UI forked from <a href="https://github.com/newbbd/ipa-distributor" target="_blank" rel="noopener" style="color:#aaa;">newbbd's IPA distributor</a> — this project is Niko's.</p>
  </div>

  <nav>
    {nav_items}
  </nav>

  <section class="quickstart-card">
    <h2>Quick Start</h2>
    <p class="quickstart-note">Follow these steps for the best success rate:</p>
    <ol class="quickstart-steps">
      <li>Set up DNS blocking first so revocation checks are blocked.</li>
      <li>Open an app tab and pick a certificate with plenty of days left.</li>
      <li>After install, trust the profile in <strong>Settings › General › VPN &amp; Device Management</strong>.</li>
    </ol>
  </section>

  <div class="novadns-banner">
    <div class="novadns-banner-icon">🛡</div>
    <div class="novadns-banner-body">
      <strong>Install NovaDNS before installing any app</strong>
      <p>NovaDNS blocks Apple's certificate revocation checks, keeping your signed apps working. Install it first — tap the button now.</p>
    </div>
    <a class="novadns-btn" href="https://novadev.vip/resources/dns/novadns.mobileconfig">⬇ Install NovaDNS</a>
  </div>

  {app_cards()}

  <section class="status-panel" id="status">
    <h2>Source Health</h2>
    <div class="status-topline">
      <div class="status-updated">Last built: <time id="last-updated" datetime="">{build_time}</time></div>
    </div>
    <ul class="health-list">
      <li class="health-item">
        <span class="health-name">Install manifests ({base_url.split("//",1)[-1]})</span>
        <span class="health-pill" id="health-manifests">Checking…</span>
      </li>
    </ul>
    <p class="status-note">Certificate revocations can happen without notice. If one cert fails, try another with more days left.</p>
  </section>

  <section class="faq-card" id="faq">
    <h2>Troubleshooting</h2>
    <div class="faq-list">
      <details>
        <summary>Unable to Install</summary>
        <p>Check storage space, confirm you are on Safari, and retry with a different healthy certificate. Remove any partial app from your home screen and retry.</p>
      </details>
      <details>
        <summary>Untrusted Enterprise Developer</summary>
        <p>Open Settings › General › VPN &amp; Device Management. Tap the profile and trust it, then reopen the app.</p>
      </details>
      <details>
        <summary>App opens then closes immediately</summary>
        <p>The certificate may be revoked or incompatible with your iOS version. Install DNS blocking first, then try a cert with more days remaining.</p>
      </details>
      <details>
        <summary>Certificate revoked</summary>
        <p>Pick a different non-expired certificate, reinstall, and keep DNS blocking active to reduce sudden breakage.</p>
      </details>
    </div>
  </section>

  <section class="safety-note">
    <h2>Trust &amp; Safety</h2>
    <p>Sideloading and enterprise certificates carry risk. Only install apps you trust, avoid entering sensitive credentials in unknown builds, and treat external install links as unverified sources.</p>
    <p>Apple can revoke certificates at any time. This page improves install reliability but cannot guarantee long-term availability.</p>
  </section>

  <footer>
    Auto-deployed from <code>{GITHUB_REPOSITORY}</code> · {build_time}
    · <a href="https://github.com/{GITHUB_REPOSITORY}" target="_blank" rel="noopener">Source</a>
    <br /><span style="font-size:.7rem;color:#555;margin-top:.3rem;display:inline-block;">UI by <a href="https://github.com/newbbd/ipa-distributor" target="_blank" rel="noopener" style="color:#666;">newbbd</a></span>
  </footer>

  <script>
  (function () {{
    const cards = Array.from(document.querySelectorAll('.app-card'));
    const navLinks = Array.from(document.querySelectorAll('nav a'));
    const APP_IDS = [{app_ids_js}];
    const INITIAL_ROWS = 12;
    const MOBILE_INITIAL_ROWS = 6;

    if (!cards.length) return;

    function isMobileUI() {{
      const ua = navigator.userAgent || '';
      return /Mobi|Android|iPhone|iPad|iPod/i.test(ua) ||
        (window.matchMedia('(max-width:820px)').matches &&
         window.matchMedia('(pointer:coarse)').matches);
    }}

    const mobileUI = isMobileUI();
    document.body.classList.toggle('mobile-ui', mobileUI);

    /* ── Particles ── */
    (function setupParticles() {{
      const canvas = document.getElementById('particle-canvas');
      if (!canvas) return;
      if (window.matchMedia('(prefers-reduced-motion:reduce)').matches) return;
      const ctx = canvas.getContext('2d');
      if (!ctx) return;
      const particles = [];
      const count = Math.min(140, Math.max(45, Math.floor((window.innerWidth * window.innerHeight) / 16000)));
      let width = 0, height = 0, dpr = 1, rafId = null;
      function rand(a, b) {{ return a + Math.random() * (b - a); }}
      function resize() {{
        dpr = Math.min(2, window.devicePixelRatio || 1);
        width = window.innerWidth; height = window.innerHeight;
        canvas.width = Math.floor(width * dpr); canvas.height = Math.floor(height * dpr);
        canvas.style.width = width + 'px'; canvas.style.height = height + 'px';
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      }}
      function seed() {{
        particles.length = 0;
        for (let i = 0; i < count; i++)
          particles.push({{ x:rand(0,width), y:rand(0,height), r:rand(.5,1.9), vx:rand(-.09,.09), vy:rand(-.08,.08), alpha:rand(.16,.52) }});
      }}
      function tick() {{
        ctx.clearRect(0, 0, width, height);
        for (const p of particles) {{
          p.x += p.vx; p.y += p.vy;
          if (p.x < -4) p.x = width+4; if (p.x > width+4) p.x = -4;
          if (p.y < -4) p.y = height+4; if (p.y > height+4) p.y = -4;
          ctx.beginPath();
          ctx.fillStyle = 'rgba(170,170,170,' + p.alpha.toFixed(3) + ')';
          ctx.arc(p.x, p.y, p.r, 0, Math.PI*2); ctx.fill();
        }}
        rafId = requestAnimationFrame(tick);
      }}
      resize(); seed(); tick();
      window.addEventListener('resize', () => {{ resize(); seed(); }});
      window.addEventListener('pagehide', () => {{ if (rafId) cancelAnimationFrame(rafId); }});
    }})();

    /* ── Expiry helpers ── */
    function getDaysLeft(row) {{
      const badge = row.querySelector('.expiry-badge');
      if (!badge) return -Infinity;
      const txt = (badge.textContent || '').trim().toLowerCase();
      const m = txt.match(/(-?\\d+)\\s*d\\s*left/);
      if (m) return parseInt(m[1], 10);
      const e = txt.match(/expired\\s+(\\d+)\\s*d\\s+ago/);
      if (e) return -parseInt(e[1], 10);
      const t = (badge.getAttribute('title') || '').match(/expires\\s+(\\d{{4}}-\\d{{2}}-\\d{{2}})/i);
      if (t) {{
        const ms = Date.parse(t[1] + 'T00:00:00Z');
        if (!isNaN(ms)) return Math.floor((ms - Date.now()) / 86400000);
      }}
      return -Infinity;
    }}
    function qualityFromDays(d) {{
      if (!isFinite(d)) return 'unknown';
      if (d <= 0) return 'expired';
      if (d < 30) return 'expiring';
      return 'healthy';
    }}

    /* ── Best-cert badge ── */
    function addBestBadge(rows) {{
      let best = null, bestDays = -1;
      for (const r of rows) {{
        const d = parseInt(r.dataset.daysLeft || '', 10);
        if (isFinite(d) && d > bestDays && d > 0) {{ bestDays = d; best = r; }}
      }}
      if (!best) return;
      best.classList.add('best-cert-row');
      const cn = best.querySelector('.cert-name');
      if (cn && !cn.querySelector('.recommended-badge')) {{
        const b = document.createElement('span');
        b.className = 'recommended-badge'; b.textContent = 'Recommended';
        cn.appendChild(b);
      }}
    }}

    /* ── Tried-cert persistence (localStorage, rebuild-safe) ── */
    const LS_PREFIX = 'tried:';
    function triedKey(appId, certName) {{ return LS_PREFIX + appId + ':' + certName; }}
    function markTried(appId, cn)   {{ try {{ localStorage.setItem(triedKey(appId, cn), '1'); }} catch(_) {{}} }}
    function unmarkTried(appId, cn) {{ try {{ localStorage.removeItem(triedKey(appId, cn)); }} catch(_) {{}} }}
    function isTried(appId, cn)     {{ try {{ return localStorage.getItem(triedKey(appId, cn)) === '1'; }} catch(_) {{ return false; }} }}
    function clearTriedForApp(appId) {{
      try {{
        const pre = LS_PREFIX + appId + ':';
        const gone = [];
        for (let i = 0; i < localStorage.length; i++) {{
          const k = localStorage.key(i);
          if (k && k.startsWith(pre)) gone.push(k);
        }}
        gone.forEach(k => localStorage.removeItem(k));
      }} catch(_) {{}}
    }}
    function pruneOrphans(appId, validNames) {{
      try {{
        const pre = LS_PREFIX + appId + ':';
        const gone = [];
        for (let i = 0; i < localStorage.length; i++) {{
          const k = localStorage.key(i);
          if (k && k.startsWith(pre) && !validNames.has(k.slice(pre.length))) gone.push(k);
        }}
        gone.forEach(k => localStorage.removeItem(k));
      }} catch(_) {{}}
    }}

    /* ── Per-card setup ── */
    /* ── Per-card setup ── */
    function setupCard(card) {{
      const tbody = card.querySelector('tbody');
      if (!tbody) return;
      const appId = card.id;
      const rows = Array.from(tbody.querySelectorAll('tr'));
      const hasExpiry = rows.some(r => r.querySelector('.expiry-badge'));

      rows.forEach(r => {{
        const d = getDaysLeft(r);
        if (isFinite(d)) r.dataset.daysLeft = String(d);
        r.dataset.quality = qualityFromDays(d);
        const cn = r.querySelector('.cert-name');
        if (cn) r.dataset.certName = (cn.textContent || '').replace(/\\s+/g, ' ').trim();
      }});
      rows.sort((a, b) => getDaysLeft(b) - getDaysLeft(a) || a.textContent.localeCompare(b.textContent));
      rows.forEach(r => tbody.appendChild(r));
      if (hasExpiry) addBestBadge(rows);

      // Prune localStorage keys for certs removed by a rebuild
      const validNames = new Set(rows.map(r => r.dataset.certName).filter(Boolean));
      pruneOrphans(appId, validNames);

      const controls   = card.querySelector('.app-controls');
      const filterInput = controls && controls.querySelector('.cert-filter');
      const toggleBtn   = controls && controls.querySelector('.row-toggle');
      const qualBtns    = controls ? Array.from(controls.querySelectorAll('.quality-chip')) : [];
      const emptyEl     = card.querySelector('.filter-empty');
      const rowsLimit   = mobileUI ? MOBILE_INITIAL_ROWS : INITIAL_ROWS;
      let expanded      = !mobileUI;
      let hideTried     = true;
      const activeQ     = new Set(['healthy', 'expiring']);

      if (!filterInput || !toggleBtn) return;

      // Inject "Hide tried" chip + "Reset tried" button into quality filter row
      const qualFilter = controls.querySelector('.quality-filter');

      const triedChip = document.createElement('button');
      triedChip.type = 'button';
      triedChip.className = 'quality-chip tried-chip active';
      triedChip.setAttribute('aria-pressed', 'true');

      const resetBtn = document.createElement('button');
      resetBtn.type = 'button';
      resetBtn.className = 'tried-reset-btn';
      resetBtn.textContent = 'Reset tried';

      if (qualFilter) {{ qualFilter.appendChild(triedChip); qualFilter.appendChild(resetBtn); }}

      function updateTriedUI() {{
        const n = rows.filter(r => r.dataset.certName && isTried(appId, r.dataset.certName)).length;
        triedChip.textContent = n > 0 ? 'Hide tried (' + n + ')' : 'Hide tried';
        resetBtn.style.display = n > 0 ? 'inline-flex' : 'none';
        rows.forEach(r => {{
          const tried = r.dataset.certName ? isTried(appId, r.dataset.certName) : false;
          r.classList.toggle('tried-row', tried);
          const btn = r.querySelector('.install-btn');
          if (btn) {{
            btn.classList.toggle('tried-btn', tried);
            btn.textContent = tried ? '\u2713 Tried' : '\u2b07 Install';
          }}
        }});
      }}
      updateTriedUI();

      // Clicking install marks cert as tried (itms-services link fires normally after)
      rows.forEach(r => {{
        const btn = r.querySelector('.install-btn');
        if (!btn) return;
        btn.addEventListener('click', () => {{
          const certName = r.dataset.certName;
          if (!certName) return;
          isTried(appId, certName) ? unmarkTried(appId, certName) : markTried(appId, certName);
          updateTriedUI();
          refreshRows();
        }});
      }});

      triedChip.addEventListener('click', () => {{
        hideTried = !hideTried;
        triedChip.classList.toggle('active', hideTried);
        triedChip.setAttribute('aria-pressed', hideTried ? 'true' : 'false');
        refreshRows();
      }});

      resetBtn.addEventListener('click', () => {{
        clearTriedForApp(appId);
        updateTriedUI();
        refreshRows();
      }});

      function refreshQBtns() {{
        qualBtns.forEach(b => {{
          const k = b.dataset.quality || '';
          const on = activeQ.has(k);
          b.classList.toggle('active', on);
          b.setAttribute('aria-pressed', on ? 'true' : 'false');
        }});
      }}

      function refreshRows() {{
        const q = (filterInput.value || '').trim().toLowerCase();
        let matched = 0;
        rows.forEach(r => {{
          const textOk = !q || r.textContent.toLowerCase().includes(q);
          const qualOk = !hasExpiry || (r.dataset.quality === 'unknown') || activeQ.has(r.dataset.quality);
          const triedOk = !hideTried || !isTried(appId, r.dataset.certName || '');
          const ok = textOk && qualOk && triedOk;
          if (ok) matched++;
          r.classList.toggle('row-hidden', !(ok && (expanded || matched <= rowsLimit)));
        }});
        if (q) {{
          toggleBtn.style.display = 'none';
          if (emptyEl) emptyEl.style.display = matched ? 'none' : 'block';
        }} else {{
          toggleBtn.style.display = matched > rowsLimit ? 'inline-block' : 'none';
          toggleBtn.textContent = expanded ? 'Show fewer' : 'Show all (' + matched + ')';
          if (emptyEl) emptyEl.style.display = matched ? 'none' : 'block';
        }}
      }}

      filterInput.addEventListener('input', refreshRows);
      toggleBtn.addEventListener('click', () => {{ expanded = !expanded; refreshRows(); }});
      qualBtns.forEach(b => b.addEventListener('click', () => {{
        const k = b.dataset.quality;
        if (!k) return;
        activeQ.has(k) ? activeQ.delete(k) : activeQ.add(k);
        refreshQBtns(); refreshRows();
      }}));
      refreshQBtns(); refreshRows();
    }}

    /* ── Tab switching ── */
    function applyTab(id) {{
      cards.forEach(c => c.classList.toggle('active', c.id === id));
      navLinks.forEach(a => {{
        const lid = (a.getAttribute('href') || '').replace('#', '');
        a.classList.toggle('active', lid === id);
      }});
    }}

    navLinks.forEach(a => {{
      a.addEventListener('click', e => {{
        const id = (a.getAttribute('href') || '').replace('#', '');
        if (!APP_IDS.includes(id)) return; // let non-card links navigate normally
        e.preventDefault();
        history.replaceState(null, '', '#' + id);
        applyTab(id);
        window.scrollTo({{ top:0, behavior:'smooth' }});
      }});
    }});

    const hashId = (window.location.hash || '').replace('#', '');
    const startId = APP_IDS.includes(hashId) ? hashId : (APP_IDS[0] || '');
    if (startId) applyTab(startId);

    cards.forEach(setupCard);

    /* ── Source health probe ── */
    function probe(url, ms) {{
      return new Promise(resolve => {{
        const ctrl = new AbortController();
        const tid = setTimeout(() => ctrl.abort(), ms);
        fetch(url, {{ method:'GET', mode:'no-cors', cache:'no-store', signal:ctrl.signal }})
          .then(() => {{ clearTimeout(tid); resolve(true); }})
          .catch(() => {{ clearTimeout(tid); resolve(false); }});
      }});
    }}
    const pill = document.getElementById('health-manifests');
    if (pill) probe('{base_url}/', 7000).then(ok => {{
      pill.classList.toggle('is-up', ok);
      pill.classList.toggle('is-warn', !ok);
      pill.textContent = ok ? 'Reachable' : 'Check manually';
    }});
  }})();
  </script>
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

    print(f"\nDeploy tree summary:")
    for app_name, entries in sorted(apps_data.items()):
        print(f"  {app_name}/  ({len(entries)} cert(s))")
        for e in entries:
            print(f"    {e['cert_slug']}/manifest.plist")


if __name__ == "__main__":
    main()
