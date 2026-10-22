import re
import urllib.parse
from datetime import time, timedelta, timezone

import streamlit as st
from streamlit_cookies_controller import CookieController

import common
import check_stale_tickets
import generate_report
import lavender_auth
import send_reminder
import supabase_backend as backend

SESSION_COOKIE = "qa_session_token"
SESSION_MAX_AGE = 7 * 24 * 3600  # 7 hari
JAKARTA_TZ = timezone(timedelta(hours=7))

st.set_page_config(page_title="QA Dashboard", layout="wide")
backend.init_db()


@st.cache_data(ttl=120, show_spinner=False)
def cached_query(query, params=None):
    return common.run_query(query, params)


@st.cache_data(ttl=300, show_spinner=False)
def cached_phone_map():
    rows = common.run_query("SELECT name, phone FROM users WHERE phone IS NOT NULL")
    return {name: phone for name, phone in rows if phone}


@st.cache_data(ttl=1800, show_spinner=False)
def cached_signed_url(storage_path):
    return backend.get_signed_url(storage_path)


@st.cache_data(ttl=300, show_spinner=False)
def cached_reporter_phones():
    return backend.get_all_reporter_phones()


@st.cache_data(ttl=60, show_spinner=False)
def cached_history_codes():
    return backend.get_issue_codes_with_followups()


@st.cache_data(ttl=30, show_spinner=False)
def cached_followups_batch(issue_codes):
    return backend.get_followups_batch(issue_codes)


@st.cache_data(ttl=1800, show_spinner=False)
def cached_signed_urls_batch(storage_paths):
    return backend.get_signed_urls_batch(storage_paths)


@st.cache_data(ttl=30, show_spinner=False)
def cached_wa_log_batch(issue_codes):
    return backend.get_wa_log_batch(issue_codes)


@st.cache_data(ttl=3600, show_spinner=False)
def cached_bot_info():
    try:
        return common.get_telegram_bot_info()
    except Exception:
        return None


def telegram_bot_caption():
    bot_info = cached_bot_info()
    if bot_info and bot_info.get("username"):
        st.caption(f"📤 Bot Telegram: @{bot_info['username']}")


TICKET_LOOKUP_QUERY = """
SELECT
  p.key || '-' || i.key_num AS issue_code,
  i.title,
  wfstat.name AS status,
  'https://tulip.prestisa.id/projects/' || p.id || '/issue/' || p.key || '-' || i.key_num AS issue_link
FROM issues i
LEFT JOIN projects p ON i.project_id = p.id
LEFT JOIN workflow_statuses wfstat ON wfstat.id = i.status_id
WHERE (p.key || '-' || i.key_num) = ANY(%(codes)s)
"""


def clear_proof_state():
    for key in list(st.session_state.keys()):
        if key.startswith("proofs_state_"):
            del st.session_state[key]


def format_wa_number(raw):
    if not raw:
        return ""
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("0"):
        digits = "62" + digits[1:]
    elif not digits.startswith("62"):
        digits = "62" + digits
    return digits


def build_wa_message(issue_code, title, status, days, hours, minutes, issue_link):
    return (
        f"Halo, mau follow up tiket [{issue_code}] {title}.\n"
        f"Status saat ini: {status}, sudah H+{days} hari {hours} jam {minutes} menit "
        f"belum ada perubahan status.\n"
        f"Link tiket: {issue_link}\n"
        f"Mohon dibantu segera di-cek dan di-update progressnya ya.\n"
        f"Jika ada kendala karena tidak bisa testing (contoh: meeting atau halangan lainnya), "
        f"tolong kabari. Jika tidak ada kabar hingga jam kerja berakhir, "
        f"maka akan dieskalasi ke management. Terima kasih!"
    )


