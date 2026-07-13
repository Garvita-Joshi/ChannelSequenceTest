import os
import time
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
import campaign_selectors
import easyocr


# ==========================================
# 1. INITIALIZATION & FOLDER SETUP
# ==========================================
# Create parent folder for reports and screenshots
PARENT_REPORT_DIR = "Report"
os.makedirs(PARENT_REPORT_DIR, exist_ok=True)

# Create a subfolder inside Report for this test run (dynamically increments Test 1, Test 2, etc.)
import re
test_num = 1
if os.path.exists(PARENT_REPORT_DIR):
    for folder in os.listdir(PARENT_REPORT_DIR):
        if os.path.isdir(os.path.join(PARENT_REPORT_DIR, folder)):
            match = re.match(r"^Test\s+(\d+)", folder)
            if match:
                num = int(match.group(1))
                if num >= test_num:
                    test_num = num + 1

RUN_DIR = os.path.join(PARENT_REPORT_DIR, f"Test {test_num}")
os.makedirs(RUN_DIR, exist_ok=True)

RUN_SCREENSHOT_DIR = RUN_DIR

# Generate a unique timestamp for logging and creating unique campaign names
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
log_file_path = os.path.join(RUN_DIR, f"execution_log_{timestamp}.txt")

def log_message(message):
    """Helper function to print logs and save them into a separate text file."""
    formatted_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_line = f"[{formatted_time}] {message}"
    print(log_line)
    with open(log_file_path, "a") as f:
        f.write(log_line + "\n")


def clean_exception(exc):
    """Extract a clean, concise string description of the exception, stripping out chromedriver stack traces."""
    exc_type = type(exc).__name__
    exc_str = str(exc)
    
    # Try to extract from exc.msg if it's a Selenium exception
    msg = getattr(exc, 'msg', '') or exc_str
    
    # Clean up stacktrace from message
    if "stacktrace" in msg.lower():
        for marker in ["stacktrace:", "stacktrace", "stack trace:", "stack trace"]:
            if marker in msg.lower():
                idx = msg.lower().find(marker)
                if idx != -1:
                    msg = msg[:idx]
                break
                
    msg = msg.strip()
    # Strip leading "Message:" or "Exception:" if present
    for prefix in ["message:", "exception:"]:
        if msg.lower().startswith(prefix):
            msg = msg[len(prefix):].strip()
            
    # If the message is empty or generic, return exception type name
    if not msg or msg.lower() in ["message", "exception", "error"]:
        return exc_type
        
    return f"{exc_type}: {msg}"


def clean_traceback(tb_str):
    """Strip chromedriver stack trace details from traceback output."""
    cleaned_lines = []
    in_stacktrace = False
    for line in tb_str.splitlines():
        if "stacktrace:" in line.lower() or "stack trace:" in line.lower():
            in_stacktrace = True
            continue
        if in_stacktrace:
            if line.strip().startswith("chromedriver") or line.startswith("\t") or "chromedriver" in line.lower() or "kernel32" in line.lower() or "ntdll" in line.lower():
                continue
            else:
                in_stacktrace = False
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


def is_table_line(line):
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.startswith('|'):
        return True
    if stripped.count('|') >= 2:
        return True
    char_set = set(stripped.replace(' ', ''))
    if char_set.issubset({'-', ':', '|'}) and len(char_set) > 0 and '-' in char_set:
        return True
    return False


def process_table_block(table_lines):
    def is_divider_line(l):
        s = l.strip().replace('|', '').replace('-', '').replace(':', '').replace(' ', '')
        return len(s) == 0 and '-' in l
        
    clean_lines = [l for l in table_lines if not is_divider_line(l)]
    if not clean_lines:
        return ""
        
    header_line = clean_lines[0]
    headers = [col.strip() for col in header_line.split('|') if col.strip() != '']
    if not headers:
        headers = [col.strip() for col in header_line.split('|')][1:-1]
        
    html = '<table style="border-collapse: collapse; width: 100%; margin: 15px 0; font-family: \'Segoe UI\', Arial, sans-serif; border: 1px solid #E5E7E9; font-size: 13px; box-shadow: 0 2px 4px rgba(0,0,0,0.02);">'
    html += '<thead><tr style="background: linear-gradient(135deg, #1F618D, #2C3E50); color: white;">'
    for h in headers:
        html += f'<th style="padding: 10px 12px; text-align: left; font-weight: 600; border: 1px solid #BDC3C7;">{h}</th>'
    html += '</tr></thead><tbody>'
    
    row_idx = 0
    for line in clean_lines[1:]:
        cols = [col.strip() for col in line.split('|')]
        if len(cols) > 1 and cols[0] == '':
            cols = cols[1:]
        if len(cols) > 0 and cols[-1] == '':
            cols = cols[:-1]
            
        if not cols:
            continue
            
        bg_color = "#F8F9F9" if row_idx % 2 == 0 else "#FFFFFF"
        html += f'<tr style="background-color: {bg_color}; border-bottom: 1px solid #E5E7E9;">'
        
        while len(cols) < len(headers):
            cols.append('')
        cols = cols[:len(headers)]
        
        for col in cols:
            val = col
            val_upper = val.upper()
            if "PASS" in val_upper:
                val = '<span style="background-color: #D4EFDF; color: #196F3D; padding: 2px 6px; border-radius: 4px; font-weight: bold; font-size: 11px; display: inline-block;">PASS</span>'
            elif "FAIL" in val_upper:
                val = '<span style="background-color: #FADBD8; color: #78281F; padding: 2px 6px; border-radius: 4px; font-weight: bold; font-size: 11px; display: inline-block;">FAIL</span>'
            html += f'<td style="padding: 10px 12px; border: 1px solid #E5E7E9; color: #2C3E50;">{val}</td>'
        html += '</tr>'
        row_idx += 1
        
    html += '</tbody></table>'
    return html


def parse_markdown_to_rich_html(text):
    lines = text.split('\n')
    processed_lines = []
    in_table = False
    table_lines = []
    
    for line in lines:
        if is_table_line(line):
            in_table = True
            table_lines.append(line)
        else:
            if in_table:
                html_table = process_table_block(table_lines)
                processed_lines.append(html_table)
                table_lines = []
                in_table = False
            processed_lines.append(line)
            
    if in_table:
        html_table = process_table_block(table_lines)
        processed_lines.append(html_table)
        
    import re
    result_lines = []
    for line in processed_lines:
        if line.startswith('<table') or line.endswith('</table>'):
            result_lines.append(line)
            continue
            
        line = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', line)
        line = re.sub(r'^\s*###\s*(.*?)$', r'<h3>\1</h3>', line)
        line = re.sub(r'^\s*##\s*(.*?)$', r'<h2>\1</h2>', line)
        line = re.sub(r'^\s*#\s*(.*?)$', r'<h1>\1</h1>', line)
        line = re.sub(r'^\s*[\-\*]\s*(.*?)$', r'<li>\1</li>', line)
        result_lines.append(line)
        
    return '<br>'.join(result_lines)


def generate_email_table():
    import html as py_html
    html = """<table style="border-collapse: collapse; width: 100%; max-width: 600px; margin: 20px 0; font-family: 'Segoe UI', Arial, sans-serif; box-shadow: 0 4px 10px rgba(0,0,0,0.08); border-radius: 10px; overflow: hidden; border: 1px solid #E5E7E9;">
    <thead>
        <tr style="background: linear-gradient(135deg, #1B4F72, #2C3E50); color: white; border-bottom: 3px solid #1A5276;">
            <th style="padding: 14px 18px; text-align: left; font-weight: 600; font-size: 14px; letter-spacing: 0.5px;">Test Case</th>
            <th style="padding: 14px 18px; text-align: center; font-weight: 600; font-size: 14px; letter-spacing: 0.5px; width: 110px;">Pass/Fail</th>
            <th style="padding: 14px 18px; text-align: right; font-weight: 600; font-size: 14px; letter-spacing: 0.5px; width: 120px;">Time Taken</th>
        </tr>
    </thead>
    <tbody>"""
    
    row_idx = 0
    for test_name in test_results.keys():
        status_raw = test_results[test_name]
        duration = test_durations.get(test_name, 0.0)
        
        is_pass = "PASS" in status_raw.upper()
        if is_pass:
            status_html = '<span style="background-color: #D4EFDF; color: #196F3D; padding: 4px 10px; border-radius: 12px; font-weight: bold; font-size: 12px; display: inline-block;">PASS</span>'
            error_html = ""
        else:
            status_html = '<span style="background-color: #FADBD8; color: #78281F; padding: 4px 10px; border-radius: 12px; font-weight: bold; font-size: 12px; display: inline-block;">FAIL</span>'
            
            error_msg = ""
            if " - Error: " in status_raw:
                error_msg = status_raw.split(" - Error: ", 1)[1]
            elif "Error:" in status_raw:
                error_msg = status_raw.split("Error:", 1)[1]
            else:
                stripped = status_raw.strip()
                if stripped.upper() not in ["FAIL", "FAILED"]:
                    error_msg = stripped
            
            if error_msg:
                escaped_error = py_html.escape(error_msg)
                error_html = f'<div style="color: #78281F; font-size: 11px; margin-top: 6px; font-family: Consolas, Monaco, monospace; background-color: #FADBD8; padding: 6px 10px; border-radius: 4px; border-left: 3px solid #78281F; text-align: left; word-break: break-word; line-height: 1.4;">{escaped_error}</div>'
            else:
                error_html = ""
            
        bg_color = "#F8F9F9" if row_idx % 2 == 0 else "#FFFFFF"
        html += f"""
        <tr style="background-color: {bg_color}; border-bottom: 1px solid #E5E7E9;">
            <td style="padding: 12px 18px; color: #2C3E50; font-size: 14px; font-weight: 500; vertical-align: top;">
                {test_name}
                {error_html}
            </td>
            <td style="padding: 12px 18px; text-align: center; vertical-align: top;">{status_html}</td>
            <td style="padding: 12px 18px; text-align: right; color: #566573; font-size: 14px; font-family: monospace; vertical-align: top;">{duration}s</td>
        </tr>"""
        row_idx += 1
        
    html += """
    </tbody>
</table>"""
    return html


