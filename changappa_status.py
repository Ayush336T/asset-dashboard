import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone


DEVREV_TOKEN = os.environ.get("DEVREV_UBER_PAT") or os.environ.get("DEVREV_PAT", "")
OKTA_DOMAIN = os.environ.get("OKTA_DOMAIN", "devrev.okta.com")
OKTA_TOKEN = os.environ.get("OKTA_API_TOKEN", "")
UBER_APP_LABEL = os.environ.get("OKTA_UBER_APP_LABEL", "Uber for Business")
EMAIL_DOMAIN = os.environ.get("EMPLOYEE_EMAIL_DOMAIN", "devrev.ai")
CHANGAPPA_EMAIL = os.environ.get("CHANGAPPA_EMAIL", "changappa.s@devrev.ai")

HTTP_TIMEOUT = 20

TICKET_TYPES = [
    ("Deactivating Uber Account", "uber"),
    ("Cab Deactivation", "cab"),
    ("ID Card/Biometric Access", "id_card"),
]


def devrev_request(path, body=None, method="POST"):
    url = f"https://api.devrev.ai/{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", DEVREV_TOKEN)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read())


def okta_request(path, method="GET"):
    url = f"https://{OKTA_DOMAIN}{path}"
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", f"SSWS {OKTA_TOKEN}")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception as e:
        print(f"[warn] okta {method} {path} failed: {e}")
        return 0, None


def find_changappa_id():
    try:
        data = devrev_request(
            f"dev-users.list?email={urllib.parse.quote(CHANGAPPA_EMAIL)}",
            method="GET",
        )
    except Exception as e:
        print(f"[warn] could not look up Changappa via dev-users.list: {e}")
        return None
    users = data.get("dev_users", [])
    if not users:
        return None
    return users[0].get("id")


def categorize(title):
    for needle, key in TICKET_TYPES:
        if needle in title:
            return key
    return None


def get_changappa_tickets(owner_id):
    issues = []
    total_scanned = 0
    employee_titles = []
    cursor = None
    pages = 0
    while pages < 50:
        body = {
            "type": ["issue", "ticket", "task"],
            "limit": 100,
        }
        if owner_id:
            body["owned_by"] = [owner_id]
        if cursor:
            body["cursor"] = cursor
        data = devrev_request("works.list", body)
        works = data.get("works", [])
        total_scanned += len(works)
        for w in works:
            title = w.get("title", "")
            tl = title.lower()
            if "employee name:" in tl or "deactivat" in tl or "uber" in tl or "id card" in tl:
                employee_titles.append((w.get("display_id", ""), title[:100]))
            kind = categorize(title)
            if kind:
                w["_kind"] = kind
                issues.append(w)
        cursor = data.get("next_cursor")
        pages += 1
        if not cursor:
            break
    print(f"Scanned {total_scanned} works across {pages} page(s); matched {len(issues)} (uber/cab/id-card)")
    print(f"[debug] {len(employee_titles)} works whose title looks offboarding-related:")
    for did, title in employee_titles[:30]:
        print(f"  - {did}: {title}")
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
    return f"{'.'.join(parts)}@{EMAIL_DOMAIN}"


def check_uber_status(app_id, email):
    user_path = f"/api/v1/users/{urllib.parse.quote(email)}"
    status, user = okta_request(user_path)
    if status == 404 or not user:
        return {"okta_user_found": False, "okta_user_status": None, "uber_assigned": False}
    user_id = user.get("id")
    a_status, _ = okta_request(f"/api/v1/apps/{app_id}/users/{user_id}")
    return {
        "okta_user_found": True,
        "okta_user_status": user.get("status", ""),
        "uber_assigned": a_status == 200,
    }


def derive_status(kind, stage_name, stage_state, okta_info):
    if stage_state == "closed" or stage_name in ("done", "resolved"):
        return "ticket_closed"
    if kind != "uber" or okta_info is None:
        return "pending"
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
        raise SystemExit("DEVREV_PAT (or DEVREV_UBER_PAT) not set")

    owner_id = find_changappa_id()
    if owner_id:
        print(f"Filtering by Changappa's DevRev id: {owner_id}")
    else:
        print("[warn] Changappa not found in DevRev; falling back to unfiltered scan")

    app_id = find_uber_app_id()
    if not app_id:
        print(f"[warn] Could not find Okta app '{UBER_APP_LABEL}'; Uber assignment checks will be skipped")

    tickets = get_changappa_tickets(owner_id)
    rows = []
    for idx, t in enumerate(tickets, 1):
        title = t.get("title", "")
        kind = t.get("_kind")
        employee = title.replace("Employee Name: ", "").split(" - ")[0].strip()
        email = name_to_email(employee)
        stage_name = t.get("stage", {}).get("name", "")
        stage_state = t.get("stage", {}).get("state", {}).get("name", "")
        is_closed = stage_state == "closed" or stage_name in ("done", "resolved")

        print(f"[{idx}/{len(tickets)}] {t.get('display_id', '')} ({kind}) {title[:60]}")

        okta_info = None
        if kind == "uber" and not is_closed and app_id and email:
            okta_info = check_uber_status(app_id, email)

        rows.append({
            "id": t.get("display_id", ""),
            "kind": kind,
            "employee": employee,
            "email": email,
            "stage": stage_name,
            "stage_state": stage_state,
            "created": t.get("created_date"),
            "lwd": t.get("target_close_date"),
            "assignee": ", ".join(o.get("full_name", "") for o in t.get("owned_by", [])),
            "okta_user_found": okta_info["okta_user_found"] if okta_info else None,
            "okta_user_status": okta_info["okta_user_status"] if okta_info else None,
            "uber_assigned": okta_info["uber_assigned"] if okta_info else None,
            "status": derive_status(kind, stage_name, stage_state, okta_info),
        })

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "okta_domain": OKTA_DOMAIN,
        "uber_app_label": UBER_APP_LABEL,
        "uber_app_id": app_id,
        "owner_email": CHANGAPPA_EMAIL,
        "owner_id": owner_id,
        "tickets": rows,
    }
    with open("changappa_status.json", "w") as f:
        json.dump(out, f, indent=2)
    print(f"Wrote {len(rows)} ticket(s) to changappa_status.json")


if __name__ == "__main__":
    main()
