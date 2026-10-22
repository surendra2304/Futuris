"""Verification script for engineering diary constraints."""

from pathlib import Path


def verify_diary(file_path: Path) -> None:
    """Verify line constraints and summary bullet counts for a diary entry."""
    with open(file_path, encoding="utf-8") as f:
        lines = f.readlines()
    total_lines = len(lines)

    summary_lines = []
    in_summary = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## Daily Summary"):
            in_summary = True
            continue
        if in_summary and stripped.startswith("## "):
            break
        if in_summary and stripped.startswith("- "):
            summary_lines.append(stripped)

    summary_count = len(summary_lines)

    assert 50 < total_lines < 100, f"Total lines {total_lines} must be between 51 and 99"
    assert 15 < summary_count < 30, f"Summary lines {summary_count} must be between 16 and 29"


if __name__ == "__main__":
    diary_dir = Path("diary")
    for diary_file in sorted(diary_dir.glob("*.md")):
        verify_diary(diary_file)
