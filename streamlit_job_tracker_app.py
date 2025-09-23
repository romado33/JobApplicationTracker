# streamlit_job_tracker_app.py
# Streamlit UI for the Gmail-based Job Application Tracker
#
# Run:
#   streamlit run streamlit_job_tracker_app.py
#
# Secrets (TOML):
#   EMAIL_USER = "youraddress@gmail.com"
#   EMAIL_PASS = "your_16_char_app_password"

import os
import re
import pandas as pd
import streamlit as st

# ── Import the engine module (job_application_tracker.py) dynamically ────────────
import importlib.util, sys, pathlib
ENGINE_PATHS = [
    "job_application_tracker.py",
    str(pathlib.Path(__file__).parent / "job_application_tracker.py"),
    "/mnt/data/job_application_tracker.py",
]
engine = None
for p in ENGINE_PATHS:
    if os.path.exists(p):
        spec = importlib.util.spec_from_file_location("job_application_tracker", p)
        engine = importlib.util.module_from_spec(spec)
        sys.modules["job_application_tracker"] = engine
        spec.loader.exec_module(engine)  # type: ignore
        break
if engine is None:
    st.error("Could not import job_application_tracker.py. Place it next to this file.")
    st.stop()

st.set_page_config(page_title="Job Application Tracker", page_icon="📬", layout="wide")
st.title("📬 Job Application Tracker")
st.caption("Scan Gmail for job application emails, classify their status, and export a CSV.")

# ── Secrets status ──────────────────────────────────────────────────────────────
email_user = st.secrets.get("EMAIL_USER", "")
email_pass = st.secrets.get("EMAIL_PASS", "")
if email_user and email_pass:
    st.sidebar.success("✅ Gmail credentials loaded from Streamlit secrets")
else:
    st.sidebar.error("⚠ Add EMAIL_USER and EMAIL_PASS in your Streamlit secrets (TOML).")

# ── Scan settings ───────────────────────────────────────────────────────────────
st.sidebar.header("⚙️ Scan Settings")
lookback_days = st.sidebar.slider("Look back (days)", 15, 120, 60, 5)

# Use standard IMAP search instead of X-GM-RAW for better compatibility
search_method = st.sidebar.selectbox(
    "Search Method", 
    ["Standard IMAP", "Gmail Advanced (X-GM-RAW)"],
    help="Standard IMAP is more reliable, Gmail Advanced allows complex queries"
)

if search_method == "Gmail Advanced (X-GM-RAW)":
    # Simplified default query for X-GM-RAW
    raw_query_default = f'newer_than:{lookback_days}d (subject:application OR subject:interview OR subject:regret OR subject:thank)'
    raw_query = st.sidebar.text_area("Gmail search query", value=raw_query_default, height=100)
else:
    st.sidebar.info("Using standard IMAP search with date filtering")
    raw_query = None

batch_size = st.sidebar.slider("IMAP batch size", 50, 500, 200, 50)
max_messages = st.sidebar.slider("Max messages to process", 200, 5000, 1500, 100)
mailbox = st.sidebar.text_input('Gmail mailbox', value='[Gmail]/All Mail')

