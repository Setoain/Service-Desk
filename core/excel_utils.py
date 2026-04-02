"""
Reads per-project metadata from an Excel workbook.

Expected sheet name : "Projects"
Required columns (names are case-insensitive and stripped):
    Project Name | GitHub Repo | Vercel | Railway Deployments | N8N Credentials | API Keys

Set the file path in your .env:
    PROJECT_EXCEL_PATH=projects_info.xlsx
"""

import os
import pandas as pd

EXCEL_PATH = os.getenv("PROJECT_EXCEL_PATH", "projects_info.xlsx")

# Maps our internal key → accepted column header aliases (lowercase)
COLUMN_MAP: dict[str, list[str]] = {
    "project_name":        ["project name", "name", "project"],
    "github_repo":         ["github repo", "github", "repo"],
    "vercel":              ["vercel", "vercel url", "vercel deployment"],
    "railway_deployments": ["railway deployments", "railway", "railway url"],
    "n8n_credentials":     ["n8n credentials", "n8n", "n8n creds"],
    "api_keys":            ["api keys", "api key", "apikeys"],
}

FRIENDLY_LABELS: dict[str, tuple[str, str]] = {
    "github_repo":         ("🐙", "GitHub Repo"),
    "vercel":              ("▲",  "Vercel"),
    "railway_deployments": ("🚂", "Railway Deployments"),
    "n8n_credentials":     ("⚙️", "N8N Credentials"),
    "api_keys":            ("🔑", "API Keys"),
}


def _load_df() -> pd.DataFrame:
    if not os.path.exists(EXCEL_PATH):
        raise FileNotFoundError(
            f"Excel file not found at '{EXCEL_PATH}'. "
            "Set PROJECT_EXCEL_PATH in your .env to the correct path."
        )
    df = pd.read_excel(EXCEL_PATH, sheet_name="Projects", dtype=str)
    df.columns = [str(c).strip().lower() for c in df.columns]
    df = df.fillna("—")
    return df


def _resolve_col(df: pd.DataFrame, key: str) -> str | None:
    """Return the actual column name in df that matches any alias for `key`."""
    for alias in COLUMN_MAP.get(key, []):
        if alias in df.columns:
            return alias
    return None


def get_project_info(project_name: str) -> dict | None:
    """
    Look up a project by name (case-insensitive).
    Returns a dict of {field_key: value} or None if not found.
    """
    df = _load_df()
    name_col = _resolve_col(df, "project_name")
    if name_col is None:
        raise ValueError("Excel sheet is missing a 'Project Name' column.")

    mask = df[name_col].str.strip().str.lower() == project_name.strip().lower()
    matches = df[mask]
    if matches.empty:
        return None

    row = matches.iloc[0]
    result: dict[str, str] = {"project_name": row[name_col]}
    for key in COLUMN_MAP:
        if key == "project_name":
            continue
        col = _resolve_col(df, key)
        result[key] = str(row[col]) if col else "—"
    return result


def get_all_project_names() -> list[str]:
    """Return every project name listed in the Excel sheet."""
    try:
        df = _load_df()
    except FileNotFoundError:
        return []
    name_col = _resolve_col(df, "project_name")
    if name_col is None:
        return []
    return df[name_col].dropna().str.strip().tolist()