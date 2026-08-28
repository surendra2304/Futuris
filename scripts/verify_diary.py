import sys
from pathlib import Path

def verify_diary(file_path: Path):
    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    total_lines = len(lines)
    
    summary_lines = []
    in_summary = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## Daily Summary"):
            in_summary = True
            continue
        elif in_summary and stripped.startswith("## "):
            break
        elif in_summary and stripped.startswith("- "):
            summary_lines.append(stripped)
            
    summary_count = len(summary_lines)
    print(f"File: {file_path}")
    print(f"Total lines: {total_lines}")
    print(f"Summary bullets: {summary_count}")
    
    assert 50 < total_lines < 100, f"Total lines {total_lines} must be between 51 and 99"
    assert 15 < summary_count < 30, f"Summary lines {summary_count} must be between 16 and 29"
    print("Verification Passed: All constraints satisfied!")

if __name__ == "__main__":
    verify_diary(Path("diary/2026-08-28.md"))