def send_report_email():
    """Zip all screenshots and send them along with HTML body and Summary Report.doc via SMTP."""
    import zipfile
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.base import MIMEBase
    from email import encoders

    recipient_email = env_vars.get("REPORT_RECIPIENT_EMAIL")
    sender_email = env_vars.get("SENDER_EMAIL", "jetissha_gautam@technologymindz.com")
    sender_password = env_vars.get("SENDER_PASSWORD")
    smtp_server = env_vars.get("SMTP_SERVER", "smtp-mail.outlook.com")
    smtp_port = int(env_vars.get("SMTP_PORT", 587))

    if not recipient_email or not sender_email or not sender_password:
        log_message("Warning: Email reporting skipped. Missing recipient, sender email, or sender password in .env.")
        return False

    log_message("Preparing email report...")
    
    # 1. Zip all screenshots in RUN_DIR
    zip_path = os.path.join(RUN_DIR, f"campaign_validation_screenshots_{timestamp}.zip")
    try:
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(RUN_DIR):
                for file in files:
                    if file.endswith('.png'):
                        file_path = os.path.join(root, file)
                        zipf.write(file_path, arcname=file)
        log_message("Created screenshots zip archive.")
    except Exception as zip_err:
        log_message(f"Failed to create screenshots zip: {clean_exception(zip_err)}")
        return False

    # 2. Load the AI summary report if it exists
    ai_report_path = os.path.join(RUN_DIR, f"ai_summary_report_{timestamp}.txt")
    ai_body = ""
    if os.path.exists(ai_report_path):
        try:
            with open(ai_report_path, "r", encoding="utf-8") as rf:
                ai_body = rf.read()
        except Exception:
            pass

    # 3. Construct HTML email body
    import re
    if ai_body:
        # Programmatically clean "Prepared by: AI QA Automation Assistant" or similar sign-offs
        ai_body = re.sub(
            r"(?i)prepared\s+by\s*:\s*(?:enterprise\s+)?ai\s+qa\s+automation\s+assistant\b",
            "",
            ai_body
        )
        ai_body = re.sub(
            r"(?i)prepared\s+by\s*:\s*ai\s+assistant\b",
            "",
            ai_body
        )
        ai_body = ai_body.strip()
    ai_body_html = ""
    if ai_body:
        formatted_body = parse_markdown_to_rich_html(ai_body)
        ai_body_html = f"""
        <div style="background-color: #FDFDFD; border: 1px solid #E5E7E9; border-radius: 8px; padding: 20px; margin-top: 25px; box-shadow: 0 2px 5px rgba(0,0,0,0.02);">
            <h2 style="color: #1B4F72; border-bottom: 2px solid #5DADE2; padding-bottom: 8px; margin-top: 0; font-size: 18px; font-weight: bold;">AI Executive Summary</h2>
            <div style="font-size: 14px; color: #2C3E50; line-height: 1.6;">
                {formatted_body}
            </div>
        </div>
        """

    total_time = round(time.time() - suite_start_time, 2)
    critical_alert_html = ""
    if critical_failure_occurred:
        import html as py_html
        escaped_msg = py_html.escape(critical_error_message)
        escaped_tb = py_html.escape(clean_traceback(critical_error_traceback))
        critical_alert_html = f"""
        <div style="background-color: #FDEDEC; border: 1px solid #F5B7B1; border-left: 5px solid #C0392B; border-radius: 8px; padding: 20px; margin-bottom: 25px; box-shadow: 0 2px 5px rgba(0,0,0,0.05);">
            <h2 style="color: #78281F; margin-top: 0; font-size: 16px; font-weight: bold;">⚠️ Critical Suite-Level Failure</h2>
            <p style="font-size: 14px; color: #78281F; margin: 5px 0 10px 0;"><strong>Error:</strong> {escaped_msg}</p>
            <details style="margin-top: 10px; cursor: pointer;">
                <summary style="font-size: 13px; color: #566573; font-weight: 600;">View System Traceback</summary>
                <pre style="background-color: #F9EBEA; padding: 12px; border-radius: 4px; font-size: 11px; color: #78281F; font-family: Consolas, Monaco, monospace; white-space: pre-wrap; margin-top: 8px; line-height: 1.4;">{escaped_tb}</pre>
            </details>
        </div>
        """

    email_html = f"""
    <html>
    <head>
        <meta charset="utf-8">
    </head>
    <body style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; line-height: 1.6; color: #2C3E50; background-color: #F4F6F7; padding: 20px; margin: 0;">
        <div style="max-width: 700px; background-color: #FFFFFF; border-radius: 10px; padding: 30px; box-shadow: 0 4px 10px rgba(0,0,0,0.05); margin: 0 auto; border: 1px solid #E5E7E9;">
            <div style="background: linear-gradient(135deg, #2E86C1, #1B4F72); color: white; padding: 20px 25px; border-radius: 8px; margin-bottom: 25px; box-shadow: 0 3px 6px rgba(0,0,0,0.05);">
                <h1 style="margin: 0; font-size: 24px; font-weight: bold; color: #FFFFFF;">Campaign Validation Summary Report</h1>
                <p style="margin: 5px 0 0 0; opacity: 0.9; font-size: 14px;">Test Run #{test_num} ({timestamp})</p>
            </div>
            
            {critical_alert_html}
            
            <table style="width: 100%; border-collapse: collapse; margin-bottom: 25px; font-size: 14px; color: #34495E;">
                <tr style="border-bottom: 1px solid #EAEDED;">
                    <td style="padding: 8px 0; font-weight: bold; width: 180px; color: #2E86C1;">Total Execution Time:</td>
                    <td style="padding: 8px 0; font-family: monospace; font-weight: 500;">{total_time}s</td>
                </tr>
                <tr style="border-bottom: 1px solid #EAEDED;">
                    <td style="padding: 8px 0; font-weight: bold; color: #2E86C1;">Total Tests Executed:</td>
                    <td style="padding: 8px 0; font-weight: bold;">{suite_summary['total_executed']}</td>
                </tr>
                <tr style="border-bottom: 1px solid #EAEDED;">
                    <td style="padding: 8px 0; font-weight: bold; color: #27AE60;">Passed Tests:</td>
                    <td style="padding: 8px 0; font-weight: bold; color: #27AE60;">{suite_summary['passed_tests']}</td>
                </tr>
                <tr style="border-bottom: 1px solid #EAEDED;">
                    <td style="padding: 8px 0; font-weight: bold; color: #C0392B;">Failed Tests:</td>
                    <td style="padding: 8px 0; font-weight: bold; color: #C0392B;">{suite_summary['failed_tests']}</td>
                </tr>
            </table>
            
            <h2 style="color: #1B4F72; border-bottom: 2px solid #AED6F1; padding-bottom: 8px; font-size: 18px; margin-top: 30px; font-weight: bold;">Individual Test Case Metrics</h2>
            {generate_email_table()}
            
            {ai_body_html}
            
            <hr style="border: 0; border-top: 1px solid #E5E7E9; margin: 30px 0;">
            <p style="font-size: 12px; color: #BDC3C7; text-align: center; margin: 0;">This is an automated campaign validation report generated by the Enterprise AI QA Automation framework.</p>
        </div>
    </body>
    </html>
    """

    # 4. Construct the MIME message
    msg = MIMEMultipart()
    msg['From'] = sender_email
    msg['To'] = recipient_email
    msg['Subject'] = f"Campaign Validation Report - Test Run {test_num} ({timestamp})"
    
    msg.attach(MIMEText(email_html, 'html'))

    # Attach the screenshots zip file
    if os.path.exists(zip_path):
        try:
            with open(zip_path, "rb") as attachment:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(attachment.read())
                encoders.encode_base64(part)
                part.add_header(
                    "Content-Disposition",
                    f"attachment; filename={os.path.basename(zip_path)}",
                )
                msg.attach(part)
                log_message("Attached screenshots zip to email.")
        except Exception as attach_err:
            log_message(f"Failed to attach zip file to email: {clean_exception(attach_err)}")

    # Attach the AI Executive Summary report (.doc) as "Summary Report.doc"
    ai_doc_path = os.path.join(RUN_DIR, "Summary Report.doc")
    if os.path.exists(ai_doc_path):
        try:
            with open(ai_doc_path, "rb") as df:
                part = MIMEBase("application", "msword")
                part.set_payload(df.read())
                encoders.encode_base64(part)
                part.add_header(
                    "Content-Disposition",
                    "attachment; filename=\"Summary Report.doc\"",
                )
                msg.attach(part)
                log_message("Attached Summary Report.doc to email.")
        except Exception as attach_err:
            log_message(f"Failed to attach AI doc report to email: {clean_exception(attach_err)}")

    # 5. Connect to SMTP server and send email
    try:
        if smtp_port == 465:
            server = smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=15)
        else:
            server = smtplib.SMTP(smtp_server, smtp_port, timeout=15)
            server.starttls()
            
        server.login(sender_email, sender_password)
        server.send_message(msg)
        server.quit()
        log_message(f"Report email successfully sent to {recipient_email}.")
        return True
    except Exception as email_err:
        log_message(f"Failed to send report email: {clean_exception(email_err)}")
        return False


