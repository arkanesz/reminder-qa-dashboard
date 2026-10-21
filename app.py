import os
import re
import urllib.parse
from datetime import datetime

import streamlit as st

import common
import check_stale_tickets
import generate_report
import lavender_auth
import local_db
import send_reminder

UPLOAD_DIR = "uploads"

st.set_page_config(page_title="QA Dashboard", layout="wide")
local_db.init_db()


def format_wa_number(raw):
    if not raw:
        return ""
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("0"):
        digits = "62" + digits[1:]
    elif not digits.startswith("62"):
        digits = "62" + digits
    return digits


def build_wa_message(issue_code, title, status, days, hours, minutes):
    return (
        f"Halo, mau follow up tiket [{issue_code}] {title}.\n"
        f"Status saat ini: {status}, sudah H+{days} hari {hours} jam {minutes} menit "
        f"belum ada perubahan status.\n"
        f"Mohon dibantu segera di-cek dan di-update progressnya ya. Terima kasih!"
    )


def save_screenshot(issue_code, uploaded_file):
    folder = os.path.join(UPLOAD_DIR, issue_code)
    os.makedirs(folder, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    ext = os.path.splitext(uploaded_file.name)[1]
    path = os.path.join(folder, f"{ts}{ext}")
    with open(path, "wb") as f:
        f.write(uploaded_file.getbuffer())
    return path


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


def login_form():
    st.title("Login - QA Dashboard")
    with st.form("login_form"):
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Login")

    if not submitted:
        return

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

    if not (lavender_auth.is_admin(user_email) or local_db.is_activated(user_email)):
        st.warning(
            "Login berhasil, tapi akun Anda belum diaktivasi untuk dashboard ini. "
            "Hubungi Faris untuk aktivasi akses."
        )
        return

    st.session_state.user = {
        "email": user_email,
        "name": name,
        "is_admin": lavender_auth.is_admin(user_email),
    }
    st.rerun()


def page_reminder():
    st.header("Reminder QA")
    rows = common.run_query(send_reminder.QUERY)
    if not rows:
        st.info("Tidak ada tiket Ready For QA / In QA / Pending QA / Checking saat ini.")
        return
    grouped = send_reminder.group_tickets(rows)

    for reporter, statuses in grouped.items():
        with st.expander(f"{reporter}", expanded=True):
            for status in send_reminder.STATUS_ORDER:
                links = statuses.get(status)
                if not links:
                    continue
                st.markdown(f"**[{status}]**")
                for project_key, key_num, issue_link in links:
                    st.markdown(f"- [{project_key}-{key_num}]({issue_link})")

    st.divider()
    if st.button("Kirim ke Telegram", key="btn_send_reminder"):
        message = send_reminder.build_message(grouped)
        common.send_telegram(message)
        st.success(f"Terkirim ke Telegram ({len(rows)} tiket, {len(grouped)} reporter).")


def page_report():
    st.header("Report Rekap Ready For QA")
    rows = common.run_query(generate_report.QUERY)
    if not rows:
        st.info("Tidak ada tiket untuk direkap.")
        return
    grouped = generate_report.group_tickets(rows)

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

    st.divider()
    if st.button("Kirim ke Telegram", key="btn_send_report"):
        message = generate_report.build_message(grouped)
        common.send_telegram(message)
        total = sum(len(v) for v in grouped.values())
        st.success(f"Terkirim ke Telegram ({total} tiket, {len(grouped)} reporter).")


def page_stale(current_user_email):
    st.header("Tiket Mangkrak (> 24 Jam)")

    selected_statuses = st.multiselect(
        "Filter status",
        check_stale_tickets.ALL_STALE_STATUSES,
        default=check_stale_tickets.ALL_STALE_STATUSES,
    )
    if not selected_statuses:
        st.warning("Pilih minimal 1 status.")
        return

    rows = common.run_query(
        check_stale_tickets.QUERY, {"statuses": tuple(selected_statuses)}
    )
    grouped = check_stale_tickets.group_stale(rows)

    if not grouped:
        st.info("Tidak ada tiket yang mangkrak lebih dari 24 jam untuk status ini.")
        return

    phone_rows = common.run_query("SELECT name, phone FROM users WHERE phone IS NOT NULL")
    phone_map = {name: phone for name, phone in phone_rows if phone}

    for reporter, tickets in grouped.items():
        st.subheader(reporter)
        for issue_code, title, status, issue_link, days, hours, minutes in tickets:
            label = f"[{issue_code}] {title} - {status} (H+{days}h {hours}j {minutes}m)"
            with st.expander(label):
                st.markdown(f"[Buka tiket]({issue_link})")

                proofs = local_db.get_followups(issue_code)
                if proofs:
                    st.caption("Bukti follow-up sebelumnya:")
                    for pid, path, wa_number, note, uploaded_by, created_at in proofs:
                        cols = st.columns([1, 3])
                        with cols[0]:
                            if path and os.path.exists(path):
                                st.image(path, width=160)
                        with cols[1]:
                            st.write(f"Oleh: {uploaded_by}")
                            st.write(f"Waktu: {created_at}")
                            if wa_number:
                                st.write(f"Nomor WA: {wa_number}")

                uploaded_file = st.file_uploader(
                    "Upload screenshot bukti follow-up WA",
                    type=["png", "jpg", "jpeg"],
                    key=f"upload_{issue_code}",
                )
                if uploaded_file is not None:
                    if st.button("Simpan Screenshot", key=f"save_{issue_code}"):
                        path = save_screenshot(issue_code, uploaded_file)
                        local_db.add_followup(
                            issue_code, path, "", "", current_user_email
                        )
                        st.success("Screenshot bukti follow-up tersimpan.")
                        st.rerun()

                st.markdown("---")
                st.caption("Kirim pesan follow-up ke WhatsApp reporter")
                default_phone = format_wa_number(phone_map.get(reporter, ""))
                wa_number = st.text_input(
                    "Nomor WhatsApp reporter",
                    value=default_phone,
                    key=f"wa_num_{issue_code}",
                )
                wa_message = st.text_area(
                    "Pesan",
                    value=build_wa_message(issue_code, title, status, days, hours, minutes),
                    key=f"wa_msg_{issue_code}",
                    height=120,
                )
                if wa_number:
                    wa_link = (
                        f"https://wa.me/{format_wa_number(wa_number)}"
                        f"?text={urllib.parse.quote(wa_message)}"
                    )
                    st.link_button("Kirim WA ke Reporter", wa_link)
                else:
                    st.caption("Isi nomor WA dulu untuk mengaktifkan tombol kirim.")

    st.divider()
    if st.button("Kirim Ringkasan ke Telegram", key="btn_send_stale"):
        message = check_stale_tickets.build_message(grouped, selected_statuses)
        common.send_telegram(message)
        total = sum(len(v) for v in grouped.values())
        st.success(f"Terkirim ke Telegram ({total} tiket, {len(grouped)} reporter).")


def page_admin(admin_email):
    st.header("Admin - Aktivasi User Dashboard")
    st.caption("Cari user dari database Lavender, lalu aktifkan/nonaktifkan akses dashboard ini.")

    query = st.text_input("Cari user (nama/email)")
    if query:
        try:
            results = lavender_auth.search_users(query)
        except Exception as e:
            show_lavender_connection_error(e)
            return
        if not results:
            st.info("Tidak ditemukan.")
        for uid, name, email in results:
            activated = local_db.is_activated(email)
            col1, col2, col3 = st.columns([3, 3, 2])
            col1.write(name)
            col2.write(email)
            label = "Nonaktifkan" if activated else "Aktifkan"
            if col3.button(label, key=f"toggle_{email}"):
                local_db.set_activation(email, name, not activated, admin_email)
                st.rerun()

    st.divider()
    st.subheader("User yang sudah aktif")
    active_rows = [r for r in local_db.list_activations() if r[2]]
    if not active_rows:
        st.caption("Belum ada user yang diaktifkan selain admin.")
    for email, name, _ in active_rows:
        st.write(f"- {name} ({email})")


def main():
    if "user" not in st.session_state:
        st.session_state.user = None

    if not st.session_state.user:
        login_form()
        return

    user = st.session_state.user
    st.sidebar.write(f"Login sebagai **{user['name']}**")
    st.sidebar.caption(user["email"])
    if st.sidebar.button("Logout"):
        st.session_state.user = None
        st.rerun()

    menu_options = ["Reminder QA", "Report Rekap", "Tiket Mangkrak"]
    if user["is_admin"]:
        menu_options.append("Admin - Aktivasi User")

    page = st.sidebar.radio("Menu", menu_options)

    if page == "Reminder QA":
        page_reminder()
    elif page == "Report Rekap":
        page_report()
    elif page == "Tiket Mangkrak":
        page_stale(user["email"])
    elif page == "Admin - Aktivasi User":
        page_admin(user["email"])


main()
