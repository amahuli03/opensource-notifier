import os
import sys
import requests
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone
import smtplib
from email.message import EmailMessage
from openai import OpenAI
import json
import logging

logging.basicConfig(
    filename="notifier.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%b %d, %Y %I:%M:%S %p",
)


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

    body = issue.get("body") or ""
    if len(body) > MAX_BODY_LENGTH:
        body = body[:MAX_BODY_LENGTH] + "\n\n[Truncated]"

    prompt = f"""
You are a GitHub issue triage assistant helping an {developer_profile['experience_level']} open source contributor find issues to work on.

Issue title: {issue['title']}
Issue body: {body}
Issue labels: {[label['name'] for label in issue.get('labels', [])]}

Developer profile:
- Languages: {developer_profile['languages']}
- Domains: {developer_profile['domains']}
- Comfortable areas: {developer_profile['comfortable_areas']}
- Areas to avoid: {developer_profile['avoid']}

Score the issue based on how well it matches this developer:
- relevance_score: 0-10. High (8-10) if the issue matches the developer's comfortable areas, languages, or domains. Low (0-3) if it falls under their avoid list. Consider whether the issue is a feature request, bug fix, docs improvement, or enhancement — these are preferred.
- urgency_score: 0-10. High if the issue is time-sensitive, has few comments (less competition), or is explicitly beginner/contributor-friendly.
- difficulty_score: 0-10. Low (1-3) for docs fixes, typos, simple config changes. Medium (4-6) for straightforward bug fixes, small features, adding tests. High (7-10) for complex architecture changes, deep domain expertise required, or large scope.
- summary: 1-2 sentence summary of what the issue is asking for and what a contributor would need to do.
- notify_immediately: true if relevance_score >= 8, else false.

Return ONLY JSON with keys: relevance_score, urgency_score, difficulty_score, summary, notify_immediately
"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        response_format={"type": "json_object"},
    )

    content = response.choices[0].message.content
    if not content or not content.strip():
        logging.warning(f"Empty LLM response for issue #{issue.get('number')} '{issue.get('title')}'")
        return None

    return json.loads(content.strip())

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
    "agno-agi/agno",
    "commons-app/apps-android-commons",
    "Significant-Gravitas/AutoGPT",
    "vitali87/code-graph-rag",
    "truefoundry/cognita",
    "deepchecks/deepchecks",
    "evidentlyai/evidently",
    "EvoAgentX/EvoAgentX",
    "flutter/flutter",
    "giselles-ai/giselle",
    "Cinnamon/kotaemon",
    "langchain-ai/langchain",
    "HKUDS/LightRAG",
    "run-llama/llama_deploy",
    "pydantic/logfire",
    "mem0ai/mem0",
    "mosecorg/mosec",
    "lutzroeder/netron",
    "numpy/numpy",
    "onyx-dot-app/onyx",
    "comet-ml/opik",
    "alibaba/OpenSandbox",
    "Future-House/paper-qa",
    "Arize-ai/phoenix",
    "promptfoo/promptfoo",
    "pydantic/pydantic",
    "pytorch/pytorch",
    "SciPhi-AI/R2R",
    "HKUDS/RAG-Anything",
    "AnswerDotAI/RAGatouille",
    "vibrantlabsai/ragas",
    "rust-lang/rust",
    "openai/swarm",
    "tensorflow/tensorflow",
    "huggingface/transformers",
    "trufflesecurity/trufflehog",
    "fetchai/uAgents",
    "uptrain-ai/uptrain",
    "weaviate/Verba",
]
MY_SKILLS = {
    "languages": ["Go", "Python", "SQL"],
    "experience_level": "intermediate",
    "domains": ["backend", "APIs", "infrastructure", "LLMs", "data pipelines"],
    "comfortable_areas": [
        "API development", "REST endpoints", "backend services",
        "data pipelines", "ETL", "data processing",
        "documentation improvements", "typo fixes",
        "bug fixes", "error handling",
        "feature requests", "small enhancements", "new endpoints",
        "testing", "unit tests", "test coverage",
        "configuration", "environment setup",
        "refactoring", "code cleanup",
        "CLI tools", "scripting", "automation"
    ],
    "avoid": [
        "complex compiler internals",
        "GPU/CUDA optimization",
        "UI/frontend design",
        "platform-specific mobile bugs",
        "build systems", "packaging", "CMake", "Bazel", "setuptools",
        "security", "cryptography", "auth protocols",
        "performance profiling", "benchmarking", "micro-optimizations"
    ]
}

def fetch_issues(repo, since):
    url = f"https://api.github.com/repos/{repo}/issues"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json"
    }
    params = {
        "state": "open",
        "since": since.isoformat(),
        "per_page": 100,
    }

    response = requests.get(url, headers=headers, params=params)
    response.raise_for_status()
    return response.json()

def check_issues():
    """Runs every 15 minutes. Sends urgent issues immediately, accumulates non-urgent ones."""
    last_check = get_last_check()
    now = datetime.now(timezone.utc)
    pending = load_pending_digest()
    fmt = "%b %d, %Y %I:%M:%S %p %Z"
    logging.info(f"=== check_issues START | last_check={last_check.astimezone().strftime(fmt)} now={now.astimezone().strftime(fmt)} ===")

    for repo in REPOS:
        print(f"\nFetching issues for {repo}...")
        issues = fetch_issues(repo, last_check)

        for issue in issues:
            if "pull_request" in issue:
                continue

            created_at = datetime.fromisoformat(issue["created_at"].replace("Z", "+00:00"))
            if created_at <= last_check:
                continue

            labels = [label["name"].lower() for label in issue.get("labels", [])]
            skip_labels = {
                "frontend", "ui", "ux", "css", "design",
                "cuda", "gpu",
                "security", "cve", "vulnerability",
                "build", "cmake", "bazel", "packaging", "setuptools",
            }
            if skip_labels & set(labels):
                logging.info(f"SKIPPING issue #{issue['number']} '{issue['title']}' from {repo} (labels: {labels})")
                continue

            score = score_issue(issue, MY_SKILLS)
            if score is None:
                continue

            easy_tags = [l for l in labels if l in ("good first issue", "easy")]

            created_str = created_at.astimezone().strftime("%b %d, %Y %I:%M %p %Z")
            tag_line = f"Tags: {', '.join(easy_tags)}\n" if easy_tags else ""
            issue_text = f"{issue['title']}\n{issue['html_url']}\nCreated: {created_str}\n{tag_line}Summary: {score['summary']}\nUrgency: {score['urgency_score']}\nRelevance: {score['relevance_score']}\nDifficulty: {score['difficulty_score']}\n"

            if easy_tags:
                logging.info(f"EMAILING easy issue #{issue['number']} '{issue['title']}' from {repo} (created={created_str})")
                send_email(
                    subject=f"🏷️ Easy Issue: {issue['title']}",
                    body=f"{repo}\n{issue_text}"
                )
            elif score["notify_immediately"]:
                logging.info(f"EMAILING urgent issue #{issue['number']} '{issue['title']}' from {repo} (created={created_str})")
                send_email(
                    subject=f"🚨 Urgent GitHub Issue: {issue['title']}",
                    body=f"{repo}\n{issue_text}"
                )
            else:
                pending.append({"repo": repo, "text": issue_text})

            print("-----")
            print("Title:", issue["title"])
            print("URL:", issue["html_url"])
            print("Created:", issue["created_at"])

    update_last_check(now)
    save_pending_digest(pending)
    logging.info(f"=== check_issues END | updated last_check to {now.astimezone().strftime(fmt)} | {len(pending)} pending ===")
    print(f"\n[{now.isoformat()}] Check complete. {len(pending)} issues pending for digest.")


def send_daily_digest():
    """Runs once daily at 9:00 AM. Sends all accumulated non-urgent issues."""
    pending = load_pending_digest()
    if not pending:
        print("No pending issues for digest.")
        return

    grouped = {}
    for item in pending:
        repo = item["repo"]
        grouped.setdefault(repo, []).append(item["text"])

    sections = []
    for repo, issues in grouped.items():
        section = f"=== {repo} ===\n\n" + "\n\n".join(issues)
        sections.append(section)

    body = "\n\n\n".join(sections)
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