@st.fragment
def render_followup_section(
    issue_code,
    current_user_email,
    initial_proofs=(),
    initial_signed_urls=None,
    initial_wa_log=(),
    wa_context=None,
    phone_map=None,
    saved_phone_map=None,
):
    initial_signed_urls = initial_signed_urls or {}
    state_key = f"proofs_state_{issue_code}"
    if state_key not in st.session_state:
        st.session_state[state_key] = list(initial_proofs)
    proofs = st.session_state[state_key]

    if proofs:
        st.caption("Bukti follow-up sebelumnya:")
        for pid, storage_path, wa_number, note, uploaded_by, created_at in proofs:
            cols = st.columns([1, 3, 1])
            with cols[0]:
                if storage_path:
                    try:
                        signed_url = initial_signed_urls.get(
                            storage_path
                        ) or cached_signed_url(storage_path)
                        st.image(signed_url, width=160)
                    except Exception:
                        st.caption("(gagal muat gambar)")
            with cols[1]:
                st.write(f"Oleh: {uploaded_by}")
                waktu_wib = created_at.astimezone(JAKARTA_TZ)
                st.write(f"Waktu: {waktu_wib.strftime('%Y-%m-%d %H:%M:%S')} WIB")
                if wa_number:
                    st.write(f"Nomor WA: {wa_number}")
                if note:
                    st.write(f"Alasan: {note}")
            with cols[2]:
                if st.button("🗑️ Hapus", key=f"delete_proof_{pid}"):
                    backend.delete_followup(pid, storage_path)
                    st.session_state[state_key] = [
                        p for p in st.session_state[state_key] if p[0] != pid
                    ]
                    cached_followups_batch.clear()
                    st.toast("Bukti follow-up dihapus.", icon="✅")
                    st.rerun(scope="fragment")

    uploaded_file = st.file_uploader(
        "Upload screenshot bukti follow-up WA",
        type=["png", "jpg", "jpeg"],
        key=f"upload_{issue_code}",
    )
    note_input = st.text_area(
        "Alasan mangkrak/warning dari reporter (opsional)",
        key=f"note_{issue_code}",
        height=68,
    )
    if uploaded_file is not None:
        if st.button("Simpan Screenshot", key=f"save_{issue_code}"):
            storage_path = backend.upload_screenshot(
                issue_code,
                uploaded_file.name,
                uploaded_file.getbuffer(),
                uploaded_file.type,
            )
            backend.add_followup(
                issue_code, storage_path, "", note_input.strip(), current_user_email
            )
            st.session_state[state_key] = backend.get_followups(issue_code)
            cached_followups_batch.clear()
            st.toast("Screenshot bukti follow-up tersimpan.", icon="✅")
            st.rerun(scope="fragment")

    wa_log_state_key = f"wa_log_state_{issue_code}"
    if wa_log_state_key not in st.session_state:
        st.session_state[wa_log_state_key] = list(initial_wa_log)
    wa_log = st.session_state[wa_log_state_key]

    if wa_log:
        st.caption("Riwayat kirim WA ke reporter:")
        for log_id, reporter_log, phone_log, message_log, sent_by, sent_at in wa_log:
            sent_at_wib = sent_at.astimezone(JAKARTA_TZ)
            log_col1, log_col2 = st.columns([5, 1])
            with log_col1:
                st.write(
                    f"- {sent_at_wib.strftime('%Y-%m-%d %H:%M:%S')} WIB — "
                    f"{sent_by} kirim ke {reporter_log} ({phone_log})"
                )
            with log_col2:
                if st.button("🗑️", key=f"delete_walog_{log_id}"):
                    backend.delete_wa_log(log_id)
                    st.session_state[wa_log_state_key] = [
                        w for w in st.session_state[wa_log_state_key] if w[0] != log_id
                    ]
                    cached_wa_log_batch.clear()
                    st.toast("Riwayat kirim WA dihapus.", icon="✅")
                    st.rerun(scope="fragment")

    if not wa_context:
        return

    reporter = wa_context["reporter"]
    title = wa_context["title"]
    status = wa_context["status"]
    issue_link = wa_context["issue_link"]
    days, hours, minutes = wa_context["days"], wa_context["hours"], wa_context["minutes"]
    phone_map = phone_map or {}
    saved_phone_map = saved_phone_map or {}

    st.markdown("---")
    st.caption("Kirim pesan follow-up ke WhatsApp reporter")

    phone_state_key = f"saved_phone_{reporter}"
    if phone_state_key not in st.session_state:
        st.session_state[phone_state_key] = saved_phone_map.get(reporter)
    saved_phone = st.session_state[phone_state_key]

    default_phone = format_wa_number(saved_phone or phone_map.get(reporter, ""))
    wa_number = st.text_input(
        "Nomor WhatsApp reporter",
        value=default_phone,
        key=f"wa_num_{issue_code}",
    )
    wa_message = st.text_area(
        "Pesan",
        value=build_wa_message(issue_code, title, status, days, hours, minutes, issue_link),
        key=f"wa_msg_{issue_code}",
        height=120,
    )
    if wa_number:
        formatted_number = format_wa_number(wa_number)
        if formatted_number and formatted_number != saved_phone:
            backend.save_reporter_phone(reporter, formatted_number)
            cached_reporter_phones.clear()
            st.session_state[phone_state_key] = formatted_number
            saved_phone = formatted_number
        wa_link = (
            f"https://wa.me/{formatted_number}?text={urllib.parse.quote(wa_message)}"
        )
        col_send, col_mark = st.columns(2)
        with col_send:
            st.link_button("Kirim WA ke Reporter", wa_link)
        with col_mark:
            if st.button("✅ Tandai sudah dikirim", key=f"mark_sent_{issue_code}"):
                new_id, sent_at = backend.log_wa_sent(
                    issue_code, reporter, formatted_number, wa_message, current_user_email
                )
                existing_ids = {w[0] for w in st.session_state[wa_log_state_key]}
                if new_id not in existing_ids:
                    st.session_state[wa_log_state_key].append(
                        (new_id, reporter, formatted_number, wa_message, current_user_email, sent_at)
                    )
                cached_wa_log_batch.clear()
                st.toast("Dicatat di riwayat kirim WA.", icon="✅")
                st.rerun(scope="fragment")
    else:
        st.caption("Isi nomor WA dulu untuk mengaktifkan tombol kirim.")

    if saved_phone:
        st.caption(f"📌 Nomor tersimpan untuk {reporter}: {saved_phone}")


