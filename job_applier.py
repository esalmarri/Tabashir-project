"""
Job Application Automation System (MVP)
=======================================
A Python-based automated job application system using browser automation.

Reads your details from user_profile.json and a list of job URLs from jobs.txt,
then for each job: navigates to the page, detects form fields, fills them,
validates critical fields, submits (if SUBMIT_FORMS is True), takes before/after
screenshots, detects a confirmation, and logs the result.

Run:
    python job_applier.py
"""

import json
import os
import hashlib
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout


# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

SUBMIT_FORMS = True   # True = actually submit applications. False = fill only (testing).

PROFILE_PATH = "user_profile.json"
JOBS_PATH = "jobs.txt"
TRACKER_PATH = "application_tracker.json"
LOGS_DIR = "logs"
SCREENSHOTS_DIR = os.path.join(LOGS_DIR, "screenshots")

# Minimum fraction of detected fields that must fill successfully.
SUCCESS_RATE_THRESHOLD = 0.6
# Fields that MUST be filled for an application to count as a success.
CRITICAL_FIELDS = ["first_name", "last_name", "email"]


# ─────────────────────────────────────────────────────────────────────────────
# Form field detection
# ─────────────────────────────────────────────────────────────────────────────

class FormFieldDetector:
    """
    Detects form fields on a page by matching input attributes against known
    patterns. Add new field types by extending FIELD_PATTERNS.
    """

    # Each profile key maps to a list of substrings we look for in a field's
    # name / id / placeholder / label to recognise it.
    FIELD_PATTERNS = {
        "first_name": ["first name", "firstname", "first_name", "fname", "given name"],
        "last_name": ["last name", "lastname", "last_name", "lname", "surname", "family name"],
        "email": ["email", "e-mail"],
        "phone": ["phone", "mobile", "telephone", "tel", "contact number"],
        "address": ["address", "street"],
        "city": ["city", "town"],
        "state": ["state", "province", "region"],
        "zip_code": ["zip", "postal", "postcode"],
        "country": ["country"],
        "linkedin_url": ["linkedin"],
        "portfolio_url": ["portfolio", "website", "personal site"],
        "resume": ["resume", "cv", "curriculum"],
        "cover_letter": ["cover letter", "coverletter", "cover_letter"],
    }

    def __init__(self, page):
        self.page = page

    def _attr_text(self, element):
        """Collect searchable text from an input's attributes and nearby label."""
        parts = []
        for attr in ("name", "id", "placeholder", "aria-label", "type"):
            try:
                val = element.get_attribute(attr)
                if val:
                    parts.append(val.lower())
            except Exception:
                pass
        # Try associated <label> text via the id.
        try:
            el_id = element.get_attribute("id")
            if el_id:
                label = self.page.query_selector(f'label[for="{el_id}"]')
                if label:
                    txt = label.inner_text()
                    if txt:
                        parts.append(txt.lower())
        except Exception:
            pass
        return " ".join(parts)

    def detect(self):
        """
        Return a list of detected fields:
            [{"profile_key": str, "element": handle, "kind": "text|file|select"}]
        """
        detected = []
        used_elements = set()

        # Text inputs and textareas
        selectors = "input[type=text], input[type=email], input[type=tel], input:not([type]), textarea"
        for element in self.page.query_selector_all(selectors):
            text = self._attr_text(element)
            key = self._match_key(text)
            if key and id(element) not in used_elements:
                detected.append({"profile_key": key, "element": element, "kind": "text"})
                used_elements.add(id(element))

        # File inputs (resume / cover letter)
        for element in self.page.query_selector_all("input[type=file]"):
            text = self._attr_text(element)
            key = self._match_key(text) or "resume"  # default a lone upload to resume
            detected.append({"profile_key": key, "element": element, "kind": "file"})

        # Dropdowns
        for element in self.page.query_selector_all("select"):
            text = self._attr_text(element)
            key = self._match_key(text)
            if key:
                detected.append({"profile_key": key, "element": element, "kind": "select"})

        return detected

    def _match_key(self, text):
        for key, patterns in self.FIELD_PATTERNS.items():
            for pat in patterns:
                if pat in text:
                    return key
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Job applier
# ─────────────────────────────────────────────────────────────────────────────

