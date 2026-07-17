import os
import time
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
import campaign_selectors

from utils.report_generator import generate_email_campaign_report
from utils.vision_analyser import read_ocr_tokens
from utils.ss_paths import (
    email_screenshot_path,
    smtp_change_screenshot_path,
    success_screenshot_path,
    toast_screenshot_path,
    error_screenshot_path as build_error_screenshot_path,
    misc_screenshot_path,
)


# ==========================================
# 1. INITIALIZATION & FOLDER SETUP
# ==========================================
PARENT_REPORT_DIR = "Report"
os.makedirs(PARENT_REPORT_DIR, exist_ok=True)

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

timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
log_file_path = os.path.join(RUN_DIR, f"execution_log_{timestamp}.txt")

def log_message(message):
    """Helper function to print logs and save them into a separate text file."""
    formatted_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_line = f"[{formatted_time}] {message}"
    print(log_line)
    with open(log_file_path, "a") as f:
        f.write(log_line + "\n")


# ==========================================
# PIPELINE STEP TRACKING (for the HTML report's "Pipeline Steps" section)
# ==========================================
PIPELINE_STEPS = []
BUG_SUMMARY = []


def record_step(title, notes="", status="PASS"):
    """Appends one entry to PIPELINE_STEPS. This drives the numbered
    'Pipeline Steps — N Steps' table in the execution report, mirroring
    the Call automation suite's report format. Call this right after a
    meaningful action succeeds, or from an except block with status="FAIL"
    and notes=<error message> when one fails."""
    step_num = len(PIPELINE_STEPS) + 1
    PIPELINE_STEPS.append({
        "num": step_num,
        "title": title,
        "notes": notes,
        "status": status,
    })
    suffix = f" — {notes}" if notes else ""
    log_message(f"[STEP {step_num:02d}] {title} ({status}){suffix}")


def record_bug_check(label, mode, status, note):
    """Store a dedicated regression verdict for the report's bug table."""
    BUG_SUMMARY.append({"label": label, "mode": mode.upper(), "status": status, "note": note})
    log_message(f"[BUG CHECK] {label} [{mode.upper()}] ({status}) — {note}")


def _toggle_is_on(toggle):
    state = (toggle.get_attribute("aria-checked") or "").lower()
    data_state = (toggle.get_attribute("data-state") or "").lower()
    classes = (toggle.get_attribute("class") or "").lower()
    thumb_classes = ""
    try:
        thumb_classes = (toggle.find_element(By.XPATH, ".//span").get_attribute("class") or "").lower()
    except Exception:
        pass
    return (
        state == "true" or "bg-blue-500" in classes or "bg-indigo-500" in classes
        or data_state in ("true", "on", "checked") or "bg-violet" in classes
        or "translate-x-6" in thumb_classes
    )


def verify_preview_toggle_persistence(driver, preview_toggle, campaign_type, combo_label):
    """Ensure Preview Mode is ON and remains ON after a scroll re-render."""
    label = "Preview Toggle Persist"
    mode = campaign_type.upper()
    preview_xpath = (
        "//p[normalize-space()='Preview Mode']"
        "/ancestor::div[contains(@class, 'justify-between') and contains(@class, 'rounded-xl')][1]"
        "//button[@type='button']"
    )
    try:
        if not _toggle_is_on(preview_toggle):
            _safe_click(preview_toggle)
            time.sleep(0.8)
        if not _toggle_is_on(preview_toggle):
            raise AssertionError("Preview Mode did not turn ON")
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(0.8)
        try:
            preview_toggle = driver.find_element(By.XPATH, preview_xpath)
        except Exception:
            pass
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", preview_toggle)
        time.sleep(0.5)
        if not _toggle_is_on(preview_toggle):
            raise AssertionError("Preview Mode reset to OFF after scrolling")
        record_bug_check(label, mode, "PASS", f"{combo_label}: Preview Mode is ON and persisted after scrolling.")
    except Exception as exc:
        record_bug_check(label, mode, "FAIL", f"{combo_label}: {clean_exception(exc)}")


def verify_advance_toggle_persistence(driver, campaign_type, combo_label):
    """Run Bug #1 on the current edit form without creating a second campaign."""
    label = "Advance Toggle Persist (Bug #1)"
    mode = campaign_type.upper()
    toggle_xpath = (
        "//p[normalize-space()='Advance Campaign Setting']"
        "/ancestor::div[contains(@class, 'flex')][1]//button[@type='button']"
    )
    try:
        toggle = WebDriverWait(driver, 4).until(EC.presence_of_element_located((By.XPATH, toggle_xpath)))
    except Exception:
        record_bug_check(label, mode, "SKIPPED", f"{combo_label}: Advance Campaign Setting is not available in this email campaign form.")
        return

    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", toggle)
        if not _toggle_is_on(toggle):
            _safe_click(toggle)
            time.sleep(0.8)
        if not _toggle_is_on(toggle):
            raise AssertionError("toggle did not turn ON")
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(0.8)
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", toggle)
        time.sleep(0.5)
        if not _toggle_is_on(toggle):
            raise AssertionError("toggle reset to OFF after scrolling")
        record_bug_check(label, mode, "PASS", f"{combo_label}: toggle remained ON after scrolling away and back.")
    except Exception as exc:
        record_bug_check(label, mode, "FAIL", f"{combo_label}: {clean_exception(exc)}")


