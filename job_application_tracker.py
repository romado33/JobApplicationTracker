import os
import re
import csv
import imaplib
import email
import logging
from datetime import datetime, timedelta, timezone

# Optional deps
try:
    import streamlit as st  # for st.secrets when running in Streamlit
except Exception:  # not in Streamlit context
    st = None

try:
    from dotenv import load_dotenv  # for local CLI runs
except Exception:
    load_dotenv = None

from email.header import decode_header
try:
    from bs4 import BeautifulSoup
except Exception:  # pragma: no cover - fallback if BeautifulSoup isn't installed
    BeautifulSoup = None

# ─── Setup Logging ────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ─── Credentials loader ───────────────────────────────────────────────────────
def _get_credentials():
    """
    Order of precedence:
      1) Streamlit secrets (when running in Streamlit)
      2) Environment variables (possibly from .env if present)
    """
    user = None
    pw = None

    # 1) Streamlit secrets (hosted safest path)
    if st is not None:
        try:
            user = st.secrets.get("EMAIL_USER", None)
            pw = st.secrets.get("EMAIL_PASS", None)
            if user and pw:
                # Optional UI hints
                try:
                    st.sidebar.success("✅ Gmail credentials loaded from Streamlit secrets")
                except Exception:
                    pass
                return user, pw
            else:
                try:
                    st.sidebar.error("❌ Missing EMAIL_USER or EMAIL_PASS in Streamlit secrets")
                except Exception:
                    pass
        except Exception:
            # st.secrets not available or misconfigured; fall through to env
            pass

    # 2) .env / environment variables (local dev & CLI)
    if load_dotenv is not None:
        load_dotenv()  # load from .env if present

    user = os.getenv("EMAIL_USER")
    pw = os.getenv("EMAIL_PASS")
    return user, pw

EMAIL_USER, EMAIL_PASS = _get_credentials()

# ─── Patterns ────────────────────────────────────────────────────────────────
STATUS_PATTERNS = {
    "Interview Requested": [
        re.compile(r'(schedule|availability|book|invite).*interview', re.I),
        re.compile(r'interview.*(scheduled|invite|booking)', re.I),
        re.compile(r'invitation to interview', re.I),
        re.compile(r'recruiter.*reach out', re.I),
        re.compile(r'(schedule|set up|arrange).*(call|meeting|interview)', re.I),
        re.compile(r'(phone|video|onsite).*interview', re.I),
    ],
    "Rejected": [
        re.compile(r'we will not be moving forward', re.I),
        re.compile(r'we have decided not to proceed', re.I),
        re.compile(r'we regret to inform you', re.I),
        re.compile(r'unfortunately', re.I),
        re.compile(r'we reviewed your application', re.I),
        re.compile(r'not a good fit', re.I),
        re.compile(r'better match', re.I),
        re.compile(r'better fit', re.I),
        re.compile(r'decided to proceed with a shortlist', re.I),
        re.compile(r'decided not to proceed', re.I),
        re.compile(r'regret to inform', re.I),
        re.compile(r'continue our search', re.I),
        re.compile(r'moving forward with other candidates', re.I),
        re.compile(r'not selected', re.I),
        re.compile(r'cannot move forward', re.I),
        re.compile(r'passed on your application', re.I),
    ],
    "Application Sent": [
        re.compile(r'thank you for applying', re.I),
        re.compile(r'thank you for your application', re.I),
        re.compile(r'we received your application', re.I),
        re.compile(r'your application was sent to', re.I),
        re.compile(r'you applied to', re.I),
        re.compile(r'(application|submission).*(received|submitted)', re.I),
        re.compile(r'thank you for your (interest|submission)', re.I),
    ],
}

INTERVIEW_FALSE_POSITIVES = [
    re.compile(r'what happens next', re.I),
    re.compile(r"you['’]ll hear from us", re.I),
    re.compile(r'shortlisted candidates', re.I),
    re.compile(r'you are not selected', re.I),
    re.compile(r'plan for what might occur', re.I),
]

EXCLUDED_KEYWORDS = [
    "practice starts", "lyrics", "trees in trust", "league registration",
    "burnout prevention", "unable to cancel", "spotlight on", "serenade",
    "rear of the property", "order confirmation", "unsubscribe"
]