class JobApplier:
    def __init__(self, user_profile, headless=False):
        self.profile = user_profile
        self.headless = headless
        self.tracker = self._load_tracker()

    # ── tracking / dedup ────────────────────────────────────────────────────
    def _load_tracker(self):
        if os.path.exists(TRACKER_PATH):
            with open(TRACKER_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _save_tracker(self):
        with open(TRACKER_PATH, "w", encoding="utf-8") as f:
            json.dump(self.tracker, f, indent=2)

    def _application_hash(self, url):
        raw = f"{url}|{self.profile.get('email','')}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def _already_applied(self, url):
        return self._application_hash(url) in self.tracker

    def _mark_applied(self, url, status):
        self.tracker[self._application_hash(url)] = {
            "url": url, "status": status,
            "timestamp": datetime.now().isoformat(),
        }
        self._save_tracker()

    # ── filling ─────────────────────────────────────────────────────────────
    def _value_for(self, key):
        """Map a detected field key to a value from the profile."""
        if key in ("resume", "cover_letter"):
            path_key = "resume_path" if key == "resume" else "cover_letter_path"
            return self.profile.get(path_key, "")
        return self.profile.get(key, "")

    def _fill_field(self, field):
        key = field["profile_key"]
        element = field["element"]
        kind = field["kind"]
        value = self._value_for(key)

        if not value:
            return False
        try:
            if kind == "file":
                if not os.path.exists(value):
                    return False
                element.set_input_files(value)
            elif kind == "select":
                try:
                    element.select_option(label=value)
                except Exception:
                    element.select_option(value=value)
            else:
                element.fill(str(value))
            return True
        except Exception:
            return False

    # ── submission ──────────────────────────────────────────────────────────
    def _find_submit_button(self, page):
        candidates = [
            "button[type=submit]",
            "input[type=submit]",
            "button:has-text('Submit')",
            "button:has-text('Submit Application')",
            "button:has-text('Apply')",
            "button:has-text('Send Application')",
        ]
        for sel in candidates:
            try:
                btn = page.query_selector(sel)
                if btn:
                    return btn
            except Exception:
                continue
        return None

    def _detect_confirmation(self, page):
        confirmation_phrases = [
            "thank you", "application received", "successfully submitted",
            "we have received", "application complete", "thanks for applying",
            "your application has been", "submission successful",
        ]
        try:
            body = page.inner_text("body").lower()
        except Exception:
            return False, ""
        for phrase in confirmation_phrases:
            if phrase in body:
                return True, phrase
        return False, ""

    # ── per-job flow ────────────────────────────────────────────────────────
    def apply_to_job(self, page, url):
        result = {"job_url": url, "status": "FAILED", "details": {}}

        if self._already_applied(url):
            result["status"] = "SKIPPED"
            result["details"]["reason"] = "Duplicate — already applied with this email."
            return result

        try:
            page.goto(url, timeout=30000, wait_until="domcontentloaded")
        except PWTimeout:
            result["details"]["reason"] = "Page load timeout."
            return result
        except Exception as e:
            result["details"]["reason"] = f"Navigation error: {e}"
            return result

        page.wait_for_timeout(2000)  # let dynamic forms render

        # 1. Detect fields
        detector = FormFieldDetector(page)
        fields = detector.detect()
        if not fields:
            result["details"]["reason"] = "No form fields detected."
            return result

        # 2. Fill fields
        fill_results = {}
        for field in fields:
            label = f"{field['profile_key']}_{field['kind']}"
            fill_results[label] = self._fill_field(field)

        detected_keys = [f["profile_key"] for f in fields]
        filled_count = sum(1 for ok in fill_results.values() if ok)
        success_rate = filled_count / len(fields) if fields else 0.0

        result["details"]["detected_fields"] = detected_keys
        result["details"]["fill_results"] = fill_results
        result["details"]["success_rate"] = f"{success_rate * 100:.1f}%"

        # 3. Validate critical fields
        critical_ok = all(
            any(f["profile_key"] == cf and fill_results.get(f"{cf}_{f['kind']}")
                for f in fields)
            for cf in CRITICAL_FIELDS
        )
        if not critical_ok:
            result["details"]["reason"] = "Critical fields (first_name, last_name, email) missing or failed."
            return result

        # 4. Success rate gate
        if success_rate < SUCCESS_RATE_THRESHOLD:
            result["details"]["reason"] = f"Success rate {success_rate*100:.1f}% below {SUCCESS_RATE_THRESHOLD*100:.0f}%."
            return result

        # 5. Before-submit screenshot
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        before_path = os.path.join(SCREENSHOTS_DIR, f"before_submit_{ts}.png")
        try:
            page.screenshot(path=before_path, full_page=True)
            result["details"]["before_screenshot"] = before_path
        except Exception:
            pass

        # 6. Submit
        if not SUBMIT_FORMS:
            result["status"] = "UNCERTAIN"
            result["details"]["form_submitted"] = False
            result["details"]["reason"] = "SUBMIT_FORMS is False — filled but not submitted (testing mode)."
            return result

        submit_btn = self._find_submit_button(page)
        if not submit_btn:
            result["details"]["reason"] = "Submit button not found."
            return result

        try:
            submit_btn.click()
            page.wait_for_timeout(3000)  # wait for confirmation page
        except Exception as e:
            result["details"]["reason"] = f"Submit click failed: {e}"
            return result

        result["details"]["form_submitted"] = True

        # 7. After-submit screenshot
        ts2 = datetime.now().strftime("%Y%m%d_%H%M%S")
        after_path = os.path.join(SCREENSHOTS_DIR, f"after_submit_confirmation_{ts2}.png")
        try:
            page.screenshot(path=after_path, full_page=True)
            result["details"]["after_screenshot"] = after_path
            result["screenshot"] = after_path
        except Exception:
            pass

        # 8. Confirmation detection
        confirmed, phrase = self._detect_confirmation(page)
        result["details"]["confirmation_detected"] = confirmed
        if confirmed:
            result["status"] = "SUCCESS"
            result["details"]["confirmation_phrase"] = phrase
        else:
            result["status"] = "UNCERTAIN"
            result["details"]["reason"] = "Submitted but no confirmation detected — review the after screenshot."

        return result

    # ── run all jobs ────────────────────────────────────────────────────────
    def run(self, job_urls):
        ensure_dirs()
        results = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            context = browser.new_context()
            page = context.new_page()

            for url in job_urls:
                print(f"\n→ Applying: {url}")
                result = self.apply_to_job(page, url)
                print(f"  Status: {result['status']}")
                if result["details"].get("reason"):
                    print(f"  Reason: {result['details']['reason']}")

                # Track non-failures so we don't re-apply
                if result["status"] in ("SUCCESS", "UNCERTAIN", "SKIPPED"):
                    if result["status"] != "SKIPPED":
                        self._mark_applied(url, result["status"])

                log_result(result)
                results.append(result)

            browser.close()
        return results


# ─────────────────────────────────────────────────────────────────────────────
# Helpers: loading, logging
# ─────────────────────────────────────────────────────────────────────────────

def ensure_dirs():
    Path(SCREENSHOTS_DIR).mkdir(parents=True, exist_ok=True)


def load_profile():
    if not os.path.exists(PROFILE_PATH):
        raise FileNotFoundError(
            f"{PROFILE_PATH} not found. Copy user_profile.example.json to "
            f"{PROFILE_PATH} and fill in your details."
        )
    with open(PROFILE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_job_urls():
    if not os.path.exists(JOBS_PATH):
        raise FileNotFoundError(f"{JOBS_PATH} not found. Add job URLs, one per line.")
    urls = []
    with open(JOBS_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)
    return urls


def log_result(result):
    ensure_dirs()
    day = datetime.now().strftime("%Y%m%d")
    log_path = os.path.join(LOGS_DIR, f"applications_{day}.jsonl")
    entry = {
        "timestamp": datetime.now().isoformat(),
        "job_url": result["job_url"],
        "status": result["status"],
        "details": result["details"],
        "screenshot": result.get("screenshot", ""),
    }
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("=== Job Application Automation System (MVP) ===")
    profile = load_profile()
    job_urls = load_job_urls()

    if not job_urls:
        print("No job URLs found in jobs.txt. Add some and try again.")
        return

    print(f"Loaded profile for: {profile.get('first_name','')} {profile.get('last_name','')}")
    print(f"Jobs to process: {len(job_urls)}")
    print(f"SUBMIT_FORMS = {SUBMIT_FORMS}")

    applier = JobApplier(profile, headless=False)
    results = applier.run(job_urls)

    # Summary
    print("\n=== Summary ===")
    counts = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    for status, n in counts.items():
        print(f"  {status}: {n}")


if __name__ == "__main__":
    main()
