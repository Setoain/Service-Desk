import requests
import os
import json
import logging
from datetime import datetime, timedelta, timezone
from collections import defaultdict

WRIKE_API_URL = "https://app-eu.wrike.com/api/v4"

# ── Hardcoded workspace IDs ───────────────────────────────────────────────────
PTO_FOLDER_ID        = "MQAAAAEDvd-7"
WRIKE_ROOT_FOLDER_ID = "IEAGL5JPI7777777"
WRIKE_RECYCLE_BIN_ID = "IEAGL5JPI7777776"

# Section folders inside Innovation Studio
INNOVATION_SECTIONS: dict[str, str] = {
    "MQAAAABn8ADE":     "1. Innovation Funnel",
    "IEAGL5JPI5PD3LXF": "2. Prototype Development",
    "MQAAAAEDhSi8":     "3. Solutioning",
    "IEAGL5JPI5QQP7U7": "4. Product Development – Customer Ready",
    "IEAGL5JPI5PD3LSP": "5. AI Management Work",
    "IEAGL5JPI5Q4QCUQ": "6. Training",
    "MQAAAAEDhMyW":     "Internships",
}

logger = logging.getLogger(__name__)


# ── Auth ──────────────────────────────────────────────────────────────────────

def get_headers() -> dict:
    token = os.getenv("WRIKE_ACCESS_TOKEN")
    if not token:
        raise ValueError("WRIKE_ACCESS_TOKEN is missing in environment")
    return {"Authorization": f"Bearer {token}"}


# ── PTO ───────────────────────────────────────────────────────────────────────

def submit_complete_pto(form_data: dict, file=None) -> dict:
    logger.info("Submitting PTO: full_name=%s start=%s end=%s",
                form_data.get("full_name"), form_data.get("start_date"), form_data.get("end_date"))

    ID_MAP = {
        "paid_unpaid":   "IEAGL5JPJUAKPAR5",
        "time_off_type": "IEAGL5JPJUAKPATC",
        "region":        "IEAGL5JPJUAJATWY",
        "days_count":    "IEAGL5JPJUAHMQTN",
    }
    hours_field_id = os.getenv("WRIKE_PTO_HOURS_FIELD_ID")

    custom_fields = [
        {"id": ID_MAP["paid_unpaid"],   "value": form_data["paid_type"]},
        {"id": ID_MAP["time_off_type"], "value": form_data["paid_category"]},
        {"id": ID_MAP["region"],        "value": form_data["region"]},
        {"id": ID_MAP["days_count"],    "value": str(form_data["days_total"])},
    ]
    if hours_field_id:
        custom_fields.append({"id": hours_field_id, "value": str(form_data.get("hours_total", 0))})

    description_html = f"""
    <h3><b>PTO REQUEST FORM</b></h3>
    <b>Full Name:</b> {form_data['full_name']}<br />
    <b>Role:</b> {form_data['role']}<br />
    <b>Paid or Unpaid:</b> {form_data['paid_type']}<br />
    <b>Time Off Type:</b> {form_data['paid_category']}<br />
    <b>Start Date:</b> {form_data['start_date']}<br />
    <b>End Date:</b> {form_data['end_date']}<br />
    <b>Days Requested:</b> {form_data['days_total']}<br />
    <b>Hours Requested:</b> {form_data.get('hours_total', 0)}<br />
    <b>Description/Reason:</b> {form_data.get('other_reason', 'N/A')}<br />
    <hr /><b>Region:</b> {form_data['region']}<br />
    """

    payload = {
        "title":        f"PTO Request: {form_data['full_name']}",
        "description":  description_html,
        "dates":        json.dumps({"type": "Planned", "start": str(form_data["start_date"]), "due": str(form_data["end_date"])}),
        "customFields": json.dumps(custom_fields),
        "status":       "Active",
    }

    resp = requests.post(f"{WRIKE_API_URL}/folders/{PTO_FOLDER_ID}/tasks", headers=get_headers(), data=payload)
    if resp.status_code != 200:
        raise Exception(f"Wrike API Error: {resp.text}")

    task_data = resp.json()["data"][0]

    if file:
        file_bytes = file.getvalue() if hasattr(file, "getvalue") else file.read()
        files = {"file": (file.name, file_bytes, getattr(file, "type", "application/octet-stream"))}
        attach_resp = requests.post(f"{WRIKE_API_URL}/tasks/{task_data['id']}/attachments", headers=get_headers(), files=files)
        if attach_resp.status_code not in (200, 201):
            raise Exception(f"Wrike attachment error: {attach_resp.text}")

    return task_data


