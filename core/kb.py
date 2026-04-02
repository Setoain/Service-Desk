import os
import requests
import sqlite3
from typing import List, Dict

DB_PATH = os.path.join(os.path.dirname(__file__), "kb_files.db")
TABLE_NAME = "kb_files"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            web_url TEXT,
            path TEXT,
            drive_id TEXT,
            site_id TEXT,
            last_modified TEXT,
            UNIQUE(name, path)
        )
    """)
    conn.commit()
    conn.close()

# SHAREPOINT FETCHING 
def list_sharepoint_files(access_token: str, site_id: str) -> List[Dict]:
    """Fetch all files from all drives in a SharePoint site."""
    headers = {"Authorization": f"Bearer {access_token}"}
    drives_url = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drives"
    drives = requests.get(drives_url, headers=headers).json()["value"]
    all_files = []
    for drive in drives:
        drive_id = drive["id"]
        files_url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root/search(q='')"
        files = requests.get(files_url, headers=headers).json()["value"]
        for f in files:
            all_files.append({
                "name": f["name"],
                "web_url": f["webUrl"],
                "path": f["parentReference"]["path"],
                "drive_id": drive_id,
                "site_id": site_id,
                "last_modified": f["lastModifiedDateTime"]
            })
    return all_files

# SQLITE UPSERT
def upsert_files_to_sqlite(files: List[Dict]):
    """Insert or update file metadata in SQLite."""
    conn = get_db()
    for file in files:
        conn.execute(f"""
            INSERT INTO {TABLE_NAME} (name, web_url, path, drive_id, site_id, last_modified)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(name, path) DO UPDATE SET
                web_url=excluded.web_url,
                drive_id=excluded.drive_id,
                site_id=excluded.site_id,
                last_modified=excluded.last_modified
        """, (
            file["name"], file["web_url"], file["path"], file["drive_id"], file["site_id"], file["last_modified"]
        ))
    conn.commit()
    conn.close()

# SQLITE QUERY
def search_files_in_kb(query: str, limit: int = 10) -> List[Dict]:
    """Search for files in SQLite KB table by name or path."""
    conn = get_db()
    cur = conn.execute(f"""
        SELECT * FROM {TABLE_NAME}
        WHERE name LIKE ? OR path LIKE ?
        ORDER BY last_modified DESC
        LIMIT ?
    """, (f"%{query}%", f"%{query}%", limit))
    rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in rows]

# LLM CONTEXT PREP
def get_kb_context_for_llm(query: str, limit: int = 10) -> str:
    """Return a string with relevant file names/paths/links for LLM context."""
    files = search_files_in_kb(query, limit)
    if not files:
        return "No relevant files found in the knowledge base."
    lines = [f"- [{f['name']}]({f['web_url']}) (Path: {f['path']})" for f in files]
    return "Relevant files from the knowledge base:\n" + "\n".join(lines)

# MAIN USAGE EXAMPLE
if __name__ == "__main__":
    # Initialize DB
    init_db()
    ACCESS_TOKEN = os.getenv("GRAPH_ACCESS_TOKEN")
    SITE_ID = os.getenv("SHAREPOINT_SITE_ID")
    files = list_sharepoint_files(ACCESS_TOKEN, SITE_ID)
    upsert_files_to_sqlite(files)
    # Example search
    print(get_kb_context_for_llm("agent 1"))
