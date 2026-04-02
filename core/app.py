import streamlit as st
from dotenv import load_dotenv
from agent import run_agent
import wrike
import excel_utils

load_dotenv()

st.set_page_config(page_title="Service Desk", page_icon="🏢", layout="centered")

_DEFAULTS = {
    "page":             "chat",
    "messages":         [],
    "hr_submit_status": None,
    "hr_submit_debug":  None,
    "show_pto_form":    False,
    "access_selected":  {},
    "expanded_project": None,
}
for k, v in _DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

# Sidebar
with st.sidebar:
    st.title("Service Desk")
    st.markdown("---")
    for label, key in [
        ("💬  Chat",           "chat"),
        ("🗂️  HR Operations",  "hr"),
        ("🔑  Access Request", "access"),
        ("📁  Projects",       "projects"),
    ]:
        if st.button(label, use_container_width=True):
            st.session_state.page = key


# CHAT PAGE
if st.session_state.page == "chat":
    st.markdown(
        "<h2 style='text-align:center;margin-bottom:0.5em;'>"
        "Hello, I'm OFI's service desk!<br>Ask me anything.</h2>",
        unsafe_allow_html=True,
    )
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    prompt = st.chat_input("Ask about projects, deployments, or Vercel info...")
    if prompt:
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant"):
            with st.spinner("..."):
                response = run_agent(prompt)
            st.markdown(response)
        st.session_state.messages.append({"role": "assistant", "content": response})


# HR OPERATIONS PAGE
elif st.session_state.page == "hr":
    st.header("HR Operations")

    if st.session_state.hr_submit_status:
        if st.session_state.hr_submit_status == "success":
            st.success("PTO request submitted to Wrike successfully.")
        else:
            st.error("PTO request failed. Check details below.")
        if st.session_state.hr_submit_debug:
            with st.expander("Submission details"):
                st.json(st.session_state.hr_submit_debug)
        if st.button("Clear message", key="clear_hr"):
            st.session_state.hr_submit_status = None
            st.session_state.hr_submit_debug = None

    if not st.session_state.show_pto_form:
        if st.button("Request PTO"):
            st.session_state.show_pto_form = True
    else:
        if st.button("Cancel PTO Request", type="secondary"):
            st.session_state.show_pto_form = False

        with st.form("pto_submission_form"):
            full_name     = st.text_input("Full Name")
            role          = st.text_input("What is your role?")
            paid_type     = st.selectbox("Paid or Unpaid?", ["Paid Time Off", "Unpaid Time Off"])
            paid_category = st.selectbox("Type of time off", [
                "Holidays", "Doctor Leave", "Sick Leave", "Maternity Leave",
                "Mourning License", "Colombian Family Day (Law 1857 of 2017)", "Other",
            ])
            other_reason  = st.text_area("If Other, describe the reason")
            col1, col2    = st.columns(2)
            start_date    = col1.date_input("Start Date")
            end_date      = col2.date_input("Finish Date")
            col3, col4    = st.columns(2)
            days_total    = col3.number_input("Days requested",  min_value=0, value=1, step=1)
            hours_total   = col4.number_input("Hours requested", min_value=0, value=8, step=1)
            region        = st.selectbox("Region", ["LATAM", "EUR", "DACH", "India"])
            file          = st.file_uploader("Supporting document", type=["pdf", "jpg", "png"])
            submit_btn    = st.form_submit_button("Submit to Wrike")

        if submit_btn:
            if not full_name:
                st.error("Please enter your name.")
            elif end_date < start_date:
                st.error("End date cannot be before start date.")
            else:
                with st.spinner("Submitting..."):
                    try:
                        result = wrike.submit_complete_pto({
                            "full_name": full_name, "role": role,
                            "paid_type": paid_type, "paid_category": paid_category,
                            "other_reason": other_reason,
                            "start_date": start_date, "end_date": end_date,
                            "days_total": days_total, "hours_total": hours_total,
                            "region": region,
                        }, file)
                        st.session_state.hr_submit_status = "success"
                        st.session_state.hr_submit_debug = {
                            "task_id": result.get("id"),
                            "task_title": result.get("title"),
                            "file_uploaded": bool(file),
                        }
                        st.session_state.show_pto_form = False
                        st.rerun()
                    except Exception as e:
                        st.session_state.hr_submit_status = "error"
                        st.session_state.hr_submit_debug = {"error": str(e)}
                        st.rerun()


