from collections import OrderedDict, defaultdict
from datetime import datetime

from common import run_query, send_telegram

STATUS_ORDER = ["Ready For QA", "In QA", "Pending QA", "Checking"]

QUERY = """
SELECT
  us1.name AS reporter,
  wfstat.name AS status,
  p.key AS project_key,
  i.key_num AS key_num,
  'https://tulip.prestisa.id/projects/' || p.id || '/issue/' || p.key || '-' || i.key_num AS issue_link
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
ORDER BY us1.name ASC, p.key ASC, i.key_num ASC;
"""


def group_tickets(rows):
    by_reporter = defaultdict(lambda: defaultdict(list))
    for reporter, status, project_key, key_num, issue_link in rows:
        reporter = reporter or "(Tanpa Reporter)"
        by_reporter[reporter][status].append((project_key, key_num, issue_link))
    for statuses in by_reporter.values():
        for links in statuses.values():
            links.sort(key=lambda t: (t[0], t[1]))
    return OrderedDict(sorted(by_reporter.items(), key=lambda kv: kv[0]))


def greeting():
    hour = datetime.now().hour
    if hour < 11:
        return "Selamat Pagi!"
    if hour < 15:
        return "Selamat Siang!"
    if hour < 19:
        return "Selamat Sore!"
    return "Selamat Malam!"


def build_message(grouped):
    lines = [
        greeting(),
        "",
        "Berikut daftar tiket terbaru untuk setiap PIC beserta statusnya:",
        "",
    ]

    for reporter, statuses in grouped.items():
        lines.append(f"@{reporter}")
        for status in STATUS_ORDER:
            links = statuses.get(status)
            if not links:
                continue
            lines.append(f"[{status}]")
            lines.extend(issue_link for _, _, issue_link in links)
            lines.append("")
        lines.append("")

    lines.append(
        "Segera di test dan komen ya, setelah itu jangan lupa komentar dan "
        "ubah statusnya sesuai hasil testing nya.. Thank you!"
    )
    return "\n".join(lines)


def main():
    rows = run_query(QUERY)
    if not rows:
        print("Tidak ada tiket dengan status Ready For QA / In QA / Pending QA / Checking.")
        return
    grouped = group_tickets(rows)
    message = build_message(grouped)
    send_telegram(message)
    print(f"Reminder terkirim ({len(rows)} tiket, {len(grouped)} reporter).")


if __name__ == "__main__":
    main()
