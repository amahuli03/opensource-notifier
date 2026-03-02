import os
import sys
import requests
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone
import smtplib
from email.message import EmailMessage
from openai import OpenAI
import json


load_dotenv()

EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

LAST_CHECK_FILE = "last_check.txt"
PENDING_DIGEST_FILE = "pending_digest.json"
MAX_BODY_LENGTH = 2000  # Max characters for issue body in prompt


def load_pending_digest():
    if not os.path.exists(PENDING_DIGEST_FILE):
        return []
    with open(PENDING_DIGEST_FILE, "r") as f:
        content = f.read().strip()
        if not content:
            return []
        return json.loads(content)


def save_pending_digest(issues):
    with open(PENDING_DIGEST_FILE, "w") as f:
        json.dump(issues, f, indent=2)


def clear_pending_digest():
    save_pending_digest([])

def get_last_check(default_hours=24):
    if not os.path.exists(LAST_CHECK_FILE):
        # First run: fetch issues from last `default_hours`
        return datetime.now(timezone.utc) - timedelta(hours=default_hours)
    
    with open(LAST_CHECK_FILE, "r") as f:
        content = f.read().strip()
        if not content:
            # Empty file, treat as first run
            return datetime.now(timezone.utc) - timedelta(hours=default_hours)
        return datetime.fromisoformat(content)
def update_last_check(time):
    with open(LAST_CHECK_FILE, "w") as f:
        f.write(time.isoformat())

def score_issue(issue, developer_profile):
    import json

    body = issue.get("body", "")
    if len(body) > MAX_BODY_LENGTH:
        body = body[:MAX_BODY_LENGTH] + "\n\n[Truncated]"

    prompt = f"""
You are a GitHub issue triage assistant.

Issue title: {issue['title']}
Issue body: {body}
Developer profile: {developer_profile}

Score the issue:
- urgency_score: 0-10
- relevance_score: 0-10
- summary: 1-2 sentence summary
- notify_immediately: true if urgency >=7 or relevance >=8, else false

Return ONLY JSON with keys: urgency_score, relevance_score, summary, notify_immediately
"""

    response = client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}],
        temperature=0
    )

    return json.loads(response.choices[0].message.content.strip())

def send_email(subject, body, to=EMAIL_ADDRESS):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = EMAIL_ADDRESS
    msg["To"] = to
    msg.set_content(body)

    # Gmail SMTP
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        smtp.send_message(msg)


REPOS = [
    "langchain-ai/langchain",
    "rust-lang/rust",
    "huggingface/transformers",
    "mem0ai/mem0",
    "numpy/numpy",
    "tensorflow/tensorflow",
    "pytorch/pytorch",
    "commons-app/apps-android-commons",
    "flutter/flutter"
]
# TODO: Add more skills and tools
MY_SKILLS = {
    "languages": ["Go", "Python", "SQL"],
    "domains": ["backend", "APIs", "infrastructure", "LLMs", "data pipelines"],
    "comfortable_areas": [
        "API development", "REST endpoints", "backend services",
        "data pipelines", "ETL", "data processing",
        "documentation improvements", "typo fixes",
        "bug fixes", "error handling",
        "simple feature requests", "small enhancements",
        "testing", "unit tests", "test coverage",
        "configuration", "environment setup",
        "refactoring", "code cleanup"
    ],
    "avoid": [
    "complex compiler internals",
    "GPU/CUDA optimization",
    "UI/frontend design",
    "platform-specific mobile bugs"
    ]
}

def fetch_issues(repo):
    url = f"https://api.github.com/repos/{repo}/issues"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json"
    }
    params = {
        "state": "open"
    }

    response = requests.get(url, headers=headers, params=params)
    response.raise_for_status()
    return response.json()

def check_issues():
    """Runs every 15 minutes. Sends urgent issues immediately, accumulates non-urgent ones."""
    last_check = get_last_check()
    now = datetime.now(timezone.utc)
    pending = load_pending_digest()

    for repo in REPOS:
        print(f"\nFetching issues for {repo}...")
        issues = fetch_issues(repo)

        for issue in issues:
            if "pull_request" in issue:
                continue

            created_at = datetime.fromisoformat(issue["created_at"].replace("Z", "+00:00"))
            if created_at <= last_check:
                continue

            score = score_issue(issue, MY_SKILLS)

            labels = [label["name"].lower() for label in issue.get("labels", [])]
            easy_tags = [l for l in labels if l in ("good first issue", "easy")]

            created_str = created_at.astimezone().strftime("%b %d, %Y %I:%M %p %Z")
            tag_line = f"Tags: {', '.join(easy_tags)}\n" if easy_tags else ""
            issue_text = f"{repo}\n{issue['title']}\n{issue['html_url']}\nCreated: {created_str}\n{tag_line}Summary: {score['summary']}\nUrgency: {score['urgency_score']}\nRelevance: {score['relevance_score']}\n"

            if easy_tags:
                send_email(
                    subject=f"🏷️ Easy Issue: {issue['title']}",
                    body=issue_text
                )
            elif score["notify_immediately"]:
                send_email(
                    subject=f"🚨 Urgent GitHub Issue: {issue['title']}",
                    body=issue_text
                )
            else:
                pending.append(issue_text)

            print("-----")
            print("Title:", issue["title"])
            print("URL:", issue["html_url"])
            print("Created:", issue["created_at"])

    update_last_check(now)
    save_pending_digest(pending)
    print(f"\n[{now.isoformat()}] Check complete. {len(pending)} issues pending for digest.")


def send_daily_digest():
    """Runs once daily at 9:00 AM. Sends all accumulated non-urgent issues."""
    pending = load_pending_digest()
    if not pending:
        print("No pending issues for digest.")
        return

    body = "\n\n".join(pending)
    send_email(
        subject=f"GitHub Issue Digest ({len(pending)} new issues)",
        body=body
    )
    clear_pending_digest()
    print(f"Digest sent with {len(pending)} issues.")


def main():
    if len(sys.argv) < 2:
        print("Usage: python main.py <check|digest>")
        sys.exit(1)

    command = sys.argv[1]
    if command == "check":
        check_issues()
    elif command == "digest":
        send_daily_digest()
    else:
        print(f"Unknown command: {command}")
        print("Usage: python main.py <check|digest>")
        sys.exit(1)


if __name__ == "__main__":
    main()