# ACCESS REQUEST PAGE
elif st.session_state.page == "access":
    st.header("🔑 Access Request")

    col_info, col_days = st.columns([4, 2])
    col_info.caption(
        "Detects people recently assigned to projects in Wrike — either as project "
        "owners on new projects, or as task assignees on existing ones. "
        "Select who should receive their access credentials."
    )
    days_back = col_days.selectbox(
        "Look back",
        [7, 14, 30],
        format_func=lambda d: f"Last {d} days",
        key="access_days_back",
    )

    st.markdown("---")

    @st.cache_data(ttl=120, show_spinner=False)
    def _load_assignments(days: int):
        return wrike.get_recent_assignments(days_back=days)

    with st.spinner("Scanning Wrike for recent assignments…"):
        try:
            assignments = _load_assignments(days_back)
            load_error  = None
        except Exception as e:
            assignments = []
            load_error  = str(e)

    if load_error:
        st.error(f"Could not load assignments: {load_error}")
        st.stop()

    if not assignments:
        st.info(f"✅ No new assignments found in the last {days_back} days.")
        st.stop()

    selected: dict[tuple, bool] = st.session_state.access_selected

    with st.form("access_selection_form"):
        for proj in assignments:
            pid   = proj["project_id"]
            title = proj["project_title"]

            badge = "🆕 " if proj["is_new"] else ""
            task_note = ""
            if proj["task_count"] > 0:
                task_note = f"  ·  *{proj['task_count']} task(s) assigned*"

            st.markdown(f"#### {badge}{title}{task_note}")

            for person in proj["people"]:
                uid  = person["user_id"]
                name = person["name"]
                key  = (pid, uid)

                checked = st.checkbox(
                    name,
                    key=f"access_{pid}_{uid}",
                    value=selected.get(key, False),
                )
                selected[key] = checked

            st.markdown("---")

        apply_selection = st.form_submit_button("Update selection")
        if apply_selection:
            st.session_state.access_selected = selected

    to_send: list[tuple[str, str, str]] = []
    for proj in assignments:
        pid   = proj["project_id"]
        title = proj["project_title"]
        for person in proj["people"]:
            if selected.get((pid, person["user_id"]), False):
                to_send.append((pid, title, person["name"]))

    col_btn, col_count = st.columns([4, 1])
    col_count.caption(f"{len(to_send)} selected")

    if col_btn.button(
        "📨  Send Access Information",
        type="primary",
        disabled=(len(to_send) == 0),
    ):
        by_project: dict[str, list[str]] = {}
        for pid, ptitle, pname in to_send:
            by_project.setdefault(ptitle, []).append(pname)

        lines = []
        for ptitle, names in by_project.items():
            try:
                info = excel_utils.get_project_info(ptitle)
                has_info = info is not None
            except Exception:
                has_info = False

            info_note = "✅ project info found in Excel" if has_info else "⚠️ no Excel entry for this project"
            lines.append(f"**{ptitle}** ({info_note}) → {', '.join(names)}")

        st.success("Access information will be sent to:\n\n" + "\n\n".join(lines))
        st.info("⚙️  Delivery logic not yet implemented — hook your method (email / Slack / Wrike comment) here.")


# PROJECTS PAGE
elif st.session_state.page == "projects":
    st.header("📁 Projects")

    col_refresh, _ = st.columns([2, 6])
    if col_refresh.button("Refresh Projects", key="refresh_projects_cache"):
        st.cache_data.clear()
        st.rerun()

    @st.cache_data(ttl=300, show_spinner="Loading projects from Wrike…")
    def _load_projects_hierarchy():
        return wrike.get_projects_hierarchy()

    try:
        sections   = _load_projects_hierarchy()
        load_error = None
    except Exception as e:
        sections   = {}
        load_error = str(e)

    if load_error:
        st.error(f"Could not load projects from Wrike: {load_error}")
        st.stop()

    if not sections:
        st.info("No projects found.")
        st.stop()

    st.caption("Wrike-style structure by section, with nested projects, epics/agents, features, and user stories.")
    st.markdown("---")

    STATUS_BADGE = {"Active": "🟢", "In Progress": "🟡", "Completed": "✅"}

    def _render_task_node(task_node: dict, depth: int = 0):
        indent = "&nbsp;" * (depth * 4)
        t_type = task_node.get("type", "Task")
        title = task_node.get("title", "Untitled")
        status = task_node.get("status", "Unknown")
        assignees = ", ".join(task_node.get("assignees", [])) if task_node.get("assignees") else "Unassigned"
        type_prefix = f"**[{t_type}]** " if t_type else ""
        st.markdown(f"{indent}- {type_prefix}{title}  ·  {status}  ·  {assignees}", unsafe_allow_html=True)
        for child_task in task_node.get("children", []):
            _render_task_node(child_task, depth + 1)

    def _render_project_node(
        node: dict,
        depth: int = 0,
        show_info_tab: bool = False,
        section_name: str | None = None,
    ):
        badge = STATUS_BADGE.get(node.get("status"), "⚪")
        indent = "&nbsp;" * (depth * 4)
        node_type = node.get("node_type", "Folder")
        dates = ""
        if node.get("start_date") != "—" or node.get("end_date") != "—":
            dates = f"<span style='color:grey;font-size:0.82em'> {node.get('start_date')} → {node.get('end_date')}</span>"

        type_prefix = f"[{node_type}] " if node_type else ""
        label = f"{indent}{badge} {type_prefix}{node.get('title', 'Untitled')}"
        with st.expander(label, expanded=False):
            if show_info_tab:
                tab_hierarchy, tab_info = st.tabs(["Hierarchy", "Info"])
            else:
                tab_hierarchy = st.container()
                tab_info = None

            with tab_hierarchy:
                if dates:
                    st.markdown(dates, unsafe_allow_html=True)

                owners = node.get("assignees", [])
                if owners:
                    st.markdown(f"**Assigned people:** {', '.join(owners)}")

                tasks = node.get("tasks", [])
                if tasks:
                    st.markdown("**Work items**")
                    for t in tasks:
                        _render_task_node(t)

                children = node.get("children", [])
                if children:
                    st.markdown("**Subprojects**")
                    for child in children:
                        _render_project_node(child, depth + 1, show_info_tab=False, section_name=section_name)

            if tab_info is not None:
                with tab_info:
                    try:
                        info = excel_utils.get_project_info(node.get("title", ""), section_name=section_name)
                        if info is None:
                            st.info("No entry found for this project in the Excel sheet.")
                        else:
                            for field, (icon, label_txt) in excel_utils.FRIENDLY_LABELS.items():
                                value = info.get(field, "—")
                                st.markdown(f"**{icon} {label_txt}:** {value}")
                    except FileNotFoundError as e:
                        st.warning(str(e))
                    except Exception as e:
                        st.warning(f"Excel read error: {e}")

    for section_name, top_projects in sections.items():
        with st.expander(section_name, expanded=False):
            if not top_projects:
                st.caption("No projects in this section.")
            for p in top_projects:
                _render_project_node(p, show_info_tab=True, section_name=section_name)
