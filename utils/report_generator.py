"""
utils/report_generator.py
──────────────────────────
Builds the HTML execution report + AI narrative + email delivery for the
AI SDR Email Campaign automation suite (Test.py). The report is delivered
inline in the email body; a full browser copy is also saved in the run folder.

Two main pieces of evidence feed the report:
  1. PIPELINE STEPS — a numbered, chronological list of every action Test.py
     performed (Open App, Login, Create Lead List, then per-combination
     steps such as Configure SMTP, Verify Preview Mode, Activate Campaign,
     Wait for Completion, Lead Activity/Email Cycle...), each with a
     PASS/FAIL badge. This mirrors the Call automation suite's report
     format. Test.py builds this list via record_step() and passes it in
     as `pipeline_steps`.
  2. EMAIL OCR EVIDENCE — Tesseract-based email metric verdicts
     (utils/vision_analyser.py), one per provider/campaign-type
     combination, showing Sent/Bounced/Skipped/Failed/Engaged.

Regression checks are recorded independently from pipeline steps and rendered
in their own Bug Test Verdict Summary table.

Screenshots are read from <run_dir>/ss/{email,toast,error,...}/ (see
utils/ss_paths.py) instead of a flat run_dir/*.png dump.

AI NARRATIVE PROVIDER PRIORITY:
  1. GEMINI_API_KEY (Google Gemini) — tried first if present in .env.
  2. GITHUB_TOKEN (GitHub Models) — tried if Gemini is absent or fails.
  3. Plain deterministic fallback paragraph — always available, never fails.
"""

import os
import json
import glob
import re
import zipfile
import smtplib
import urllib.request
import urllib.error
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

from .vision_analyser import analyse_email_screenshot
from .ss_paths import ss_root


# ─────────────────────────────────────────────────────────────────────────
# AI NARRATIVE — Gemini (primary) + GitHub Models (fallback) + plain text
# ─────────────────────────────────────────────────────────────────────────

def _gemini_list_models(api_key: str):
    """Diagnostic helper: asks Google which models this key can actually
    call generateContent on. Used only to print a helpful hint when every
    hardcoded model name 404s."""
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    try:
        with urllib.request.urlopen(endpoint, timeout=15) as response:
            res_data = json.loads(response.read().decode("utf-8"))
        names = []
        for m in res_data.get("models", []):
            methods = m.get("supportedGenerationMethods", [])
            if "generateContent" in methods:
                names.append(m.get("name", "").replace("models/", ""))
        return names
    except Exception:
        return []


def _gemini_text(prompt: str, api_key: str, max_tokens: int = 900) -> str:
    """Calls the Gemini API's generateContent endpoint. Tries several
    current model names in case one isn't enabled for this API key/project,
    and logs each individual attempt's failure (not just the last one) so
    it's clear which models were tried and why they failed."""
    models_to_try = [
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-2.0-flash-001",
        "gemini-1.5-flash",
        "gemini-1.5-flash-latest",
        "gemini-1.5-pro",
        "gemini-pro",
    ]
    last_err = None
    for model in models_to_try:
        endpoint = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={api_key}"
        )
        data = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.5, "maxOutputTokens": max_tokens},
        }
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(data).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                res_data = json.loads(response.read().decode("utf-8"))
            candidates = res_data.get("candidates") or []
            if not candidates:
                raise RuntimeError(f"Gemini returned no candidates: {res_data}")
            parts = candidates[0].get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts).strip()
            if not text:
                raise RuntimeError(f"Gemini returned an empty response: {res_data}")
            return text
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")
            print(f"  [AI] Gemini model '{model}' failed: HTTP {e.code}: {body[:300]}")
            last_err = RuntimeError(f"Gemini HTTP {e.code} on model '{model}': {body}")
            continue
        except Exception as e:
            print(f"  [AI] Gemini model '{model}' failed: {e}")
            last_err = e
            continue

    available = _gemini_list_models(api_key)
    if available:
        print(f"  [AI] Models this API key CAN use for generateContent: {available}")
    raise last_err or RuntimeError("Gemini call failed for an unknown reason.")


