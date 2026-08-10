#!/usr/bin/env python3
"""
=============================================================================
REBUILD ALL PBIP ARCHIVES WITH CERTIFIED PBIR 4.0 METADATA
=============================================================================
1. Regenerates each project directory using ai_qvf_to_powerbi.py.
2. Removes any illegal root report.json inside .Report folders.
3. Builds fresh <name>_PBIP.zip archives for instant Web UI downloads.
=============================================================================
"""

import os
import shutil
import zipfile
import subprocess
from pathlib import Path

PROJECT_MAP = [
    ("Superstore_Sales_Dashboard.qvf", "Superstore_PowerBI_Project", "Superstore_Sales_Dashboard"),
    ("Helpdesk Management.qvf", "Helpdesk_PowerBI_Project", "Helpdesk_Management"),
    ("Executive Dashboard.qvf", "Executive_Dashboard_PowerBI_Project", "Executive_Dashboard"),
    ("Demo 2.qvf", "Demo_2_PowerBI_Project", "Demo_2"),
    ("first_qlik_project.qvf", "first_qlik_project_PowerBI_Project", "first_qlik_project")
]

def rebuild_project(qvf_name, out_dir, base_name):
    print(f"\n==================================================")
    print(f" REBUILDING: {qvf_name} -> {out_dir}")
    print(f"==================================================")

    # 1. Regenerate via ai_qvf_to_powerbi.py
    cmd = ["python", "ai_qvf_to_powerbi.py", "--qvf", qvf_name, "--output", out_dir]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"  [ERROR] Regeneration failed for {qvf_name}:\n{res.stderr}")
        return False
    print(f"  [OK] Generated Power BI metadata files.")

    # 2. Guarantee NO root report.json in .Report/
    report_root_json = Path(out_dir) / f"{base_name}.Report" / "report.json"
    if report_root_json.exists():
        report_root_json.unlink()
        print(f"  [OK] Removed legacy report.json from .Report root.")

    # 3. Build clean ZIP Archive
    zip_path = Path(out_dir) / f"{base_name}_PBIP.zip"
    if zip_path.exists():
        zip_path.unlink()

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # Add root files (.pbip, .pbit)
        for ext in [".pbip", ".pbit"]:
            p = Path(out_dir) / f"{base_name}{ext}"
            if p.exists():
                zf.write(p, arcname=p.name)

        # Add .SemanticModel folder
        sm_dir = Path(out_dir) / f"{base_name}.SemanticModel"
        if sm_dir.exists():
            for root, dirs, files in os.walk(sm_dir):
                for f in files:
                    full_path = Path(root) / f
                    rel_path = full_path.relative_to(out_dir)
                    zf.write(full_path, arcname=str(rel_path).replace("\\", "/"))

        # Add .Report folder (strictly PBIR 4.0 definition tree)
        rp_dir = Path(out_dir) / f"{base_name}.Report"
        if rp_dir.exists():
            for root, dirs, files in os.walk(rp_dir):
                for f in files:
                    # Skip report.json if in root of .Report
                    if f == "report.json" and Path(root) == rp_dir:
                        continue
                    full_path = Path(root) / f
                    rel_path = full_path.relative_to(out_dir)
                    zf.write(full_path, arcname=str(rel_path).replace("\\", "/"))

    print(f"  [OK] Successfully created clean ZIP Archive: {zip_path} ({zip_path.stat().st_size} bytes)")

    # 4. Also copy/sync to alternative stem folder if different
    stem_dir = f"{Path(qvf_name).stem}_PowerBI_Project"
    if stem_dir != out_dir:
        stem_path = Path(stem_dir)
        stem_path.mkdir(parents=True, exist_ok=True)
        shutil.copytree(out_dir, stem_dir, dirs_exist_ok=True)
        print(f"  [OK] Synchronized copy to: {stem_dir}")

    return True

def main():
    print("Starting full batch rebuild of all PBIP archives...")
    success_count = 0
    for qvf, out_dir, base_name in PROJECT_MAP:
        if rebuild_project(qvf, out_dir, base_name):
            success_count += 1
    print(f"\n==================================================")
    print(f" COMPLETE: Rebuilt {success_count}/{len(PROJECT_MAP)} PBIP Projects & Archives")
    print(f"==================================================")

if __name__ == "__main__":
    main()