def show_lavender_connection_error(exc):
    st.error(f"Gagal konek ke database Lavender: {exc}")
    ip = lavender_auth.get_public_ip()
    if ip:
        st.warning(
            f"Kemungkinan IP ini belum di-whitelist di Lavender: **{ip}**\n\n"
            "Tolong tambahkan IP ini ke whitelist Lavender, lalu coba lagi."
        )
    else:
        st.warning("Gagal juga mendeteksi IP publik saat ini. Coba lagi beberapa saat.")


def login_form(cookies):
    st.title("Login - QA Dashboard")
    with st.form("login_form"):
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Login")

    if not submitted:
        return

    with st.spinner("Memverifikasi akun..."):
        try:
            user = lavender_auth.find_user(email.strip())
        except Exception as e:
            show_lavender_connection_error(e)
            return

        if not user:
            st.error("Email tidak ditemukan di Lavender.")
            return

        _, name, user_email, hashed = user
        if not lavender_auth.verify_password(password, hashed):
            st.error("Password salah.")
            return

        if not (lavender_auth.is_admin(user_email) or backend.is_activated(user_email)):
            st.warning(
                "Login berhasil, tapi akun Anda belum diaktivasi untuk dashboard ini. "
                "Hubungi Faris untuk aktivasi akses."
            )
            return

        token = backend.create_session(user_email, name)

    cookies.set(SESSION_COOKIE, token, max_age=SESSION_MAX_AGE)
    st.session_state.user = {
        "email": user_email,
        "name": name,
        "is_admin": lavender_auth.is_admin(user_email),
    }
    st.rerun()


def page_reminder():
    st.header("Reminder QA")
    with st.spinner("Memuat data tiket..."):
        rows = cached_query(send_reminder.QUERY)
    grouped = send_reminder.group_tickets(rows) if rows else {}

    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔄 Refresh data", key="refresh_reminder"):
            cached_query.clear()
            st.rerun()
    with col2:
        if rows and st.button("Kirim ke Telegram", key="btn_send_reminder"):
            message = send_reminder.build_message(grouped)
            common.send_telegram(message)
            st.success(f"Terkirim ke Telegram ({len(rows)} tiket, {len(grouped)} reporter).")
        telegram_bot_caption()

    if not rows:
        st.info("Tidak ada tiket Ready For QA / In QA / Pending QA / Checking saat ini.")
        return

    for reporter, statuses in grouped.items():
        with st.expander(f"{reporter}", expanded=True):
            for status in send_reminder.STATUS_ORDER:
                links = statuses.get(status)
                if not links:
                    continue
                st.markdown(f"**[{status}]**")
                for project_key, key_num, issue_link in links:
                    st.markdown(f"- [{project_key}-{key_num}]({issue_link})")


