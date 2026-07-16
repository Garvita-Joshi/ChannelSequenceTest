"""
utils/ss_paths.py
──────────────────
Single source of truth for where campaign screenshots live on disk.

Before: every module (Test.py, bug_list.py, report_generator.py) built its
own screenshot filenames and dumped them straight into run_dir. That's why
the report gallery only ever globbed run_dir/*.png flat — there was no
folder structure to walk.

After: everything lives under

    <run_dir>/
      ss/
        email/     <- "email activity" table screenshots (OCR input for
                       vision_analyser.analyse_email_screenshot)
        toast/     <- notification/toast screenshots (bug_list.py)
        error/     <- full-page diagnostic screenshots taken on failure

Import this module from Test.py, bug_list.py, and report_generator.py so
nobody hardcodes a path shape again.
"""

import os


def ss_root(run_dir: str) -> str:
    path = os.path.join(run_dir, "ss")
    os.makedirs(path, exist_ok=True)
    return path


def ss_subfolder(run_dir: str, name: str) -> str:
    path = os.path.join(ss_root(run_dir), name)
    os.makedirs(path, exist_ok=True)
    return path


def email_screenshot_path(run_dir: str, provider: str, campaign_type: str, timestamp: str) -> str:
    """Where Test.py should save the email-activity table screenshot for a
    given combo, and where vision_analyser should read it from."""
    folder = ss_subfolder(run_dir, "email")
    filename = f"{provider.lower()}_{campaign_type.lower()}_email_activity_{timestamp}.png"
    return os.path.join(folder, filename)


def toast_screenshot_path(run_dir: str, campaign_type: str, timestamp: str) -> str:
    """Where bug_list.py should save the toast-notification screenshot."""
    folder = ss_subfolder(run_dir, "toast")
    filename = f"toast_{campaign_type.lower()}_{timestamp}.png"
    return os.path.join(folder, filename)


def error_screenshot_path(run_dir: str, label: str, timestamp: str) -> str:
    """Where any module should save a full-page diagnostic screenshot on failure."""
    folder = ss_subfolder(run_dir, "error")
    filename = f"ERROR_{label}_{timestamp}.png"
    return os.path.join(folder, filename)


def smtp_change_screenshot_path(run_dir: str, provider: str, campaign_type: str, timestamp: str) -> str:
    """SMTP-provider-change confirmation screenshot, filed alongside email evidence."""
    folder = ss_subfolder(run_dir, "email")
    filename = f"smtp_change_{provider.lower()}_{campaign_type.lower()}_{timestamp}.png"
    return os.path.join(folder, filename)


def success_screenshot_path(run_dir: str, provider: str, campaign_type: str, timestamp: str) -> str:
    """Full-page confirmation screenshot on a passing combination run."""
    folder = ss_subfolder(run_dir, "success")
    filename = f"success_{provider.lower()}_{campaign_type.lower()}_{timestamp}.png"
    return os.path.join(folder, filename)


def misc_screenshot_path(run_dir: str, filename: str) -> str:
    """Catch-all for one-off screenshots (e.g. the initial login confirmation)
    that don't belong to any of the categories above."""
    folder = ss_subfolder(run_dir, "misc")
    return os.path.join(folder, filename)