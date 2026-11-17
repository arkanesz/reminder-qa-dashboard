import sys
from collections import OrderedDict, defaultdict
from datetime import datetime, timezone, timedelta

from common import run_query, send_telegram

JAKARTA_TZ = timezone(timedelta(hours=7))

# A comment from this user also counts as "reporter sudah komen" - Faris reviews
# and approves releases himself, so his comment on behalf of/alongside the
# reporter satisfies the release requirement too.
AUTHORIZED_RELEASE_COMMENTER_EMAIL = "faris@prestisa.com"

QA_PHASE_STATUSES = ("Ready For QA", "In QA", "Pending QA", "Checking", "Done QA")

QUERY_PARAMS = {
    "authorized_email": AUTHORIZED_RELEASE_COMMENTER_EMAIL,
    "qa_statuses": QA_PHASE_STATUSES,
}

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
    SELECT ia.created_at
    FROM issue_activities ia
    LEFT JOIN workflow_statuses wo ON wo.id = (ia.old_value #>> '{}')
    LEFT JOIN workflow_statuses wn ON wn.id = (ia.new_value #>> '{}')
    WHERE ia.issue_id = i.id
      AND ia.field_name = 'status_id'
      AND wn.name IN %(qa_statuses)s
      AND (wo.name IS NULL OR wo.name NOT IN %(qa_statuses)s)
    ORDER BY ia.created_at DESC
    LIMIT 1
  ) AS qa_round_started_at,
  (
    SELECT MAX(c.created_at)
    FROM comments c
    LEFT JOIN users cu ON cu.id = c.author_id
    WHERE c.issue_id = i.id
      AND c.deleted_at IS NULL
      AND (c.author_id = i.reporter_id OR cu.email = %(authorized_email)s)
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


# The approving comment is often written moments before someone flips the status.
COMMENT_TOLERANCE = timedelta(minutes=10)


def reporter_has_commented(status_changed_at, qa_round_started_at, last_reporter_comment_at):
    # qa_round_started_at is when the ticket last entered a QA-phase status from a
    # non-QA one. Anchoring there (not on the previous status change) keeps bounces
    # between QA statuses, e.g. Done QA -> Ready For QA -> Done QA, from making the
    # approving comment look stale.
    if last_reporter_comment_at is None:
        return False
    anchor = qa_round_started_at or status_changed_at
    if anchor is None:
        return False
    return last_reporter_comment_at >= anchor - COMMENT_TOLERANCE


def group_tickets(rows):
    by_reporter = defaultdict(list)
    for reporter, issue_code, title, issue_link, status_changed_at, qa_round_started_at, last_reporter_comment_at in rows:
        reporter = reporter or "(Tanpa Reporter)"
        commented = reporter_has_commented(status_changed_at, qa_round_started_at, last_reporter_comment_at)
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
    rows = run_query(QUERY, QUERY_PARAMS)
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