# ── Contacts ──────────────────────────────────────────────────────────────────

def get_contacts() -> dict[str, str]:
    """Return {userId: displayName} for every account contact."""
    resp = requests.get(f"{WRIKE_API_URL}/contacts", headers=get_headers())
    resp.raise_for_status()
    result = {}
    for c in resp.json().get("data", []):
        name = (c.get("profiles") or [{}])[0].get("name") or c.get("id", "Unknown")
        result[c["id"]] = name
    return result


# ── Recent assignments ────────────────────────────────────────────────────────

def get_recent_assignments(days_back: int = 7) -> list[dict]:
    """
    Surfaces WHO has been assigned to WHICH project in the last `days_back` days.

    Two signals are combined:

      1. NEW PROJECTS  – folders whose project.createdDate falls within the window.
                         Their ownerIds are the people who were assigned.

      2. TASK ASSIGNEES – tasks *created* within the window that have responsibleIds.
                          Their parentIds resolve to the containing project.

    Returns a list sorted new-projects-first, then alphabetically:
    [
      {
        "project_id":    str,
        "project_title": str,
        "is_new":        bool,
        "task_count":    int,
        "people": [{"user_id": str, "name": str}, ...]
      },
      ...
    ]
    """
    since_dt  = datetime.now(timezone.utc) - timedelta(days=days_back)
    since_iso = since_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    contacts = get_contacts()

    # ── Fetch full folder tree (needed for both signals) ──────────────────────
    folders_resp = requests.get(
        f"{WRIKE_API_URL}/folders/{WRIKE_ROOT_FOLDER_ID}/folders",
        headers=get_headers(),
        params={"descendants": "true"},
    )
    folders_resp.raise_for_status()
    all_folders: list[dict] = folders_resp.json().get("data", [])

    # Build helper structures
    recycle_children: set[str] = set()
    folder_title_map: dict[str, str] = {}
    for f in all_folders:
        folder_title_map[f["id"]] = f["title"]
        if f["id"] == WRIKE_RECYCLE_BIN_ID:
            recycle_children = set(f.get("childIds", []))

    # projects_data accumulates entries keyed by project_id
    # Each value: {"project_id", "project_title", "is_new", "people_ids": set, "task_count"}
    projects_data: dict[str, dict] = {}

    def _ensure_project(pid: str, title: str, is_new: bool = False) -> dict:
        if pid not in projects_data:
            projects_data[pid] = {
                "project_id":    pid,
                "project_title": title,
                "is_new":        is_new,
                "people_ids":    set(),
                "task_count":    0,
            }
        if is_new:
            projects_data[pid]["is_new"] = True
        return projects_data[pid]

    # ── Signal 1: new projects created in the window ──────────────────────────
    for f in all_folders:
        if "project" not in f:
            continue
        if f.get("scope") in ("RbFolder", "RbRoot", "WsRoot"):
            continue
        if f["id"] in recycle_children or f["id"] in (WRIKE_ROOT_FOLDER_ID, WRIKE_RECYCLE_BIN_ID):
            continue

        proj       = f["project"]
        created_str = proj.get("createdDate", "")
        owner_ids  = proj.get("ownerIds", [])

        if not created_str or not owner_ids:
            continue

        try:
            created_dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
        except ValueError:
            continue

        if created_dt >= since_dt:
            entry = _ensure_project(f["id"], f["title"], is_new=True)
            entry["people_ids"].update(owner_ids)

    # ── Signal 2: tasks created in the window with assignees ──────────────────
    tasks_resp = requests.get(
        f"{WRIKE_API_URL}/folders/{WRIKE_ROOT_FOLDER_ID}/tasks",
        headers=get_headers(),
        params={
            "descendants": "true",
            "createdDate": json.dumps({"start": since_iso}),
            "fields":      json.dumps(["responsibleIds", "parentIds"]),
        },
    )
    tasks_resp.raise_for_status()

    for task in tasks_resp.json().get("data", []):
        responsible_ids: list[str] = task.get("responsibleIds", [])
        if not responsible_ids:
            continue

        for pid in task.get("parentIds", []):
            if pid in (WRIKE_ROOT_FOLDER_ID, WRIKE_RECYCLE_BIN_ID):
                continue
            if pid in recycle_children:
                continue

            title = folder_title_map.get(pid, pid)
            entry = _ensure_project(pid, title)
            entry["people_ids"].update(responsible_ids)
            entry["task_count"] += 1

    # ── Build final list ──────────────────────────────────────────────────────
    results = []
    for entry in projects_data.values():
        people = [
            {"user_id": uid, "name": contacts.get(uid, uid)}
            for uid in sorted(entry["people_ids"])
        ]
        if not people:
            continue
        results.append({
            "project_id":    entry["project_id"],
            "project_title": entry["project_title"],
            "is_new":        entry["is_new"],
            "task_count":    entry["task_count"],
            "people":        people,
        })

    results.sort(key=lambda x: (not x["is_new"], x["project_title"].lower()))
    return results