def page_report():
    st.header("Report Rekap Ready For QA")
    with st.spinner("Memuat data tiket..."):
        rows = cached_query(generate_report.QUERY)
    grouped = generate_report.group_tickets(rows) if rows else {}

    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔄 Refresh data", key="refresh_report"):
            cached_query.clear()
            st.rerun()
    with col2:
        if rows and st.button("Kirim ke Telegram", key="btn_send_report"):
            message = generate_report.build_message(grouped)
            common.send_telegram(message)
            total = sum(len(v) for v in grouped.values())
            st.success(f"Terkirim ke Telegram ({total} tiket, {len(grouped)} reporter).")
        telegram_bot_caption()

    if not rows:
        st.info("Tidak ada tiket untuk direkap.")
        return

    for reporter, tickets in grouped.items():
        subtask_sum = sum(t[2] for t in tickets if t[2])
        main_ticket_count = sum(1 for t in tickets if not t[2])
        parts = []
        if subtask_sum > 0:
            parts.append(f"{subtask_sum} Subtask")
        if main_ticket_count > 0:
            parts.append(f"{main_ticket_count} Tiket Utama")
        if not parts:
            parts = ["0 Subtask"]

        with st.expander(f"{reporter} (Total: {', '.join(parts)})", expanded=True):
            for issue_code, title, total_subtasks, status in tickets:
                label = f"{total_subtasks} Subtask" if total_subtasks else "1 Tiket Utama"
                st.markdown(f"- [{issue_code}] {title} ({label}) - {status}")


DASHBOARD_STATUS_OPTIONS = check_stale_tickets.ALL_STALE_STATUSES + ["Done QA"]


def render_ticket_list_page(
    current_user_email, page_title, min_hours, max_hours, key_prefix, telegram_title, empty_message
):
    st.header(page_title)

    selected_statuses = st.multiselect(
        "Filter status",
        DASHBOARD_STATUS_OPTIONS,
        default=check_stale_tickets.ALL_STALE_STATUSES,
        key=f"statuses_{key_prefix}",
    )
    if not selected_statuses:
        st.warning("Pilih minimal 1 status.")
        return

    with st.spinner("Memuat data tiket..."):
        rows = cached_query(
            check_stale_tickets.QUERY, {"statuses": tuple(selected_statuses)}
        )
        grouped = check_stale_tickets.group_by_elapsed(
            rows, min_hours=min_hours, max_hours=max_hours
        )

        phone_map = {}
        saved_phone_map = {}
        followups_by_code = {}
        signed_urls_by_path = {}
        wa_log_by_code = {}
        if grouped:
            phone_map = cached_phone_map()
            saved_phone_map = cached_reporter_phones()
            all_issue_codes = tuple(
                t[0] for tickets in grouped.values() for t in tickets
            )
            followups_by_code = cached_followups_batch(all_issue_codes)
            all_storage_paths = tuple(
                p[1] for proofs in followups_by_code.values() for p in proofs if p[1]
            )
            signed_urls_by_path = cached_signed_urls_batch(all_storage_paths)
            wa_log_by_code = cached_wa_log_batch(all_issue_codes)

    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔄 Refresh data", key=f"refresh_{key_prefix}"):
            cached_query.clear()
            cached_phone_map.clear()
            cached_followups_batch.clear()
            cached_signed_urls_batch.clear()
            cached_reporter_phones.clear()
            cached_wa_log_batch.clear()
            clear_proof_state()
            st.rerun()
    with col2:
        if grouped and st.button("Kirim Ringkasan ke Telegram", key=f"btn_send_{key_prefix}"):
            message = check_stale_tickets.build_message(
                grouped, selected_statuses, title=telegram_title
            )
            common.send_telegram(message)
            total = sum(len(v) for v in grouped.values())
            st.success(f"Terkirim ke Telegram ({total} tiket, {len(grouped)} reporter).")
        telegram_bot_caption()

    if not grouped:
        st.info(empty_message)
        return

    for reporter, tickets in grouped.items():
        st.subheader(reporter)
        for issue_code, title, status, issue_link, days, hours, minutes in tickets:
            label = f"[{issue_code}] {title} - {status} (H+{days}h {hours}j {minutes}m)"
            with st.expander(label):
                st.markdown(f"[Buka tiket]({issue_link})")
                render_followup_section(
                    issue_code,
                    current_user_email,
                    initial_proofs=followups_by_code.get(issue_code, []),
                    initial_signed_urls=signed_urls_by_path,
                    initial_wa_log=wa_log_by_code.get(issue_code, []),
                    wa_context={
                        "reporter": reporter,
                        "title": title,
                        "status": status,
                        "issue_link": issue_link,
                        "days": days,
                        "hours": hours,
                        "minutes": minutes,
                    },
                    phone_map=phone_map,
                    saved_phone_map=saved_phone_map,
                )


