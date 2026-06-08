import json
import os
import urllib.request
from datetime import datetime, timezone, timedelta


DEVREV_TOKEN = os.environ.get("DEVREV_PAT", "")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")


def devrev_request(path, body):
    url = f"https://api.devrev.ai/{path}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", DEVREV_TOKEN)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def send_slack(message):
    payload = json.dumps({"text": message}).encode()
    req = urllib.request.Request(
        SLACK_WEBHOOK_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req)
    except Exception as e:
        print(f"Slack notification failed: {e}")


def get_asset_collection_issues():
    issues = []
    cursor = None
    while True:
        body = {"type": ["issue"], "limit": 100}
        if cursor:
            body["cursor"] = cursor
        data = devrev_request("works.list", body)
        for w in data.get("works", []):
            if "Asset Collection" in w.get("title", ""):
                issues.append(w)
        cursor = data.get("next_cursor")
        if not cursor:
            break
    return issues


def main():
    print("Checking asset collection issues for upcoming LWDs...")
    issues = get_asset_collection_issues()
    today = datetime.now(timezone.utc).date()
    tomorrow = today + timedelta(days=1)
    day_after = today + timedelta(days=2)

    alerts = []
    for issue in issues:
        stage = issue.get("stage", {}).get("name", "")
        if stage in ("done", "resolved", "closed"):
            continue

        lwd = issue.get("target_close_date")
        if not lwd:
            continue

        lwd_date = datetime.fromisoformat(lwd.replace("Z", "+00:00")).date()
        employee = issue.get("title", "").replace("Employee Name: ", "").replace(" - Asset Collection", "")
        issue_id = issue.get("display_id", "")
        assignee = ", ".join(o.get("full_name", "") for o in issue.get("owned_by", []))

        if lwd_date == tomorrow:
            alerts.append({
                "urgency": ":rotating_light:",
                "label": "TOMORROW",
                "employee": employee,
                "issue_id": issue_id,
                "lwd": lwd_date.strftime("%b %d"),
                "assignee": assignee,
            })
        elif lwd_date == today:
            alerts.append({
                "urgency": ":fire:",
                "label": "TODAY",
                "employee": employee,
                "issue_id": issue_id,
                "lwd": lwd_date.strftime("%b %d"),
                "assignee": assignee,
            })
        elif lwd_date < today:
            days_overdue = (today - lwd_date).days
            alerts.append({
                "urgency": ":warning:",
                "label": f"OVERDUE ({days_overdue}d)",
                "employee": employee,
                "issue_id": issue_id,
                "lwd": lwd_date.strftime("%b %d"),
                "assignee": assignee,
            })
        elif lwd_date <= day_after:
            alerts.append({
                "urgency": ":clock3:",
                "label": "IN 2 DAYS",
                "employee": employee,
                "issue_id": issue_id,
                "lwd": lwd_date.strftime("%b %d"),
                "assignee": assignee,
            })

    if not alerts:
        print("No upcoming LWDs. No notifications sent.")
        return

    # Sort: today first, then tomorrow, then overdue
    priority = {"TODAY": 0, "TOMORROW": 1, "IN 2 DAYS": 2}
    alerts.sort(key=lambda a: priority.get(a["label"], 3))

    msg = ":clipboard: *Asset Collection Reminders*\n\n"
    for a in alerts:
        msg += (
            f"{a['urgency']} *{a['label']}* — {a['employee']} "
            f"(LWD: {a['lwd']}) | `{a['issue_id']}` | Assignee: {a['assignee']}\n"
            f"   → Collect laptop & close the ticket\n\n"
        )

    msg += f"_Total pending: {len(issues)} issues_"
    send_slack(msg)
    print(f"Sent {len(alerts)} alert(s) to Slack.")


if __name__ == "__main__":
    main()