# ── All projects (Projects page) ──────────────────────────────────────────────

def _infer_status(proj: dict) -> str:
    if proj.get("completedDate"):
        return "Completed"
    if proj.get("startDate"):
        return "In Progress"
    return "Active"


def _find_section(folder_id: str, parent_map: dict[str, str]) -> str:
    current = folder_id
    for _ in range(10):
        parent = parent_map.get(current)
        if parent is None:
            return "Other"
        if parent in INNOVATION_SECTIONS:
            return INNOVATION_SECTIONS[parent]
        if parent == WRIKE_ROOT_FOLDER_ID:
            return "Top Level"
        current = parent
    return "Other"


def get_all_wrike_projects() -> list[dict]:
    """Return all non-deleted projects, each with id, title, status, dates, section."""
    resp = requests.get(
        f"{WRIKE_API_URL}/folders/{WRIKE_ROOT_FOLDER_ID}/folders",
        headers=get_headers(),
        params={"descendants": "true"},
    )
    resp.raise_for_status()
    all_folders: list[dict] = resp.json().get("data", [])

    parent_map: dict[str, str] = {}
    recycle_children: set[str] = set()
    for f in all_folders:
        for child_id in f.get("childIds", []):
            parent_map[child_id] = f["id"]
        if f["id"] == WRIKE_RECYCLE_BIN_ID:
            recycle_children = set(f.get("childIds", []))

    projects = []
    for f in all_folders:
        if "project" not in f:
            continue
        if f.get("scope") in ("RbFolder", "RbRoot", "WsRoot"):
            continue
        if f["id"] in recycle_children or f["id"] in (WRIKE_ROOT_FOLDER_ID, WRIKE_RECYCLE_BIN_ID):
            continue

        proj = f.get("project", {})
        projects.append({
            "id":         f["id"],
            "title":      f["title"],
            "status":     _infer_status(proj),
            "start_date": proj.get("startDate", "—"),
            "end_date":   proj.get("endDate", "—"),
            "section":    _find_section(f["id"], parent_map),
        })

    return sorted(projects, key=lambda x: (x["section"], x["title"].lower()))