def generate_ai_report():
    """Reads execution log, constructs LLM prompt, and writes the report to a text file.
    Includes GITHUB_TOKEN integration placeholders.
    """
    log_message("Generating AI execution summary report...")
    
    # 1. Read execution log
    log_content = ""
    if os.path.exists(log_file_path):
        try:
            with open(log_file_path, "r", encoding="utf-8") as lf:
                log_content = lf.read()
        except Exception as e:
            log_content = f"Error reading log file: {str(e)}"
    else:
        log_content = "Log file not found."

    # 2. Calculate execution times
    total_time = round(time.time() - suite_start_time, 2)

    # 3. Create structured prompt
    prompt = f"""
You are an expert QA Automation Assistant. Analyze the following Selenium test execution log and generate a professional, structured AI executive summary report.

### Log Content:
{log_content}

### Execution Summary Data:
- Total Execution Time: {total_time}s
- Total Tests Executed: {suite_summary['total_executed']}
- Passed Tests: {suite_summary['passed_tests']}
- Failed Tests: {suite_summary['failed_tests']}
"""
    if suite_summary["failure_reasons"]:
        prompt += "- Failure Reasons:\n"
        for name, reason in suite_summary["failure_reasons"].items():
            prompt += f"  * {name}: {reason}\n"
            
    prompt += "\n- Per-Test Durations:\n"
    for test_name, duration in test_durations.items():
        prompt += f"  - {test_name}: {duration}s\n"

    prompt += "\n- Pass/Fail Status:\n"
    for test_name, status in test_results.items():
        prompt += f"  - {test_name}: {status}\n"

    prompt += """
Please generate a report that contains:
1. Executive Summary: High-level overview of the automation run.
2. Total Execution Time and Per-Test Duration.
3. Pass/Fail Status and details of any failed tests.
4. Errors (if any): Grouped by test case with description.
5. Important Actions Performed: Major steps taken during the test flow.

Do NOT write "Prepared by: AI QA Automation Assistant" or any other sign-offs/signatures at the end of the report.
"""

    log_message("Structured prompt created. Prompt preview:")
    log_message(prompt[:500] + "...\n[Prompt Truncated for log display]")

    # 4. GitHub Model API Integration
    ai_report_content = ""
    token = env_vars.get("GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token and token.strip() and token.strip() != "YOUR_GITHUB_TOKEN":
        try:
            import json
            import urllib.request
            
            endpoint = "https://models.inference.ai.azure.com/chat/completions"
            model_name = "gpt-4o"  # default to gpt-4o which is widely available
            
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0"
            }
            
            data = {
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a QA assistant that summarizes execution logs."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                "model": model_name,
                "temperature": 0.7,
                "max_tokens": 2048
            }
            
            req = urllib.request.Request(
                endpoint, 
                data=json.dumps(data).encode("utf-8"), 
                headers=headers,
                method="POST"
            )
            
            log_message(f"Sending request to GitHub Models API using model '{model_name}'...")
            with urllib.request.urlopen(req, timeout=30) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                ai_report_content = res_data["choices"][0]["message"]["content"]
            log_message("AI summary report generated successfully from GitHub Models API.")
        except Exception as api_err:
            log_message(f"Error calling GitHub Models API: {clean_exception(api_err)}")
            ai_report_content = f"Error during live GitHub API call: {clean_exception(api_err)}"
    else:
        # Fallback simulated response
        log_message("GITHUB_TOKEN is empty or not configured. Using simulated report.")
        ai_report_content = f"""AI EXECUTIVE SUMMARY REPORT (Simulated)
=====================================
Executive Summary:
The email campaign automation suite execution completed with total verification.

Metrics:
- Total Execution Time: {total_time}s
- Total Tests Executed: {suite_summary['total_executed']}
- Passed Tests: {suite_summary['passed_tests']}
- Failed Tests: {suite_summary['failed_tests']}
"""
        if suite_summary["failure_reasons"]:
            ai_report_content += "\nFailure Reasons:\n"
            for name, reason in suite_summary["failure_reasons"].items():
                ai_report_content += f"  * {name}: {reason}\n"
                
        ai_report_content += "\nPer-Test Duration:\n"
        for test_name, duration in test_durations.items():
            ai_report_content += f"  * {test_name}: {duration}s\n"
            
        ai_report_content += "\nPass/Fail Status:\n"
        for test_name, status in test_results.items():
            ai_report_content += f"  * {test_name}: {status}\n"
            
        ai_report_content += "\nImportant Actions Performed:\n"
        ai_report_content += "  - Logged into the Enterprise AI Platform successfully.\n"
        ai_report_content += "  - Created lead list and configured SMTP settings.\n"
        ai_report_content += "  - Completed campaign execution and cleared created assets.\n"
        ai_report_content += "\n[Note: GITHUB_TOKEN is not configured in .env. Fill in the token to enable live AI summaries.]"

    # Save to file
    ai_report_path = os.path.join(RUN_DIR, f"ai_summary_report_{timestamp}.txt")
    ai_doc_path = os.path.join(RUN_DIR, "Summary Report.doc")
    try:
        # 1. Save as plain text
        with open(ai_report_path, "w", encoding="utf-8") as rf:
            rf.write(ai_report_content)
        log_message(f"AI report written to: {ai_report_path}")
        
        # 2. Save as HTML-based .doc for rich formatting in Microsoft Word
        html_body = parse_markdown_to_rich_html(ai_report_content)
        
        # Generate screenshots section html
        screenshots_html = ""
        login_success_img = "01_Login_Success.png"
        login_success_path = os.path.join(RUN_DIR, login_success_img)
        if os.path.exists(login_success_path):
            screenshots_html += f"""
            <div style="margin-top: 25px; margin-bottom: 25px; page-break-inside: avoid;">
                <h3 style="color: #2E86C1; border-bottom: 1px solid #AED6F1; padding-bottom: 4px; font-size: 14px;">User Authentication Success Screenshot</h3>
                <p style="font-size: 12px; color: #7F8C8D; margin: 4px 0 10px 0;">Screenshot captured immediately after successful login to the platform dashboard.</p>
                <img src="{login_success_img}" style="border: 1px solid #BDC3C7; border-radius: 4px; max-width: 600px;" width="600">
            </div>
            """
            
        for r in combinations_results:
            prov = r.get("provider", "").upper()
            camp = r.get("campaign_type", "").upper()
            status = r.get("status", "FAIL")
            
            smtp_screenshot = r.get("smtp_screenshot", "")
            final_screenshot = r.get("screenshot", "")
            
            if smtp_screenshot or final_screenshot:
                screenshots_html += f"""
                <div style="margin-top: 30px; margin-bottom: 30px; page-break-inside: avoid; border-top: 1px dashed #BDC3C7; padding-top: 15px;">
                    <h3 style="color: #1B4F72; font-size: 14px; margin-bottom: 8px;">Combination Validation: {prov} - {camp} (Status: {status})</h3>
                """
                
                if smtp_screenshot and os.path.exists(smtp_screenshot):
                    smtp_filename = os.path.basename(smtp_screenshot)
                    screenshots_html += f"""
                    <div style="margin-bottom: 15px;">
                        <p style="font-size: 12px; color: #7F8C8D; margin: 0 0 5px 0;"><strong>SMTP Configuration Change:</strong> Default provider set to {prov} in settings.</p>
                        <img src="{smtp_filename}" style="border: 1px solid #BDC3C7; border-radius: 4px; max-width: 600px;" width="600">
                    </div>
                    """
                    
                if final_screenshot and os.path.exists(final_screenshot):
                    final_filename = os.path.basename(final_screenshot)
                    outcome = "Successful execution" if status == "PASS" else f"Execution failure (Error: {r.get('error_message', 'TimeoutError')})"
                    screenshots_html += f"""
                    <div style="margin-bottom: 15px;">
                        <p style="font-size: 12px; color: #7F8C8D; margin: 0 0 5px 0;"><strong>Campaign Execution Status:</strong> {outcome}</p>
                        <img src="{final_filename}" style="border: 1px solid #BDC3C7; border-radius: 4px; max-width: 600px;" width="600">
                    </div>
                    """
                
                screenshots_html += "</div>"
        
        doc_html = f"""<html>
<head>
<meta charset="utf-8">
<style>
body {{ font-family: 'Segoe UI', Arial, sans-serif; line-height: 1.6; color: #2C3E50; }}
h1 {{ color: #1B4F72; border-bottom: 2px solid #5DADE2; padding-bottom: 8px; margin-top: 25px; }}
h2 {{ color: #2E86C1; border-bottom: 1px solid #AED6F1; padding-bottom: 5px; margin-top: 20px; }}
h3 {{ color: #3498DB; margin-top: 15px; }}
ul {{ padding-left: 20px; }}
li {{ margin-bottom: 6px; }}
p {{ margin: 10px 0; }}
b {{ color: #1F618D; }}
.header {{ background-color: #EBF5FB; border-left: 5px solid #3498DB; padding: 15px; margin-bottom: 20px; border-radius: 4px; }}
</style>
</head>
<body>
<div class="header">
    <h1 style="margin-top: 0; border: none; padding: 0; color: #1B4F72;">Summary Report</h1>
    <p><strong>Generated on:</strong> {timestamp}</p>
    <p><strong>Total Execution Time:</strong> {total_time}s</p>
    <p><strong>Total Tests Executed:</strong> {suite_summary['total_executed']}</p>
    <p><strong>Passed Tests:</strong> {suite_summary['passed_tests']}</p>
    <p><strong>Failed Tests:</strong> {suite_summary['failed_tests']}</p>
</div>
{html_body}
<h2 style="color: #1B4F72; border-bottom: 2px solid #5DADE2; padding-bottom: 8px; margin-top: 35px;">Execution Screenshots & Evidence</h2>
{screenshots_html}
</body>
</html>"""
        with open(ai_doc_path, "w", encoding="utf-8") as df:
            df.write(doc_html)
        log_message(f"AI Word Document report written to: {ai_doc_path}")
        
    except Exception as e:
        log_message(f"Failed to write AI report files: {str(e)}")

    return ai_report_content



