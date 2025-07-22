import os
import re
import csv
import time
import imaplib
import email
import logging
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from email.header import decode_header

# Optional: Google Sheets integration
try:
    import gspread
    from google.oauth2.service_account import Credentials
    HAS_SHEETS = True
except ImportError:
    HAS_SHEETS = False

# ─── Setup Logging ──────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ─── Load .env ──────────────────────────────────────────────────────────────
load_dotenv()
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")

# ─── Patterns ──────────────────────────────────────────────────────────────
THANK_YOU_PATTERNS = [
    re.compile(r'thank you for applying', re.I),
    re.compile(r'thank you for your application', re.I),
    re.compile(r'we received your application', re.I),
]
REJECTION_PATTERNS = [
    re.compile(r'we will not be moving forward', re.I),
    re.compile(r'we have decided not to proceed', re.I),
    re.compile(r'we regret to inform you', re.I),
    re.compile(r'unfortunately, we will not', re.I),
    re.compile(r'decided to move forward with another candidate', re.I),
    re.compile(r'we don\'t see a fit at this time', re.I),
    re.compile(r'decided to move forward with other candidates', re.I),
    re.compile(r'not the news you were hoping for', re.I),
    re.compile(r'we appreciate your interest.*?but', re.I),
    re.compile(r'we have decided to move forward with other candidates', re.I),
    re.compile(r'we have moved forward with other candidates', re.I),
    re.compile(r'have decided to pursue other candidates', re.I),
]
INTERVIEW_PATTERNS = [
    re.compile(r'(schedule|availability|book|invite).*interview', re.I),
    re.compile(r'interview.*(scheduled|invite|booking)', re.I),
    re.compile(r'invitation to interview', re.I),
    re.compile(r'recruiter.*reach out', re.I),
]
INTERVIEW_FALSE_POSITIVES = [
    re.compile(r'what happens next', re.I),
    re.compile(r"you['’]ll hear from us", re.I),
    re.compile(r'shortlisted candidates', re.I),
    re.compile(r'you are not selected', re.I),
]

# Companies to exclude
EXCLUDED_COMPANIES = {"linkedin", "gmail", "chapelridge", "prayvine", "squaretrade"}

# ─── Helpers ──────────────────────────────────────────────────────────────
def decode_str(s):
    decoded, encoding = decode_header(s)[0]
    if isinstance(decoded, bytes):
        return decoded.decode(encoding or 'utf-8', errors='ignore')
    return decoded

def extract_text_from_email(msg):
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                return part.get_payload(decode=True).decode(errors='ignore')
    else:
        return msg.get_payload(decode=True).decode(errors='ignore')
    return ""

def classify_email(subject, body):
    if any(pat.search(subject) or pat.search(body) for pat in INTERVIEW_FALSE_POSITIVES):
        return None
    for pat in INTERVIEW_PATTERNS:
        if pat.search(subject) or pat.search(body):
            return "Interview Requested"
    for pat in REJECTION_PATTERNS:
        if pat.search(subject) or pat.search(body):
            return "Rejected"
    for pat in THANK_YOU_PATTERNS:
        if pat.search(subject) or pat.search(body):
            return "Application Sent"
    return None

# ─── Main Logic ──────────────────────────────────────────────────────────

def process_job_emails():
    applications = {}
    three_months_ago = datetime.now(timezone.utc) - timedelta(days=90)
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(EMAIL_USER, EMAIL_PASS)
        mail.select('"[Gmail]/All Mail"')

        logger.info("📬 Scanning Gmail inbox...")
        result, data = mail.search(None, 'X-GM-RAW', 'newer_than:90d')

        if result != "OK":
            logger.error("IMAP search failed")
            return {}

        email_ids = data[0].split()
        logger.info(f"📧 Found {len(email_ids)} recent emails to check")
        if not email_ids:
            return {}

        BATCH_SIZE = 50
        for i in range(0, len(email_ids), BATCH_SIZE):
            batch_ids = email_ids[i:i+BATCH_SIZE]
            id_str = ','.join(eid.decode() for eid in batch_ids)
            result, msg_data = mail.fetch(id_str, "(BODY.PEEK[HEADER])")

            if result != "OK":
                continue

            for j in range(0, len(msg_data), 2):
                if len(msg_data[j]) < 2:
                    continue
                msg = email.message_from_bytes(msg_data[j][1])
                subject = decode_str(msg.get("Subject", ""))
                sender = decode_str(msg.get("From", ""))
                date_str = msg.get("Date")
                date_obj = email.utils.parsedate_to_datetime(date_str)
                if date_obj.tzinfo is None:
                    date_obj = date_obj.replace(tzinfo=timezone.utc)
                if date_obj < three_months_ago:
                    continue

                eid = batch_ids[j//2]
                result, full_msg_data = mail.fetch(eid, "(RFC822)")
                if result != "OK":
                    continue
                full_msg = email.message_from_bytes(full_msg_data[0][1])
                body = extract_text_from_email(full_msg)

                status = classify_email(subject, body)
                if not status:
                    continue

                company = re.findall(r'@([\w.-]+)', sender)
                company = company[0].split(".")[0].title() if company else "Unknown"
                if company.lower() in EXCLUDED_COMPANIES:
                    continue

                job_title = subject.split(" at ")[-1] if " at " in subject else subject
                key = (company, job_title)

                if key not in applications or date_obj > applications[key]["last_update"]:
                    applications[key] = {
                        "company": company,
                        "job_title": job_title.strip(),
                        "status": status,
                        "date_applied": date_obj.strftime("%Y-%m-%d"),
                        "last_update": date_obj,
                        "subject": subject,
                    }

    except Exception as e:
        logger.exception("Failed to process emails: %s", e)

    return applications

# ─── Output to CSV and Google Sheets ────────────────────────────────────────────────────────
def save_to_csv(applications, filename="job_applications.csv"):
    with open(filename, "w", newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(["Company", "Job Title", "Date Applied", "Current Status", "Last Update", "Email Subject"])
        for app in applications.values():
            writer.writerow([
                app["company"], app["job_title"], app["date_applied"],
                app["status"], app["last_update"].strftime("%Y-%m-%d"), app["subject"]
            ])
    logger.info(f"✅ CSV saved to {filename}")

# ─── Main Entrypoint ────────────────────────────────────────────────────────
if __name__ == "__main__":
    logger.info("🚀 Starting job application tracker...")
    applications = process_job_emails()

    if applications:
        save_to_csv(applications)
    else:
        logger.info("📭 No relevant job application updates found.")
