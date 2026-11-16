import json
import re
import shutil
import subprocess
import tempfile

import requests

from common import run_query

QUERY = """
SELECT
  i.id AS issue_id,
  p.key || '-' || i.key_num AS issue_code,
  i.title,
  us1.name AS reporter,
  i.reporter_id,
  'https://tulip.prestisa.id/projects/' || p.id || '/issue/' || p.key || '-' || i.key_num AS issue_link
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

COMMENTS_QUERY = """
SELECT c.author_id, c.created_at, c.body_md
FROM comments c
WHERE c.issue_id = %(issue_id)s AND c.deleted_at IS NULL
ORDER BY c.created_at ASC
"""

SUBTASK_QUERY = """
SELECT
  p.key || '-' || s.key_num AS issue_code,
  s.title,
  s.description,
  wfstat.name AS status,
  'https://tulip.prestisa.id/projects/' || p.id || '/issue/' || p.key || '-' || s.key_num AS issue_link
FROM issues s
LEFT JOIN projects p ON s.project_id = p.id
LEFT JOIN workflow_statuses wfstat ON wfstat.id = s.status_id
WHERE s.parent_issue_id = %(parent_id)s
ORDER BY s.key_num ASC;
"""

CLAUDE_MODEL = "claude-haiku-5-5"
MAX_IMAGES_PER_TICKET = 8
IMAGE_URL_RE = re.compile(r"!\[[^\]]*\]\((https?://[^\s)]+)\)")

ANALYSIS_SYSTEM_PROMPT = (
    "Kamu adalah asisten yang membantu tim QA menentukan subtask mana yang perlu "
    "direvisi berdasarkan komentar dan screenshot reporter. Jawab HANYA dengan JSON "
    "valid, tanpa teks lain di luar JSON, dan tanpa markdown code fence."
)

ANALYSIS_PROMPT = """Tiket parent ini diubah statusnya menjadi "Revision" oleh reporter setelah testing, tapi status subtask-nya tidak otomatis ikut berubah. Tentukan subtask mana yang seharusnya ikut diubah ke Revision, berdasarkan komentar (dan screenshot, jika ada) dari reporter.

Judul tiket parent: {parent_title}

Komentar reporter (diurutkan dari paling lama ke paling baru, ini rentetan komentar terakhirnya sebelum tiket masuk Revision):
{comment_block}

Daftar subtask dari tiket parent ini (judul + deskripsi):
{subtask_list_text}

Bandingkan isi komentar (termasuk screenshot jika ada) dengan JUDUL MAUPUN DESKRIPSI tiap subtask, jangan hanya dari judulnya saja. Subtask mana saja yang paling relevan dengan masalah/revisi yang disebutkan reporter? Reporter bisa menyebutkan bagian/fitur secara eksplisit atau implisit (lewat deskripsi perilaku yang salah atau lewat screenshot).

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


def get_trailing_reporter_comments(issue_id, reporter_id):
    """Walks backward from the newest comment, collecting the consecutive run
    written by the reporter (they often explain a Revision across several
    messages in a row). Stops as soon as a comment from someone else is hit.
    Falls back to just the single most recent reporter comment if the very
    last comment on the issue isn't theirs."""
    rows = run_query(COMMENTS_QUERY, {"issue_id": issue_id})

    trailing = []
    for author_id, created_at, body in reversed(rows):
        if author_id == reporter_id:
            trailing.append((created_at, body))
        else:
            break
    trailing.reverse()

    if trailing:
        return trailing

    reporter_rows = [(created_at, body) for author_id, created_at, body in rows if author_id == reporter_id]
    return reporter_rows[-1:]


def _empty_result(reasoning):
    return {"recommended_codes": [], "confidence": "low", "reasoning": reasoning}


