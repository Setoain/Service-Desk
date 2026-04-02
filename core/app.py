import streamlit as st
from dotenv import load_dotenv
from agent import run_agent
import wrike
import excel_utils

load_dotenv()

st.set_page_config(page_title="Service Desk", page_icon="🏢", layout="centered")

# ── Session state defaults ────────────────────────────────────────────────────
_DEFAULTS = {
    "page":             "chat",
    "messages":         [],
    "hr_submit_status": None,
    "hr_submit_debug":  None,
    "show_pto_form":    False,
    # Access Request: {(project_id, user_id): bool}
    "access_selected":  {},
    # Projects page: title of currently open info panel
    "expanded_project": None,
}
for k, v in _DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ── Sidebar navigation ────────────────────────────────────────────────────────
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


# ══════════════════════════════════════════════════════════════════════════════
#  CHAT
# ══════════════════════════════════════════════════════════════════════════════
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


# ══════════════════════════════════════════════════════════════════════════════
#  HR OPERATIONS
# ══════════════════════════════════════════════════════════════════════════════
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


# ══════════════════════════════════════════════════════════════════════════════
#  ACCESS REQUEST  —  recent Wrike assignments
# ══════════════════════════════════════════════════════════════════════════════
elif st.session_state.page == "access":
    st.header("🔑 Access Request")

    # ── Time-window selector ──────────────────────────────────────────────────
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

    # ── Load assignments ──────────────────────────────────────────────────────
    with st.spinner("Scanning Wrike for recent assignments…"):
        try:
            assignments = wrike.get_recent_assignments(days_back=days_back)
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

    # ── Render one card per project ───────────────────────────────────────────
    # selected: {(project_id, user_id): bool}
    selected: dict[tuple, bool] = st.session_state.access_selected

    for proj in assignments:
        pid   = proj["project_id"]
        title = proj["project_title"]

        # Card header
        badge = "🆕 " if proj["is_new"] else ""
        task_note = ""
        if proj["task_count"] > 0:
            task_note = f"  ·  *{proj['task_count']} task(s) assigned*"

        st.markdown(f"#### {badge}{title}{task_note}")

        # One checkbox per person in this project
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

        st.session_state.access_selected = selected
        st.markdown("---")

    # ── Send button ───────────────────────────────────────────────────────────
    # Build list of (project_title, person_name) for selected checkboxes
    to_send: list[tuple[str, str, str]] = []  # (project_id, project_title, person_name)
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
        # ── Group by project to show what info will be sent ───────────────────
        by_project: dict[str, list[str]] = {}
        for pid, ptitle, pname in to_send:
            by_project.setdefault(ptitle, []).append(pname)

        lines = []
        for ptitle, names in by_project.items():
            # Look up what Excel info exists for this project
            try:
                info = excel_utils.get_project_info(ptitle)
                has_info = info is not None
            except Exception:
                has_info = False

            info_note = "✅ project info found in Excel" if has_info else "⚠️ no Excel entry for this project"
            lines.append(f"**{ptitle}** ({info_note}) → {', '.join(names)}")

        st.success("Access information will be sent to:\n\n" + "\n\n".join(lines))
        st.info("⚙️  Delivery logic not yet implemented — hook your method (email / Slack / Wrike comment) here.")


# ══════════════════════════════════════════════════════════════════════════════
#  PROJECTS  —  full Wrike project list + Excel info panel
# ══════════════════════════════════════════════════════════════════════════════
elif st.session_state.page == "projects":
    st.header("📁 Projects")

    @st.cache_data(ttl=300, show_spinner="Loading projects from Wrike…")
    def _load_projects():
        return wrike.get_all_wrike_projects()

    try:
        projects   = _load_projects()
        load_error = None
    except Exception as e:
        projects   = []
        load_error = str(e)

    if load_error:
        st.error(f"Could not load projects from Wrike: {load_error}")
        st.stop()

    if not projects:
        st.info("No projects found.")
        st.stop()

    # ── Filters ───────────────────────────────────────────────────────────────
    all_sections = sorted({p["section"] for p in projects})

    col_s, col_f, col_q = st.columns([2, 2, 3])
    selected_section = col_s.selectbox("Section", ["All sections"] + all_sections, key="proj_section_filter")
    selected_status  = col_f.selectbox("Status",  ["All", "Active", "In Progress", "Completed"], key="proj_status_filter")
    search_query     = col_q.text_input("Search",  placeholder="Filter by name…", key="proj_search")

    filtered = projects
    if selected_section != "All sections":
        filtered = [p for p in filtered if p["section"] == selected_section]
    if selected_status != "All":
        filtered = [p for p in filtered if p["status"] == selected_status]
    if search_query.strip():
        q = search_query.strip().lower()
        filtered = [p for p in filtered if q in p["title"].lower()]

    st.caption(f"Showing **{len(filtered)}** of {len(projects)} projects")
    st.markdown("---")

    STATUS_BADGE = {"Active": "🟢", "In Progress": "🟡", "Completed": "✅"}
    current_section = None

    for proj in filtered:
        if proj["section"] != current_section:
            current_section = proj["section"]
            st.markdown(f"### {current_section}")

        badge = STATUS_BADGE.get(proj["status"], "⚪")
        dates = ""
        if proj["start_date"] != "—" or proj["end_date"] != "—":
            dates = f"&nbsp;&nbsp;<span style='color:grey;font-size:0.82em'>{proj['start_date']} → {proj['end_date']}</span>"

        col_title, col_btn = st.columns([7, 1])
        col_title.markdown(f"{badge} **{proj['title']}**{dates}", unsafe_allow_html=True)

        is_open   = st.session_state.expanded_project == proj["title"]
        btn_label = "▲" if is_open else "ℹ️"
        if col_btn.button(btn_label, key=f"info_{proj['id']}"):
            st.session_state.expanded_project = None if is_open else proj["title"]
            st.rerun()

        if st.session_state.expanded_project == proj["title"]:
            try:
                info      = excel_utils.get_project_info(proj["title"])
                excel_err = None
            except FileNotFoundError as e:
                info, excel_err = None, str(e)
            except Exception as e:
                info, excel_err = None, f"Excel read error: {e}"

            if excel_err:
                st.warning(excel_err)
            elif info is None:
                st.info(f"No entry found for **{proj['title']}** in the Excel sheet.")
            else:
                with st.expander(f"📋  {proj['title']} — project info", expanded=True):
                    for field, (icon, label) in excel_utils.FRIENDLY_LABELS.items():
                        value = info.get(field, "—")
                        st.markdown(f"**{icon} {label}:** &nbsp; `{value}`", unsafe_allow_html=True)

        st.divider()
