import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

"""
Migration entry point used by the web server.

Runs the full Qlik -> Power BI migration for one .qvf and writes a JSON
summary describing what was actually migrated: the real tables and their real
row counts, the real fields, the DAX each Qlik expression became, and the
visuals rebuilt from each Qlik sheet. The UI renders that summary directly, so
what the browser shows is what the generated Power BI project contains.
"""

import argparse
import json
from pathlib import Path

from ai_qvf_to_powerbi import AIConverterBrain, VISUAL_MAP, as_text, convert


def build_summary(generator, qvf_path: Path) -> dict:
    model = generator.model
    builder = generator.builder

    tables = []
    for t in model.tables:
        tables.append({
            "name": t["name"],
            "columns": [{"name": c["name"], "type": c["type"]} for c in t["columns"]],
            "rowsEmbedded": len(t["rows"]),
            "rowsInApp": t["total_rows"],
        })

    measures = []
    for expr, (table, name) in builder.measure_lookup.items():
        dax = next((m["expression"] for m in builder.measures_by_table[table]
                    if m["name"] == name), "")
        measures.append({
            "qlik": expr,
            "name": name,
            "dax": dax,
            "table": table,
            # Set analysis restricts which rows Qlik aggregates; DAX needs an
            # explicit CALCULATE filter for that, so flag it for review.
            "needsReview": "{" in expr,
        })

    sheets = []
    total_visuals = 0
    for sheet in generator._sheets_for_output():
        placed = model.sheet_charts(sheet)
        total_visuals += len(placed)
        visuals = []
        for chart, box in placed:
            qtype = as_text(chart.get("visualization") or chart.get("type"))
            x, y, w, h = box
            visuals.append({
                "qlikType": qtype,
                "powerBiType": VISUAL_MAP.get(qtype.lower(), "clusteredColumnChart"),
                "title": as_text(chart.get("title")) or "Untitled",
                # The position Qlik gave the object, mapped onto the Power BI
                # canvas, so the migrated page keeps the original layout.
                "position": {"x": round(x), "y": round(y),
                             "width": round(w), "height": round(h)},
                "dimensions": [as_text(d.get("field")) for d in chart.get("dimensions", [])
                               if as_text(d.get("field"))],
                "measures": [as_text(m.get("label")) or as_text(m.get("expression"))
                             for m in chart.get("measures", [])],
            })
        sheets.append({
            "name": as_text(sheet.get("title")) or "Sheet",
            "visualCount": len(placed),
            "visuals": visuals,
        })

    rows_embedded = sum(t["rowsEmbedded"] for t in tables)
    return {
        "app": model.title,
        "sourceFile": qvf_path.name,
        "sourceBytes": qvf_path.stat().st_size,
        "projectName": generator.project_name,
        "tables": tables,
        "columnCount": sum(len(t["columns"]) for t in tables),
        "rowsEmbedded": rows_embedded,
        "hasRealData": rows_embedded > 0,
        "measures": measures,
        "sheets": sheets,
        "visualCount": total_visuals,
        "pbit": f"{generator.project_name}.pbit",
        "pbip_zip": f"{generator.project_name}_PBIP.zip",
        "audit": "MIGRATION_AUDIT_REPORT.md",
        "outputDir": str(generator.output_dir),
    }


def main():
    parser = argparse.ArgumentParser(description="Migrate one .qvf and summarise the result")
    parser.add_argument("--qvf", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--max-rows", type=int, default=None)
    parser.add_argument("--provider", default="ollama")
    parser.add_argument("--model", default="llama3.2")
    args = parser.parse_args()

    kwargs = {}
    if args.max_rows:
        kwargs["max_rows"] = args.max_rows

    generator = convert(
        qvf_path=args.qvf,
        output_dir=args.output,
        ai_brain=AIConverterBrain(provider=args.provider, model=args.model),
        **kwargs)

    summary = build_summary(generator, Path(args.qvf))
    Path(args.summary).parent.mkdir(parents=True, exist_ok=True)
    with open(args.summary, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"  [OK] summary written to {args.summary}")


if __name__ == "__main__":
    main()
