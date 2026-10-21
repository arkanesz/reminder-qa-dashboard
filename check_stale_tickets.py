import sys
from collections import OrderedDict, defaultdict
from datetime import datetime, timezone

from common import run_query, send_telegram

ALL_STALE_STATUSES = ["Ready For QA", "Pending QA", "Code Review", "Checking"]
STALE_THRESHOLD_HOURS = 24

QUERY = """
SELECT
  us1.name AS reporter,
  p.key || '-' || i.key_num AS issue_code,
  i.title,
  wfstat.name AS status,
  'https://tulip.prestisa.id/projects/' || p.id || '/issue/' || p.key || '-' || i.key_num AS issue_link,
  COALESCE(
    (
      SELECT ia.created_at
      FROM issue_activities ia
      WHERE ia.issue_id = i.id
        AND ia.field_name = 'status_id'
      ORDER BY ia.created_at DESC
      LIMIT 1
    ),
    i.created_at
  ) AS status_changed_at
FROM issues i
LEFT JOIN issues parent ON parent.id = i.parent_issue_id
LEFT JOIN projects p ON i.project_id = p.id
LEFT JOIN users us1 ON us1.id = i.reporter_id
LEFT JOIN workflow_statuses wfstat ON wfstat.id = i.status_id
LEFT JOIN issue_types itp ON itp.id = i.type_id
WHERE
  wfstat.name IN %(statuses)s
  AND (itp.name != 'Sub-task' OR itp.name IS NULL)
  AND p.key || '-' || parent.key_num IS NULL
ORDER BY us1.name ASC;
"""


def elapsed(changed_at):
    now = datetime.now(timezone.utc)
    total_minutes = int((now - changed_at).total_seconds() // 60)
    days, rem = divmod(total_minutes, 1440)
    hours, minutes = divmod(rem, 60)
    return days, hours, minutes


def group_stale(rows):
    by_reporter = defaultdict(list)
    for reporter, issue_code, title, status, issue_link, changed_at in rows:
        days, hours, minutes = elapsed(changed_at)
        if days * 24 + hours < STALE_THRESHOLD_HOURS:
            continue
        reporter = reporter or "(Tanpa Reporter)"
        by_reporter[reporter].append((issue_code, title, status, issue_link, days, hours, minutes))

    for tickets in by_reporter.values():
        tickets.sort(key=lambda t: (t[4], t[5], t[6]), reverse=True)

    return OrderedDict(
        sorted(by_reporter.items(), key=lambda kv: kv[1][0][4:7], reverse=True)
    )


def build_message(grouped, statuses):
    today = datetime.now().strftime("%d-%m-%Y %H:%M")
    status_label = " / ".join(statuses)
    lines = [f"⚠️ Tiket Mangkrak > 24 Jam - {status_label} ({today})", ""]

    for reporter, tickets in grouped.items():
        lines.append(f"\U0001F464 {reporter}")
        for issue_code, title, status, issue_link, days, hours, minutes in tickets:
            lines.append(f"▪️ [{issue_code}] {title} - {status}")
            lines.append(f"   H+{days} hari {hours} jam {minutes} menit")
            lines.append(f"   {issue_link}")
        lines.append("")

    return "\n".join(lines).rstrip()


def main():
    requested = sys.argv[1:] if len(sys.argv) > 1 else ALL_STALE_STATUSES
    statuses = [s for s in requested if s in ALL_STALE_STATUSES]
    if not statuses:
        print(f"Status tidak dikenali: {requested}")
        return

    rows = run_query(QUERY, {"statuses": tuple(statuses)})
    grouped = group_stale(rows)
    if not grouped:
        print(f"Tidak ada tiket [{', '.join(statuses)}] yang mangkrak lebih dari 24 jam.")
        return
    message = build_message(grouped, statuses)
    send_telegram(message)
    total = sum(len(v) for v in grouped.values())
    print(f"Peringatan tiket mangkrak terkirim ({total} tiket, {len(grouped)} reporter).")


if __name__ == "__main__":
    main()
