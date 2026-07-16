"""
utils/vision_analyser.py
──────────────────────────
Tesseract-based analyser for AI SDR email campaign activity screenshots.

Reads TWO things off the "Email" campaign activity page (the page reached
by clicking the Email card on Campaign Activities — see Test.py's
EMAIL_CARD_XPATH):

  1. The summary cards row (Email Enabled / Total Tasks / Sent / Bounced /
     Skipped / Failed / Leads Engaged).
  2. The per-lead table beneath it (columns: Lead Name, Email, Company,
     Subject, Date & Time, Status, Meeting, Tasks, Actions) — specifically
     the STATUS column (e.g. "FAILED", "SENT", "DELIVERED"), which is the
     ground truth per lead and can disagree with the summary cards on a
     one-lead test list.

Why direct Tesseract instead of a Python OCR package:
the Anaconda environment has a NumPy/Pandas binary mismatch. PaddleOCR and
pytesseract both import that broken dependency chain before OCR can start.
This module runs the installed `tesseract` executable directly and parses its
TSV output using only Python's standard library. TSV gives each text token a
bounding box, so we can still group tokens into visual rows and pair each
metric label with the value in the same row.

Install once if needed: brew install tesseract

Scope note: this module intentionally only analyses the email activity
screenshot. It is not used for any other screenshot type (SMTP config,
success, error, misc) — those are just embedded as images in the report,
never OCR'd.
"""

import csv
import os
import re
import shutil
import subprocess
from collections import Counter

def _empty_verdict(combo_key, provider, campaign_type, image_path, reason):
    return {
        "combo": combo_key, "provider": provider, "campaign_type": campaign_type,
        "sent": 0, "bounced": 0, "skipped": 0, "failed": 0, "leads_engaged": 0,
        "emails_sent": "NO", "email_status": "Unknown", "evidence_confidence": "Low",
        "reasoning": reason,
        "lead_table_statuses": [], "table_status_counts": {},
        "screenshot_used": image_path, "raw_response": "", "source": "ocr_metrics",
    }


def read_ocr_tokens(image_path):
    """Run the Tesseract CLI and return (text, confidence, box) tokens.

    No Python OCR package is imported here. This makes the report independent
    of PaddleX/PDX and of the broken NumPy/Pandas binary packages.
    """
    executable = os.environ.get("TESSERACT_CMD") or shutil.which("tesseract")
    if not executable:
        raise RuntimeError("Tesseract is not installed or not available on PATH.")

    psm = os.environ.get("TESSERACT_PSM", "11")
    command = [executable, image_path, "stdout", "--oem", "3", "--psm", psm, "tsv"]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or "unknown Tesseract error"
        raise RuntimeError(detail)

    tokens = []
    rows = csv.DictReader(completed.stdout.splitlines(), delimiter="\t")
    for row in rows:
        clean = (row.get("text") or "").strip()
        try:
            confidence = float(row.get("conf", "-1"))
            left = int(row.get("left", "0"))
            top = int(row.get("top", "0"))
            width = int(row.get("width", "0"))
            height = int(row.get("height", "0"))
        except (TypeError, ValueError):
            continue
        if not clean or confidence < 0:
            continue
        box = [[left, top], [left + width, top], [left + width, top + height], [left, top + height]]
        tokens.append((clean, confidence / 100.0, box))
    return tokens


def _run_tesseract_ocr(image_path):
    """Internal OCR entry point using the Tesseract executable."""
    return read_ocr_tokens(image_path)


def _box_center_y(box):
    ys = [p[1] for p in box]
    return sum(ys) / len(ys)


def _box_left_x(box):
    return min(p[0] for p in box)


def _group_into_rows(tokens, row_tolerance=12):
    """Groups OCR tokens into visual rows by bounding-box vertical center,
    then sorts each row left-to-right. row_tolerance is in pixels — bump it
    up if your screenshots are high-DPI and rows are being split apart."""
    if not tokens:
        return []
    tokens_sorted = sorted(tokens, key=lambda t: _box_center_y(t[2]))
    rows = []
    current_row = [tokens_sorted[0]]
    current_y = _box_center_y(tokens_sorted[0][2])
    for tok in tokens_sorted[1:]:
        y = _box_center_y(tok[2])
        if abs(y - current_y) <= row_tolerance:
            current_row.append(tok)
        else:
            rows.append(sorted(current_row, key=lambda t: _box_left_x(t[2])))
            current_row = [tok]
            current_y = y
    rows.append(sorted(current_row, key=lambda t: _box_left_x(t[2])))
    return rows


def _extract_metric(rows, keyword):
    """Finds the row whose text contains `keyword` as a label and returns
    the first integer found in that row that ISN'T part of the label token
    itself (handles 'Sent   120' and 'Sent: 120' layouts, plus the fused
    'Sent120' case as a fallback)."""
    for row in rows:
        row_text = " ".join(tok[0] for tok in row).lower()
        if keyword not in row_text:
            continue
        for tok_text, _, _ in row:
            if keyword in tok_text.lower():
                continue
            digits = re.findall(r'\d+', tok_text)
            if digits:
                return int(digits[0])
        # keyword and number were fused into a single OCR token
        digits = re.findall(r'\d+', row_text)
        if digits:
            return int(digits[0])
    return 0