EXCLUDED_COMPANIES = {
    "gmail", "chapelridge", "prayvine", "squaretrade", "amazon", "ottawa",
    "substack", "rallyandtap", "79246730"
}

# ─── Helpers ─────────────────────────────────────────────────────────────────
def decode_str(s):
    decoded, encoding = decode_header(s)[0]
    if isinstance(decoded, bytes):
        return decoded.decode(encoding or 'utf-8', errors='ignore')
    return decoded

def extract_text_from_email(msg):
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type == "text/plain":
                return part.get_payload(decode=True).decode(errors='ignore')
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                html = part.get_payload(decode=True).decode(errors='ignore')
                if BeautifulSoup:
                    soup = BeautifulSoup(html, "html.parser")
                    return soup.get_text()
                return re.sub(r'<[^>]+>', '', html)
    else:
        payload = msg.get_payload(decode=True).decode(errors='ignore')
        if msg.get_content_type() == "text/html":
            if BeautifulSoup:
                soup = BeautifulSoup(payload, "html.parser")
                return soup.get_text()
            return re.sub(r'<[^>]+>', '', payload)
        return payload
    return ""

def classify_email(subject, body):
    if any(pat.search(subject) or pat.search(body) for pat in INTERVIEW_FALSE_POSITIVES):
        return None
    for status, patterns in STATUS_PATTERNS.items():
        for pat in patterns:
            if pat.search(subject) or pat.search(body):
                return status
    return None

def is_irrelevant_email(subject, sender, company):
    lower_subject = subject.lower()
    lower_company = company.lower()
    if any(keyword in lower_subject for keyword in EXCLUDED_KEYWORDS):
        return True
    if lower_company in EXCLUDED_COMPANIES:
        return True
    return False