def _download_images(comments, tmp_dir):
    """Replaces markdown image links in each comment body with a local
    @path reference Claude Code can view, downloading each image once."""
    url_to_path = {}
    processed = []

    for created_at, body in comments:
        body = body or ""

        def _replace(match):
            url = match.group(1)
            if len(url_to_path) >= MAX_IMAGES_PER_TICKET:
                return "[gambar dilewati - batas jumlah gambar tercapai]"
            if url not in url_to_path:
                try:
                    resp = requests.get(url, timeout=20)
                    resp.raise_for_status()
                    ext = url.rsplit(".", 1)[-1].split("?")[0][:5] or "png"
                    path = f"{tmp_dir}\\img_{len(url_to_path)}.{ext}"
                    with open(path, "wb") as f:
                        f.write(resp.content)
                    url_to_path[url] = path
                except Exception:
                    return "[gagal download gambar]"
            return f" @{url_to_path[url]} "

        processed.append((created_at, IMAGE_URL_RE.sub(_replace, body)))

    return processed


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
        cmd, capture_output=True, text=True, encoding="utf-8", timeout=90
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI gagal (exit {proc.returncode}): {proc.stderr.strip()[:500]}")
    outer = json.loads(proc.stdout)
    return outer.get("result", "")


def analyze_revision_comment(parent_title, issue_id, reporter_id, subtasks):
    """subtasks: list of (issue_code, title, description, status, issue_link) tuples."""
    comments = get_trailing_reporter_comments(issue_id, reporter_id)
    if not comments or not any((body or "").strip() for _, body in comments):
        return _empty_result("Reporter belum menulis komentar di tiket ini, tidak ada yang bisa dianalisa."), []
    if not subtasks:
        return _empty_result("Tiket ini tidak punya subtask."), []

    tmp_dir = tempfile.mkdtemp(prefix="revision_analysis_")
    try:
        processed_comments = _download_images(comments, tmp_dir)
        comment_block = "\n\n".join(
            f"--- Komentar ({created_at.strftime('%Y-%m-%d %H:%M')} WIB) ---\n{body}"
            for created_at, body in processed_comments
        )

        subtask_list_text = "\n".join(
            f"- {code}: {title} (status saat ini: {status})\n  Deskripsi: {(description or '(tidak ada deskripsi)').strip()[:500]}"
            for code, title, description, status, _ in subtasks
        )

        prompt = ANALYSIS_PROMPT.format(
            parent_title=parent_title,
            comment_block=comment_block,
            subtask_list_text=subtask_list_text,
        )

        text = _call_claude_code(prompt)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    try:
        start = text.index("{")
        end = text.rindex("}") + 1
        result = json.loads(text[start:end])
    except (ValueError, json.JSONDecodeError):
        return _empty_result(f"Gagal membaca respons AI: {text[:300]}"), comments

    valid_codes = {code for code, _, _, _, _ in subtasks}
    result["recommended_codes"] = [c for c in result.get("recommended_codes", []) if c in valid_codes]
    result.setdefault("confidence", "low")
    result.setdefault("reasoning", "")
    return result, comments


def main():
    rows = get_revision_tickets()
    if not rows:
        print("Tidak ada tiket parent dengan status Revision saat ini.")
        return

    print(f"Ditemukan {len(rows)} tiket parent berstatus Revision.\n")

    for issue_id, issue_code, title, reporter, reporter_id, issue_link in rows:
        print("=" * 70)
        print(f"[{issue_code}] {title}")
        print(f"Reporter: {reporter}")
        print(f"Link: {issue_link}")

        subtasks = get_subtasks(issue_id)
        if not subtasks:
            print("Tidak punya subtask - dilewati.\n")
            continue

        print(f"Subtask ({len(subtasks)}):")
        for code, sub_title, description, status, _ in subtasks:
            print(f"  - [{code}] {sub_title} (status: {status})")

        print("\nMenganalisa dengan Claude Code...")
        try:
            result, comments = analyze_revision_comment(title, issue_id, reporter_id, subtasks)
        except Exception as e:
            print(f"Gagal menganalisa: {e}\n")
            continue

        print(f"Jumlah komentar reporter yang dianalisa: {len(comments)}")
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
