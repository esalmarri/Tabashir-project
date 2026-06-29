# Job Application Automation System (MVP)

A Python-based automated job application system using browser automation.

## Features

✅ **Smart Form Detection** - Automatically detects and fills text inputs, dropdowns, checkboxes  
✅ **Intelligent Success Validation** - Only marks as success if critical fields filled + 60% success rate  
✅ **Duplicate Prevention** - Tracks applications to avoid applying twice  
✅ **Detailed Logging** - Logs every attempt with success/failure reasons  
✅ **Screenshot Capture** - Saves BOTH before-submit AND after-submit confirmation screenshots  
✅ **File Upload Support** - Handles resume and cover letter uploads  
✅ **Automatic Form Submission** - Actually submits applications (can be disabled)  
✅ **Confirmation Detection** - Attempts to detect success confirmation messages  
✅ **Visual Debugging** - Runs in non-headless mode so you can see what's happening  

## Setup

### 1. Install Python Dependencies

```bash
pip install -r requirements.txt
```

### 2. Install Playwright Browsers

```bash
playwright install chromium
```

### 3. Configure Your Profile

Edit `user_profile.json` with your information:

```json
{
  "first_name": "Your First Name",
  "last_name": "Your Last Name",
  "email": "your.email@example.com",
  "phone": "+1-555-123-4567",
  "address": "Your Street Address",
  "city": "Your City",
  "state": "CA",
  "zip_code": "12345",
  "country": "United States",
  "linkedin_url": "https://linkedin.com/in/yourprofile",
  "portfolio_url": "https://yourwebsite.com",
  "resume_path": "/absolute/path/to/resume.pdf",
  "cover_letter_path": "/absolute/path/to/cover_letter.pdf"
}
```

**Important:** Use absolute paths for resume and cover letter files.

### 4. Add Job URLs

Edit `jobs.txt` and add job application URLs, one per line:

```
https://company1.com/careers/apply/12345
https://company2.com/jobs/data-scientist
https://jobs.company3.com/position/analyst
```

## Usage

Run the application:

```bash
python job_applier.py
```

The script will:
1. Load your profile from `user_profile.json`
2. Load job URLs from `jobs.txt`
3. Open a browser window (non-headless by default)
4. For each job:
   - Navigate to the URL
   - Detect form fields (text inputs, dropdowns, file uploads)
   - Fill in your information intelligently
   - Validate that critical fields were filled (first name, last name, email)
   - Check success rate (requires 60%+ fields filled successfully)
   - **Submit the application**
   - Wait for confirmation page to load
   - Take screenshot of confirmation
   - Log the results
   - Move to the next job

### Configuration Options

Edit the `SUBMIT_FORMS` variable in `job_applier.py`:

```python
SUBMIT_FORMS = True   # Actually submit applications
SUBMIT_FORMS = False  # Only fill forms, don't submit (testing mode)
```

## Output

### Logs Directory
- `logs/applications_YYYYMMDD.jsonl` - Daily log file with all attempts
- `logs/screenshots/` - Screenshots of filled forms and errors

### Application Tracker
- `application_tracker.json` - Tracks all applications to prevent duplicates

## Log Format

Each log entry contains:
```json
{
  "timestamp": "2024-01-15T10:30:45.123456",
  "job_url": "https://example.com/job/12345",
  "status": "SUCCESS",
  "details": {
    "detected_fields": ["first_name", "last_name", "email", "phone", "resume"],
    "fill_results": {
      "first_name_#firstName": true,
      "last_name_#lastName": true,
      "email_#email": true,
      "phone_#phoneNumber": true,
      "resume_#resume": true
    },
    "success_rate": "100.0%",
    "form_submitted": true,
    "confirmation_detected": true,
    "before_screenshot": "logs/screenshots/before_submit_20240115_103045.png",
    "after_screenshot": "logs/screenshots/after_submit_confirmation_20240115_103052.png"
  },
  "screenshot": "logs/screenshots/after_submit_confirmation_20240115_103052.png"
}
```

## Understanding Results

### SUCCESS ✅
- Form was detected and filled successfully
- Critical fields (first name, last name, email) were all filled
- At least 60% of detected fields were filled successfully
- Form was submitted
- Confirmation message was detected on the next page
- Both before-submit AND after-submit screenshots are saved

### UNCERTAIN ⚠️
- Form was filled and submitted
- But automatic confirmation detection failed
- **Review the after_submit screenshot to manually verify**
- This doesn't mean it failed - just that the script couldn't auto-detect success

### FAILED ❌
- Common reasons:
  - No form fields detected (might not be an application page)
  - Critical fields missing or failed to fill (first_name, last_name, email)
  - Success rate below 60% (too many fields failed)
  - Submit button not found
  - Page load timeout (slow site or network issues)
  - Missing files (resume/cover letter paths incorrect)

### SKIPPED ⏭️
- Duplicate application (already applied to this URL with this email)

## Customization

### Run in Headless Mode
Edit `job_applier.py` and change:
```python
applier = JobApplier(user_profile, headless=True)
```

### Add More Field Types
Edit the `FIELD_PATTERNS` dictionary in `FormFieldDetector` class to add custom field patterns.

### Adjust Success Criteria
In `apply_to_job` method, you can modify:
- `success_rate < 0.6` - Change 0.6 to require different percentage (e.g., 0.8 for 80%)
- `critical_fields` list - Add or remove required fields

### Disable Form Submission for Testing
In `job_applier.py`, set:
```python
SUBMIT_FORMS = False
```
This will fill forms and take screenshots but NOT submit them.

## Troubleshooting

### "No form fields detected"
- The page might not be a standard application form
- Try running in non-headless mode to see what the page looks like
- Check the screenshot in logs/screenshots/

### File upload fails
- Verify file paths in `user_profile.json` are absolute paths
- Ensure files exist at those locations
- Check file permissions

### Browser doesn't open
- Make sure you ran `playwright install chromium`
- Try running with `headless=False` to see errors

### Applications marked as duplicates incorrectly
- Delete `application_tracker.json` to reset tracking
- The hash is based on URL + email combination

## Next Steps

This MVP is intentionally simple. Future enhancements:
- FastAPI backend for API-based control
- PostgreSQL database for better tracking
- Credential management for site logins
- CAPTCHA handling strategies
- Form submission (currently disabled for safety)
- Retry logic for failed applications
- Web dashboard for monitoring

## Safety Notes

⚠️ **Form submission is ENABLED by default** - review screenshots carefully  
⚠️ Set `SUBMIT_FORMS = False` for testing without actually submitting  
⚠️ Always verify the after-submit confirmation screenshot  
⚠️ Some sites may have anti-automation measures (CAPTCHAs, etc.)  
⚠️ Respect websites' terms of service  
⚠️ The script can't handle CAPTCHAs automatically - these will cause failures  