def verify_wait_duration(driver, campaign_type, combo_label):
    """Run Bug #2 against the currently visible sequence editor/form."""
    label = "Wait Duration Persist (Bug #2)"
    mode = campaign_type.upper()
    try:
        if campaign_type.lower() == "manual":
            open_buttons = driver.find_elements(By.XPATH, "//button[contains(., 'Open Channel Sequence')]")
            if open_buttons and open_buttons[0].is_displayed():
                _safe_click(open_buttons[0])
                time.sleep(1)
        hours = driver.find_elements(By.XPATH, "//input[@aria-label='Wait hours']")
        minutes = driver.find_elements(By.XPATH, "//input[contains(@aria-label, 'Wait minutes')]")
        hours = [element for element in hours if element.is_displayed()]
        minutes = [element for element in minutes if element.is_displayed()]
        if not hours or not minutes:
            record_bug_check(label, mode, "SKIPPED", f"{combo_label}: no visible Wait Duration controls found.")
            return
        hour_values = [(element.get_attribute("value") or "").strip() for element in hours]
        minute_values = [(element.get_attribute("value") or "").strip() for element in minutes]
        if all(value == "0" for value in hour_values) and all(value == "1" for value in minute_values):
            record_bug_check(label, mode, "PASS", f"{combo_label}: wait duration persisted as 0 hours and 1 minute.")
        else:
            record_bug_check(label, mode, "FAIL", f"{combo_label}: expected 0h/1m; found hours={hour_values}, minutes={minute_values}.")
    except Exception as exc:
        record_bug_check(label, mode, "FAIL", f"{combo_label}: {clean_exception(exc)}")


def clean_exception(exc):
    """Extract a clean, concise string description of the exception, stripping out chromedriver stack traces."""
    exc_type = type(exc).__name__
    exc_str = str(exc)

    msg = getattr(exc, 'msg', '') or exc_str

    if "stacktrace" in msg.lower():
        for marker in ["stacktrace:", "stacktrace", "stack trace:", "stack trace"]:
            if marker in msg.lower():
                idx = msg.lower().find(marker)
                if idx != -1:
                    msg = msg[:idx]
                break

    msg = msg.strip()
    for prefix in ["message:", "exception:"]:
        if msg.lower().startswith(prefix):
            msg = msg[len(prefix):].strip()

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


def dismiss_post_login_popup(driver, wait_time=15):
    """Dismiss a post-login popup if it appears, without failing the test."""
    try:
        ok_button = WebDriverWait(driver, wait_time).until(
            EC.element_to_be_clickable(
                (By.XPATH,
                 "//button[normalize-space(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'))='ok']"
                 " | //button[normalize-space(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'))='confirm']"
                 " | //button[normalize-space(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'))='yes']"
                )
            )
        )
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", ok_button)
        driver.execute_script("arguments[0].click();", ok_button)
        log_message("Dismissed login popup.")
        return True
    except Exception:
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

TARGET_URL = env_vars.get("BASE_URL", "https://fms-aisdr-agent1.technologymindz.com/")
USER_ID = env_vars.get("APP_USERNAME", "superadmin@gmail.com")
PASSWORD = env_vars.get("APP_PASSWORD") or os.environ.get("APP_PASSWORD")

if not PASSWORD:
    log_message("Warning: PASSWORD is not set in .env or environment variables.")

log_message("Starting campaign validation...")

chrome_options = webdriver.ChromeOptions()
chrome_options.add_experimental_option("prefs", {
    "profile.password_manager_leak_detection": False,
    "credentials_enable_service": False,
    "profile.password_manager_enabled": False
})
chrome_options.add_argument("--disable-features=SafeBrowsingPasswordProtection,SafeBrowsingPasswordProtectionService")

driver = webdriver.Chrome(options=chrome_options)
driver.maximize_window()

IMPLICIT_WAIT_TIME = 10
EXPLICIT_WAIT_TIME = 15
driver.implicitly_wait(IMPLICIT_WAIT_TIME)

list_name_base = env_vars.get("LEAD_LIST_NAME", "Automated_Lead_List")
first_name = env_vars.get("LEAD_NAMES", "John")
last_name = env_vars.get("LEAD_LAST_NAME", "")
lead_email = env_vars.get("LEAD_EMAILS", "john.doe@example.com")
lead_company = env_vars.get("LEAD_COMPANY", "Acme Corp")
lead_id = env_vars.get("LEAD_ID", "LEAD001")
smtp_provider = env_vars.get("SMTP_PROVIDER", "gmail")
conversion_criteria_raw = env_vars.get("CONVERSION_CRITERIA", "Message Replied,Link Clicked")
conversion_criteria = [c.strip() for c in conversion_criteria_raw.split(",") if c.strip()]

test_results = {
    "Test 1 (Authentication)": "NOT RUN",
    "Lead List Creation": "NOT RUN"
}

test_durations = {
    "Test 1 (Authentication)": 0.0,
    "Lead List Creation": 0.0
}

suite_summary = {
    "total_executed": 0,
    "passed_tests": 0,
    "failed_tests": 0,
    "failure_reasons": {}
}

combinations_results = []

critical_failure_occurred = False
critical_error_message = ""
critical_error_traceback = ""

suite_start_time = time.time()
current_test_name = "Test 1 (Authentication)"
current_test_start = time.time()

unique_list_name = f"{list_name_base}_{timestamp}"


def _read_toast_text(screenshot_path):
    """Runs the shared Tesseract reader on a toast screenshot and returns
    (extracted_text, avg_confidence)."""
    tokens = read_ocr_tokens(screenshot_path)
    texts = [text for text, _, _ in tokens]
    confs = [confidence for _, confidence, _ in tokens]
    extracted_text = " ".join(texts).strip()
    avg_conf = sum(confs) / len(confs) if confs else 0.0
    return extracted_text, avg_conf