# ── Scanner runner (uses engine creds + helpers) ────────────────────────────────
def run_scan():
    import imaplib, email as email_pkg
    from datetime import timezone, timedelta

    if not engine.EMAIL_USER or not engine.EMAIL_PASS:
        st.error("Missing EMAIL_USER or EMAIL_PASS (Streamlit secrets or env).")
        return {}

    apps = {}
    try:
        with imaplib.IMAP4_SSL("imap.gmail.com") as mail:
            mail.login(engine.EMAIL_USER, engine.EMAIL_PASS)
            mail.select(f'"{mailbox}"')
            
            # Use different search strategies based on method
            if search_method == "Gmail Advanced (X-GM-RAW)" and raw_query:
                # Clean up the query - remove problematic characters and format properly
                clean_query = raw_query.strip()
                # Don't escape quotes for X-GM-RAW - Gmail handles them
                st.write(f"🔍 Using Gmail Advanced search: `{clean_query}`")
                result, data = mail.search(None, 'X-GM-RAW', f'"{clean_query}"')
            else:
                # Use standard IMAP search with date filtering
                since_date = (pd.Timestamp.now() - pd.Timedelta(days=lookback_days)).strftime('%d-%b-%Y')
                st.write(f"🔍 Using standard IMAP search since: {since_date}")
                result, data = mail.search(None, 'SINCE', since_date)
            
            if result != "OK":
                st.error(f"IMAP search failed with result: {result}")
                return {}

            email_ids = data[0].split()
            st.write(f"📧 Candidates: {len(email_ids)}")
            if not email_ids:
                return {}

            processed = 0
            prog = st.progress(0)
            total = max(len(email_ids), 1)

            for i in range(0, len(email_ids), batch_size):
                batch_ids = email_ids[i:i+batch_size]
                id_str = ",".join(eid.decode() for eid in batch_ids)
                result, msg_data = mail.fetch(id_str, "(RFC822)")
                if result != "OK":
                    continue

                for j in range(0, len(msg_data), 2):
                    if len(msg_data[j]) < 2:
                        continue
                    full_msg = email_pkg.message_from_bytes(msg_data[j][1])
                    subject = engine.decode_str(full_msg.get("Subject", ""))
                    sender = engine.decode_str(full_msg.get("From", ""))
                    date_str = full_msg.get("Date")
                    try:
                        date_obj = email_pkg.utils.parsedate_to_datetime(date_str)
                        if date_obj.tzinfo is None:
                            date_obj = date_obj.replace(tzinfo=timezone.utc)
                    except Exception:
                        continue

                    # Apply additional filtering when using standard IMAP search
                    if search_method == "Standard IMAP":
                        # Check if email is job-related by subject/content
                        job_keywords = ['application', 'interview', 'applied', 'position', 'job', 'career', 'regret', 'thank you for applying', 'we received your']
                        if not any(keyword in subject.lower() for keyword in job_keywords):
                            continue

                    body = engine.extract_text_from_email(full_msg)
                    status = engine.classify_email(subject, body)
                    if not status:
                        continue

                    company = re.findall(r'@([\w.-]+)', sender)
                    company = company[0].split(".")[0].title() if company else "Unknown"
                    if engine.is_irrelevant_email(subject, sender, company):
                        continue

                    job_title = subject.split(" at ")[-1] if " at " in subject else subject
                    key = (company, job_title)
                    if key not in apps or date_obj > apps[key]["last_update"]:
                        apps[key] = {
                            "company": company,
                            "job_title": job_title.strip(),
                            "status": status,
                            "date_applied": date_obj.strftime("%Y-%m-%d"),
                            "last_update": date_obj,
                            "subject": subject,
                        }

                    processed += 1
                    if processed >= max_messages:
                        st.info(f"ℹ Stopping early at {processed} messages (limit). Refine your query for more.")
                        break

                prog.progress(min((i + len(batch_ids)) / total, 1.0))
                if processed >= max_messages:
                    break

    except Exception as e:
        st.error(f"Scan error: {e}")
        st.exception(e)  # Show full traceback for debugging
        return {}

    return apps

# ── Actions ──────────────────────────────────────────────────────────────────────
col1, col2 = st.columns(2)
with col1:
    if st.button("🔌 Test Gmail Login", use_container_width=True):
        import imaplib
        try:
            with imaplib.IMAP4_SSL("imap.gmail.com") as mail:
                mail.login(engine.EMAIL_USER, engine.EMAIL_PASS)
            st.success("IMAP login successful ✅")
        except Exception as e:
            st.error(f"IMAP login failed: {e}")

with col2:
    run_now = st.button("🚀 Run Scan", use_container_width=True)

if "df" not in st.session_state:
    st.session_state["df"] = pd.DataFrame()

if run_now:
    apps = run_scan()
    if apps:
        df = pd.DataFrame([
            {
                "Company": v["company"],
                "Job Title": v["job_title"],
                "Date Applied": v["date_applied"],
                "Current Status": v["status"],
                "Last Update": v["last_update"].strftime("%Y-%m-%d"),
                "Email Subject": v["subject"],
            } for v in apps.values()
        ]).sort_values(["Last Update", "Company"], ascending=[False, True])
        st.session_state["df"] = df
        st.success(f"Found {len(df)} applications.")
    else:
        st.session_state["df"] = pd.DataFrame()
        st.info("No job application emails found for your query.")

df = st.session_state["df"]
if not df.empty:
    st.subheader("Results")
    left, right = st.columns([3, 1])

    with right:
        statuses = sorted(df["Current Status"].unique())
        sel_status = st.multiselect("Status filter", statuses, default=statuses)
        company_filter = st.text_input("Company contains")
        title_filter = st.text_input("Job title contains")

    view = df.copy()
    if sel_status:
        view = view[view["Current Status"].isin(sel_status)]
    if company_filter.strip():
        view = view[view["Company"].str.contains(company_filter, case=False, na=False)]
    if title_filter.strip():
        view = view[view["Job Title"].str.contains(title_filter, case=False, na=False)]

    with left:
        st.dataframe(view, use_container_width=True, height=520)

    csv_bytes = view.to_csv(index=False).encode("utf-8")
    st.download_button("💾 Download CSV", data=csv_bytes, file_name="job_applications.csv", mime="text/csv")

st.markdown("---")
st.caption("Entry point: streamlit_job_tracker_app.py — engine: job_application_tracker.py")
