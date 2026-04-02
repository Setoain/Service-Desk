import requests
import os
import json
import logging
from datetime import datetime, timedelta, timezone
from collections import defaultdict

WRIKE_API_URL = "https://app-eu.wrike.com/api/v4"

# workspace folder ids
PTO_FOLDER_ID        = "MQAAAAEDvd-7"
WRIKE_ROOT_FOLDER_ID = "IEAGL5JPI7777777"
WRIKE_RECYCLE_BIN_ID = "IEAGL5JPI7777776"

# Section folders in wrike
INNOVATION_SECTIONS: dict[str, str] = {
    "MQAAAABn8ADE":     "Innovation Funnel",
    "IEAGL5JPI5PD3LXF": "Prototype Development",
    "MQAAAAEDhSi8":     "Solutioning",
    "IEAGL5JPI5QQP7U7": "Product Development - Customer Ready!",
    "IEAGL5JPI5PD3LSP": "AI Management Work",
    "IEAGL5JPI5Q4QCUQ": "Training",
    "MQAAAAEDhMyW":     "Internships",
}

logger = logging.getLogger(__name__)


def get_headers() -> dict:
    token = os.getenv("WRIKE_ACCESS_TOKEN")
    if not token:
        raise ValueError("WRIKE_ACCESS_TOKEN is missing in environment")
    return {"Authorization": f"Bearer {token}"}


# PTO process
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


def get_contacts() -> dict[str, str]:
    """Return {userId: displayName} for every account contact."""
    resp = requests.get(f"{WRIKE_API_URL}/contacts", headers=get_headers())
    resp.raise_for_status()

    def _display_name(contact: dict) -> str:
        profiles = contact.get("profiles") or []
        profile = profiles[0] if profiles else {}

        for candidate in (profile.get("name"), contact.get("name")):
            if candidate:
                return candidate

        first = profile.get("firstName") or contact.get("firstName") or ""
        last = profile.get("lastName") or contact.get("lastName") or ""
        full = f"{first} {last}".strip()
        if full:
            return full

        return profile.get("email") or contact.get("email") or contact.get("id", "Unknown")

    result = {}
    for c in resp.json().get("data", []):
        name = _display_name(c)
        result[c["id"]] = name
    return result


def get_custom_item_types_map() -> dict[str, str]:
    """Return {customItemTypeId: title} for Wrike custom item types."""
    try:
        resp = requests.get(f"{WRIKE_API_URL}/custom_item_types", headers=get_headers())
        resp.raise_for_status()
    except Exception as e:
        logger.warning("Could not load custom item types from Wrike: %s", e)
        return {}

    mapping: dict[str, str] = {}
    for row in resp.json().get("data", []):
        type_id = row.get("id")
        title = row.get("title") or row.get("name")
        if type_id and title:
            mapping[type_id] = str(title)
    return mapping


def get_recent_assignments(days_back: int = 7) -> list[dict]:
    since_dt  = datetime.now(timezone.utc) - timedelta(days=days_back)
    since_iso = since_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    contacts = get_contacts()

    folders_resp = requests.get(
        f"{WRIKE_API_URL}/folders/{WRIKE_ROOT_FOLDER_ID}/folders",
        headers=get_headers(),
        params={
            "descendants": "true",
            "fields": json.dumps(["childIds", "customItemTypeId"]),
        },
    )
    folders_resp.raise_for_status()
    all_folders: list[dict] = folders_resp.json().get("data", [])

    recycle_children: set[str] = set()
    folder_title_map: dict[str, str] = {}
    for f in all_folders:
        folder_title_map[f["id"]] = f["title"]
        if f["id"] == WRIKE_RECYCLE_BIN_ID:
            recycle_children = set(f.get("childIds", []))

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


# Projects Page
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