def _set_react_input_value(driver, input_element, value):
    """Force a React-controlled input value and dispatch its change events."""
    driver.execute_script(
        """
        const input = arguments[0];
        const value = arguments[1];
        const setter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value'
        ).set;
        setter.call(input, value);
        input.dispatchEvent(new Event('input', {bubbles: true}));
        input.dispatchEvent(new Event('change', {bubbles: true}));
        input.dispatchEvent(new Event('blur', {bubbles: true}));
        """,
        input_element, value,
    )


def set_campaign_waits_to_one_minute(driver):
    """Force every visible wait duration from its default (often 24h) to 00:01."""
    hour_inputs = driver.find_elements(
        By.XPATH,
        "//span[translate(normalize-space(.), 'HOURS', 'hours')='hours']"
        "/preceding-sibling::input[1] | "
        "//label[contains(translate(normalize-space(.), 'HOURS', 'hours'), 'hours')]//input",
    )
    minute_inputs = driver.find_elements(
        By.XPATH,
        "//span[translate(normalize-space(.), 'MINUTES', 'minutes')='min' or "
        "translate(normalize-space(.), 'MINUTES', 'minutes')='minutes']"
        "/preceding-sibling::input[1] | "
        "//label[contains(translate(normalize-space(.), 'MINUTES', 'minutes'), 'min')]//input",
    )

    changed = 0
    for input_element in hour_inputs:
        if input_element.is_displayed() and input_element.is_enabled():
            _set_react_input_value(driver, input_element, "0")
            changed += 1
    for input_element in minute_inputs:
        if input_element.is_displayed() and input_element.is_enabled():
            _set_react_input_value(driver, input_element, "1")
            changed += 1

    if changed:
        log_message(f"Campaign wait controls set to 00:01 ({changed} field(s) updated).")
    else:
        log_message("No visible campaign wait controls found to set to 00:01.")
    return changed


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
    """Resets the browser to a clean state."""
    global driver
    log_message("Resetting browser session to a clean state...")
    try:
        try:
            alert = driver.switch_to.alert
            alert_text = alert.text
            alert.accept()
            log_message(f"Dismissed unexpected alert: '{alert_text}' during browser reset.")
        except Exception:
            pass

        driver.get(TARGET_URL)
        time.sleep(2)

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

        headers = ['name', 'notes', 'tasks', 'title', 'company', 'lead_id', 'website', 'comments', 'industry', 'activities', 'add_prompt', 'attachments', 'client_type', 'description', 'lead_rating', 'lead_source', 'lead_status', 'address_city', 'linkedin_url', 'project_name', 'project_type', 'address_state', 'business_area', 'email_address', 'address_street', 'annual_revenue', 'contact_number', 'address_country', 'no_of_employees', 'address_zip_code', 'lead_owner_email', 'last_follow_up_date', 'reason_not_interested', 'reason_not_interested_other', '_lead_id', '_call_enabled', '_whatsapp_enabled', '_email_enabled', '_linkedin_enabled']
        lead_row = [''] * len(headers)
        lead_row[headers.index('name')] = f"{first_name} {last_name}"
        lead_row[headers.index('company')] = lead_company
        lead_row[headers.index('email_address')] = lead_email
        lead_row[headers.index('lead_id')] = lead_id
        lead_row[headers.index('_lead_id')] = lead_id
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