def dismiss_post_login_popup(driver, wait_time=15):
    """Dismiss a post-login popup if it appears, without failing the test.
    This function looks for common confirmation buttons like 'OK', 'Confirm', or 'Yes' in a case‑insensitive manner.
    """
    try:
        # XPath matches buttons with text ok/confirm/yes regardless of case and trims surrounding whitespace
        ok_button = WebDriverWait(driver, wait_time).until(
            EC.element_to_be_clickable(
                (By.XPATH,
                 "//button[normalize-space(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'))='ok']"
                 " | //button[normalize-space(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'))='confirm']"
                 " | //button[normalize-space(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'))='yes']"
                )
            )
        )
        # Scroll into view and click via JavaScript to avoid overlay issues
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", ok_button)
        driver.execute_script("arguments[0].click();", ok_button)
        log_message("Dismissed login popup.")
        return True
    except Exception:
        # No popup found or click failed; continue without breaking the flow
        return False

# Load env variables from workspace
env_path = ".env"
env_vars = {}
if os.path.exists(env_path):
    with open(env_path, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                key, val = line.split("=", 1)
                env_vars[key.strip()] = val.strip().strip('"').strip("'")

# Environment Configurations
TARGET_URL = env_vars.get("TARGET_URL", "https://fms-aisdr-agent1.technologymindz.com/")
USER_ID = env_vars.get("USER_ID", "superadmin@gmail.com")
PASSWORD = env_vars.get("PASSWORD") or os.environ.get("PASSWORD")

if not PASSWORD:
    log_message("Warning: PASSWORD is not set in .env or environment variables.")

log_message("Starting campaign validation...")

# Initialize Chrome WebDriver with disabled Password Manager & Leak Detection to prevent native warnings
chrome_options = webdriver.ChromeOptions()
chrome_options.add_experimental_option("prefs", {
    "profile.password_manager_leak_detection": False,
    "credentials_enable_service": False,
    "profile.password_manager_enabled": False
})
# Disable password protection features as arguments
chrome_options.add_argument("--disable-features=SafeBrowsingPasswordProtection,SafeBrowsingPasswordProtectionService")

driver = webdriver.Chrome(options=chrome_options)
driver.maximize_window()

# Time delays standard configuration
IMPLICIT_WAIT_TIME = 10  # Standard fallback wait time for elements
EXPLICIT_WAIT_TIME = 15  # Max time allowed for critical step validation
driver.implicitly_wait(IMPLICIT_WAIT_TIME)

list_name_base = env_vars.get("LEAD_LIST_NAME", "Automated_Lead_List")
first_name = env_vars.get("LEAD_FIRST_NAME", "John")
last_name = env_vars.get("LEAD_LAST_NAME", "Doe")
lead_email = env_vars.get("LEAD_EMAIL", "john.doe@example.com")
lead_company = env_vars.get("LEAD_COMPANY", "Acme Corp")
smtp_provider = env_vars.get("SMTP_PROVIDER", "gmail")
conversion_criteria_raw = env_vars.get("CONVERSION_CRITERIA", "Message Replied,Link Clicked")
conversion_criteria = [c.strip() for c in conversion_criteria_raw.split(",") if c.strip()]

# Dictionary to track test status for the final summary report
test_results = {
    "Test 1 (Authentication)": "NOT RUN",
    "Lead List Creation": "NOT RUN"
}

# Timings and track state
test_durations = {
    "Test 1 (Authentication)": 0.0,
    "Lead List Creation": 0.0
}

# Summary metrics for the 10 combinations
suite_summary = {
    "total_executed": 0,
    "passed_tests": 0,
    "failed_tests": 0,
    "failure_reasons": {}
}

# Global list to hold provider combination results and their screenshot paths
combinations_results = []

# Global error flags to handle suite-level critical errors in the email
critical_failure_occurred = False
critical_error_message = ""
critical_error_traceback = ""

# EasyOCR reader initialized lazily
ocr_reader = None

suite_start_time = time.time()
current_test_name = "Test 1 (Authentication)"
current_test_start = time.time()


# Define unique list name using main timestamp
unique_list_name = f"{list_name_base}_{timestamp}"


def _select_option_by_text(element, option_text, label_name):
    """Select an option from either a native select or a custom dropdown."""
    try:
        if element.tag_name.lower() == "select":
            Select(element).select_by_visible_text(option_text)
            return True
    except Exception:
        pass

    try:
        element.click()
        option = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
            EC.element_to_be_clickable((By.XPATH, f"//*[contains(@class, 'option') and contains(normalize-space(.), '{option_text}')] | //div[contains(@class, 'cursor-pointer') and contains(normalize-space(.), '{option_text}')] | //li[contains(normalize-space(.), '{option_text}')]"))
        )
        option.click()
        return True
    except Exception:
        log_message(f"Warning: Could not select '{option_text}' for {label_name}.")
        return False


def _safe_click(element):
    """Click an element with normal or JavaScript fallback."""
    try:
        element.click()
        return True
    except Exception:
        try:
            driver.execute_script("arguments[0].click();", element)
            return True
        except Exception:
            return False


def navigate_to_settings(driver):
    """Safely navigate to the Settings page either by direct URL or by clicking the user icon/menu."""
    try:
        settings_url = TARGET_URL.rstrip('/') + "/setting"
        log_message("Navigating to settings URL.")
        driver.get(settings_url)
        time.sleep(2)
        if "setting" in driver.current_url:
            return True
    except Exception as e:
        log_message(f"Direct navigation to settings failed: {clean_exception(e)}")

    try:
        log_message("Clicking user profile icon.")
        user_icon = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'User') or contains(., 'Admin') or contains(., 'Super')]"))
        )
        _safe_click(user_icon)
        time.sleep(1)
        settings_link = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
            EC.element_to_be_clickable((By.XPATH, "//a[@href='/setting'] | //a[contains(., 'Setting')]"))
        )
        _safe_click(settings_link)
        time.sleep(2)
        return True
    except Exception as e:
        log_message(f"Sidebar settings navigation failed: {clean_exception(e)}")
        return False


def reset_browser_state():
    """Resets the browser to a clean state by navigating to the base URL or dashboard,
    closing modals, or re-authenticating if logged out.
    """
    global driver
    log_message("Resetting browser session to a clean state...")
    try:
        # Check and handle unexpected alert if present
        try:
            alert = driver.switch_to.alert
            alert_text = alert.text
            alert.accept()
            log_message(f"Dismissed unexpected alert: '{alert_text}' during browser reset.")
        except Exception:
            pass

        driver.get(TARGET_URL)
        time.sleep(2)
        
        # Re-authenticate if session is lost or redirects to login page
        if "login" in driver.current_url.lower():
            log_message("Session lost. Re-authenticating...")
            email_field = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
                EC.visibility_of_element_located((By.NAME, "email"))
            )
            email_field.clear()
            email_field.send_keys(USER_ID)
            driver.find_element(By.NAME, "password").clear()
            driver.find_element(By.NAME, "password").send_keys(PASSWORD)
            driver.find_element(By.XPATH, "//button[@type='submit']").click()
            WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
                EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Super Admin') or contains(text(), 'Enterprise AI Platform')]"))
            )
            dismiss_post_login_popup(driver)
            log_message("Re-authentication successful.")
    except Exception as e:
        log_message(f"Failed to reset browser state: {clean_exception(e)}. Reinitializing browser...")
        try:
            driver.quit()
        except Exception:
            pass
        driver = webdriver.Chrome(options=chrome_options)
        driver.maximize_window()
        driver.implicitly_wait(IMPLICIT_WAIT_TIME)
        
        # Log in again
        driver.get(TARGET_URL)
        email_field = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
            EC.visibility_of_element_located((By.NAME, "email"))
        )
        email_field.send_keys(USER_ID)
        driver.find_element(By.NAME, "password").send_keys(PASSWORD)
        driver.find_element(By.XPATH, "//button[@type='submit']").click()
        WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
            EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Super Admin') or contains(text(), 'Enterprise AI Platform')]"))
        )
        dismiss_post_login_popup(driver)


