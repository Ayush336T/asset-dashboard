import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone


HTTP_TIMEOUT = 20

ORGS = [
    {"slug": "peopleops", "label": "People Ops", "token_env": "DEVREV_PAT_PEOPLEOPS"},
    {"slug": "devrev", "label": "DevRev", "token_env": "DEVREV_PAT_DEVREV"},
]


def devrev_request(token, path, body=None, method="POST"):
    url = f"https://api.devrev.ai/{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", token)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read())


def find_self_id(token):
    data = devrev_request(token, "dev-users.self", method="GET")
    return data.get("dev_user", {}).get("id")


def get_open_tickets(token, owner_id):
    issues = []
    cursor = None
    pages = 0
    while pages < 50:
        body = {
            "type": ["issue", "ticket", "task"],
            "limit": 100,
            "owned_by": [owner_id],
        }
        if cursor:
            body["cursor"] = cursor
        data = devrev_request(token, "works.list", body)
        for w in data.get("works", []):
            if _is_closed(w):
                continue
            issues.append(w)
        cursor = data.get("next_cursor")
        pages += 1
        if not cursor:
            break
    return issues


def get_open_asset_collection_tickets(token):
    issues = []
    cursor = None
    pages = 0
    while pages < 50:
        body = {"type": ["issue", "ticket", "task"], "limit": 100}
        if cursor:
            body["cursor"] = cursor
        data = devrev_request(token, "works.list", body)
        for w in data.get("works", []):
            if "asset collection" not in w.get("title", "").lower():
                continue
            if _is_closed(w):
                continue
            issues.append(w)
        cursor = data.get("next_cursor")
        pages += 1
        if not cursor:
            break
    return issues


def _is_closed(w):
    if w.get("actual_close_date"):
        return True
    stage_name = w.get("stage", {}).get("name", "")
    stage_state = w.get("stage", {}).get("state", {}).get("name", "")
    return stage_state == "closed" or stage_name in ("done", "resolved")


def categorize(title):
    t = title.lower()
    if "deactivating uber" in t or "uber account" in t:
        return "uber"
    if "cab deactivation" in t:
        return "cab"
    if "id card" in t or "biometric" in t:
        return "id_card"
    if "asset collection" in t:
        return "asset"
    return "other"


def main():
    rows = []
    seen_ids = set()
    org_summaries = []
    for org in ORGS:
        token = os.environ.get(org["token_env"], "")
        if not token:
            print(f"[skip] {org['slug']}: ${org['token_env']} not set")
            org_summaries.append({"slug": org["slug"], "label": org["label"], "error": "token not set"})
            continue
        try:
            self_id = find_self_id(token)
            tickets = get_open_tickets(token, self_id)
            if org["slug"] == "peopleops":
                extra = get_open_asset_collection_tickets(token)
                owned_ids = {t.get("id") for t in tickets}
                tickets = tickets + [t for t in extra if t.get("id") not in owned_ids]
        except Exception as e:
            print(f"[error] {org['slug']}: {e}")
            org_summaries.append({"slug": org["slug"], "label": org["label"], "error": str(e)})
            continue

        print(f"{org['slug']}: {len(tickets)} open tickets (owned by {self_id} + asset-collection in peopleops)")
        org_summaries.append({"slug": org["slug"], "label": org["label"], "count": len(tickets)})

        for w in tickets:
            if w.get("id") in seen_ids:
                continue
            seen_ids.add(w.get("id"))
            title = w.get("title", "")
            kind = categorize(title)
            employee = ""
            if title.startswith("Employee Name:"):
                employee = title.replace("Employee Name: ", "").split(" - ")[0].strip()
            rows.append({
                "id": w.get("display_id", ""),
                "org": org["slug"],
                "org_label": org["label"],
                "title": title,
                "kind": kind,
                "employee": employee,
                "stage": w.get("stage", {}).get("name", ""),
                "created": w.get("created_date"),
                "lwd": w.get("target_close_date"),
                "url": f"https://app.devrev.ai/{org['slug']}/works/{w.get('display_id', '')}",
            })

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "orgs": org_summaries,
        "tickets": rows,
    }
    with open("changappa_status.json", "w") as f:
        json.dump(out, f, indent=2)
    print(f"Wrote {len(rows)} ticket(s) to changappa_status.json")


if __name__ == "__main__":
    main()