def page_stale(current_user_email):
    render_ticket_list_page(
        current_user_email,
        page_title="Tiket Mangkrak (> 24 Jam)",
        min_hours=24,
        max_hours=None,
        key_prefix="stale",
        telegram_title="Tiket Mangkrak > 24 Jam",
        empty_message="Tidak ada tiket yang mangkrak lebih dari 24 jam untuk status ini.",
    )


def page_warning(current_user_email):
    render_ticket_list_page(
        current_user_email,
        page_title="Tiket Warning (< 24 Jam)",
        min_hours=0,
        max_hours=24,
        key_prefix="warning",
        telegram_title="Tiket Warning < 24 Jam",
        empty_message="Tidak ada tiket di bawah 24 jam untuk status ini.",
    )


def page_history(current_user_email):
    st.header("Riwayat Follow-up")
    st.caption(
        "Semua tiket yang pernah ada bukti follow-up, termasuk yang statusnya "
        "sudah berubah / tidak lagi mangkrak."
    )

    if st.button("🔄 Refresh data", key="refresh_history"):
        cached_history_codes.clear()
        cached_query.clear()
        cached_followups_batch.clear()
        cached_signed_urls_batch.clear()
        cached_wa_log_batch.clear()
        clear_proof_state()
        st.rerun()

    with st.spinner("Memuat riwayat..."):
        issue_codes = cached_history_codes()
        if not issue_codes:
            st.info("Belum ada riwayat follow-up.")
            return
        ticket_rows = cached_query(TICKET_LOOKUP_QUERY, {"codes": list(issue_codes)})
        ticket_info = {row[0]: row[1:] for row in ticket_rows}

        followups_by_code = cached_followups_batch(tuple(issue_codes))
        all_storage_paths = tuple(
            p[1] for proofs in followups_by_code.values() for p in proofs if p[1]
        )
        signed_urls_by_path = cached_signed_urls_batch(all_storage_paths)
        wa_log_by_code = cached_wa_log_batch(tuple(issue_codes))

    for issue_code in issue_codes:
        info = ticket_info.get(issue_code)
        if info:
            title, status, issue_link = info
            label = f"[{issue_code}] {title} — Status saat ini: {status}"
        else:
            issue_link = None
            label = f"[{issue_code}] (tiket tidak ditemukan di Tulip, mungkin sudah dihapus)"
        with st.expander(label):
            if issue_link:
                st.markdown(f"[Buka tiket]({issue_link})")
            render_followup_section(
                issue_code,
                current_user_email,
                initial_proofs=followups_by_code.get(issue_code, []),
                initial_signed_urls=signed_urls_by_path,
                initial_wa_log=wa_log_by_code.get(issue_code, []),
            )


def page_admin(admin_email):
    st.header("Admin - Aktivasi User Dashboard")
    st.caption("Cari user dari database Lavender, lalu aktifkan/nonaktifkan akses dashboard ini.")

    query = st.text_input("Cari user (nama/email)")
    if query:
        with st.spinner("Mencari user..."):
            try:
                results = lavender_auth.search_users(query)
            except Exception as e:
                show_lavender_connection_error(e)
                return
        if not results:
            st.info("Tidak ditemukan.")
        activation_map = {e: a for e, _, a in backend.list_activations()}
        for uid, name, email in results:
            activated = activation_map.get(email, False)
            col1, col2, col3 = st.columns([3, 3, 2])
            col1.write(name)
            col2.write(email)
            label = "Nonaktifkan" if activated else "Aktifkan"
            if col3.button(label, key=f"toggle_{email}"):
                backend.set_activation(email, name, not activated, admin_email)
                st.rerun()

    st.divider()
    st.subheader("User yang sudah aktif")
    active_rows = [r for r in backend.list_activations() if r[2]]
    if not active_rows:
        st.caption("Belum ada user yang diaktifkan selain admin.")
    for email, name, _ in active_rows:
        st.write(f"- {name} ({email})")


def _parse_hhmm(value, fallback):
    try:
        hour, minute = value.split(":")
        return time(int(hour), int(minute))
    except (AttributeError, ValueError):
        return fallback