def get_projects_hierarchy() -> dict[str, list[dict]]:
    folders_resp = requests.get(
        f"{WRIKE_API_URL}/folders/{WRIKE_ROOT_FOLDER_ID}/folders",
        headers=get_headers(),
        params={"descendants": "true"},
    )
    folders_resp.raise_for_status()
    all_folders: list[dict] = folders_resp.json().get("data", [])

    contacts = get_contacts()
    custom_type_map = get_custom_item_types_map()

    folder_map: dict[str, dict] = {f["id"]: f for f in all_folders}
    child_ids_map: dict[str, list[str]] = {f["id"]: f.get("childIds", []) for f in all_folders}
    parent_map: dict[str, str] = {}
    recycle_children: set[str] = set()

    for f in all_folders:
        for child_id in f.get("childIds", []):
            parent_map[child_id] = f["id"]
        if f["id"] == WRIKE_RECYCLE_BIN_ID:
            recycle_children = set(f.get("childIds", []))

    section_ids = set(INNOVATION_SECTIONS.keys())

    def _find_section_id_ancestor(folder_id: str) -> str | None:
        current = folder_id
        for _ in range(20):
            parent = parent_map.get(current)
            if parent is None:
                return None
            if parent in section_ids:
                return parent
            if parent in (WRIKE_ROOT_FOLDER_ID, WRIKE_RECYCLE_BIN_ID):
                return None
            current = parent
        return None

    node_ids: set[str] = set()
    node_section_map: dict[str, str] = {}
    for f in all_folders:
        if f.get("scope") in ("RbFolder", "RbRoot", "WsRoot"):
            continue
        if f["id"] in recycle_children or f["id"] in (WRIKE_ROOT_FOLDER_ID, WRIKE_RECYCLE_BIN_ID):
            continue
        if f["id"] in section_ids:
            section_id = f["id"]
        else:
            section_id = _find_section_id_ancestor(f["id"])
        if not section_id:
            continue
        node_ids.add(f["id"])
        node_section_map[f["id"]] = section_id

    all_tasks: list[dict] = []
    next_page_token = None
    while True:
        params = {
            "descendants": "true",
            "subTasks": "true",
            "pageSize": 200,
            "fields": json.dumps(["parentIds", "superTaskIds", "responsibleIds", "customItemTypeId"]),
        }
        if next_page_token:
            params["nextPageToken"] = next_page_token

        tasks_resp = requests.get(
            f"{WRIKE_API_URL}/folders/{WRIKE_ROOT_FOLDER_ID}/tasks",
            headers=get_headers(),
            params=params,
        )
        tasks_resp.raise_for_status()

        payload = tasks_resp.json()
        all_tasks.extend(payload.get("data", []))
        next_page_token = payload.get("nextPageToken")
        if not next_page_token:
            break

    def _extract_item_type_id(item: dict) -> str | None:
        if item.get("customItemTypeId"):
            return item.get("customItemTypeId")
        custom_item_type = item.get("customItemType")
        if isinstance(custom_item_type, dict) and custom_item_type.get("id"):
            return custom_item_type.get("id")
        metadata = item.get("metadata")
        if isinstance(metadata, dict):
            if metadata.get("customItemTypeId"):
                return metadata.get("customItemTypeId")
            nested = metadata.get("customItemType")
            if isinstance(nested, dict) and nested.get("id"):
                return nested.get("id")
        return None

    def _infer_task_type(task: dict) -> str:
        wrike_type_id = _extract_item_type_id(task)
        if wrike_type_id and wrike_type_id in custom_type_map:
            return custom_type_map[wrike_type_id]
        return ""

    def _infer_node_type(folder: dict) -> str:
        wrike_type_id = _extract_item_type_id(folder)
        if wrike_type_id and wrike_type_id in custom_type_map:
            return custom_type_map[wrike_type_id]
        return ""

    task_meta: dict[str, dict] = {}
    tasks_children: dict[str, list[str]] = defaultdict(list)
    node_task_roots: dict[str, list[str]] = defaultdict(list)

    for t in all_tasks:
        tid = t.get("id")
        if not tid:
            continue
        parent_node_ids = [pid for pid in t.get("parentIds", []) if pid in node_ids]

        assignees = [contacts.get(uid, uid) for uid in t.get("responsibleIds", [])]
        task_meta[tid] = {
            "id": tid,
            "title": t.get("title", "Untitled task"),
            "status": t.get("status", "Unknown"),
            "assignees": assignees,
            "type": _infer_task_type(t),
            "parent_task_ids": [sid for sid in t.get("superTaskIds", []) if sid],
            "node_parent_ids": parent_node_ids,
        }

    anchor_cache: dict[str, set[str]] = {}

    def _task_anchors(task_id: str) -> set[str]:
        if task_id in anchor_cache:
            return anchor_cache[task_id]

        meta = task_meta.get(task_id)
        if not meta:
            anchor_cache[task_id] = set()
            return anchor_cache[task_id]

        anchors = set(meta.get("node_parent_ids", []))
        for parent_tid in meta.get("parent_task_ids", []):
            anchors.update(_task_anchors(parent_tid))

        anchor_cache[task_id] = anchors
        return anchors

    included_task_ids = {tid for tid in task_meta if _task_anchors(tid)}

    for tid, meta in task_meta.items():
        if tid not in included_task_ids:
            continue
        linked_to_task_parent = False
        for parent_tid in meta.get("parent_task_ids", []):
            if parent_tid in included_task_ids:
                tasks_children[parent_tid].append(tid)
                linked_to_task_parent = True
        if not linked_to_task_parent:
            for node_id in _task_anchors(tid):
                node_task_roots[node_id].append(tid)

    def _build_task_node(task_id: str) -> dict:
        meta = task_meta[task_id]
        return {
            "id": meta["id"],
            "title": meta["title"],
            "status": meta["status"],
            "assignees": meta["assignees"],
            "type": meta["type"],
            "children": sorted([_build_task_node(cid) for cid in tasks_children.get(task_id, [])], key=lambda x: x["title"].lower()),
        }

    node_cache: dict[str, dict] = {}

    def build_node(folder_id: str) -> dict:
        if folder_id in node_cache:
            return node_cache[folder_id]

        folder = folder_map.get(folder_id, {})
        proj = folder.get("project", {})
        owner_names = [contacts.get(uid, uid) for uid in proj.get("ownerIds", [])] if proj else []
        node = {
            "id": folder_id,
            "title": folder.get("title", folder_id),
            "node_type": _infer_node_type(folder),
            "status": _infer_status(proj) if proj else "Folder",
            "start_date": proj.get("startDate", "—"),
            "end_date": proj.get("endDate", "—"),
            "assignees": owner_names,
            "children": [],
            "tasks": sorted([_build_task_node(tid) for tid in node_task_roots.get(folder_id, [])], key=lambda x: x["title"].lower()),
        }

        for child_id in child_ids_map.get(folder_id, []):
            if child_id in node_ids:
                node["children"].append(build_node(child_id))

        node["children"].sort(key=lambda x: x["title"].lower())
        node_cache[folder_id] = node
        return node

    grouped: dict[str, list[dict]] = defaultdict(list)
    for nid in sorted(node_ids):
        parent_id = parent_map.get(nid)
        section_id = node_section_map.get(nid)
        if not section_id:
            continue
        section_name = INNOVATION_SECTIONS[section_id]

        if parent_id in node_ids:
            continue

        if nid == section_id:
            section_root = build_node(nid)
            for child in section_root.get("children", []):
                grouped[section_name].append(child)

            root_tasks = section_root.get("tasks", [])
            if root_tasks:
                grouped[section_name].append({
                    "id": f"{section_id}::section-items",
                    "title": "Section Items",
                    "node_type": "Folder",
                    "status": "Folder",
                    "start_date": "—",
                    "end_date": "—",
                    "assignees": [],
                    "children": [],
                    "tasks": root_tasks,
                })
            continue

        grouped[section_name].append(build_node(nid))

    for section in grouped:
        grouped[section].sort(key=lambda x: x["title"].lower())

    ordered: dict[str, list[dict]] = {}
    ordered_section_names = list(INNOVATION_SECTIONS.values())
    for sec in ordered_section_names:
        ordered[sec] = grouped.pop(sec, [])

    return ordered