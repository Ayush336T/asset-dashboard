import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone


DEVREV_TOKEN = os.environ.get("DEVREV_PAT", "")
OKTA_DOMAIN = os.environ.get("OKTA_DOMAIN", "devrev.okta.com")
OKTA_TOKEN = os.environ.get("OKTA_API_TOKEN", "")
UBER_APP_LABEL = os.environ.get("OKTA_UBER_APP_LABEL", "Uber for Business")
EMAIL_DOMAIN = os.environ.get("EMPLOYEE_EMAIL_DOMAIN", "devrev.ai")


def devrev_request(path, body):
    url = f"https://api.devrev.ai/{path}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", DEVREV_TOKEN)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def okta_request(path, method="GET"):
    url = f"https://{OKTA_DOMAIN}{path}"
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", f"SSWS {OKTA_TOKEN}")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, None


def get_uber_tickets():
    issues = []
    cursor = None
    while True:
        body = {"type": ["issue"], "limit": 100}
        if cursor:
            body["cursor"] = cursor
        data = devrev_request("works.list", body)
        for w in data.get("works", []):
            title = w.get("title", "")
            if "Deactivating Uber Account" in title:
                issues.append(w)
        cursor = data.get("next_cursor")
        if not cursor:
            break
    return issues


def find_uber_app_id():
    path = f"/api/v1/apps?q={urllib.parse.quote(UBER_APP_LABEL)}&limit=20"
    status, data = okta_request(path)
    if status != 200 or not data:
        return None
    for app in data:
        if app.get("label", "").lower() == UBER_APP_LABEL.lower():
            return app.get("id")
    return data[0].get("id") if data else None


def name_to_email(full_name):
    parts = [p for p in full_name.strip().lower().split() if p]
    if not parts:
        return None
    local = ".".join(parts)
    return f"{local}@{EMAIL_DOMAIN}"


def check_user_status(app_id, email):
    user_path = f"/api/v1/users/{urllib.parse.quote(email)}"
    status, user = okta_request(user_path)
    if status == 404 or not user:
        return {"okta_user_found": False, "okta_user_status": None, "uber_assigned": False}

    okta_status = user.get("status", "")
    user_id = user.get("id")
    assignment_path = f"/api/v1/apps/{app_id}/users/{user_id}"
    a_status, _ = okta_request(assignment_path)
    return {
        "okta_user_found": True,
        "okta_user_status": okta_status,
        "uber_assigned": a_status == 200,
    }


def derive_status(stage_name, stage_state, okta_info):
    if stage_state == "closed" or stage_name in ("done", "resolved"):
        return "ticket_closed"
    if not okta_info["okta_user_found"]:
        return "user_not_in_okta"
    if okta_info["okta_user_status"] in ("DEPROVISIONED", "SUSPENDED"):
        return "user_deactivated_in_okta"
    if not okta_info["uber_assigned"]:
        return "uber_unassigned"
    return "uber_still_assigned"


def main():
    if not OKTA_TOKEN:
        raise SystemExit("OKTA_API_TOKEN not set")
    if not DEVREV_TOKEN:
        raise SystemExit("DEVREV_PAT not set")

    app_id = find_uber_app_id()
    if not app_id:
        raise SystemExit(f"Could not find Okta app matching '{UBER_APP_LABEL}'")

    tickets = get_uber_tickets()
    rows = []
    for t in tickets:
        title = t.get("title", "")
        employee = title.replace("Employee Name: ", "").split(" - ")[0].strip()
        email = name_to_email(employee)
        stage_name = t.get("stage", {}).get("name", "")
        stage_state = t.get("stage", {}).get("state", {}).get("name", "")

        if stage_state == "closed" or stage_name in ("done", "resolved"):
            okta_info = {"okta_user_found": None, "okta_user_status": None, "uber_assigned": None}
        else:
            okta_info = check_user_status(app_id, email) if email else {
                "okta_user_found": False, "okta_user_status": None, "uber_assigned": False
            }

        rows.append({
            "id": t.get("display_id", ""),
            "employee": employee,
            "email": email,
            "stage": stage_name,
            "stage_state": stage_state,
            "created": t.get("created_date"),
            "lwd": t.get("target_close_date"),
            "assignee": ", ".join(o.get("full_name", "") for o in t.get("owned_by", [])),
            "okta_user_found": okta_info["okta_user_found"],
            "okta_user_status": okta_info["okta_user_status"],
            "uber_assigned": okta_info["uber_assigned"],
            "status": derive_status(stage_name, stage_state, okta_info),
        })

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "okta_domain": OKTA_DOMAIN,
        "uber_app_label": UBER_APP_LABEL,
        "uber_app_id": app_id,
        "tickets": rows,
    }
    with open("uber_status.json", "w") as f:
        json.dump(out, f, indent=2)
    print(f"Wrote {len(rows)} ticket(s) to uber_status.json")


if __name__ == "__main__":
    main()