def page_settings(admin_email):
    st.header("Pengaturan")
    st.caption(
        "Atur jadwal auto-kirim Reminder QA dan Report Rekap ke Telegram. "
        "Jadwal ini dijalankan oleh GitHub Actions (cek berkala), bukan oleh app Streamlit ini — "
        "jadi tetap jalan walau tidak ada yang buka dashboard ini."
    )

    settings = backend.get_all_settings()

    st.subheader("Reminder QA Otomatis")
    reminder_enabled = st.checkbox(
        "Aktifkan auto-kirim Reminder QA",
        value=settings.get("reminder_auto_enabled") == "true",
        key="set_reminder_enabled",
    )
    reminder_time = st.time_input(
        "Jam kirim (WIB)",
        value=_parse_hhmm(settings.get("reminder_auto_time"), time(9, 0)),
        key="set_reminder_time",
    )

    st.divider()
    st.subheader("Report Rekap Otomatis")
    report_enabled = st.checkbox(
        "Aktifkan auto-kirim Report Rekap",
        value=settings.get("report_auto_enabled") == "true",
        key="set_report_enabled",
    )
    report_time = st.time_input(
        "Jam kirim (WIB)",
        value=_parse_hhmm(settings.get("report_auto_time"), time(16, 0)),
        key="set_report_time",
    )

    st.divider()
    if st.button("💾 Simpan Pengaturan"):
        backend.set_setting(
            "reminder_auto_enabled", "true" if reminder_enabled else "false", admin_email
        )
        backend.set_setting("reminder_auto_time", reminder_time.strftime("%H:%M"), admin_email)
        backend.set_setting(
            "report_auto_enabled", "true" if report_enabled else "false", admin_email
        )
        backend.set_setting("report_auto_time", report_time.strftime("%H:%M"), admin_email)
        st.success("Pengaturan tersimpan.")

    last_reminder = settings.get("reminder_last_sent_date")
    last_report = settings.get("report_last_sent_date")
    if last_reminder or last_report:
        st.divider()
        st.caption("Terakhir dikirim otomatis:")
        if last_reminder:
            st.write(f"- Reminder QA: {last_reminder}")
        if last_report:
            st.write(f"- Report Rekap: {last_report}")


def main():
    cookies = CookieController()

    if "user" not in st.session_state:
        st.session_state.user = None

    if not st.session_state.user:
        token = cookies.get(SESSION_COOKIE)
        if token:
            with st.spinner("Memuat sesi..."):
                session = backend.get_session(token)
            if session:
                email, name = session
                if lavender_auth.is_admin(email) or backend.is_activated(email):
                    st.session_state.user = {
                        "email": email,
                        "name": name,
                        "is_admin": lavender_auth.is_admin(email),
                    }

    if not st.session_state.user:
        login_form(cookies)
        return

    user = st.session_state.user
    st.sidebar.write(f"Login sebagai **{user['name']}**")
    st.sidebar.caption(user["email"])
    if st.sidebar.button("Logout"):
        token = cookies.get(SESSION_COOKIE)
        if token:
            backend.delete_session(token)
            cookies.remove(SESSION_COOKIE)
        st.session_state.user = None
        st.rerun()

    pages = {
        "Tiket": [
            st.Page(page_reminder, title="Reminder QA", icon="📋", url_path="reminder", default=True),
            st.Page(page_report, title="Report Rekap", icon="📊", url_path="report"),
            st.Page(
                lambda: page_warning(user["email"]),
                title="Tiket Warning (< 24 Jam)",
                icon="⏳",
                url_path="warning",
            ),
            st.Page(
                lambda: page_stale(user["email"]),
                title="Tiket Mangkrak",
                icon="🔥",
                url_path="mangkrak",
            ),
            st.Page(
                lambda: page_history(user["email"]),
                title="Riwayat Follow-up",
                icon="🕑",
                url_path="riwayat",
            ),
        ],
    }
    if user["is_admin"]:
        pages["Admin"] = [
            st.Page(
                lambda: page_admin(user["email"]),
                title="Aktivasi User",
                icon="👤",
                url_path="admin-aktivasi",
            ),
            st.Page(
                lambda: page_settings(user["email"]),
                title="Pengaturan",
                icon="⚙️",
                url_path="pengaturan",
            ),
        ]

    nav = st.navigation(pages)
    nav.run()


main()