def _github_models_text(prompt: str, token: str, max_tokens: int = 900) -> str:
    endpoint = "https://models.inference.ai.azure.com/chat/completions"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
    }
    data = {
        "messages": [
            {"role": "system", "content": "You are a QA assistant that summarizes email campaign execution logs."},
            {"role": "user", "content": prompt},
        ],
        "model": "gpt-4o",
        "temperature": 0.5,
        "max_tokens": max_tokens,
    }
    req = urllib.request.Request(endpoint, data=json.dumps(data).encode("utf-8"),
                                  headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as response:
        res_data = json.loads(response.read().decode("utf-8"))
    return res_data["choices"][0]["message"]["content"].strip()


def _generate_narrative(combinations_results: list, suite_summary: dict,
                        email_verdicts: list, pipeline_steps: list, env_vars: dict) -> str:
    combo_lines = "\n".join(
        f"  {r['provider'].upper()} - {r['campaign_type'].upper()}: {r['status']} ({r['duration']}s)"
        + (f" — Error: {r['error_message']}" if r.get("error_message") else "")
        for r in combinations_results
    )
    email_lines = "\n".join(
        f"  {v['combo']}: Sent={v['sent']}, Bounced={v['bounced']}, Skipped={v['skipped']}, "
        f"Failed={v['failed']}, Engaged={v['leads_engaged']}, Status={v['email_status']}"
        for v in email_verdicts
    )
    steps_passed = sum(1 for s in pipeline_steps if (s.get("status") or "").upper() == "PASS")
    steps_failed = len(pipeline_steps) - steps_passed
    failed_step_lines = "\n".join(
        f"  Step {s['num']:02d} — {s['title']}: {s.get('notes','')}"
        for s in pipeline_steps if (s.get("status") or "").upper() != "PASS"
    )

    prompt = f"""You are a senior QA automation engineer writing an execution report narrative
for an email campaign automation run across multiple SMTP providers and campaign types.

Suite Summary:
  Total Combinations Executed: {suite_summary['total_executed']}
  Passed: {suite_summary['passed_tests']}
  Failed: {suite_summary['failed_tests']}
  Pipeline Steps: {len(pipeline_steps)} total, {steps_passed} passed, {steps_failed} failed

Per-Combination Pipeline Results:
{combo_lines or "  None recorded."}

Failed Pipeline Steps (if any):
{failed_step_lines or "  None — every recorded step passed."}

Email Delivery Evidence (from local OCR of the campaign activity overview page):
{email_lines or "  No email evidence available."}

Write a professional 4-6 sentence paragraph summarising:
1. Which provider/campaign-type combinations passed or failed at the pipeline level.
2. What the OCR-derived email metrics showed for each combination — sent, bounced, skipped, failed, engaged.
3. Whether any provider shows a pattern of bounces/failures worth investigating.
4. Whether any specific pipeline step failed and where in the flow it happened.
5. Whether the overall run can be considered a success or needs follow-up.

Do NOT use bullet points. Write in plain paragraph form. Be factual and specific."""

    # 1. Try Gemini first (this is what most .env files in this project set).
    gemini_key = env_vars.get("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if gemini_key and gemini_key.strip() and gemini_key.strip().upper() != "YOUR_GEMINI_API_KEY":
        try:
            return _gemini_text(prompt, gemini_key.strip())
        except Exception as e:
            print(f"  [AI] Gemini narrative generation failed, trying GitHub Models fallback: {e}")

    # 2. Try GitHub Models if a token is present.
    token = env_vars.get("GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token and token.strip() and token.strip().upper() != "YOUR_GITHUB_TOKEN":
        try:
            return _github_models_text(prompt, token.strip())
        except Exception as e:
            print(f"  [AI] GitHub Models narrative generation failed, using deterministic fallback: {e}")

    # 3. Deterministic fallback — always works, never raises.
    passed = suite_summary["passed_tests"]
    failed = suite_summary["failed_tests"]
    return (
        f"The email campaign automation run completed with an overall result of "
        f"{'PASS' if failed == 0 else 'FAIL'}. {steps_passed} step(s) passed, {steps_failed} failed, "
        f"out of {len(pipeline_steps)} total pipeline steps across {suite_summary['total_executed']} "
        f"provider/campaign combination(s) ({passed} passed, {failed} failed at the combination level). "
        f"Email delivery evidence was captured via local OCR for each combination; see the evidence "
        f"cards below for sent, bounced, skipped, failed, and engaged counts."
    )


# ─────────────────────────────────────────────────────────────────────────
# HTML BUILDING — shared badges
# ─────────────────────────────────────────────────────────────────────────

def _status_badge(status: str) -> str:
    is_pass = "PASS" in (status or "").upper()
    if is_pass:
        return ('<span style="background:#f0fdf4;color:#16a34a;border:1px solid #bbf7d0;'
                'padding:3px 10px;border-radius:4px;font-size:11px;font-weight:700;white-space:nowrap;">✅ PASS</span>')
    return ('<span style="background:#fef2f2;color:#dc2626;border:1px solid #fecaca;'
            'padding:3px 10px;border-radius:4px;font-size:11px;font-weight:700;white-space:nowrap;">❌ FAIL</span>')


def _email_status_badge(emails_sent: str, email_status: str) -> str:
    status = (email_status or "").upper()
    if emails_sent == "YES" and status == "DELIVERED":
        style, icon = "background:#f0fdf4;color:#16a34a;border:1px solid #bbf7d0;", "✅"
    elif status in ("FAILED", "PARTIAL FAILURE"):
        style, icon = "background:#fef2f2;color:#dc2626;border:1px solid #fecaca;", "❌"
    elif status == "SKIPPED":
        style, icon = "background:#fffbeb;color:#d97706;border:1px solid #fde68a;", "⏭️"
    elif status == "UNKNOWN":
        style, icon = "background:#f8fafc;color:#64748b;border:1px solid #e2e8f0;", "❓"
    else:
        style, icon = "background:#eff6ff;color:#2563eb;border:1px solid #bfdbfe;", "📧"
    return (f'<span style="display:inline-block;padding:3px 10px;border-radius:4px;font-size:11px;'
            f'font-weight:700;{style}">{icon} Emails Sent: {emails_sent} | Status: {email_status}</span>')


# ─────────────────────────────────────────────────────────────────────────
# HTML BUILDING — Pipeline Steps section (mirrors the Call suite's report)
# ─────────────────────────────────────────────────────────────────────────

def _build_pipeline_steps_html(pipeline_steps: list) -> str:
    if not pipeline_steps:
        return '<div style="color:#94a3b8;font-size:13px;">No pipeline steps were recorded.</div>'

    rows = ""
    for step in pipeline_steps:
        badge = _status_badge(step.get("status", "PASS"))
        notes = step.get("notes", "")
        notes_html = (
            f'<div style="font-size:12px;color:#64748b;margin-top:4px;line-height:1.5;">{notes}</div>'
            if notes else ""
        )
        rows += f"""
      <div style="display:flex;align-items:flex-start;gap:16px;padding:14px 18px;
                  border-bottom:1px solid #e2e8f0;">
        <div style="min-width:56px;font-size:11px;font-weight:800;color:#64748b;
                    text-transform:uppercase;letter-spacing:0.04em;line-height:1.35;">
          STEP<br/>{step['num']:02d}
        </div>
        <div style="flex:1;min-width:0;">
          <div style="font-size:13px;font-weight:700;color:#1e293b;">{step['title']}</div>
          {notes_html}
        </div>
        <div style="flex-shrink:0;">{badge}</div>
      </div>"""

    return f'''<div style="background:#fff;border:1px solid #e2e8f0;border-radius:8px;overflow:hidden;">
      <div style="display:flex;gap:16px;padding:10px 18px;background:#f8fafc;
                  font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.08em;
                  color:#64748b;border-bottom:1px solid #e2e8f0;">
        <div style="min-width:56px;">Step</div>
        <div style="flex:1;">Action &amp; Notes</div>
        <div style="flex-shrink:0;">Status</div>
      </div>
      {rows}
    </div>'''


def _build_bug_checks_html(bug_summary: list) -> str:
    """Render the separate per-run bug-test verdict table."""
    if not bug_summary:
        return '<div style="color:#94a3b8;font-size:13px;">No bug checks were run.</div>'

    badge_styles = {
        "PASS": "background:#f0fdf4;color:#16a34a;border:1px solid #bbf7d0;",
        "FAIL": "background:#fef2f2;color:#dc2626;border:1px solid #fecaca;",
        "SKIPPED": "background:#f8fafc;color:#64748b;border:1px solid #e2e8f0;",
    }
    badge_labels = {"PASS": "✅ PASS", "FAIL": "❌ FAIL", "SKIPPED": "⏭️ SKIP"}
    rows = ""
    for check in bug_summary:
        status = (check.get("status") or "SKIPPED").upper()
        if status not in badge_styles:
            status = "FAIL"
        rows += f'''<tr style="border-bottom:1px solid #e2e8f0;">
          <td style="padding:12px 16px;font-size:12px;font-weight:700;color:#334155;">{check.get('label', '')}</td>
          <td style="padding:12px 16px;font-size:11px;font-weight:700;color:#475569;">{check.get('mode', '')}</td>
          <td style="padding:12px 16px;text-align:center;"><span style="display:inline-block;padding:3px 8px;border-radius:4px;font-size:10px;font-weight:700;{badge_styles[status]}">{badge_labels[status]}</span></td>
          <td style="padding:12px 16px;font-size:11px;line-height:1.5;color:#475569;">{check.get('note', '')}</td>
        </tr>'''
    return f'''<div style="background:#fff;border:1px solid #ddd6fe;border-radius:8px;overflow:hidden;border-top:3px solid #7c3aed;">
      <div style="padding:12px 16px;background:#1e293b;color:#cbd5e1;font-size:12px;font-weight:700;letter-spacing:0.06em;text-transform:uppercase;">🐞 Bug Test Verdict Summary — This Run</div>
      <table style="width:100%;border-collapse:collapse;">
        <thead><tr style="background:#f8fafc;text-align:left;">
          <th style="padding:10px 16px;font-size:10px;color:#64748b;text-transform:uppercase;">Bug Check</th>
          <th style="padding:10px 16px;font-size:10px;color:#64748b;text-transform:uppercase;">Mode</th>
          <th style="padding:10px 16px;font-size:10px;color:#64748b;text-transform:uppercase;text-align:center;">Result</th>
          <th style="padding:10px 16px;font-size:10px;color:#64748b;text-transform:uppercase;">Detail / Note</th>
        </tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </div>'''


# ─────────────────────────────────────────────────────────────────────────
# HTML BUILDING — Combination evidence cards (Pipeline result + Email OCR)
# ─────────────────────────────────────────────────────────────────────────

def _build_combo_rows_html(combinations_results: list, email_verdicts_by_combo: dict) -> str:
    html = ""
    seen = set()
    for r in combinations_results:
        combo_key = f"{r['provider'].upper()} - {r['campaign_type'].upper()}"
        if combo_key in seen:
            continue
        seen.add(combo_key)

        v = email_verdicts_by_combo.get(combo_key, {})
        pipeline_badge = _status_badge(r["status"])
        email_badge = _email_status_badge(v.get("emails_sent", "NO"), v.get("email_status", "Unknown"))
        error_html = (
            f'<div style="margin-top:8px;font-size:11px;color:#dc2626;font-family:monospace;">{r["error_message"]}</div>'
            if r.get("error_message") else ""
        )
        html += f"""
      <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:16px 18px;margin-bottom:12px;">
        <div style="font-size:13px;font-weight:700;color:#334155;margin-bottom:8px;">{combo_key}</div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px;">{pipeline_badge}{email_badge}</div>
        {error_html}
        <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin:10px 0;">
          <div style="background:#fff;border:1px solid #e2e8f0;border-radius:6px;padding:8px;text-align:center;">
            <div style="font-size:9px;color:#94a3b8;text-transform:uppercase;font-weight:700;">Sent</div>
            <div style="font-size:16px;font-weight:800;color:#1e293b;">{v.get('sent', 0)}</div>
          </div>
          <div style="background:#fff;border:1px solid #e2e8f0;border-radius:6px;padding:8px;text-align:center;">
            <div style="font-size:9px;color:#94a3b8;text-transform:uppercase;font-weight:700;">Bounced</div>
            <div style="font-size:16px;font-weight:800;color:#dc2626;">{v.get('bounced', 0)}</div>
          </div>
          <div style="background:#fff;border:1px solid #e2e8f0;border-radius:6px;padding:8px;text-align:center;">
            <div style="font-size:9px;color:#94a3b8;text-transform:uppercase;font-weight:700;">Skipped</div>
            <div style="font-size:16px;font-weight:800;color:#d97706;">{v.get('skipped', 0)}</div>
          </div>
          <div style="background:#fff;border:1px solid #e2e8f0;border-radius:6px;padding:8px;text-align:center;">
            <div style="font-size:9px;color:#94a3b8;text-transform:uppercase;font-weight:700;">Failed</div>
            <div style="font-size:16px;font-weight:800;color:#dc2626;">{v.get('failed', 0)}</div>
          </div>
          <div style="background:#fff;border:1px solid #e2e8f0;border-radius:6px;padding:8px;text-align:center;">
            <div style="font-size:9px;color:#94a3b8;text-transform:uppercase;font-weight:700;">Engaged</div>
            <div style="font-size:16px;font-weight:800;color:#16a34a;">{v.get('leads_engaged', 0)}</div>
          </div>
        </div>
        <div style="font-size:12px;color:#1e293b;line-height:1.6;background:#fff;border:1px solid #e2e8f0;
                    border-radius:6px;padding:8px 10px;">
          <strong>Reasoning:</strong> {v.get('reasoning', 'No evidence captured.')}
        </div>
        {_table_status_html(v.get('table_status_counts', {}))}
      </div>"""
    return html


def _table_status_html(table_status_counts: dict) -> str:
    """Renders the per-lead table STATUS breakdown (e.g. {'FAILED': 1}) as
    small badges under the Reasoning line, if the table was detected."""
    if not table_status_counts:
        return ""
    badges = ""
    for status, count in table_status_counts.items():
        is_ok = status.upper() in ("SENT", "DELIVERED", "OPENED", "CLICKED", "REPLIED")
        style = (
            "background:#f0fdf4;color:#16a34a;border:1px solid #bbf7d0;" if is_ok
            else "background:#fef2f2;color:#dc2626;border:1px solid #fecaca;"
        )
        badges += (f'<span style="{style}padding:2px 8px;border-radius:4px;font-size:10px;'
                   f'font-weight:700;margin-right:6px;">{status.title()}: {count}</span>')
    return (f'<div style="margin-top:8px;font-size:11px;color:#475569;">'
            f'<strong>Per-Lead Table:</strong> {badges}</div>')


def _screenshot_card(path: str) -> str:
    try:
        import base64
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
        name = os.path.basename(path).replace(".png", "").replace("_", " ").title()
        return f"""
      <div style="background:#fff;border:1px solid #e2e8f0;border-radius:8px;overflow:hidden;">
        <img src="data:image/png;base64,{b64}" style="width:100%;display:block;" alt="{name}"/>
        <div style="padding:8px 10px;font-size:11px;color:#64748b;font-family:monospace;">{name}</div>
      </div>"""
    except Exception as e:
        print(f"  [Report] Could not embed screenshot {path}: {e}")
        return ""


def _build_screenshots_gallery(run_dir: str) -> str:
    """
    Walks <run_dir>/ss/<subfolder>/ (see utils/ss_paths.py) and renders one
    gallery section per subfolder that actually has screenshots in it.
    Subfolders are discovered dynamically so adding a new category in
    ss_paths.py just works without touching this function again.
    """
    root = ss_root(run_dir)
    html = ""
    found_any = False

    if os.path.isdir(root):
        subfolders = sorted(d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d)))
        for subfolder in subfolders:
            shots = sorted(glob.glob(os.path.join(root, subfolder, "*.png")))
            if not shots:
                continue
            found_any = True
            cards = "".join(_screenshot_card(p) for p in shots)
            title = subfolder.replace("_", " ").title()
            html += f"""
      <div style="margin-bottom:24px;">
        <div style="font-size:11px;font-weight:700;color:#475569;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:10px;">
          {title}
        </div>
        <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px;">{cards}</div>
      </div>"""

    # Fallback: legacy flat run_dir/*.png (older runs before ss/ existed)
    if not found_any:
        legacy_shots = sorted(glob.glob(os.path.join(run_dir, "*.png")))
        if legacy_shots:
            cards = "".join(_screenshot_card(p) for p in legacy_shots)
            html = f'<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px;">{cards}</div>'
        else:
            html = '<div style="color:#94a3b8;font-size:13px;">No screenshots found.</div>'

    return html


def _build_html_report(combinations_results, suite_summary, narrative, pipeline_steps,
                       email_verdicts_by_combo, run_dir, timestamp, test_num,
                       critical_failure_occurred, critical_error_message, bug_summary=None) -> str:

    total_pass = suite_summary["passed_tests"]
    total_fail = suite_summary["failed_tests"]
    pipeline_passed = sum("PASS" in (r.get("status") or "").upper() for r in combinations_results)

    steps_passed = sum(1 for s in pipeline_steps if (s.get("status") or "").upper() == "PASS")
    steps_failed = len(pipeline_steps) - steps_passed

    business_result = "PASS" if total_fail == 0 and not critical_failure_occurred else "FAIL"
    result_color = "#16a34a" if business_result == "PASS" else "#dc2626"
    result_bg    = "#f0fdf4" if business_result == "PASS" else "#fef2f2"

    steps_html   = _build_pipeline_steps_html(pipeline_steps)
    bug_checks_html = _build_bug_checks_html(bug_summary or [])
    combo_html   = _build_combo_rows_html(combinations_results, email_verdicts_by_combo)
    gallery_html = _build_screenshots_gallery(run_dir)

    critical_html = ""
    if critical_failure_occurred:
        critical_html = f"""
    <div style="background:#fef2f2;border:1px solid #fecaca;border-radius:8px;padding:16px 20px;margin-bottom:24px;">
      <div style="font-size:13px;font-weight:700;color:#dc2626;margin-bottom:6px;">⚠️ Critical Suite-Level Failure</div>
      <div style="font-size:12px;color:#7f1d1d;font-family:monospace;">{critical_error_message}</div>
    </div>"""

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<title>Email Campaign Execution Report</title>
<style>
  *,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:Arial,sans-serif;background:#f1f5f9;color:#1e293b;line-height:1.6}}
  .topbar{{background:#0f172a;color:#fff;padding:24px 40px;display:flex;justify-content:space-between;flex-wrap:wrap;gap:12px}}
  .banner{{background:{result_bg};border-bottom:3px solid {result_color};padding:14px 40px;font-size:14px;font-weight:700;color:{result_color}}}
  .container{{max-width:1100px;margin:0 auto;padding:32px 24px 64px}}
  .section{{margin-bottom:40px}}
  .section-title{{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.12em;color:#64748b;
                  border-bottom:2px solid #e2e8f0;padding-bottom:8px;margin-bottom:20px}}
  .narrative{{background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:20px 24px;font-size:13px;
              color:#334155;line-height:1.8;border-left:4px solid {result_color}}}
  .counters{{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:12px}}
  .counter-card{{background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:16px;text-align:center}}
  .counter-num{{font-size:28px;font-weight:800;line-height:1;margin-bottom:4px}}
  .counter-label{{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.06em;color:#94a3b8}}
</style></head>
<body>
<div class="topbar">
  <div><div style="font-size:20px;font-weight:700;">AI SDR Email Campaign</div>
       <div style="font-size:12px;color:#94a3b8;margin-top:2px;">Automation Execution Report — Test Run #{test_num}</div></div>
  <div style="font-size:11px;color:#64748b;text-align:right;">{timestamp}</div>
</div>
<div class="banner">OVERALL RESULT: <span style="font-size:18px;text-decoration:underline;">{business_result}</span></div>
<div class="container">
  {critical_html}
  <div class="section">
    <div class="section-title">AI-Generated Execution Narrative</div>
    <div class="narrative">{narrative}</div>
  </div>
  <div class="section">
    <div class="section-title">Pipeline Steps — {len(pipeline_steps)} Steps</div>
    {steps_html}
  </div>
  <div class="section">
    <div class="section-title" style="color:#7c3aed;border-bottom-color:#ddd6fe;">🐞 Automation Bug Checks</div>
    {bug_checks_html}
  </div>
  <div class="section">
    <div class="section-title">Run Summary</div>
    <div class="counters">
      <div class="counter-card"><div class="counter-num" style="color:#1e293b;">{suite_summary['total_executed']}</div><div class="counter-label">Combinations Executed</div></div>
      <div class="counter-card"><div class="counter-num" style="color:#16a34a;">{total_pass}</div><div class="counter-label">Combinations Passed</div></div>
      <div class="counter-card"><div class="counter-num" style="color:#dc2626;">{total_fail}</div><div class="counter-label">Combinations Failed</div></div>
      <div class="counter-card"><div class="counter-num" style="color:#2563eb;">{pipeline_passed}/{len(combinations_results)}</div><div class="counter-label">Pipeline Succeeded</div></div>
      <div class="counter-card"><div class="counter-num" style="color:#16a34a;">{steps_passed}</div><div class="counter-label">Steps Passed</div></div>
      <div class="counter-card"><div class="counter-num" style="color:#dc2626;">{steps_failed}</div><div class="counter-label">Steps Failed</div></div>
    </div>
  </div>
  <div class="section">
    <div class="section-title">Pipeline Result &amp; Email OCR Evidence</div>
    {combo_html or '<div style="color:#94a3b8;font-size:13px;">No combinations recorded.</div>'}
  </div>
  <!-- EXECUTION_SCREENSHOTS_START -->
  <div class="section">
    <div class="section-title">Execution Screenshots</div>
    {gallery_html}
  </div>
  <!-- EXECUTION_SCREENSHOTS_END -->
</div>
</body></html>"""


def _build_txt_log(combinations_results, suite_summary, narrative, pipeline_steps,
                   email_verdicts_by_combo, timestamp, bug_summary=None) -> str:
    lines = [
        "=" * 60, "  AI SDR EMAIL CAMPAIGN — EXECUTION LOG", "=" * 60,
        f"  Run Timestamp: {timestamp}",
        f"  Combinations — Total: {suite_summary['total_executed']}  "
        f"Passed: {suite_summary['passed_tests']}  Failed: {suite_summary['failed_tests']}",
        "=" * 60, "", "PIPELINE STEPS", "-" * 60,
    ]
    for s in pipeline_steps:
        lines.append(f"STEP {s['num']:02d} [{s.get('status','PASS')}] {s['title']}")
        if s.get("notes"):
            lines.append(f"          {s['notes']}")
    lines += ["", "BUG CHECKS", "-" * 60]
    for check in bug_summary or []:
        lines.append(f"[{check.get('status', 'SKIPPED')}] {check.get('label', '')} [{check.get('mode', '')}] — {check.get('note', '')}")
    lines += ["", "COMBINATION RESULTS", "-" * 60]
    seen = set()
    for r in combinations_results:
        combo_key = f"{r['provider'].upper()} - {r['campaign_type'].upper()}"
        if combo_key in seen:
            continue
        seen.add(combo_key)
        v = email_verdicts_by_combo.get(combo_key, {})
        lines += [
            f"{combo_key}: {r['status']} ({r['duration']}s)",
            f"  Sent={v.get('sent',0)} Bounced={v.get('bounced',0)} Skipped={v.get('skipped',0)} "
            f"Failed={v.get('failed',0)} Engaged={v.get('leads_engaged',0)} Status={v.get('email_status','Unknown')}",
            f"  Reasoning: {v.get('reasoning','N/A')}",
            "-" * 40,
        ]
    lines += ["", "NARRATIVE", "-" * 60, narrative, "", "=" * 60]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────
# OCR PASS OVER ALL COMBINATION SCREENSHOTS — email activity only
# ─────────────────────────────────────────────────────────────────────────

def _run_email_ocr(combinations_results: list) -> dict:
    """
    For each provider/campaign_type combination, OCR its email_activity
    screenshot (stored on the result dict under 'email_activity_screenshot'
    — points into <run_dir>/ss/email/, see utils/ss_paths.py) and build a
    verdict. Returns {combo_label: verdict}. This is the ONLY OCR pass in
    the report pipeline — vision_analyser.py's analyse_email_screenshot()
    is scoped exclusively to the email activity screenshot.

    If Tesseract isn't installed or errors out, analyse_email_screenshot()
    catches that internally and returns an "Unknown"-status verdict rather
    than raising — so a broken/missing OCR install never blocks the HTML
    report or the AI narrative from being generated and emailed.
    """
    verdicts_by_combo = {}
    for r in combinations_results:
        combo_key = f"{r['provider'].upper()} - {r['campaign_type'].upper()}"
        if combo_key in verdicts_by_combo:
            continue
        shot = r.get("email_activity_screenshot", "")
        if shot and os.path.exists(shot):
            verdict = analyse_email_screenshot(shot, r["provider"], r["campaign_type"])
        else:
            verdict = {
                "combo": combo_key, "provider": r["provider"], "campaign_type": r["campaign_type"],
                "sent": 0, "bounced": 0, "skipped": 0, "failed": 0, "leads_engaged": 0,
                "emails_sent": "NO", "email_status": "Unknown", "evidence_confidence": "Low",
                "reasoning": f"No email activity screenshot was captured for {combo_key}.",
                "screenshot_used": "", "raw_response": "", "source": "ocr_metrics",
            }
        verdicts_by_combo[combo_key] = verdict
    return verdicts_by_combo


# ─────────────────────────────────────────────────────────────────────────
# EMAIL SENDING
# ─────────────────────────────────────────────────────────────────────────

def _email_safe_html(html_body: str) -> str:
    """Prepare the saved report for an email body.

    The browser copy embeds every screenshot as base64 data.  Keeping those
    images in the email can make the message too large for the SMTP server or
    the recipient's mail client, so the screenshot gallery is removed from
    the email. Screenshots remain in the locally saved HTML report and in the
    optional zip attachment.
    """
    html_body = re.sub(
        r'<!-- EXECUTION_SCREENSHOTS_START -->.*?<!-- EXECUTION_SCREENSHOTS_END -->',
        '',
        html_body,
        flags=re.DOTALL,
    )
    # Safety net for any image left outside the gallery.
    return re.sub(r'<img\b[^>]*>', '', html_body, flags=re.IGNORECASE)

def _send_report_email(html_body: str, txt_log: str, run_dir: str, timestamp: str,
                       test_num: int, env_vars: dict, business_result: str,
                       report_path: str = ""):
    recipient_email = env_vars.get("REPORT_EMAILS")
    sender_email    = env_vars.get("SMTP_EMAIL", "jetissha_gautam@technologymindz.com")
    sender_password = env_vars.get("SMTP_PASSWORD")
    smtp_server     = env_vars.get("SMTP_HOST", "smtp-mail.outlook.com")
    smtp_port       = int(env_vars.get("SMTP_PORT", 587))

    if not recipient_email or not sender_email or not sender_password:
        print("  [Email] Skipped — missing recipient/sender/password in .env.")
        return False

    icon = "✅" if business_result == "PASS" else "❌"
    msg = MIMEMultipart("mixed")
    msg["From"]    = sender_email
    msg["To"]      = recipient_email
    msg["Subject"] = f"{icon} Email Campaign Report — Result: {business_result} | Test Run {test_num} ({timestamp})"
    # The report must be the HTML body, not an .html attachment.  This is the
    # same delivery approach as the Call Campaign report.  The full report is
    # still saved locally; large base64 screenshots are removed only from the
    # emailed copy so normal email-size limits are not exceeded.
    msg.attach(MIMEText(_email_safe_html(html_body), "html", "utf-8"))
    print("  [Email] HTML execution report added to the email body.")

    zip_path = os.path.join(run_dir, f"campaign_validation_screenshots_{timestamp}.zip")
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for root, _, files in os.walk(run_dir):
                for filename in files:
                    if filename.lower().endswith(".png"):
                        full_path = os.path.join(root, filename)
                        archive.write(full_path, os.path.relpath(full_path, run_dir))
        zip_size_mb = os.path.getsize(zip_path) / (1024 * 1024)
        if zip_size_mb > 18:
            print(f"  [Email] Screenshot archive is {zip_size_mb:.1f} MB; saved locally, not attached.")
            zip_path = ""
        else:
            print(f"  [Email] Screenshot archive prepared: {zip_size_mb:.1f} MB.")
    except Exception as e:
        print(f"  [Email] Could not build screenshots archive: {e}")
        zip_path = ""

    if zip_path:
        try:
            with open(zip_path, "rb") as attachment:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(attachment.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f"attachment; filename={os.path.basename(zip_path)}")
            msg.attach(part)
        except Exception as e:
            print(f"  [Email] Could not attach screenshots archive: {e}")

    if txt_log:
        txt_path = os.path.join(run_dir, f"execution_summary_{timestamp}.txt")
        try:
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(txt_log)
            with open(txt_path, "rb") as attachment:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(attachment.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f"attachment; filename={os.path.basename(txt_path)}")
            msg.attach(part)
        except Exception as e:
            print(f"  [Email] Could not attach txt log: {e}")

    recipients = [address.strip() for address in recipient_email.split(",") if address.strip()]
    try:
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, recipients, msg.as_string())
        server.quit()
        print(f"  [Email] Report sent to {recipient_email} via {smtp_server}:{smtp_port}.")
        return True
    except Exception as e:
        print(f"  [Email] Send failed ({type(e).__name__}): {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────
# PUBLIC ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────

def generate_email_campaign_report(combinations_results, suite_summary, test_durations,
                                   test_results, run_dir, timestamp, test_num,
                                   suite_start_time, critical_failure_occurred,
                                   critical_error_message, env_vars, pipeline_steps=None,
                                   bug_summary=None):
    """
    Called from Test.py in place of generate_ai_report()/send_report_email().
    Runs OCR over each combination's email activity screenshot, builds the
    HTML report (with the numbered Pipeline Steps section) + txt log, and
    emails the report inline. The screenshots zip and txt log are attached
    when available; the full HTML report remains saved in the run folder.

    `pipeline_steps` is the list Test.py accumulates via record_step() —
    each entry: {"num": int, "title": str, "notes": str, "status": "PASS"|"FAIL"}.
    """
    pipeline_steps = pipeline_steps or []

    print("  [OCR] Analysing email activity screenshots locally (Tesseract)...")
    email_verdicts_by_combo = _run_email_ocr(combinations_results)

    print("  [AI] Generating execution narrative...")
    narrative = _generate_narrative(combinations_results, suite_summary,
                                    list(email_verdicts_by_combo.values()),
                                    pipeline_steps, env_vars)

    business_result = "PASS" if suite_summary["failed_tests"] == 0 and not critical_failure_occurred else "FAIL"

    html_report = _build_html_report(
        combinations_results, suite_summary, narrative, pipeline_steps, email_verdicts_by_combo,
        run_dir, timestamp, test_num, critical_failure_occurred, critical_error_message, bug_summary,
    )
    report_path = os.path.join(run_dir, f"execution_report_{timestamp}.html")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(html_report)
    print(f"  [Report] HTML saved: {report_path}")

    txt_log = _build_txt_log(combinations_results, suite_summary, narrative,
                             pipeline_steps, email_verdicts_by_combo, timestamp, bug_summary)

    _send_report_email(html_report, txt_log, run_dir, timestamp, test_num, env_vars,
                       business_result, report_path=report_path)

    return report_path