# ─────────────────────────────────────────────────────────────────────────
# PER-LEAD TABLE — STATUS column
# ─────────────────────────────────────────────────────────────────────────

# Values that can legitimately appear in the table's STATUS column.
_KNOWN_TABLE_STATUSES = {
    "SENT", "FAILED", "BOUNCED", "SKIPPED", "DELIVERED",
    "PENDING", "OPENED", "CLICKED", "REPLIED",
}


def _find_table_header_row(rows):
    """Locates the table header row — the one containing column labels
    like LEAD NAME / EMAIL / COMPANY / SUBJECT / STATUS — and returns its
    index, or -1 if the table isn't visible in this screenshot."""
    for idx, row in enumerate(rows):
        row_text = " ".join(tok[0] for tok in row).upper()
        if "STATUS" in row_text and ("LEAD" in row_text or "SUBJECT" in row_text or "COMPANY" in row_text):
            return idx
    return -1


def _status_column_x(rows, header_idx):
    """x-position of the STATUS header token, used to disambiguate a status
    word from other columns if a value ever collides."""
    for tok_text, _, box in rows[header_idx]:
        if tok_text.strip().upper() == "STATUS":
            return _box_left_x(box)
    return None


def _extract_table_statuses(rows):
    """Walks the rows below the table header and pulls out one STATUS
    value per lead row (e.g. 'FAILED', 'SENT'). Returns a list in row
    order — one entry per lead detected in the table."""
    header_idx = _find_table_header_row(rows)
    if header_idx == -1:
        return []

    status_x = _status_column_x(rows, header_idx)
    statuses = []
    for row in rows[header_idx + 1:]:
        row_candidates = []
        for tok_text, _, box in row:
            token_upper = tok_text.strip().upper()
            if token_upper in _KNOWN_TABLE_STATUSES:
                # If we know the STATUS column's x-position, prefer a token
                # reasonably close to it (guards against a stray status-like
                # word appearing elsewhere in the row, e.g. inside a subject
                # line). If we don't know it, take whatever we find.
                if status_x is None or abs(_box_left_x(box) - status_x) < 300:
                    row_candidates.append(token_upper)
        if row_candidates:
            statuses.append(row_candidates[0])
    return statuses


def analyse_email_screenshot(image_path, provider, campaign_type):
    """
    Runs Tesseract on the given email-activity screenshot and extracts:
      - sent / bounced / skipped / failed / engaged counts (summary cards)
      - lead_table_statuses: one STATUS value per row of the per-lead table
      - table_status_counts: tally of lead_table_statuses, e.g. {"FAILED": 1}

    Returns the verdict dict shape report_generator.py expects.
    """
    combo_key = f"{provider.upper()} - {campaign_type.upper()}"

    if not image_path or not os.path.exists(image_path):
        return _empty_verdict(combo_key, provider, campaign_type, image_path,
                               f"Screenshot does not exist at {image_path}")

    try:
        tokens = _run_tesseract_ocr(image_path)
    except Exception as e:
        return _empty_verdict(combo_key, provider, campaign_type, image_path,
                               f"Tesseract OCR failed with error: {e}")

    if not tokens:
        return _empty_verdict(combo_key, provider, campaign_type, image_path,
                               "Tesseract OCR returned no text for this screenshot.")

    rows = _group_into_rows(tokens)

    sent    = _extract_metric(rows, "sent")
    bounced = _extract_metric(rows, "bounce")
    skipped = _extract_metric(rows, "skip")
    failed  = _extract_metric(rows, "fail")
    engaged = _extract_metric(rows, "engag")

    lead_table_statuses = _extract_table_statuses(rows)
    table_status_counts = dict(Counter(lead_table_statuses))

    emails_sent = "YES" if sent > 0 else "NO"
    if sent > 0 and failed > 0:
        email_status = "Partial Failure"
    elif failed > 0:
        email_status = "Failed"
    elif sent > 0:
        email_status = "Delivered"
    else:
        email_status = "Unknown"

    avg_conf = sum(t[1] for t in tokens) / len(tokens)
    confidence = "High" if avg_conf >= 0.75 else "Medium" if avg_conf >= 0.5 else "Low"
    raw_response = "\n".join(f"{t[0]} ({t[1]:.2f})" for t in tokens)

    reasoning = (
        f"Tesseract OCR detected {sent} sent, {bounced} bounced, {skipped} skipped, "
        f"{failed} failed, {engaged} engaged from the summary cards (avg OCR confidence {avg_conf:.2f})."
    )
    if table_status_counts:
        table_summary = ", ".join(f"{count}x {status.title()}" for status, count in table_status_counts.items())
        reasoning += f" Per-lead table showed: {table_summary}."
    else:
        reasoning += " Per-lead table was not detected in this screenshot (may be scrolled out of view)."

    return {
        "combo": combo_key,
        "provider": provider,
        "campaign_type": campaign_type,
        "sent": sent,
        "bounced": bounced,
        "skipped": skipped,
        "failed": failed,
        "leads_engaged": engaged,
        "emails_sent": emails_sent,
        "email_status": email_status,
        "evidence_confidence": confidence,
        "reasoning": reasoning,
        "lead_table_statuses": lead_table_statuses,
        "table_status_counts": table_status_counts,
        "screenshot_used": image_path,
        "raw_response": raw_response,
        "source": "ocr_metrics",
    }
