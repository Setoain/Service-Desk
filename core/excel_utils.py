"""
Excel structure:
- One sheet per Wrike section, e.g.:
Innovation Funnel
Prototype Development
Solutioning
Product Development - Customer Ready!
AI Management Work
Training
Internships
- One row per top-level Wrike project root.

Required column:
    Project Name

Optional column:
    Wrike Project ID
"""

import os
import pandas as pd


def _get_excel_path() -> str:
    return os.getenv("PROJECT_EXCEL_PATH", "projects_info.xlsx")

# Maps our internal key → accepted column header aliases (lowercase)
COLUMN_MAP: dict[str, list[str]] = {
    "project_name":        ["project name", "name", "project"],
    "project_id":          ["project id", "wrike project id", "wrike id"],
    "github_repositories": ["github repositories", "github repo", "github", "repo"],
    "api_keys":            ["api keys", "api key", "apikeys"],
    "login_credentials":   ["login credentials", "credentials", "login"],
    "documentation":       ["documentation", "docs", "doc"],
    "notes":               ["notes", "note"],
}

FRIENDLY_LABELS: dict[str, tuple[str, str]] = {
    "project_id":          ("🆔", "Project ID"),
    "github_repositories": ("🐙", "GitHub Repositories"),
    "api_keys":            ("🔑", "API Keys"),
    "login_credentials":   ("👤", "Login Credentials"),
    "documentation":       ("📘", "Documentation"),
    "notes":               ("📝", "Notes"),
}


def _load_df() -> pd.DataFrame:
    raise NotImplementedError("Use _load_sheet or _load_workbook_sheets instead.")


def _ensure_workbook_exists() -> None:
    excel_path = _get_excel_path()
    if isinstance(excel_path, str) and excel_path.lower().startswith(("http://", "https://")):
        raise FileNotFoundError(
            "PROJECT_EXCEL_PATH is set to a SharePoint/web URL. "
            "Use a local file path instead, for example a OneDrive-synced path like "
            "'C:/Users/<user>/OneDrive - <org>/.../projects_info.xlsx'."
        )
    if not os.path.exists(excel_path):
        raise FileNotFoundError(
            f"Excel file not found at '{excel_path}'. "
            "Set PROJECT_EXCEL_PATH in your .env to the correct path."
        )


def _normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [str(c).strip().lower() for c in df.columns]
    return df.fillna("")


def _load_sheet(sheet_name: str) -> pd.DataFrame:
    _ensure_workbook_exists()
    df = pd.read_excel(_get_excel_path(), sheet_name=sheet_name, dtype=str)
    return _normalize_df(df)


def _load_workbook_sheets() -> dict[str, pd.DataFrame]:
    _ensure_workbook_exists()
    workbook = pd.read_excel(_get_excel_path(), sheet_name=None, dtype=str)
    return {name: _normalize_df(df) for name, df in workbook.items()}


def _resolve_col(df: pd.DataFrame, key: str) -> str | None:
    """Return the actual column name in df that matches any alias for `key`."""
    for alias in COLUMN_MAP.get(key, []):
        if alias in df.columns:
            return alias
    return None


def _row_to_result(row: pd.Series, df: pd.DataFrame, sheet_name: str) -> dict[str, str]:
    name_col = _resolve_col(df, "project_name")
    result: dict[str, str] = {
        "project_name": str(row[name_col]) if str(row[name_col]).strip() else "No data",
        "sheet_name": sheet_name,
    }
    project_id_col = _resolve_col(df, "project_id")
    if project_id_col:
        val = str(row[project_id_col]).strip()
        result["project_id"] = val if val else "No data"

    for key in COLUMN_MAP:
        if key in ("project_name", "project_id"):
            continue
        col = _resolve_col(df, key)
        if not col:
            result[key] = "No data"
            continue
        value = str(row[col]).strip()
        result[key] = value if value else "No data"
    return result


def _find_project_in_df(df: pd.DataFrame, project_name: str) -> pd.DataFrame:
    name_col = _resolve_col(df, "project_name")
    if name_col is None:
        raise ValueError("Excel sheet is missing a 'Project Name' column.")
    mask = df[name_col].str.strip().str.lower() == project_name.strip().lower()
    return df[mask]


def get_project_info(project_name: str, section_name: str | None = None) -> dict | None:
    """
    Look up a project by name (case-insensitive).
    If section_name is provided, search that sheet first.
    Otherwise search across all sheets and return the first exact match.
    Returns a dict of {field_key: value} or None if not found.
    """
    if section_name:
        try:
            df = _load_sheet(section_name)
            matches = _find_project_in_df(df, project_name)
            if not matches.empty:
                return _row_to_result(matches.iloc[0], df, section_name)
        except ValueError:
            raise
        except Exception:
            pass

    workbook = _load_workbook_sheets()
    for sheet_name, df in workbook.items():
        matches = _find_project_in_df(df, project_name)
        if not matches.empty:
            return _row_to_result(matches.iloc[0], df, sheet_name)
    return None


def get_all_project_names() -> list[str]:
    """Return every project name listed across all workbook sheets."""
    try:
        workbook = _load_workbook_sheets()
    except FileNotFoundError:
        return []
    names: list[str] = []
    for df in workbook.values():
        name_col = _resolve_col(df, "project_name")
        if name_col is None:
            continue
        names.extend(df[name_col].dropna().astype(str).str.strip().tolist())
    return names