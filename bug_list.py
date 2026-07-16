# bug_list.py
# Automation test suite for Bugs reported in the QA bug tracker.
# Reuses settings, driver configuration, and helper methods from Test.py.
#
# CHANGED: no longer initializes its own easyocr.Reader. That second OCR
# engine was pulling in a numpy/scipy version that conflicted with the rest
# of the environment (same root cause as the report_generator OCR issue).
# Toast OCR now reuses the shared Tesseract reader from
# utils.vision_analyser — one OCR path for the whole project.

import os
import time
from datetime import datetime
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# Import the core Test module containing our configuration and campaign creation steps
import Test
from utils.vision_analyser import read_ocr_tokens
from utils.ss_paths import toast_screenshot_path, error_screenshot_path

# Re-expose logger and constants for convenience
log_message = Test.log_message


def _read_toast_text(screenshot_path):
    """Runs the shared Tesseract reader on a toast screenshot and returns
    (extracted_text, avg_confidence)."""
    tokens = read_ocr_tokens(screenshot_path)
    texts = [text for text, _, _ in tokens]
    confs = [confidence for _, confidence, _ in tokens]
    extracted_text = " ".join(texts).strip()
    avg_conf = sum(confs) / len(confs) if confs else 0.0
    return extracted_text, avg_conf


def run_preview_mode_verification(campaign_type):
    """Executes validation for Preview Mode email send on a given campaign type."""
    driver = Test.driver

    # 1. Generate unique campaign name
    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    unique_campaign_name = f"{campaign_type.title()}_Preview_Campaign_{run_timestamp}"

    log_message("\n" + "="*50)
    log_message(f"STARTING BUG 1: {campaign_type.upper()} CAMPAIGN PREVIEW MODE TEST")
    log_message("="*50)

    try:
        # Reset browser to a clean dashboard state
        log_message("Resetting browser state before campaign execution...")
        Test.reset_browser_state()

        # 2. Configure SMTP settings & execute campaign creation steps (Reusing creation helper)
        log_message(f"Executing campaign creation workflow for {campaign_type.upper()} Campaign...")
        smtp_screenshot, create_ts = Test.create_campaign_steps(
            driver=driver,
            provider="gmail",
            campaign_type=campaign_type,
            unique_campaign_name=unique_campaign_name,
            unique_list_name=Test.unique_list_name,
            preview_mode=True  # Step 3: Enable Preview Mode toggle
        )
        log_message("Campaign created.")

        # Step 4: Wait until campaign is created successfully (redirected back to campaign list)
        log_message("Waiting for campaign to be created successfully...")
        WebDriverWait(driver, 30).until(
            EC.url_contains("/campaign")
        )
        campaign_card_xpath = f"//*[contains(normalize-space(.), '{unique_campaign_name}')]"
        campaign_card = WebDriverWait(driver, Test.EXPLICIT_WAIT_TIME).until(
            EC.presence_of_element_located((By.XPATH, campaign_card_xpath))
        )
        log_message(f"Campaign card '{unique_campaign_name}' is visible on the campaign listing page.")

        # Step 5: Return to Campaign Listing page explicitly if not there
        if "/campaign" not in driver.current_url:
            log_message("Returning to Campaign Listing page...")
            driver.get(Test.TARGET_URL.rstrip('/') + "/campaign")
            time.sleep(2)

        # Step 6: Locate the newly created campaign and click its Preview icon
        log_message("Locating the newly created campaign to open preview...")
        campaign_card = WebDriverWait(driver, Test.EXPLICIT_WAIT_TIME).until(
            EC.presence_of_element_located((By.XPATH, campaign_card_xpath))
        )
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", campaign_card)
        time.sleep(1)

        # XPath selectors defined in placeholders
        PREVIEW_ICON_SELECTOR = "/html/body/div[3]/div[2]/main/div/div[4]/div[2]/div/div[4]/div[2]/button[2]"
        PREVIEW_LEAD_LIST_SELECTOR = "/html/body/div[3]/div[2]/main/div[3]/div[2]/table/tbody/tr/td[1]/input"
        SEND_PREVIEW_EMAIL_BUTTON_SELECTOR = "/html/body/div[3]/div[2]/main/div[1]/button[2]"
        NOTIFICATION_TOAST_SELECTOR = "/html/body/div[4]/div/div"

        try:
            # Locate preview icon relative to card for robust multi-campaign handling
            preview_btn = campaign_card.find_element(By.XPATH, ".//button[contains(@title, 'Preview') or contains(., 'Preview') or contains(@class, 'preview')]")
        except Exception:
            # Fallback to absolute placeholder path
            preview_btn = WebDriverWait(driver, Test.EXPLICIT_WAIT_TIME).until(
                EC.element_to_be_clickable((By.XPATH, PREVIEW_ICON_SELECTOR))
            )

        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", preview_btn)
        Test._safe_click(preview_btn)
        log_message("Preview dialog opened.")

        # Step 7: Select required Lead List inside Preview dialog
        log_message("Selecting the required Lead List for sending the preview email...")
        lead_list_input = WebDriverWait(driver, Test.EXPLICIT_WAIT_TIME).until(
            EC.element_to_be_clickable((By.XPATH, PREVIEW_LEAD_LIST_SELECTOR))
        )
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", lead_list_input)
        if not lead_list_input.is_selected():
            Test._safe_click(lead_list_input)
        log_message("Lead List selected.")

        # Step 8: Click Send button
        log_message("Clicking the Send button in the Preview dialog...")
        send_btn = WebDriverWait(driver, Test.EXPLICIT_WAIT_TIME).until(
            EC.element_to_be_clickable((By.XPATH, SEND_PREVIEW_EMAIL_BUTTON_SELECTOR))
        )
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", send_btn)
        Test._safe_click(send_btn)
        log_message("Preview email sent.")

        # Step 9: Wait for the notification (toast message)
        log_message("Waiting for notification toast message...")
        toast_elem = WebDriverWait(driver, Test.EXPLICIT_WAIT_TIME).until(
            EC.visibility_of_element_located((By.XPATH, NOTIFICATION_TOAST_SELECTOR))
        )
        log_message("Notification detected.")

        # Step 10: Capture a screenshot of ONLY the notification area
        # -> now saved under <run_dir>/ss/toast/ instead of the run_dir root
        screenshot_path = toast_screenshot_path(Test.RUN_DIR, campaign_type, run_timestamp)
        toast_elem.screenshot(screenshot_path)
        log_message(f"Screenshot of toast area captured: {screenshot_path}")

        # Step 11: OCR the toast with the shared Tesseract reader
        log_message("Executing OCR on the captured screenshot...")
        extracted_text, confidence = _read_toast_text(screenshot_path)
        log_message("OCR completed.")
        log_message(f"Raw OCR Output: '{extracted_text}' (Confidence: {confidence:.2f})")

        # Step 12: Determine notification status
        if not extracted_text or confidence < 0.30:
            raise AssertionError(f"OCR could not confidently read the toast message. Extracted: '{extracted_text}' (Confidence: {confidence:.2f})")

        lower_text = extracted_text.lower()
        if "success" in lower_text or "sent" in lower_text or "successfully" in lower_text:
            status = "SUCCESSFUL"
        elif "fail" in lower_text or "failed" in lower_text or "error" in lower_text:
            status = "FAILED"
        else:
            status = f"OTHER STATUS: {extracted_text}"

        log_message(f"Notification status identified: {status}")
        log_message(f"Bug 1 {campaign_type.upper()} test completed successfully.")
        return True, status, screenshot_path

    except Exception as e:
        error_msg = Test.clean_exception(e)
        log_message(f"Bug 1 {campaign_type.upper()} test FAILED: {error_msg}")

        # Take full page diagnostic screenshot -> now saved under ss/error/
        fallback_screenshot = error_screenshot_path(Test.RUN_DIR, f"Bug1_{campaign_type}", run_timestamp)
        try:
            driver.save_screenshot(fallback_screenshot)
            log_message(f"Diagnostic page screenshot saved to: {fallback_screenshot}")
        except Exception:
            pass
        return False, error_msg, fallback_screenshot


