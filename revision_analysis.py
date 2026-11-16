import json
import subprocess
import sys

from common import run_query

QUERY = """
SELECT
  i.id AS issue_id,
  p.key || '-' || i.key_num AS issue_code,
  i.title,
  us1.name AS reporter,
  'https://tulip.prestisa.id/projects/' || p.id || '/issue/' || p.key || '-' || i.key_num AS issue_link,
  (
    SELECT c.body_md
    FROM comments c
    WHERE c.issue_id = i.id
      AND c.author_id = i.reporter_id
      AND c.deleted_at IS NULL
    ORDER BY c.created_at DESC
    LIMIT 1
  ) AS last_reporter_comment
FROM issues i
LEFT JOIN projects p ON i.project_id = p.id
LEFT JOIN users us1 ON us1.id = i.reporter_id
LEFT JOIN workflow_statuses wfstat ON wfstat.id = i.status_id
LEFT JOIN issue_types itp ON itp.id = i.type_id
WHERE
  wfstat.name = 'Revision'
  AND (itp.name != 'Sub-task' OR itp.name IS NULL)
  AND i.parent_issue_id IS NULL
ORDER BY us1.name ASC;
"""

SUBTASK_QUERY = """
SELECT
  p.key || '-' || s.key_num AS issue_code,
  s.title,
  wfstat.name AS status,
  'https://tulip.prestisa.id/projects/' || p.id || '/issue/' || p.key || '-' || s.key_num AS issue_link
FROM issues s
LEFT JOIN projects p ON s.project_id = p.id
LEFT JOIN workflow_statuses wfstat ON wfstat.id = s.status_id
WHERE s.parent_issue_id = %(parent_id)s
ORDER BY s.key_num ASC;
"""

CLAUDE_MODEL = "claude-haiku-5-5"

ANALYSIS_SYSTEM_PROMPT = (
    "Kamu adalah asisten yang membantu tim QA menentukan subtask mana yang perlu "
    "direvisi berdasarkan komentar reporter. Jawab HANYA dengan JSON valid, tanpa "
    "teks lain di luar JSON, dan tanpa markdown code fence."
)

ANALYSIS_PROMPT = """Tiket parent ini diubah statusnya menjadi "Revision" oleh reporter setelah testing, tapi status subtask-nya tidak otomatis ikut berubah. Tentukan subtask mana yang seharusnya ikut diubah ke Revision, berdasarkan komentar hasil testing reporter.

Judul tiket parent: {parent_title}

Komentar terakhir reporter (hasil testing):
\"\"\"
{comment_text}
\"\"\"

Daftar subtask dari tiket parent ini:
{subtask_list_text}

Berdasarkan isi komentar di atas, subtask mana saja yang paling relevan dengan masalah/revisi yang disebutkan reporter? Reporter bisa menyebutkan bagian/fitur secara eksplisit atau implisit (lewat deskripsi perilaku yang salah).

Jawab HANYA dengan JSON valid, persis format ini:
{{
  "recommended_codes": ["KODE-1", "KODE-2"],
  "confidence": "high" | "medium" | "low",
  "reasoning": "penjelasan singkat kenapa subtask ini yang dipilih, dalam Bahasa Indonesia"
}}

Kalau tidak ada subtask yang jelas relevan, kembalikan "recommended_codes": [] dan jelaskan alasannya di reasoning."""


def get_revision_tickets():
    return run_query(QUERY)


def get_subtasks(parent_issue_id):
    return run_query(SUBTASK_QUERY, {"parent_id": parent_issue_id})


def _empty_result(reasoning):
    return {"recommended_codes": [], "confidence": "low", "reasoning": reasoning}


def _call_claude_code(prompt):
    """Runs the local Claude Code CLI in headless mode (reuses the logged-in
    Claude Code session - no separate Anthropic API key needed). Uses a cheap
    model and strips the default agent context (CLAUDE.md, tool list, MCP) to
    keep each call to a fraction of a cent instead of Claude Code's normal
    full-context overhead."""
    cmd = [
        "claude",
        "-p",
        prompt,
        "--model",
        CLAUDE_MODEL,
        "--system-prompt",
        ANALYSIS_SYSTEM_PROMPT,
        "--exclude-dynamic-system-prompt-sections",
        "--tools",
        "none",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--output-format",
        "json",
    ]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", timeout=60
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI gagal (exit {proc.returncode}): {proc.stderr.strip()[:500]}")
    outer = json.loads(proc.stdout)
    return outer.get("result", "")


def analyze_revision_comment(parent_title, comment_text, subtasks):
    """subtasks: list of (issue_code, title, status, issue_link) tuples."""
    if not comment_text or not comment_text.strip():
        return _empty_result("Reporter belum menulis komentar di tiket ini, tidak ada yang bisa dianalisa.")
    if not subtasks:
        return _empty_result("Tiket ini tidak punya subtask.")

    subtask_list_text = "\n".join(
        f"- {code}: {title} (status saat ini: {status})" for code, title, status, _ in subtasks
    )
    prompt = ANALYSIS_PROMPT.format(
        parent_title=parent_title,
        comment_text=comment_text,
        subtask_list_text=subtask_list_text,
    )

    text = _call_claude_code(prompt)
    try:
        start = text.index("{")
        end = text.rindex("}") + 1
        result = json.loads(text[start:end])
    except (ValueError, json.JSONDecodeError):
        return _empty_result(f"Gagal membaca respons AI: {text[:300]}")

    valid_codes = {code for code, _, _, _ in subtasks}
    result["recommended_codes"] = [c for c in result.get("recommended_codes", []) if c in valid_codes]
    result.setdefault("confidence", "low")
    result.setdefault("reasoning", "")
    return result


def main():
    rows = get_revision_tickets()
    if not rows:
        print("Tidak ada tiket parent dengan status Revision saat ini.")
        return

    print(f"Ditemukan {len(rows)} tiket parent berstatus Revision.\n")

    for issue_id, issue_code, title, reporter, issue_link, comment in rows:
        print("=" * 70)
        print(f"[{issue_code}] {title}")
        print(f"Reporter: {reporter}")
        print(f"Link: {issue_link}")
        print(f"Komentar terakhir reporter: {comment or '(belum ada komentar)'}")

        subtasks = get_subtasks(issue_id)
        if not subtasks:
            print("Tidak punya subtask - dilewati.\n")
            continue

        print(f"Subtask ({len(subtasks)}):")
        for code, sub_title, status, _ in subtasks:
            print(f"  - [{code}] {sub_title} (status: {status})")

        print("\nMenganalisa dengan Claude Code...")
        try:
            result = analyze_revision_comment(title, comment, subtasks)
        except Exception as e:
            print(f"Gagal menganalisa: {e}\n")
            continue

        codes = result.get("recommended_codes", [])
        if codes:
            print(f">> Rekomendasi: ubah subtask {', '.join(codes)} ke status Revision")
        else:
            print(">> Tidak ada subtask yang jelas relevan dari komentar ini.")
        print(f"   Keyakinan: {result.get('confidence', '-')}")
        if result.get("reasoning"):
            print(f"   Alasan: {result['reasoning']}")
        print()


if __name__ == "__main__":
    main()
