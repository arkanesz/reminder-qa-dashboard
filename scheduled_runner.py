from datetime import datetime, timedelta, timezone

import generate_report
import send_reminder
import supabase_backend as backend
from common import run_query, send_telegram

JAKARTA_TZ = timezone(timedelta(hours=7))


def should_run(settings, enabled_key, time_key, last_sent_key, now_wib, today_str):
    if settings.get(enabled_key) != "true":
        return False
    target = settings.get(time_key)
    if not target:
        return False
    try:
        target_hour, target_minute = (int(x) for x in target.split(":"))
    except ValueError:
        return False
    if settings.get(last_sent_key) == today_str:
        return False
    return (now_wib.hour, now_wib.minute) >= (target_hour, target_minute)


def run_reminder():
    rows = run_query(send_reminder.QUERY)
    if not rows:
        print("Reminder QA: tidak ada tiket, skip kirim.")
        return
    grouped = send_reminder.group_tickets(rows)
    message = send_reminder.build_message(grouped)
    send_telegram(message)
    print(f"Reminder QA auto-sent ({len(rows)} tiket, {len(grouped)} reporter).")


def run_report():
    rows = run_query(generate_report.QUERY)
    if not rows:
        print("Report Rekap: tidak ada tiket, skip kirim.")
        return
    grouped = generate_report.group_tickets(rows)
    message = generate_report.build_message(grouped)
    send_telegram(message)
    print(f"Report Rekap auto-sent ({len(rows)} tiket, {len(grouped)} reporter).")


def main():
    now_wib = datetime.now(JAKARTA_TZ)
    today_str = now_wib.strftime("%Y-%m-%d")
    settings = backend.get_all_settings()

    if should_run(settings, "reminder_auto_enabled", "reminder_auto_time", "reminder_last_sent_date", now_wib, today_str):
        run_reminder()
        backend.set_setting("reminder_last_sent_date", today_str, "scheduler")

    if should_run(settings, "report_auto_enabled", "report_auto_time", "report_last_sent_date", now_wib, today_str):
        run_report()
        backend.set_setting("report_last_sent_date", today_str, "scheduler")


if __name__ == "__main__":
    main()
