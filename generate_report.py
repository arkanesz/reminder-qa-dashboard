from collections import OrderedDict, defaultdict
from datetime import datetime

from common import run_query, send_telegram

BULAN_ID = [
    "Januari", "Februari", "Maret", "April", "Mei", "Juni",
    "Juli", "Agustus", "September", "Oktober", "November", "Desember",
]

QUERY = """
SELECT
  p.key || '-' || i.key_num AS issue_code,
  i.title,
  (
    SELECT NULLIF(COUNT(sub.id), 0)
    FROM issues sub
    WHERE sub.parent_issue_id = i.id
  ) AS total_subtasks,
  us1.name AS reporter,
  wfstat.name AS status
FROM issues i
LEFT JOIN issues parent ON parent.id = i.parent_issue_id
LEFT JOIN projects p ON i.project_id = p.id
LEFT JOIN users us1 ON us1.id = i.reporter_id
LEFT JOIN workflow_statuses wfstat ON wfstat.id = i.status_id
LEFT JOIN issue_types itp ON itp.id = i.type_id
WHERE
  wfstat.name IN ('Ready For QA', 'In QA', 'Pending QA', 'Checking')
  AND (itp.name != 'Sub-task' OR itp.name IS NULL)
  AND p.key || '-' || parent.key_num IS NULL
ORDER BY us1.name ASC;
"""


def tanggal_indonesia():
    now = datetime.now()
    return f"{now.day} {BULAN_ID[now.month - 1]} {now.year}"


def group_tickets(rows):
    by_reporter = defaultdict(list)
    for issue_code, title, total_subtasks, reporter, status in rows:
        reporter = reporter or "(Tanpa Reporter)"
        by_reporter[reporter].append((issue_code, title, total_subtasks, status))
    return OrderedDict(sorted(by_reporter.items(), key=lambda kv: kv[0]))


def build_message(grouped):
    lines = [f"Rekap Data Tiket ({tanggal_indonesia()}) \U0001F4CA", ""]

    for reporter, tickets in grouped.items():
        subtask_sum = sum(t[2] for t in tickets if t[2])
        main_ticket_count = sum(1 for t in tickets if not t[2])

        header_parts = []
        if subtask_sum > 0:
            header_parts.append(f"{subtask_sum} Subtask")
        if main_ticket_count > 0:
            header_parts.append(f"{main_ticket_count} Tiket Utama")
        if not header_parts:
            header_parts = ["0 Subtask"]

        lines.append(f"\U0001F464 {reporter} (Total: {', '.join(header_parts)})")
        for issue_code, title, total_subtasks, status in tickets:
            label = f"{total_subtasks} Subtask" if total_subtasks else "1 Tiket Utama"
            lines.append(f"▪️ [{issue_code}] {title} ({label}) - {status}")
        lines.append("")

    return "\n".join(lines).rstrip()


def main():
    rows = run_query(QUERY)
    if not rows:
        print("Tidak ada tiket untuk direkap.")
        return
    grouped = group_tickets(rows)
    message = build_message(grouped)
    send_telegram(message)
    print(f"Report terkirim ({len(rows)} tiket, {len(grouped)} reporter).")


if __name__ == "__main__":
    main()