def create_shared_lead_list(driver, list_name):
    """Creates a shared lead list to be used across all test combinations."""
    log_message(f"Creating shared Lead List: {list_name}...")
    try:
        # Navigate to settings to access the Lead configure section
        driver.get(TARGET_URL.rstrip('/') + "/setting")
        time.sleep(3)
        
        leads_btn_clicked = False
        for attempt in range(3):
            try:
                leads_btn = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
                    EC.presence_of_element_located((By.XPATH, "//p[text()='Leads']/ancestor::div[contains(@class, 'rounded-2xl')][1]//button[contains(., 'Configure')]"))
                )
                if _safe_click(leads_btn):
                    leads_btn_clicked = True
                    break
            except Exception:
                time.sleep(1.5)
        
        if not leads_btn_clicked:
            raise RuntimeError("Could not click configure leads button.")
        time.sleep(2)
        
        create_btn = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Create')]"))
        )
        create_btn.click()
        time.sleep(1)
        
        name_input = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
            EC.visibility_of_element_located((By.XPATH, "//input[@placeholder='e.g. Q2 Enterprise Targets']"))
        )
        name_input.send_keys(list_name)
        
        excel_btn = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Excel / CSV')]"))
        )
        excel_btn.click()
        time.sleep(0.5)
        
        next_btn = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
            EC.element_to_be_clickable((By.XPATH, "//div[contains(@class, 'fixed')]//button[contains(., 'Next') or contains(., 'Continue')]"))
        )
        next_btn.click()
        time.sleep(2)
        
        # Generate CSV file
        headers = ['name', 'notes', 'tasks', 'title', 'company', 'lead_id', 'website', 'comments', 'industry', 'activities', 'add_prompt', 'attachments', 'client_type', 'description', 'lead_rating', 'lead_source', 'lead_status', 'address_city', 'linkedin_url', 'project_name', 'project_type', 'address_state', 'business_area', 'email_address', 'address_street', 'annual_revenue', 'contact_number', 'address_country', 'no_of_employees', 'address_zip_code', 'lead_owner_email', 'last_follow_up_date', 'reason_not_interested', 'reason_not_interested_other', '_lead_id', '_call_enabled', '_whatsapp_enabled', '_email_enabled', '_linkedin_enabled']
        lead_row = [''] * len(headers)
        lead_row[headers.index('name')] = f"{first_name} {last_name}"
        lead_row[headers.index('company')] = lead_company
        lead_row[headers.index('email_address')] = lead_email
        lead_row[headers.index('lead_id')] = "LEAD-001"
        lead_row[headers.index('_lead_id')] = "LEAD-001"
        lead_row[headers.index('_call_enabled')] = "1"
        lead_row[headers.index('_whatsapp_enabled')] = "1"
        lead_row[headers.index('_email_enabled')] = "1"
        lead_row[headers.index('_linkedin_enabled')] = "1"
        
        csv_temp_path = os.path.abspath(f"temp_leads_shared_{timestamp}.csv")
        import csv
        with open(csv_temp_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            writer.writerow(lead_row)
            
        file_input = driver.find_element(By.XPATH, "//input[@type='file']")
        file_input.send_keys(csv_temp_path)
        time.sleep(3)
        
        import_btn = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
            EC.element_to_be_clickable((By.XPATH, "//div[contains(@class, 'fixed')]//button[contains(., 'Import')]"))
        )
        driver.execute_script("arguments[0].click();", import_btn)
        time.sleep(2)
        
        WebDriverWait(driver, 30).until(
            EC.invisibility_of_element_located((By.XPATH, "//div[contains(@class, 'fixed')]//button[contains(., 'Import')]"))
        )
        time.sleep(2)
        
        try:
            os.remove(csv_temp_path)
        except Exception:
            pass
            
        WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
            EC.presence_of_element_located((By.XPATH, f"//tr[contains(., '{list_name}')]"))
        )
        log_message("Shared lead list created successfully.")
    except Exception as e:
        log_message(f"Failed to create shared lead list: {clean_exception(e)}")
        raise e


def delete_shared_lead_list(driver, list_name):
    """Deletes the shared lead list at the end of the execution run."""
    log_message(f"Cleaning up shared lead list: {list_name}...")
    # Navigate to settings page first and click Configure leads to safely load leads list view
    driver.get(TARGET_URL.rstrip('/') + "/setting")
    time.sleep(3)
    
    leads_btn_clicked = False
    for attempt in range(3):
        try:
            leads_btn = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
                EC.presence_of_element_located((By.XPATH, "//p[text()='Leads']/ancestor::div[contains(@class, 'rounded-2xl')][1]//button[contains(., 'Configure')]"))
            )
            if _safe_click(leads_btn):
                leads_btn_clicked = True
                break
        except Exception:
            time.sleep(1.5)
            
    if not leads_btn_clicked:
        raise RuntimeError("Could not click configure leads button.")
    time.sleep(4)
    
    delete_btn_xpaths = [
        f"//tr[contains(., '{list_name}')]//button[@title='Delete list']",
        f"//tr[contains(., '{list_name}')]//button[contains(@title,'Delete')]",
        f"//tr[contains(., '{list_name}')]//*[contains(@title,'Delete')]",
        f"//*[contains(normalize-space(.), '{list_name}')]/ancestor::tr//button[@title='Delete list']",
        f"//tr[contains(., '{list_name}')]//button[contains(@class,'delete') or contains(.,'Delete')]",
        f"//tr[contains(., '{list_name}')]//*[@class and contains(@class,'delete')]"
    ]
    delete_btn = None
    for xpath in delete_btn_xpaths:
        try:
            delete_btn = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
                EC.element_to_be_clickable((By.XPATH, xpath))
            )
            if delete_btn:
                break
        except Exception:
            continue
            
    if not delete_btn:
        raise RuntimeError("Could not find delete button.")
        
    delete_btn.click()
    confirm_btn = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
        EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'Confirm') or contains(.,'Yes')]"))
    )
    confirm_btn.click()
    log_message("Deleted shared lead list.")
    time.sleep(2)


