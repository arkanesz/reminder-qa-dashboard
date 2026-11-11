import sys
from collections import OrderedDict, defaultdict
from datetime import datetime, timezone, timedelta

from common import run_query, send_telegram

JAKARTA_TZ = timezone(timedelta(hours=7))

QUERY = """
SELECT
  us1.name AS reporter,
  p.key || '-' || i.key_num AS issue_code,
  i.title,
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
  ) AS status_changed_at,
  (
    SELECT MAX(c.created_at)
    FROM comments c
    WHERE c.issue_id = i.id
      AND c.author_id = i.reporter_id
      AND c.deleted_at IS NULL
  ) AS last_reporter_comment_at
FROM issues i
LEFT JOIN issues parent ON parent.id = i.parent_issue_id
LEFT JOIN projects p ON i.project_id = p.id
LEFT JOIN users us1 ON us1.id = i.reporter_id
LEFT JOIN workflow_statuses wfstat ON wfstat.id = i.status_id
LEFT JOIN issue_types itp ON itp.id = i.type_id
WHERE
  wfstat.name = 'Done QA'
  AND (itp.name != 'Sub-task' OR itp.name IS NULL)
  AND p.key || '-' || parent.key_num IS NULL
ORDER BY us1.name ASC;
"""


COMMENT_TOLERANCE = timedelta(minutes=10)


def reporter_has_commented(status_changed_at, last_reporter_comment_at):
    if status_changed_at is None or last_reporter_comment_at is None:
        return False
    return last_reporter_comment_at >= status_changed_at - COMMENT_TOLERANCE


def group_tickets(rows):
    by_reporter = defaultdict(list)
    for reporter, issue_code, title, issue_link, status_changed_at, last_reporter_comment_at in rows:
        reporter = reporter or "(Tanpa Reporter)"
        commented = reporter_has_commented(status_changed_at, last_reporter_comment_at)
        by_reporter[reporter].append(
            (issue_code, title, issue_link, status_changed_at, last_reporter_comment_at, commented)
        )
    return OrderedDict(sorted(by_reporter.items(), key=lambda kv: kv[0]))


def build_message(grouped):
    today = datetime.now(JAKARTA_TZ).strftime("%d-%m-%Y %H:%M")
    lines = [f"✅ Cek Komentar Reporter - Tiket Done QA ({today})", ""]

    for reporter, tickets in grouped.items():
        lines.append(f"\U0001F464 {reporter}")
        for issue_code, title, issue_link, status_changed_at, last_reporter_comment_at, commented in tickets:
            marker = "✅ Sudah komen" if commented else "⚠️ Belum komen (belum siap rilis)"
            lines.append(f"▪️ [{issue_code}] {title} - {marker}")
            lines.append(f"   {issue_link}")
        lines.append("")

    return "\n".join(lines).rstrip()


def main():
    rows = run_query(QUERY)
    if not rows:
        print("Tidak ada tiket dengan status Done QA saat ini.")
        return
    grouped = group_tickets(rows)
    message = build_message(grouped)
    send_telegram(message)
    total = sum(len(v) for v in grouped.values())
    belum = sum(1 for tickets in grouped.values() for t in tickets if not t[5])
    print(f"Terkirim ke Telegram ({total} tiket Done QA, {belum} belum komen).")


if __name__ == "__main__":
    main()