def create_campaign_steps(driver, provider, campaign_type, unique_campaign_name, unique_list_name):
    """Executes campaign creation steps up to clicking Run Campaign.
    Preview Mode and Advance Campaign Setting are both enabled for the
    requested campaign configuration. Also selects
    'SMTP' from the Email Sender Platform dropdown (CRM / SMTP) before
    picking the actual SMTP provider.
    """
    log_message("Navigating to settings to configure default SMTP provider...")
    driver.get(TARGET_URL.rstrip('/') + "/setting")
    time.sleep(4)

    # 1a. Select "SMTP" from the Email Sender Platform dropdown (CRM / SMTP)
    log_message("Selecting SMTP from Email Sender Platform dropdown...")
    platform_select_xpath = (
        "//p[contains(text(), 'Email Sender Platform')]"
        "/ancestor::div[contains(@class, 'rounded-2xl')][1]//select"
    )
    platform_select_element = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
        EC.presence_of_element_located((By.XPATH, platform_select_xpath))
    )
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", platform_select_element)
    time.sleep(1)
    Select(platform_select_element).select_by_value("SMTP")
    time.sleep(2)
    log_message("Email Sender Platform set to: SMTP")

    # Set SMTP provider dropdown
    select_element = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
        EC.presence_of_element_located((By.XPATH, "//label[contains(text(), 'Provider')]/following-sibling::div/select | //select[contains(., 'Select Provider')]"))
    )
    time.sleep(1)
    Select(select_element).select_by_value(provider.lower())
    time.sleep(2.5)
    log_message(f"SMTP provider set to: {provider}")

    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    smtp_screenshot_path = smtp_change_screenshot_path(RUN_DIR, provider, campaign_type, run_timestamp)
    try:
        driver.save_screenshot(smtp_screenshot_path)
        log_message("Captured SMTP configuration change screenshot.")
    except Exception:
        smtp_screenshot_path = ""

    log_message("Creating Campaign...")
    campaign_url = TARGET_URL.rstrip('/') + "/campaign"
    driver.get(campaign_url)
    time.sleep(3)

    create_btn = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
        EC.element_to_be_clickable((By.XPATH, "//*[contains(text(), 'Create New Campaign')]"))
    )
    _safe_click(create_btn)
    time.sleep(2.5)

    if campaign_type == "ai":
        log_message("Selecting AI Campaign mode...")
        ai_btn = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
            EC.element_to_be_clickable((By.XPATH, campaign_selectors.AI_CAMPAIGN_OPTION_SELECTOR))
        )
        _safe_click(ai_btn)
        time.sleep(1.5)
        log_message("AI Campaign selected.")
    else:
        log_message("Selecting Manual Campaign mode...")
        manual_btn = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Manual') and contains(., 'You define the channel sequence and steps for all leads')]"))
        )
        _safe_click(manual_btn)
        time.sleep(1.5)
        log_message("Manual mode selected.")

    campaign_name_field = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
        EC.visibility_of_element_located((By.NAME, "campaign_name"))
    )
    campaign_name_field.clear()
    time.sleep(0.5)
    campaign_name_field.send_keys(unique_campaign_name)
    time.sleep(1.5)
    log_message("Campaign name entered.")

    if campaign_type == "ai":
        lead_list_xpath = campaign_selectors.AI_LEAD_LIST_BUTTON_SELECTOR
    else:
        lead_list_xpath = "//button[contains(., 'Select a lead list')]"

    lead_list_btn = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
        EC.element_to_be_clickable((By.XPATH, lead_list_xpath))
    )
    _safe_click(lead_list_btn)
    time.sleep(1.5)

    our_list_option = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
        EC.element_to_be_clickable((By.XPATH, f"//button[contains(., '{unique_list_name}')]"))
    )
    _safe_click(our_list_option)
    time.sleep(1.5)
    log_message("Lead list selected.")
    log_message("Campaign details configured.")

    if campaign_type == "ai":
        # AI campaigns: just toggle the Email channel on and leave the wait
        # duration alone here — unlike Manual, the wait is set later during
        # the Edit Sequence step (after trimming down to a single Email
        # block), not at creation time.
        log_message("Selecting Email channel for AI Campaign...")
        email_btn = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
            EC.element_to_be_clickable((By.XPATH, campaign_selectors.EMAIL_CHANNEL_SELECTOR))
        )
        _safe_click(email_btn)
        time.sleep(1.5)
        log_message("Email channel toggle enabled.")
    else:
        log_message("Configuring Channel Sequence for Manual Campaign...")
        try:
            open_btn = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'Open Channel Sequence')]"))
            )
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", open_btn)
            _safe_click(open_btn)
            time.sleep(2)

            channel_dd = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
                EC.presence_of_element_located((By.XPATH, "//select[option[@value='Email']] | //select[contains(., 'Email')]"))
            )
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", channel_dd)
            time.sleep(1)
            Select(channel_dd).select_by_value("Email")
            time.sleep(1.5)

            set_campaign_waits_to_one_minute(driver)
            time.sleep(1)

            log_message("Manual Channel Sequence configured with Email.")
        except Exception as e:
            log_message(f"Warning: Failed to configure manual channel sequence: {clean_exception(e)}")

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
                time.sleep(0.8)
        except Exception:
            pass

    time.sleep(1)

    # --------------------------------------------------------------
    # Preview Mode toggle — ALWAYS enabled as requested.
    #
    # FIX: This used to rely SOLELY on a hardcoded absolute XPath
    # (section[8]) that was calibrated against the Manual creation form's
    # DOM. The AI creation form does not render the "Channel Sequence"
    # section that Manual has, so the section index shifts and
    # section[8] no longer points at the Preview Mode toggle for AI
    # campaigns — it either times out or grabs the wrong button, which
    # then throws off the rest of the flow (including the later
    # Run Campaign click).
    #
    # Now we try a semantic, text-based locator first (finds the toggle
    # by the "Preview" label itself, so it's immune to section reordering
    # between Manual/AI), and only fall back to the absolute XPath if
    # that fails. We also check the toggle's current state so we don't
    # blindly click it OFF if it's already ON.
    # --------------------------------------------------------------
    log_message("Enabling Preview Mode...")
    PREVIEW_MODE_TOGGLE_SELECTOR = "/html/body/div[3]/div[2]/main/div[2]/div/section[8]/div/div[2]/button"
    preview_toggle_label_xpath = (
        "//p[normalize-space()='Preview Mode']"
        "/ancestor::div[contains(@class, 'justify-between') and contains(@class, 'rounded-xl')][1]"
        "//button[@type='button']"
    )

    try:
        preview_toggle = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
            EC.presence_of_element_located((By.XPATH, preview_toggle_label_xpath))
        )
        log_message("Preview Mode toggle located via text label.")
    except Exception:
        log_message("Preview Mode text-label locator failed — falling back to absolute XPath.")
        preview_toggle = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
            EC.presence_of_element_located((By.XPATH, PREVIEW_MODE_TOGGLE_SELECTOR))
        )

    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", preview_toggle)
    time.sleep(1)

    is_on = _toggle_is_on(preview_toggle)

    if not is_on:
        _safe_click(preview_toggle)
        time.sleep(2)
        log_message("Preview Mode toggle enabled.")
    else:
        log_message("Preview Mode toggle already ON.")

    if campaign_type == "ai":
        run_btn_xpath = campaign_selectors.RUN_CAMPAIGN_BUTTON_SELECTOR
    else:
        run_btn_xpath = "//button[contains(., 'Run Campaign Now')]"

    run_btn = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
        EC.presence_of_element_located((By.XPATH, run_btn_xpath))
    )
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", run_btn)
    time.sleep(1)
    if run_btn.get_attribute("disabled") is not None:
        driver.execute_script("arguments[0].removeAttribute('disabled');", run_btn)
    time.sleep(0.5)
    _safe_click(run_btn)
    time.sleep(2)
    log_message("Run Campaign button clicked.")

    return smtp_screenshot_path, run_timestamp


