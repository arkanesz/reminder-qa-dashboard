import uuid
from datetime import datetime

from common import run_query

KEYWORD_PATTERNS = {"a": "%meet IT%", "b": "%meeting IT%"}

TARGET_USERS = {
    "sahid it": "6e1df8c5-4cf3-4e34-82e9-612a8b015ca7",
    "ahya": "364d5a50-921b-47d9-8c65-c8ff11921a5a",
    "edo": "f337ba60-4dc3-401d-96a0-773665e36244",
    "wisnu IT": "6a5161e6-fada-4161-9561-17d9f371eb17",
    "Aditya": "dd0d76df-357d-43c8-89f1-66b80a6ef0fe",
    "farisark": "2766f1f3-431f-4b78-a540-12bdab35c2a9",
    "chaula": "b46fbd02-afe3-4747-b70d-64955f3d877b",
    "Fillah": "ae46ac15-b4da-4a83-99e0-139d24325760",
    "Wahyu Prakoso IT": "04a7d861-c243-4e8a-bce0-43836802c1a9",
}

# Recorded as added_by on every inserted row.
ADDED_BY_USER_ID = TARGET_USERS["farisark"]

OUTPUT_FILE = "backfill_meet_it_attendees.sql"
ROLLBACK_FILE = "rollback_meet_it_attendees.sql"


def find_matching_meetings():
    return run_query(
        """
        SELECT id, title
        FROM moms
        WHERE (title ILIKE %(a)s OR title ILIKE %(b)s)
          AND deleted_at IS NULL
        ORDER BY started_at ASC;
        """,
        KEYWORD_PATTERNS,
    )


def existing_attendee_user_ids(mom_id):
    rows = run_query(
        """
        SELECT user_id FROM mom_attendees
        WHERE mom_id = %(mid)s AND deleted_at IS NULL AND user_id IS NOT NULL;
        """,
        {"mid": mom_id},
    )
    return {r[0] for r in rows}


def main():
    meetings = find_matching_meetings()
    lines = [
        f"-- Backfill peserta meeting 'meet IT' / 'meeting IT'",
        f"-- Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"-- Menambahkan peserta yang belum ada, per meeting. Aman dijalankan ulang (NOT EXISTS guard).",
        "",
        "BEGIN;",
        "",
    ]

    total_inserts = 0
    generated_ids = []
    for mom_id, title in meetings:
        existing = existing_attendee_user_ids(mom_id)
        missing = [(name, uid) for name, uid in TARGET_USERS.items() if uid not in existing]
        if not missing:
            continue

        lines.append(f"-- {title} (mom_id: {mom_id})")
        for name, user_id in missing:
            new_id = str(uuid.uuid4())
            generated_ids.append(new_id)
            lines.append(
                "INSERT INTO mom_attendees (id, mom_id, user_id, added_by, status)\n"
                f"SELECT '{new_id}', '{mom_id}', '{user_id}', '{ADDED_BY_USER_ID}', 'hadir'\n"
                "WHERE NOT EXISTS (\n"
                "  SELECT 1 FROM mom_attendees\n"
                f"  WHERE mom_id = '{mom_id}' AND user_id = '{user_id}' AND deleted_at IS NULL\n"
                ");"
            )
            total_inserts += 1
        lines.append("")

    lines.append("COMMIT;")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    rollback_lines = [
        "-- Rollback untuk backfill_meet_it_attendees.sql",
        f"-- Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "-- Soft-delete baris yang ditambahkan backfill (pakai id unik per baris, tidak menyentuh data lain).",
        "-- Hanya jalankan ini kalau backfill_meet_it_attendees.sql SUDAH di-COMMIT dan ingin dibatalkan.",
        "",
        "BEGIN;",
        "",
        "UPDATE mom_attendees",
        "SET deleted_at = now()",
        "WHERE id IN (",
        "  " + ",\n  ".join(f"'{gid}'" for gid in generated_ids),
        ")",
        "AND deleted_at IS NULL;",
        "",
        "COMMIT;",
    ]
    with open(ROLLBACK_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(rollback_lines))

    print(f"{total_inserts} baris INSERT untuk {len(meetings)} meeting ditulis ke {OUTPUT_FILE}")
    print(f"Rollback (soft-delete) disiapkan di {ROLLBACK_FILE}")


if __name__ == "__main__":
    main()
