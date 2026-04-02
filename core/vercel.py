def flatten_vercel_project(project: dict) -> dict:
    """
    Format the vercel information so its cleaner for the LLM to parse through it
    """
    summary = {
        # Top-level summary fields
        "name": project.get("name", "unknown"),
        "project_id": project.get("id", ""),
        "framework": project.get("framework", "unknown"),
        # Main production URL (from latestDeployments or targets)
        "main_url": None,
        # All production URLs (from latestDeployments/targets/alias)
        "all_urls": [],
    }


    # Prefer canonical .vercel.app alias if available
    canonical_url = None
    # Check aliases in latestDeployments
    for dep in project.get("latestDeployments", []):
        for alias in dep.get("alias", []):
            if alias.endswith(".vercel.app"):
                canonical_url = f"https://{alias}"
                break
        if canonical_url:
            break
    # Also check targets.production.alias
    if not canonical_url:
        prod_target = project.get("targets", {}).get("production", {})
        for alias in prod_target.get("alias", []):
            if alias.endswith(".vercel.app"):
                canonical_url = f"https://{alias}"
                break

    if canonical_url:
        summary["main_url"] = canonical_url
    else:
        # Fallback: latestDeployments[0]['url']
        latest = project.get("latestDeployments", [])
        if latest and isinstance(latest, list) and latest[0].get("url"):
            summary["main_url"] = f"https://{latest[0]['url']}"
        # Fallback: try targets.production.url
        elif (
            "targets" in project and
            isinstance(project["targets"], dict) and
            "production" in project["targets"] and
            isinstance(project["targets"]["production"], dict) and
            project["targets"]["production"].get("url")
        ):
            summary["main_url"] = f"https://{project['targets']['production']['url']}"

    # Collect all possible URLs from aliases in latestDeployments and targets
    urls = set()
    for dep in project.get("latestDeployments", []):
        if dep.get("url"):
            urls.add(f"https://{dep['url']}")
        for alias in dep.get("alias", []):
            if not alias.startswith("http"):
                urls.add(f"https://{alias}")
            else:
                urls.add(alias)
    # Also check targets.production.alias
    prod_target = project.get("targets", {}).get("production", {})
    for alias in prod_target.get("alias", []):
        if not alias.startswith("http"):
            urls.add(f"https://{alias}")
        else:
            urls.add(alias)
    summary["all_urls"] = sorted(urls)
    if not summary["main_url"] and summary["all_urls"]:
        summary["main_url"] = summary["all_urls"][0]

    # Documented structure for LLMs
    summary["_doc"] = {
        "name": "Project name (string)",
        "project_id": "Vercel project ID (string)",
        "framework": "Framework used (string, e.g. 'vite', 'nextjs')",
        "main_url": "Primary production URL (string, e.g. 'https://project.vercel.app')",
        "all_urls": "List of all known production URLs (list of strings)",
        "raw": "Full original Vercel API project dict for advanced lookups"
    }

    # Attach the full raw dict for fallback
    summary["raw"] = project
    return summary

import os
import requests

def fetch_all_projects() -> list[dict]:
    """
    Fetch all Vercel projects using the API key and (optional) team ID from environment variables.
    Returns a list of project dicts as returned by the Vercel API.
    """
    api_key = os.getenv("VERCEL_API_KEY")
    team_id = os.getenv("VERCEL_TEAM_ID")
    if not api_key:
        raise ValueError("VERCEL_API_KEY not set in environment")
    headers = {"Authorization": f"Bearer {api_key}"}
    params = {"limit": 100}
    if team_id:
        params["teamId"] = team_id
    projects = []
    url = "https://api.vercel.com/v9/projects"
    while url:
        resp = requests.get(url, headers=headers, params=params)
        resp.raise_for_status()
        data = resp.json()
        projects.extend(data.get("projects", []))
        next_cursor = data.get("pagination", {}).get("next")
        if next_cursor:
            params["from"] = next_cursor
        else:
            url = None
    return projects

def get_production_url(project: dict) -> str:
    """
    Return the best production URL for a Vercel project, preferring custom domains over .vercel.app.
    """
    aliases = project.get("alias", [])
    custom = [a["domain"] for a in aliases if "vercel.app" not in a.get("domain", "")]
    fallback = [a["domain"] for a in aliases if "vercel.app" in a.get("domain", "")]
    domain = (custom or fallback or [None])[0]
    return f"https://{domain}" if domain else ""

def get_project_info(project: dict) -> dict:
    """
    Return a clean, LLM-friendly dict for a Vercel project (see flatten_vercel_project).
    """
    # Remove sensitive env fields
    project = dict(project)
    project.pop("env", None)
    if "build" in project and isinstance(project["build"], dict):
        project["build"].pop("env", None)
    return flatten_vercel_project(project)

def get_all_project_infos() -> list[dict]:
    """
    Fetch all projects and return a list of summary dicts for each project.
    """
    return [get_project_info(p) for p in fetch_all_projects()]

if __name__ == "__main__":
    import json
    from dotenv import load_dotenv
    load_dotenv()
    infos = get_all_project_infos()
    print(json.dumps(infos, indent=2))

# USAGE FOR LLM CONTEXT:
# To provide all project info as context to an LLM, run:
#   python core/vercel.py
# This will output a JSON array of project dicts, each with:
#   - name: project name
#   - project_id: Vercel project ID
#   - framework: framework used
#   - main_url: primary production URL
#   - all_urls: list of all known URLs
#   - raw: full original Vercel API dict (for advanced lookups)
# Pass this JSON to the LLM as context for answering any project-related question.