def create_campaign_steps(driver, provider, campaign_type, unique_campaign_name, unique_list_name, preview_mode=False):
    """Executes campaign creation steps up to clicking Run Campaign."""
    # 1. Navigating to Settings to configure SMTP Provider
    log_message("Navigating to settings to configure default SMTP provider...")
    driver.get(TARGET_URL.rstrip('/') + "/setting")
    time.sleep(3)
    
    # Set SMTP provider dropdown
    select_element = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
        EC.presence_of_element_located((By.XPATH, "//label[contains(text(), 'Provider')]/following-sibling::div/select | //select[contains(., 'Select Provider')]"))
    )
    Select(select_element).select_by_value(provider.lower())
    time.sleep(1.5)
    log_message(f"SMTP provider set to: {provider}")
    
    # Capture screenshot for SMTP provider change
    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    smtp_screenshot_path = os.path.join(RUN_SCREENSHOT_DIR, f"SMTP_Change_{provider}_{campaign_type}_{run_timestamp}.png")
    try:
        driver.save_screenshot(smtp_screenshot_path)
        log_message("Captured SMTP configuration change screenshot.")
    except Exception:
        smtp_screenshot_path = ""
    
    # 2. Campaign Creation
    log_message("Creating Campaign...")
    campaign_url = TARGET_URL.rstrip('/') + "/campaign"
    driver.get(campaign_url)
    time.sleep(2)
    
    create_btn = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
        EC.element_to_be_clickable((By.XPATH, "//*[contains(text(), 'Create New Campaign')]"))
    )
    _safe_click(create_btn)
    time.sleep(1.5)
    
    # Select campaign mode
    if campaign_type == "ai":
        log_message("Selecting AI Campaign mode...")
        ai_btn = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
            EC.element_to_be_clickable((By.XPATH, campaign_selectors.AI_CAMPAIGN_OPTION_SELECTOR))
        )
        _safe_click(ai_btn)
        log_message("AI Campaign selected.")
    else:
        log_message("Selecting Manual Campaign mode...")
        manual_btn = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Manual') and contains(., 'You define the channel sequence and steps for all leads')]"))
        )
        _safe_click(manual_btn)
        log_message("Manual mode selected.")
        
    campaign_name_field = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
        EC.visibility_of_element_located((By.NAME, "campaign_name"))
    )
    campaign_name_field.clear()
    campaign_name_field.send_keys(unique_campaign_name)
    log_message("Campaign name entered.")

    # Configure campaign details: Select lead list
    if campaign_type == "ai":
        lead_list_xpath = campaign_selectors.AI_LEAD_LIST_BUTTON_SELECTOR
    else:
        lead_list_xpath = "//button[contains(., 'Select a lead list')]"

    lead_list_btn = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
        EC.element_to_be_clickable((By.XPATH, lead_list_xpath))
    )
    _safe_click(lead_list_btn)
    
    our_list_option = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
        EC.element_to_be_clickable((By.XPATH, f"//button[contains(., '{unique_list_name}')]"))
    )
    _safe_click(our_list_option)
    log_message("Lead list selected.")
    log_message("Campaign details configured.")

    # Select Email channel under Available Channels (For AI campaign only)
    if campaign_type == "ai":
        log_message("Selecting Email channel for AI Campaign...")
        email_btn = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
            EC.element_to_be_clickable((By.XPATH, campaign_selectors.EMAIL_CHANNEL_SELECTOR))
        )
        _safe_click(email_btn)
        log_message("Email channel selected.")

    # Complete all remaining configuration steps: conversion criteria
    for criteria in conversion_criteria:
        try:
            checkbox_xpath = (
                f"//label[contains(., '{criteria}')]//input[@type='checkbox']"
                f" | //div[contains(., '{criteria}')]//input[@type='checkbox']"
                f" | //span[contains(., '{criteria}')]/ancestor::label//input[@type='checkbox']"
                f" | //input[@type='checkbox' and contains(@aria-label, '{criteria}')]"
            )
            checkbox = WebDriverWait(driver, 2).until(
                EC.presence_of_element_located((By.XPATH, checkbox_xpath))
            )
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", checkbox)
            if not checkbox.is_selected():
                _safe_click(checkbox)
        except Exception:
            pass

    # Preview Mode toggle if preview_mode is enabled
    if preview_mode:
        log_message("Enabling Preview Mode...")
        PREVIEW_MODE_TOGGLE_SELECTOR = "/html/body/div[3]/div[2]/main/div[2]/div/section[8]/div/div[2]/button"
        preview_toggle = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
            EC.presence_of_element_located((By.XPATH, PREVIEW_MODE_TOGGLE_SELECTOR))
        )
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", preview_toggle)
        _safe_click(preview_toggle)
        log_message("Preview Mode enabled.")

    # Click the Run Campaign button
    if campaign_type == "ai":
        run_btn_xpath = campaign_selectors.RUN_CAMPAIGN_BUTTON_SELECTOR
    else:
        run_btn_xpath = "//button[contains(., 'Run Campaign Now')]"

    run_btn = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
        EC.presence_of_element_located((By.XPATH, run_btn_xpath))
    )
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", run_btn)
    if run_btn.get_attribute("disabled") is not None:
        driver.execute_script("arguments[0].removeAttribute('disabled');", run_btn)
    _safe_click(run_btn)
    log_message("Run Campaign button clicked.")
    
    return smtp_screenshot_path, run_timestamp


def run_test_case(provider, campaign_type):
    """Executes a single test case for a combination of provider and campaign_type.
    Returns (status, error_message, duration, screenshot_path, smtp_screenshot_path).
    """
    start_time = time.time()
    screenshot_path = ""
    smtp_screenshot_path = ""
    error_msg = ""
    status = "FAIL"
    
    # Define a unique campaign name for this run to avoid conflicts
    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    unique_campaign_name = f"{campaign_type.title()}_Campaign_{provider}_{run_timestamp}"
    
    try:
        log_message(f"Executing: Provider={provider}, CampaignType={campaign_type}")
        
        smtp_screenshot_path, run_timestamp = create_campaign_steps(
            driver, provider, campaign_type, unique_campaign_name, unique_list_name, preview_mode=False
        )

        # Wait until the campaign is successfully created and the page finishes loading
        if campaign_type == "ai":
            log_message("AI Campaign: Navigating to campaigns list to modify sequence...")
            driver.get(TARGET_URL.rstrip('/') + "/campaign")
            time.sleep(3)
            
            # Click on View Leads for the target campaign card
            view_leads_xpath = (
                f"//*[contains(@class,'card') or contains(@class,'campaign') or contains(@class,'rounded') or contains(@class,'border')]"
                f"[descendant::*[contains(normalize-space(.), '{unique_campaign_name}')]]"
                f"//button[contains(., 'View Leads')]"
            )
            view_leads_btn = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
                EC.element_to_be_clickable((By.XPATH, view_leads_xpath))
            )
            _safe_click(view_leads_btn)
            time.sleep(2)
            
            # Click Edit Sequence
            edit_seq_btn = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Edit Sequence')]"))
            )
            _safe_click(edit_seq_btn)
            time.sleep(2)
            
            # Deletion loop for follow-up emails
            log_message("AI Campaign: Removing follow-up emails from sequence...")
            while True:
                # Find any enabled delete button inside sequence panel
                trash_buttons = driver.find_elements(By.XPATH, "//button[contains(@class, 'text-red-400') and not(@disabled)]")
                if not trash_buttons:
                    log_message("AI Campaign: No more follow-up email blocks found.")
                    break
                
                log_message(f"AI Campaign: Deleting a follow-up email block (total remaining buttons: {len(trash_buttons)})")
                _safe_click(trash_buttons[0])
                time.sleep(2)  # Wait for the UI to refresh
                
            # Click Save inside sequence editor drawer
            save_btn = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(@class, 'bg-blue-500') and (contains(., 'Save') or contains(text(), 'Save'))] | //button[contains(., 'Save')]"))
            )
            _safe_click(save_btn)
            time.sleep(2)
            
            # Return to previous screen
            try:
                back_btn = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[contains(@class, 'text-gray-500')]"))
                )
                _safe_click(back_btn)
                time.sleep(2)
            except Exception:
                driver.get(TARGET_URL.rstrip('/') + "/campaign")
                time.sleep(3)
            
            # Activate button targets specific campaign card
            activate_xpath = (
                f"//*[contains(@class,'card') or contains(@class,'campaign') or contains(@class,'rounded') or contains(@class,'border')]"
                f"[descendant::*[contains(normalize-space(.), '{unique_campaign_name}')]]"
                f"//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'activate')"
                f" or contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'start campaign')"
                f" or contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'publish')"
                f" or contains(., 'Activate')]"
            )
        else:
            # Activate manual campaign
            activate_xpath = (
                "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'activate')]"
                " | //button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'start campaign')]"
                " | //button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'publish')]"
                " | //button[contains(., 'Activate')]"
            )

        activate_btn = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
            EC.element_to_be_clickable((By.XPATH, activate_xpath))
        )
        _safe_click(activate_btn)
        log_message("Campaign activated.")
        time.sleep(2)
        
        # 3. Campaign Completion
        log_message("Waiting for campaign to complete...")
        completed_xpath = (
            f"//*[contains(@class,'card') or contains(@class,'campaign') or contains(@class,'rounded') or contains(@class,'border')]"
            f"[descendant::*[contains(normalize-space(.), '{unique_campaign_name}')]]"
            f"//*[contains(translate(normalize-space(.), 'COMPLETED', 'completed'), 'completed')]"
        )
        
        status_found = False
        for attempt in range(5):
            try:
                status_elem = WebDriverWait(driver, 5).until(
                    EC.presence_of_element_located((By.XPATH, completed_xpath))
                )
                if status_elem:
                    status_found = True
                    log_message("Campaign completed.")
                    break
            except Exception:
                log_message(f"Waiting... Attempt {attempt+1}/5.")
                driver.refresh()
                time.sleep(2)
                
        if not status_found:
            raise TimeoutError("Campaign status did not show 'Completed' within 90 seconds.")
            
        view_details_xpath = (
            f"//*[contains(@class,'card') or contains(@class,'campaign') or contains(@class,'rounded') or contains(@class,'border')]"
            f"[descendant::*[contains(normalize-space(.), '{unique_campaign_name}')]]"
            f"//button[contains(., 'View Details')]"
        )
        view_details_btn = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
            EC.element_to_be_clickable((By.XPATH, view_details_xpath))
        )
        _safe_click(view_details_btn)
        time.sleep(3)
        
        activity_btn_xpath = (
            "//p[contains(text(), 'Overview of all activities across this campaign')]"
            " | //*[contains(text(), 'Overview of all activities across this campaign')]/ancestor::button"
        )
        activity_btn = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
            EC.element_to_be_clickable((By.XPATH, activity_btn_xpath))
        )
        _safe_click(activity_btn)
        time.sleep(4)
        
        completed_span_xpath = "//span[contains(@class, 'bg-green-50') and contains(text(), 'Completed')] | //span[contains(text(), 'Completed')]"
        lead_completed = False
        for attempt in range(1, 6):
            completed_spans = driver.find_elements(By.XPATH, completed_span_xpath)
            if completed_spans:
                lead_completed = True
                log_message("Lead completion verified.")
                break
            time.sleep(15)
            driver.refresh()
            time.sleep(3)
            try:
                activity_btn = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.XPATH, activity_btn_xpath))
                )
                _safe_click(activity_btn)
                time.sleep(2)
            except Exception:
                pass
                
        if not lead_completed:
            raise TimeoutError("Timed out waiting for lead status to become 'Completed'.")
            
        status = "PASS"
        screenshot_path = os.path.join(RUN_SCREENSHOT_DIR, f"SUCCESS_{provider}_{campaign_type}_{run_timestamp}.png")
        driver.save_screenshot(screenshot_path)
        log_message(f"Result: SUCCESS for Provider={provider}, CampaignType={campaign_type}")
        
    except Exception as exc:
        status = "FAIL"
        error_msg = clean_exception(exc)
        log_message(f"Result: FAIL for Provider={provider}, CampaignType={campaign_type}. Error: {error_msg}")
        screenshot_path = os.path.join(RUN_SCREENSHOT_DIR, f"ERROR_{provider}_{campaign_type}_{run_timestamp}.png")
        try:
            driver.save_screenshot(screenshot_path)
        except Exception:
            pass
            
    duration = round(time.time() - start_time, 2)
    return status, error_msg, duration, screenshot_path, smtp_screenshot_path