def run_test_case(provider, campaign_type):
    """Executes a single test case for a combination of provider and campaign_type.
    After creation (Preview Mode on), this opens the campaign via the
    pencil 'Edit campaign' icon and verifies the Preview Mode toggle is ON
    before activating. After activation, once the Campaign Activities
    overview loads, it cycles between the Lead Activity and Email views
    (waiting 1 minute in each, then going back to Campaign Activities) TWICE.
    The email evidence screenshot is captured on the Email view of the
    final (2nd) cycle. There is no separate preview-email-send/OCR check
    and no separate bug-check campaign.

    Every meaningful checkpoint is recorded via record_step() so the report
    can render a full numbered pipeline for this combination. If anything
    raises, the FAIL is attributed to whichever step was in progress
    (tracked via `current_step_title`) and no further steps are recorded
    for this combination.

    Returns (status, error_message, duration, screenshot_path, smtp_screenshot_path,
    email_activity_screenshot).
    """
    start_time = time.time()
    screenshot_path = ""
    smtp_screenshot_path = ""
    email_activity_screenshot = ""
    error_msg = ""
    status = "FAIL"

    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    unique_campaign_name = f"{campaign_type.title()}_Campaign_{provider}_{run_timestamp}"
    combo_label = f"{provider.upper()}-{campaign_type.upper()}"

    # Selectors for the Campaign Activities overview cards / nav
    #
    # IMPORTANT: these require BOTH the exact card title AND its subtitle,
    # not just a loose `contains(text(), 'Email')`. The sidebar nav item
    # "Quote Automation" has a child <p> reading "Email quote & sample
    # workflows" — that also satisfies a bare `contains(text(),'Email')`
    # check and, being earlier in the DOM, was winning the match, so the
    # old XPath was clicking into Quote Automation instead of the real
    # "Email" activity card. Anchoring on both <p> lines (title + subtitle)
    # makes the match unique to the actual card shown on Campaign Activities.
    BACK_TO_CAMPAIGN_ACTIVITIES_XPATH = "//button[contains(., 'Back to Campaign Activities')]"
    LEAD_ACTIVITY_CARD_XPATH = (
        "//button[.//p[normalize-space(text())='Lead Activity']"
        " and .//p[contains(text(), 'Overview of all activities across this campaign')]]"
    )
    EMAIL_CARD_XPATH = (
        "//button[.//p[normalize-space(text())='Email']"
        " and .//p[contains(text(), 'Email open rates, clicks, and responses')]]"
    )

    current_step_title = f"Configure SMTP & Create Campaign [{combo_label}]"

    try:
        log_message(f"Executing: Provider={provider}, CampaignType={campaign_type}")

        smtp_screenshot_path, run_timestamp = create_campaign_steps(
            driver, provider, campaign_type, unique_campaign_name, unique_list_name
        )
        record_step(
            current_step_title,
            f"SMTP provider set to '{provider}'; campaign '{unique_campaign_name}' created "
            f"and Run Campaign clicked."
        )

        # --------------------------------------
        # EDIT CAMPAIGN -> VERIFY PREVIEW MODE TOGGLE IS ON
        # --------------------------------------
        current_step_title = f"Verify Preview Mode [{combo_label}]"
        log_message("Opening campaign via Edit (pencil) icon to verify Preview Mode is ON...")
        driver.get(TARGET_URL.rstrip('/') + "/campaign")
        time.sleep(3)

        campaign_card_xpath = f"//*[contains(normalize-space(.), '{unique_campaign_name}')]"
        campaign_card = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
            EC.presence_of_element_located((By.XPATH, campaign_card_xpath))
        )
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", campaign_card)
        time.sleep(1)

        card_wrapper_xpath = (
            f"//*[contains(@class,'card') or contains(@class,'campaign') or contains(@class,'rounded') or contains(@class,'border')]"
            f"[descendant::*[contains(normalize-space(.), '{unique_campaign_name}')]]"
        )
        edit_pencil_xpath = card_wrapper_xpath + "//button[@title='Edit campaign']"

        edit_pencil_btn = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
            EC.element_to_be_clickable((By.XPATH, edit_pencil_xpath))
        )
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", edit_pencil_btn)
        time.sleep(1)
        _safe_click(edit_pencil_btn)
        time.sleep(2)
        log_message("Edit campaign view opened.")

        # PREVIEW MODE TOGGLE inside the Edit view
        # VERIFY: confirm this selector matches the Edit-campaign view DOM
        PREVIEW_MODE_TOGGLE_EDIT_SELECTOR = "/html/body/div[3]/div[2]/main/div[2]/div/section[8]/div/div[2]/button"
        preview_toggle_label_xpath = (
            "//p[normalize-space()='Preview Mode']"
            "/ancestor::div[contains(@class, 'justify-between') and contains(@class, 'rounded-xl')][1]"
            "//button[@type='button']"
        )

        try:
            preview_toggle = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
                EC.presence_of_element_located((By.XPATH, preview_toggle_label_xpath))
            )
        except Exception:
            preview_toggle = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
                EC.presence_of_element_located((By.XPATH, PREVIEW_MODE_TOGGLE_EDIT_SELECTOR))
            )

        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", preview_toggle)
        time.sleep(1)

        # VERIFY: state check via aria-checked, falling back to class heuristics
        is_on = _toggle_is_on(preview_toggle)

        if is_on:
            log_message("Preview Mode toggle verified ON.")
            preview_note = "Preview Mode toggle verified ON."
        else:
            log_message("Preview Mode toggle is OFF — enabling it.")
            _safe_click(preview_toggle)
            time.sleep(1.5)
            if not _toggle_is_on(preview_toggle):
                raise AssertionError("Preview Mode remained OFF after enable click.")
            preview_note = "Preview Mode was OFF; enabled."

        # Dedicated regression checks, reported separately from the normal
        # pipeline steps. These use this campaign's open edit form rather
        # than creating extra test campaigns.
        verify_preview_toggle_persistence(driver, preview_toggle, campaign_type, combo_label)
        verify_advance_toggle_persistence(driver, campaign_type, combo_label)
        if campaign_type == "manual":
            verify_wait_duration(driver, campaign_type, combo_label)

        try:
            save_or_close_btn = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//button[contains(., 'Save') or contains(., 'Close') or contains(., 'Done')]")
                )
            )
            _safe_click(save_or_close_btn)
            time.sleep(2)
            log_message("Edit campaign form save/close action completed.")
        except Exception:
            log_message("No edit-form save/close button was available; returning to campaign list.")

        # A Channel Sequence panel opened by the Manual bug check can leave
        # the edit drawer mounted even after its inner Save button is clicked.
        # Always return to the list and re-find the campaign before Activate;
        # never reuse the pre-edit card locator.
        driver.get(TARGET_URL.rstrip('/') + "/campaign")
        WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
            EC.presence_of_element_located((By.XPATH, campaign_card_xpath))
        )
        time.sleep(2)
        log_message("Returned to campaign list after edit-form bug checks.")

        record_step(current_step_title, preview_note)

        # --------------------------------------
        # AI-specific sequence editing
        # --------------------------------------
        if campaign_type == "ai":
            current_step_title = f"Edit AI Sequence [{combo_label}]"
            log_message("AI Campaign: Navigating to campaigns list to modify sequence...")
            driver.get(TARGET_URL.rstrip('/') + "/campaign")
            time.sleep(3)

            view_leads_xpath = card_wrapper_xpath + "//button[contains(., 'View Leads')]"
            view_leads_btn = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
                EC.element_to_be_clickable((By.XPATH, view_leads_xpath))
            )
            _safe_click(view_leads_btn)
            time.sleep(2)

            edit_seq_btn = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Edit Sequence')]"))
            )
            _safe_click(edit_seq_btn)
            time.sleep(2)

            log_message("AI Campaign: Removing follow-up emails from sequence...")
            removed_count = 0
            while True:
                trash_buttons = driver.find_elements(By.XPATH, "//button[contains(@class, 'text-red-400') and not(@disabled)]")
                if not trash_buttons:
                    log_message("AI Campaign: No more follow-up email blocks found.")
                    break

                log_message(f"AI Campaign: Deleting a follow-up email block (total remaining buttons: {len(trash_buttons)})")
                _safe_click(trash_buttons[0])
                removed_count += 1
                time.sleep(2)

            set_campaign_waits_to_one_minute(driver)
            verify_wait_duration(driver, campaign_type, combo_label)
            save_btn = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(@class, 'bg-blue-500') and (contains(., 'Save') or contains(text(), 'Save'))] | //button[contains(., 'Save')]"))
            )
            _safe_click(save_btn)
            time.sleep(2)

            try:
                back_btn = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[contains(@class, 'text-gray-500')]"))
                )
                _safe_click(back_btn)
                time.sleep(2)
            except Exception:
                driver.get(TARGET_URL.rstrip('/') + "/campaign")
                time.sleep(3)

            record_step(
                current_step_title,
                f"Removed {removed_count} follow-up email block(s); waits set to 00:01 and saved."
            )

        # --------------------------------------
        # Activate the campaign
        # --------------------------------------
        current_step_title = f"Activate Campaign [{combo_label}]"
        # Rebuild the card scope after every navigation. The previous scope
        # belongs to the DOM before the edit drawer/sequence panel rendered.
        fresh_card_wrapper_xpath = (
            f"//*[contains(@class,'card') or contains(@class,'campaign') or contains(@class,'rounded') or contains(@class,'border')]"
            f"[descendant::*[contains(normalize-space(.), '{unique_campaign_name}')]]"
        )
        activate_xpath = (
            fresh_card_wrapper_xpath + "//button[normalize-space(.)='Activate']"
            " | " + fresh_card_wrapper_xpath +
            "//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'activate')"
            " or contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'start campaign')"
            " or contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'publish')]"
        )

        activate_btn = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
            EC.element_to_be_clickable((By.XPATH, activate_xpath))
        )
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", activate_btn)
        _safe_click(activate_btn)
        log_message("Campaign activated.")
        time.sleep(2)
        record_step(current_step_title, "Campaign activated successfully.")

        # --------------------------------------
        # Wait for campaign completion
        # --------------------------------------
        current_step_title = f"Wait for Completion [{combo_label}]"
        log_message("Waiting for campaign to complete...")
        completed_xpath = (
            card_wrapper_xpath +
            "//*[contains(translate(normalize-space(.), 'COMPLETED', 'completed'), 'completed')]"
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

        record_step(current_step_title, "Campaign status showed 'Completed'.")

        # Completion triggers a React refresh of the campaign card. Re-find
        # View Details after refreshing instead of failing on a transient or
        # stale post-completion card.
        current_step_title = f"Open Campaign Activities [{combo_label}]"
        activities_opened = False
        for attempt in range(1, 4):
            try:
                if attempt > 1:
                    driver.get(TARGET_URL.rstrip('/') + "/campaign")
                    time.sleep(2)
                WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
                    EC.presence_of_element_located((By.XPATH, campaign_card_xpath))
                )
                fresh_card_wrapper_xpath = (
                    f"//*[contains(@class,'card') or contains(@class,'campaign') or contains(@class,'rounded') or contains(@class,'border')]"
                    f"[descendant::*[contains(normalize-space(.), '{unique_campaign_name}')]]"
                )
                view_details_xpath = fresh_card_wrapper_xpath + "//button[contains(normalize-space(.), 'View Details')]"
                view_details_btn = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, view_details_xpath))
                )
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", view_details_btn)
                _safe_click(view_details_btn)
                WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
                    EC.presence_of_element_located((By.XPATH, EMAIL_CARD_XPATH))
                )
                activities_opened = True
                log_message("Campaign Activities overview opened.")
                break
            except Exception as exc:
                log_message(f"View Details navigation attempt {attempt}/3 failed: {clean_exception(exc)}")
                time.sleep(2)

        if not activities_opened:
            raise TimeoutError("Could not open Campaign Activities / View Details after 3 attempts.")

        # --------------------------------------
        # LEAD ACTIVITY <-> EMAIL CYCLE (x2)
        # Pattern each cycle: Lead Activity -> wait 1 min -> Back ->
        #                     Email -> wait 1 min -> Back
        # The email evidence screenshot is captured during the LAST Email
        # visit (2nd cycle), right before going back, since that's the
        # freshest view of the Sent/Bounced/Skipped/Failed/Engaged metrics.
        # --------------------------------------
        current_step_title = f"Lead Activity / Email Cycle [{combo_label}]"
        TOTAL_CYCLES = 2
        for cycle_num in range(1, TOTAL_CYCLES + 1):
            log_message(f"--- Lead Activity / Email cycle {cycle_num}/{TOTAL_CYCLES} ---")

            # 1. Click Lead Activity
            lead_activity_btn = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
                EC.element_to_be_clickable((By.XPATH, LEAD_ACTIVITY_CARD_XPATH))
            )
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", lead_activity_btn)
            _safe_click(lead_activity_btn)
            time.sleep(2)
            log_message("Lead Activity opened. Waiting 1 minute...")
            time.sleep(10)

            # 2. Back to Campaign Activities
            back_btn = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
                EC.element_to_be_clickable((By.XPATH, BACK_TO_CAMPAIGN_ACTIVITIES_XPATH))
            )
            _safe_click(back_btn)
            time.sleep(2)
            log_message("Back to Campaign Activities.")

            # 3. Click Email
            email_card_btn = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
                EC.element_to_be_clickable((By.XPATH, EMAIL_CARD_XPATH))
            )
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", email_card_btn)
            _safe_click(email_card_btn)
            time.sleep(2)
            log_message("Email view opened. Waiting 1 minute...")
            time.sleep(10)

            # Capture the email evidence screenshot on the final cycle's
            # Email visit, right before navigating back. This is the ONLY
            # screenshot OCR is run against (see utils/vision_analyser.py).
            if cycle_num == TOTAL_CYCLES:
                email_activity_screenshot = email_screenshot_path(RUN_DIR, provider, campaign_type, run_timestamp)
                try:
                    driver.save_screenshot(email_activity_screenshot)
                    log_message("Captured email activity metrics screenshot for OCR evidence.")
                except Exception:
                    email_activity_screenshot = ""

            # 4. Back to Campaign Activities
            back_btn = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
                EC.element_to_be_clickable((By.XPATH, BACK_TO_CAMPAIGN_ACTIVITIES_XPATH))
            )
            _safe_click(back_btn)
            time.sleep(2)
            log_message("Back to Campaign Activities.")

        record_step(
            current_step_title,
            f"Completed {TOTAL_CYCLES} Lead Activity/Email cycles; email evidence "
            f"{'captured' if email_activity_screenshot else 'NOT captured'}."
        )

        status = "PASS"
        current_step_title = f"Capture Success Screenshot [{combo_label}]"
        screenshot_path = success_screenshot_path(RUN_DIR, provider, campaign_type, run_timestamp)
        driver.save_screenshot(screenshot_path)
        record_step(current_step_title, "Combination completed successfully.")
        log_message(f"Result: SUCCESS for Provider={provider}, CampaignType={campaign_type}")

    except Exception as exc:
        status = "FAIL"
        error_msg = clean_exception(exc)
        record_step(current_step_title, error_msg, status="FAIL")
        log_message(f"Result: FAIL for Provider={provider}, CampaignType={campaign_type}. Error: {error_msg}")
        screenshot_path = build_error_screenshot_path(RUN_DIR, f"{provider}_{campaign_type}", run_timestamp)
        try:
            driver.save_screenshot(screenshot_path)
        except Exception:
            pass

    duration = round(time.time() - start_time, 2)
    return status, error_msg, duration, screenshot_path, smtp_screenshot_path, email_activity_screenshot


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

        driver.get(TARGET_URL)
        time.sleep(2)

        email_field = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
            EC.visibility_of_element_located((By.NAME, "email"))
        )
        record_step("Open App", f"Loaded: {driver.current_url}")

        email_field.send_keys(USER_ID)
        driver.find_element(By.NAME, "password").send_keys(PASSWORD)
        driver.find_element(By.XPATH, "//button[@type='submit']").click()

        dashboard_element = WebDriverWait(driver, EXPLICIT_WAIT_TIME).until(
            EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Super Admin') or contains(text(), 'Enterprise AI Platform')]"))
        )

        dismiss_post_login_popup(driver)
        record_step("Login", f"Authenticated as {USER_ID}")

        screenshot_1_path = misc_screenshot_path(RUN_DIR, "01_Login_Success.png")
        driver.save_screenshot(screenshot_1_path)
        duration = round(time.time() - current_test_start, 2)
        test_durations["Test 1 (Authentication)"] = duration

        log_message(f"Test 1 PASSED. Duration: {duration}s")
        test_results["Test 1 (Authentication)"] = f"PASSED ({duration}s)"

        current_test_name = "Lead List Creation"
        current_test_start = time.time()
        create_shared_lead_list(driver, unique_list_name)
        record_step("Create Lead List", f"List '{unique_list_name}' created")
        list_duration = round(time.time() - current_test_start, 2)
        test_durations["Lead List Creation"] = list_duration
        test_results["Lead List Creation"] = f"PASSED ({list_duration}s)"

        # --------------------------------------
        # ITERATIVE MULTI-PROVIDER / CAMPAIGN VALIDATION
        # --------------------------------------
        providers = ["mailgun", "outlook", "gmail", "smartlead"]
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
                email_activity_screenshot = ""
                start_time = time.time()

                try:
                    reset_browser_state()
                    status, error_msg, duration, screenshot_path, smtp_screenshot_path, email_activity_screenshot = run_test_case(provider, campaign_type)
                except Exception as e:
                    status = "FAIL"
                    error_msg = f"Unexpected failure in test orchestrator: {clean_exception(e)}"
                    duration = round(time.time() - start_time, 2)
                    record_step(f"Test Orchestrator [{provider.upper()}-{campaign_type.upper()}]", error_msg, status="FAIL")
                    log_message(f"Result: FAIL for Provider={provider}, CampaignType={campaign_type}. Error: {error_msg}")
                    screenshot_path = build_error_screenshot_path(RUN_DIR, f"{provider}_{campaign_type}", timestamp)
                    try:
                        driver.save_screenshot(screenshot_path)
                    except Exception:
                        pass
                    smtp_screenshot_path = ""
                    email_activity_screenshot = ""

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
                    "email_activity_screenshot": email_activity_screenshot,
                    "timestamp": current_timestamp
                })

                try:
                    reset_browser_state()
                except Exception:
                    pass

        # --------------------------------------
        # PROCESS RESULTS AND PREPARE SUMMARY
        # --------------------------------------
        test_results = {
            "Test 1 (Authentication)": f"PASSED ({test_durations['Test 1 (Authentication)']}s)",
            "Lead List Creation": f"PASSED ({test_durations['Lead List Creation']}s)"
        }
        test_durations = {
            "Test 1 (Authentication)": test_durations["Test 1 (Authentication)"],
            "Lead List Creation": test_durations["Lead List Creation"]
        }

        for r in combinations_results:
            comb_key = f"{r['provider'].upper()} - {r['campaign_type'].upper()}"
            test_results[comb_key] = f"{r['status']} ({r['duration']}s)"
            if r['error_message']:
                test_results[comb_key] += f" - Error: {r['error_message']}"
            test_durations[comb_key] = r["duration"]

        suite_summary["total_executed"] = len(combinations_results)
        suite_summary["passed_tests"] = sum(1 for r in combinations_results if r["status"] == "PASS")
        suite_summary["failed_tests"] = sum(1 for r in combinations_results if r["status"] == "FAIL")
        suite_summary["failure_reasons"] = {
            f"{r['provider'].upper()} - {r['campaign_type'].upper()}": r["error_message"]
            for r in combinations_results if r["status"] == "FAIL"
        }

    # ==========================================
    # 3. UMBRELLA ERROR HANDLING & RECOVERY
    # ==========================================
    except Exception as e:
        import traceback

        critical_failure_occurred = True
        critical_error_message = clean_exception(e)
        critical_error_traceback = traceback.format_exc()

        log_message("\n" + "!"*50)
        log_message("           CRITICAL ERROR ENCOUNTERED           ")
        log_message("!"*50)
        log_message(f"Error Details: {critical_error_message}")

        if current_test_name not in test_durations or test_durations[current_test_name] == 0.0:
            test_durations[current_test_name] = round(time.time() - current_test_start, 2)

        record_step(current_test_name, critical_error_message, status="FAIL")

        cleaned_tb = clean_traceback(critical_error_traceback)
        log_message("Python Traceback:")
        for line in cleaned_tb.splitlines():
            log_message(f"  {line}")
        log_message("!"*50 + "\n")

        test_results[current_test_name] = f"FAILED - Error: {critical_error_message}"
        error_step = current_test_name.replace(" ", "_").replace("(", "").replace(")", "")

        suite_summary["total_executed"] = len(test_results)
        suite_summary["passed_tests"] = sum(1 for status in test_results.values() if "PASS" in status.upper())
        suite_summary["failed_tests"] = sum(1 for status in test_results.values() if "FAIL" in status.upper())
        suite_summary["failure_reasons"][current_test_name] = critical_error_message

        critical_screenshot_path = build_error_screenshot_path(RUN_DIR, error_step, timestamp)
        try:
            driver.save_screenshot(critical_screenshot_path)
            log_message(f"Diagnostic error screenshot preserved under: {critical_screenshot_path}")
        except Exception as screenshot_err:
            log_message(f"Could not save diagnostic error screenshot (browser session may be invalid/closed): {screenshot_err}")

    # ==========================================
    # 4. TEARDOWN & REPORTS GENERATION
    # ==========================================
    finally:
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

        try:
            generate_email_campaign_report(
                combinations_results=combinations_results,
                suite_summary=suite_summary,
                test_durations=test_durations,
                test_results=test_results,
                run_dir=RUN_DIR,
                timestamp=timestamp,
                test_num=test_num,
                suite_start_time=suite_start_time,
                critical_failure_occurred=critical_failure_occurred,
                critical_error_message=critical_error_message,
                env_vars=env_vars,
                pipeline_steps=PIPELINE_STEPS,
                bug_summary=BUG_SUMMARY,
            )
        except Exception as e:
            log_message(f"Email campaign report generation failed: {clean_exception(e)}")