# ─── Core logic ──────────────────────────────────────────────────────────────
def process_job_emails():
    if not EMAIL_USER or not EMAIL_PASS:
        logger.error("Missing EMAIL_USER or EMAIL_PASS (Streamlit secrets or env vars).")
        return {}

    applications = {}
    three_months_ago = datetime.now(timezone.utc) - timedelta(days=90)
    try:
        # Enable IMAP debug logging
        imaplib.Debug = 4  # Maximum debug output
        
        imap_server = os.environ.get("IMAP_SERVER", "imap.gmail.com")
        logger.info(f"Connecting to IMAP server: {imap_server}")
        logger.info(f"Using email user: {EMAIL_USER}")
        mailbox = "INBOX"
        logger.info(f"Selecting mailbox: {mailbox}")
        
        with imaplib.IMAP4_SSL(imap_server) as mail:
            try:
                login_result = mail.login(EMAIL_USER, EMAIL_PASS)
                logger.info(f"Login result: {login_result}")
            except Exception as e:
                logger.error(f"Login failed: {str(e)}")
                raise

            try:
                select_result = mail.select(mailbox)
                logger.info(f"Select mailbox result: {select_result}")
            except Exception as e:
                logger.error(f"Select mailbox failed: {str(e)}")
                raise

            logger.info("📬 Scanning inbox...")
            
            # Start with the most basic IMAP command possible to verify the connection works
            logger.info("Testing basic IMAP search...")
            try:
                result, data = mail.search(None, 'ALL')
                logger.info(f"Basic search result: {result}")
                logger.info(f"Basic search data: {data}")
                if result != "OK":
                    raise Exception(f"Basic IMAP search failed with result: {result}")
            except Exception as e:
                logger.error(f"Basic IMAP search failed: {str(e)}")
                raise

            # If basic search works, try date-based search
            since_date = (datetime.now() - timedelta(days=45)).strftime('%d-%b-%Y')
            logger.info(f"Using date: {since_date}")
            
            # Convert date to INTERNAL IMAP format (DD-MMM-YYYY)
            try:
                parsed_date = datetime.strptime(since_date, '%d-%b-%Y')
                imap_date = parsed_date.strftime('%d-%b-%Y')  # Ensure proper IMAP date format
                logger.info(f"IMAP formatted date: {imap_date}")
            except Exception as e:
                logger.error(f"Date parsing failed: {str(e)}")
                raise

            success = False
            try:
                # Use the most standard IMAP SINCE syntax
                search_cmd = f'(SINCE {imap_date})'
                logger.info(f"Attempting search with: {search_cmd}")
                result, data = mail.search(None, search_cmd)
                logger.info(f"Search result: {result}")
                logger.info(f"Raw server response: {data}")
                
                if result == "OK":
                    success = True
                else:
                    raise Exception(f"IMAP SINCE search failed with result: {result}")
                    
            except Exception as e:
                logger.error(f"IMAP SINCE search failed: {str(e)}")
                # Fall back to basic search if date search fails
                logger.info("Falling back to basic search...")
                result, data = mail.search(None, 'ALL')
                if result == "OK":
                    success = True
            
            if not success:
                logger.error("All search attempts failed")
                raise Exception("Could not perform IMAP search with any syntax variation")

            if result != "OK":
                logger.error("IMAP search failed")
                return {}

            email_ids = data[0].split()
            logger.info(f"📧 Found {len(email_ids)} recent emails to check")
            if not email_ids:
                return {}

            # Sort newest first (so we can break early once older than 90d)
            try:
                id_str = ','.join(eid.decode() for eid in email_ids)
                result, header_data = mail.fetch(id_str, "(BODY.PEEK[HEADER.FIELDS (DATE)])")
                if result == "OK":
                    dates = []
                    for k in range(0, len(header_data), 2):
                        if len(header_data[k]) < 2:
                            continue
                        hdr_msg = email.message_from_bytes(header_data[k][1])
                        date_header = hdr_msg.get("Date")
                        date_obj = email.utils.parsedate_to_datetime(date_header)
                        if date_obj.tzinfo is None:
                            date_obj = date_obj.replace(tzinfo=timezone.utc)
                        dates.append(date_obj)
                    email_ids = [eid for _, eid in sorted(zip(dates, email_ids), key=lambda p: p[0], reverse=True)]
            except Exception:
                logger.exception("Failed to sort emails by date")

            BATCH_SIZE = 50
            stop_processing = False
            for i in range(0, len(email_ids), BATCH_SIZE):
                batch_ids = email_ids[i:i+BATCH_SIZE]
                id_str = ','.join(eid.decode() for eid in batch_ids)
                result, msg_data = mail.fetch(id_str, "(RFC822)")
                if result != "OK":
                    continue

                for j in range(0, len(msg_data), 2):
                    if len(msg_data[j]) < 2:
                        continue
                    full_msg = email.message_from_bytes(msg_data[j][1])
                    subject = decode_str(full_msg.get("Subject", ""))
                    sender = decode_str(full_msg.get("From", ""))
                    date_str = full_msg.get("Date")
                    try:
                        date_obj = email.utils.parsedate_to_datetime(date_str)
                        if date_obj.tzinfo is None:
                            date_obj = date_obj.replace(tzinfo=timezone.utc)
                    except Exception:
                        logger.warning("Failed to parse email date '%s'; skipping message", date_str)
                        continue
                    if date_obj < three_months_ago:
                        stop_processing = True
                        break

                    body = extract_text_from_email(full_msg)

                    status = classify_email(subject, body)
                    if not status:
                        continue

                    company = re.findall(r'@([\w.-]+)', sender)
                    company = company[0].split(".")[0].title() if company else "Unknown"
                    if is_irrelevant_email(subject, sender, company):
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

                if stop_processing:
                    break

    except Exception as e:
        logger.exception("Failed to process emails: %s", e)

    return applications

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

# ─── CLI entrypoint ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys  # added import for sys
    # Check command-line arguments for '--open-url'
    if '--open-url' in sys.argv:
        index = sys.argv.index('--open-url')
        if len(sys.argv) > index + 1:
            url = sys.argv[index + 1]
            logger.info(f"Opening URL: {url}")
            os.system(f"$BROWSER {url}")
            sys.exit(0)
        else:
            logger.error("No URL provided after '--open-url'")
            sys.exit(1)
    import os
    if os.getenv("RUN_SCANNER_ON_STARTUP", "0") == "1":
        logger.info("🚀 Starting job application tracker...")
        if not EMAIL_USER or not EMAIL_PASS:
            logger.error("❌ Set EMAIL_USER and EMAIL_PASS via Streamlit secrets or environment variables.")
        else:
            applications = process_job_emails()
            if applications:
                logger.info(f"🧪 Found {len(applications)} job applications.")
                save_to_csv(applications)
            else:
                logger.info("📭 No job application emails found.")
    else:
        logger.info("Startup scan disabled (set RUN_SCANNER_ON_STARTUP=1 to enable).")