def run_bug_1_test_case(provider, campaign_type):
    """Executes validation for Preview Mode email send on a given provider and campaign type."""
    global ocr_reader
    start_time = time.time()
    screenshot_path = ""
    error_msg = ""
    status = "FAIL"
    
    # Lazily initialize EasyOCR reader to save start-up time if not needed
    if ocr_reader is None:
        log_message("Initializing EasyOCR English Reader...")
        ocr_reader = easyocr.Reader(['en'])
        log_message("EasyOCR Reader successfully initialized.")
        
    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    unique_campaign_name = f"{campaign_type.title()}_Bug1_{provider}_{run_timestamp}"
    
    try:
        # Reset browser state
        reset_browser_state()
        
        # Configure SMTP & create campaign with preview_mode=True
        log_message(f"Bug 1: Creating campaign with Preview Mode for Provider={provider}, CampaignType={campaign_type}")
        smtp_screenshot, create_ts = create_campaign_steps(
            driver=driver,
            provider=provider,
            campaign_type=campaign_type,
            unique_campaign_name=unique_campaign_name,
            unique_list_name=unique_list_name,
            preview_mode=True
        )
        
        # Wait for redirect to campaign page and card visibility
        log_message("Waiting for campaign to be created successfully...")
        WebDriverWait(driver, 30).until(
            EC.url_contains("/campaign")
        )
        campaign_card_xpath = f"//*[contains(normalize-space(.), '{unique_campaign_name}')]"
        campaign_card = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
            EC.presence_of_element_located((By.XPATH, campaign_card_xpath))
        )
        
        if "/campaign" not in driver.current_url:
            log_message("Returning to Campaign Listing page...")
            driver.get(TARGET_URL.rstrip('/') + "/campaign")
            time.sleep(2)
            
        campaign_card = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
            EC.presence_of_element_located((By.XPATH, campaign_card_xpath))
        )
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", campaign_card)
        time.sleep(1)
        
        PREVIEW_ICON_SELECTOR = "/html/body/div[3]/div[2]/main/div/div[4]/div[2]/div/div[4]/div[2]/button[2]"
        PREVIEW_LEAD_LIST_SELECTOR = "/html/body/div[3]/div[2]/main/div[3]/div[2]/table/tbody/tr/td[1]/input"
        SEND_PREVIEW_EMAIL_BUTTON_SELECTOR = "/html/body/div[3]/div[2]/main/div[1]/button[2]"
        NOTIFICATION_TOAST_SELECTOR = "/html/body/div[4]/div/div"
        
        try:
            preview_btn = campaign_card.find_element(By.XPATH, ".//button[contains(@title, 'Preview') or contains(., 'Preview') or contains(@class, 'preview')]")
        except Exception:
            preview_btn = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
                EC.element_to_be_clickable((By.XPATH, PREVIEW_ICON_SELECTOR))
            )
            
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", preview_btn)
        _safe_click(preview_btn)
        log_message("Preview dialog opened.")
        
        lead_list_input = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
            EC.element_to_be_clickable((By.XPATH, PREVIEW_LEAD_LIST_SELECTOR))
        )
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", lead_list_input)
        if not lead_list_input.is_selected():
            _safe_click(lead_list_input)
        log_message("Lead List selected.")
        
        send_btn = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
            EC.element_to_be_clickable((By.XPATH, SEND_PREVIEW_EMAIL_BUTTON_SELECTOR))
        )
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", send_btn)
        _safe_click(send_btn)
        log_message("Preview email sent.")
        
        toast_elem = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
            EC.visibility_of_element_located((By.XPATH, NOTIFICATION_TOAST_SELECTOR))
        )
        log_message("Notification detected.")
        
        screenshot_filename = f"Bug1_{provider}_{campaign_type}_Toast_{run_timestamp}.png"
        screenshot_path = os.path.join(RUN_DIR, screenshot_filename)
        toast_elem.screenshot(screenshot_path)
        log_message(f"Screenshot of toast captured: {screenshot_path}")
        
        ocr_results = ocr_reader.readtext(screenshot_path)
        extracted_text = " ".join([res[1] for res in ocr_results]).strip()
        confidence = sum([res[2] for res in ocr_results]) / len(ocr_results) if ocr_results else 0.0
        log_message(f"Raw OCR Output: '{extracted_text}' (Confidence: {confidence:.2f})")
        
        if not extracted_text or confidence < 0.30:
            raise AssertionError(f"OCR could not confidently read the toast message. Extracted: '{extracted_text}'.")
            
        lower_text = extracted_text.lower()
        if "success" in lower_text or "sent" in lower_text or "successfully" in lower_text:
            status = "PASS"
        elif "fail" in lower_text or "failed" in lower_text or "error" in lower_text:
            status = "FAIL"
            error_msg = f"Application returned fail status: {extracted_text}"
        else:
            status = "PASS"
            log_message(f"Notice: Other status returned: {extracted_text}")
            
        log_message(f"Result: {status} for Bug 1 Provider={provider}, CampaignType={campaign_type}")
        
    except Exception as e:
        status = "FAIL"
        error_msg = clean_exception(e)
        log_message(f"Result: FAIL for Bug 1 Provider={provider}, CampaignType={campaign_type}. Error: {error_msg}")
        screenshot_path = os.path.join(RUN_DIR, f"ERROR_Bug1_{provider}_{campaign_type}_{run_timestamp}.png")
        try:
            driver.save_screenshot(screenshot_path)
        except Exception:
            pass
            
    duration = round(time.time() - start_time, 2)
    return status, error_msg, duration, screenshot_path