def main():
    driver = Test.driver

    # 1. Login to application
    log_message("Logging in to platform for Bug Tracker verification...")
    driver.get(Test.TARGET_URL)
    time.sleep(2)

    try:
        email_field = WebDriverWait(driver, Test.EXPLICIT_WAIT_TIME).until(
            EC.visibility_of_element_located((By.NAME, "email"))
        )
        email_field.send_keys(Test.USER_ID)
        driver.find_element(By.NAME, "password").send_keys(Test.PASSWORD)
        driver.find_element(By.XPATH, "//button[@type='submit']").click()

        WebDriverWait(driver, Test.EXPLICIT_WAIT_TIME).until(
            EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Super Admin') or contains(text(), 'Enterprise AI Platform')]"))
        )
        Test.dismiss_post_login_popup(driver)
        log_message("Log in successful. Dashboard accessed.")

        # 2. Create the shared lead list for sending preview
        log_message("Initializing shared lead list for verification...")
        Test.create_shared_lead_list(driver, Test.unique_list_name)
        log_message(f"Lead list '{Test.unique_list_name}' initialized.")

        # 3. Run Bug 1 Manual Campaign Preview test
        manual_ok, manual_status, manual_ss = run_preview_mode_verification("manual")

        # 4. Run Bug 1 AI Campaign Preview test
        ai_ok, ai_status, ai_ss = run_preview_mode_verification("ai")

        # Clean up shared lead list at the very end
        log_message("Tearing down shared lead list...")
        try:
            Test.delete_shared_lead_list(driver, Test.unique_list_name)
        except Exception as cleanup_err:
            log_message(f"Warning: Failed to delete shared lead list: {Test.clean_exception(cleanup_err)}")

        # Display Final Summary
        log_message("\n" + "="*50)
        log_message("             BUG TRACKER RUN SUMMARY            ")
        log_message("="*50)
        log_message(f"Bug 1 (Manual Campaign Preview) : {'PASSED' if manual_ok else 'FAILED'} (Status: {manual_status})")
        log_message(f"Bug 1 (AI Campaign Preview)     : {'PASSED' if ai_ok else 'FAILED'} (Status: {ai_status})")
        log_message("="*50 + "\n")

    finally:
        log_message("Terminating Chrome session...")
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
