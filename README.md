# 📬 Job Application Tracker

Automatically scans your Gmail inbox to detect job application updates and classifies them as:
- ✅ Application Sent
- ❌ Rejected
- 📅 Interview Requested

It outputs the results to a CSV file.

---

## 🛠️ Setup

### 1. Clone this Repository

```bash
git clone https://github.com/yourusername/job-application-tracker.git
cd job-application-tracker
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

---

### 3. Configure Gmail Credentials

Create a `.env` file in the root directory with the following content:

```
EMAIL_USER=your_gmail_address@gmail.com
EMAIL_PASS=your_app_password_here
```

> 🔐 **Important:** Use a [Gmail App Password](https://support.google.com/accounts/answer/185833) — not your actual Gmail password — especially if you have 2FA enabled.

---

## 🚀 Usage

Run the script:

```bash
python job_application_tracker.py
```

This will generate `job_applications.csv` with the latest statuses.

---

## 🧠 Features

- Classifies emails into:
  - `Application Sent`
  - `Rejected`
  - `Interview Requested`
- Scans up to 90 days of email history
- Ignores known false positives
- Excludes companies like LinkedIn or Gmail notifications
- Outputs results to CSV

---

## 🧾 Example Output (CSV)

| Company | Job Title | Date Applied | Current Status | Last Update | Email Subject |
|---------|-----------|--------------|----------------|-------------|----------------|
| Acme    | Data Analyst | 2025-07-18 | Interview Requested | 2025-07-20 | Invitation to Interview |

---

## 🗂 File Structure

```
job-application-tracker/
├── job_application_tracker.py
├── .env.example              # Template only
├── job_applications.csv      # Output (ignored in .gitignore)
├── requirements.txt
├── README.md
└── .gitignore
```

---

## 🙈 .gitignore

```gitignore
.env
client_secret.json
__pycache__/
*.pyc
job_applications.csv
```

---

## 🛡️ Disclaimer

This tool is for personal productivity use only. Always review any automated classifications before acting on them.