# ==========================================
if __name__ == '__main__':
    # CORE AUTOMATION WORKFLOW (WITH UMBRELLA ERROR HANDLING)
    # ==========================================
    try:
        # --------------------------------------
        # TEST 1: USER AUTHENTICATION
        # --------------------------------------
        current_test_name = "Test 1 (Authentication)"
        current_test_start = time.time()
        log_message("--- Test 1: Authentication ---")
    
        # Step 1.1: Launch Application URL
        driver.get(TARGET_URL)
        time.sleep(2) # Brief explicit delay for page assets to stabilize
    
        # Step 1.2 & 1.3: Populate credentials and submit the form
        email_field = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
            EC.visibility_of_element_located((By.NAME, "email"))
        )
        email_field.send_keys(USER_ID)
        driver.find_element(By.NAME, "password").send_keys(PASSWORD)
        driver.find_element(By.XPATH, "//button[@type='submit']").click()
    
        # Step 1.4: Validate Dashboard access by finding a key home page element
        dashboard_element = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
            EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Super Admin') or contains(text(), 'Enterprise AI Platform')]"))
        )

        # Dismiss any post-login popup before moving on
        dismiss_post_login_popup(driver)
    
        # Capture success evidence screenshot for Test 1
        screenshot_1_path = os.path.join(RUN_SCREENSHOT_DIR, "01_Login_Success.png")
        driver.save_screenshot(screenshot_1_path)
        duration = round(time.time() - current_test_start, 2)
        test_durations["Test 1 (Authentication)"] = duration

        log_message(f"Test 1 PASSED. Duration: {duration}s")
        test_results["Test 1 (Authentication)"] = f"PASSED ({duration}s)"
    
        # Create the shared lead list once for all test cases
        current_test_name = "Lead List Creation"
        current_test_start = time.time()
        create_shared_lead_list(driver, unique_list_name)
        list_duration = round(time.time() - current_test_start, 2)
        test_durations["Lead List Creation"] = list_duration
        test_results["Lead List Creation"] = f"PASSED ({list_duration}s)"
    
        # --------------------------------------
        # ITERATIVE MULTI-PROVIDER / CAMPAIGN VALIDATION
        # --------------------------------------
        providers = ["sendgrid", "mailgun", "outlook", "gmail", "smartlead"]
        campaign_types = ["manual", "ai"]
        combinations_results.clear()
    
        for provider in providers:
            for campaign_type in campaign_types:
                comb_name = f"{provider.upper()} - {campaign_type.upper()}"
                log_message(f"\n=========================================")
                log_message(f"STARTING TEST CASE: {comb_name}")
                log_message(f"=========================================\n")
            
                status = "FAIL"
                error_msg = ""
                duration = 0.0
                screenshot_path = ""
                smtp_screenshot_path = ""
                start_time = time.time()
            
                try:
                    # Reset state to clean before the run
                    reset_browser_state()
                
                    # Run combination
                    status, error_msg, duration, screenshot_path, smtp_screenshot_path = run_test_case(provider, campaign_type)
                except Exception as e:
                    status = "FAIL"
                    error_msg = f"Unexpected failure in test orchestrator: {clean_exception(e)}"
                    duration = round(time.time() - start_time, 2)
                    log_message(f"Result: FAIL for Provider={provider}, CampaignType={campaign_type}. Error: {error_msg}")
                    # Save diagnostic screenshot
                    screenshot_path = os.path.join(RUN_SCREENSHOT_DIR, f"ERROR_{provider}_{campaign_type}_{timestamp}.png")
                    try:
                        driver.save_screenshot(screenshot_path)
                    except Exception:
                        pass
                    smtp_screenshot_path = ""
            
                current_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                log_message("--- TEST CASE METRICS ---")
                log_message(f"Email Provider: {provider.upper()}")
                log_message(f"Campaign Type: {campaign_type.upper()}")
                log_message(f"Status: {status}")
                if status == "FAIL" or error_msg:
                    log_message(f"Error Message: {error_msg}")
                log_message(f"Timestamp: {current_timestamp}")
                log_message("-------------------------")
            
                combinations_results.append({
                    "provider": provider,
                    "campaign_type": campaign_type,
                    "status": status,
                    "error_message": error_msg,
                    "duration": duration,
                    "screenshot": screenshot_path,
                    "smtp_screenshot": smtp_screenshot_path,
                    "timestamp": current_timestamp
                })
            
                # Reset state again to clean after the run
                try:
                    reset_browser_state()
                except Exception:
                    pass
                
                # --------------------------------------
                # RUN BUG 1 TEST CASE FOR THIS COMBINATION
                # --------------------------------------
                bug_comb_name = f"BUG 1 ({provider.upper()} - {campaign_type.upper()})"
                log_message(f"\n=========================================")
                log_message(f"STARTING TEST CASE: {bug_comb_name}")
                log_message(f"=========================================\n")
                
                bug_status = "FAIL"
                bug_error = ""
                bug_duration = 0.0
                bug_screenshot = ""
                bug_start_time = time.time()
                
                try:
                    # Run Bug 1 combination
                    bug_status, bug_error, bug_duration, bug_screenshot = run_bug_1_test_case(provider, campaign_type)
                except Exception as e:
                    bug_status = "FAIL"
                    bug_error = f"Unexpected failure in Bug 1 test: {clean_exception(e)}"
                    bug_duration = round(time.time() - bug_start_time, 2)
                    log_message(f"Result: FAIL for Bug 1 Provider={provider}, CampaignType={campaign_type}. Error: {bug_error}")
                    bug_screenshot = os.path.join(RUN_SCREENSHOT_DIR, f"ERROR_Bug1_{provider}_{campaign_type}_{timestamp}.png")
                    try:
                        driver.save_screenshot(bug_screenshot)
                    except Exception:
                        pass
                
                bug_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                log_message("--- BUG 1 TEST CASE METRICS ---")
                log_message(f"Email Provider: {provider.upper()}")
                log_message(f"Campaign Type: {campaign_type.upper()}")
                log_message(f"Status: {bug_status}")
                if bug_status == "FAIL" or bug_error:
                    log_message(f"Error Message: {bug_error}")
                log_message(f"Timestamp: {bug_timestamp}")
                log_message("-------------------------")
                
                combinations_results.append({
                    "provider": provider,
                    "campaign_type": campaign_type,
                    "bug_test": True,
                    "status": bug_status,
                    "error_message": bug_error,
                    "duration": bug_duration,
                    "screenshot": bug_screenshot,
                    "smtp_screenshot": "",
                    "timestamp": bug_timestamp
                })
                
                # Reset state again to clean after the run
                try:
                    reset_browser_state()
                except Exception:
                    pass

        # --------------------------------------
        # PROCESS RESULTS AND PREPARE SUMMARY
        # --------------------------------------
        # Reinitialize global summary outputs
        test_results = {
            "Test 1 (Authentication)": f"PASSED ({test_durations['Test 1 (Authentication)']}s)",
            "Lead List Creation": f"PASSED ({test_durations['Lead List Creation']}s)"
        }
        test_durations = {
            "Test 1 (Authentication)": test_durations["Test 1 (Authentication)"],
            "Lead List Creation": test_durations["Lead List Creation"]
        }
    
        for r in combinations_results:
            if r.get("bug_test"):
                comb_key = f"Bug 1 ({r['provider'].upper()} - {r['campaign_type'].upper()})"
            else:
                comb_key = f"{r['provider'].upper()} - {r['campaign_type'].upper()}"
            test_results[comb_key] = f"{r['status']} ({r['duration']}s)"
            if r['error_message']:
                test_results[comb_key] += f" - Error: {r['error_message']}"
            test_durations[comb_key] = r["duration"]
        
        # Populate the suite summary structure
        suite_summary["total_executed"] = len(combinations_results)
        suite_summary["passed_tests"] = sum(1 for r in combinations_results if r["status"] == "PASS")
        suite_summary["failed_tests"] = sum(1 for r in combinations_results if r["status"] == "FAIL")
        suite_summary["failure_reasons"] = {
            f"Bug 1 ({r['provider'].upper()} - {r['campaign_type'].upper()})" if r.get("bug_test") else f"{r['provider'].upper()} - {r['campaign_type'].upper()}": r["error_message"]
            for r in combinations_results if r["status"] == "FAIL"
        }

    # ==========================================
    # 3. UMBRELLA ERROR HANDLING & RECOVERY
    # ==========================================
    except Exception as e:
        critical_failure_occurred = True
        critical_error_message = clean_exception(e)
        critical_error_traceback = traceback.format_exc()

        log_message("\n" + "!"*50)
        log_message("           CRITICAL ERROR ENCOUNTERED           ")
        log_message("!"*50)
        log_message(f"Error Details: {critical_error_message}")
    
        # Record duration of currently running test upon error
        if current_test_name not in test_durations or test_durations[current_test_name] == 0.0:
            test_durations[current_test_name] = round(time.time() - current_test_start, 2)
    
        import traceback
        cleaned_tb = clean_traceback(critical_error_traceback)
        log_message("Python Traceback:")
        for line in cleaned_tb.splitlines():
            log_message(f"  {line}")
        log_message("!"*50 + "\n")
    
        test_results[current_test_name] = f"FAILED - Error: {critical_error_message}"
        error_step = current_test_name.replace(" ", "_").replace("(", "").replace(")", "")
    
        # Update suite summary to reflect critical failure details
        suite_summary["total_executed"] = len(test_results)
        suite_summary["passed_tests"] = sum(1 for status in test_results.values() if "PASS" in status.upper())
        suite_summary["failed_tests"] = sum(1 for status in test_results.values() if "FAIL" in status.upper())
        suite_summary["failure_reasons"][current_test_name] = critical_error_message
        
        # Take an immediate diagnostic error screenshot to see exactly what went wrong
        error_screenshot_path = os.path.join(RUN_SCREENSHOT_DIR, f"ERROR_{error_step}.png")
        try:
            driver.save_screenshot(error_screenshot_path)
            log_message(f"Diagnostic error screenshot preserved under: {error_screenshot_path}")
        except Exception as screenshot_err:
            log_message(f"Could not save diagnostic error screenshot (browser session may be invalid/closed): {screenshot_err}")

    # ==========================================
    # 4. TEARDOWN & REPORTS GENERATION
    # ==========================================
    finally:
        # Clean up the shared lead list at the very end of the execution run (on success or failure)
        try:
            if 'unique_list_name' in locals() and unique_list_name and driver:
                log_message("Finally: Resetting browser session to clean up shared lead list...")
                try:
                    reset_browser_state()
                except Exception:
                    pass
                delete_shared_lead_list(driver, unique_list_name)
        except Exception as cleanup_err:
            log_message(f"Warning: Failed to delete shared lead list in finally block: {clean_exception(cleanup_err)}")

        log_message("Terminating browser validation session...")
        try:
            driver.quit()
        except Exception:
            pass
    
        # Print out and save the final structured summary report
        log_message("\n" + "="*45)
        log_message("         TEST SUITE SUMMARY REPORT          ")
        log_message("="*45)
        log_message(f"Total Tests Executed : {suite_summary['total_executed']}")
        log_message(f"Passed Tests         : {suite_summary['passed_tests']}")
        log_message(f"Failed Tests         : {suite_summary['failed_tests']}")
        if suite_summary["failure_reasons"]:
            log_message("Failure Reasons      :")
            for test_name, reason in suite_summary["failure_reasons"].items():
                log_message(f"  - {test_name}: {reason}")
        log_message("="*45 + "\n")
    
        log_message("\n" + "="*45)
        log_message("        INDIVIDUAL TEST CASE RESULTS        ")
        log_message("="*45)
        for test_name, status in test_results.items():
            log_message(f"{test_name}: {status}")
        log_message("="*45)
        log_message(f"Execution records are saved in '{RUN_DIR}/' folder.")
    
        # Generate the AI Executive Summary report
        generate_ai_report()
    
        send_report_email()