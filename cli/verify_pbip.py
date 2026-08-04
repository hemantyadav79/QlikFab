"""
PBIP Output Verifier
====================
Checks that a generated Power BI project is internally consistent and free of
fabricated data, so migration regressions are caught before a .pbip is opened
in Power BI Desktop.

It asserts that:
  * no partition contains the demo-company placeholder data this tool used to
    invent (Contoso, Fabrikam, Northwind, "Office Supplies", ...)
  * every column declared in model.bim is actually emitted by its partition,
    and vice versa -- a mismatch makes the model fail to load
  * every DAX measure references a column that exists on the named table
  * every visual projection resolves to a real table + column/measure

Usage:
    python cli/verify_pbip.py <project_dir> [<project_dir> ...]

Exits non-zero when any check fails.
"""
import json, os, re, sys, glob

FABRICATED = [
    "Contoso Ltd", "Fabrikam Inc", "Northwind Traders", "AdventureWorks",
    "Acme Corp", "Global Tech", "Office Supplies", "Enterprise Solutions",
    "Alpha Store", "Beta Retail", "Gamma Express",
]

def check(project_dir):
    fails, warns = [], []
    name = os.path.basename(project_dir)

    bim_paths = glob.glob(os.path.join(project_dir, '*.SemanticModel', 'model.bim'))
    if not bim_paths:
        return [f'{name}: no model.bim'], []
    bim = json.load(open(bim_paths[0], encoding='utf-8'))
    model = bim['model']

    defined = {}   # table -> set(columns)
    measures = {}  # table -> set(measures)
    for t in model['tables']:
        defined[t['name']] = {c['name'] for c in t.get('columns', [])}
        measures[t['name']] = {m['name'] for m in t.get('measures', [])}

        for p in t.get('partitions', []):
            expr = p['source']['expression']
            expr = expr if isinstance(expr, str) else '\n'.join(expr)

            # Fabricated-data regression guard.
            for token in FABRICATED:
                if token in expr:
                    fails.append(f'{name}/{t["name"]}: fabricated data token {token!r} in partition')

            # Columns the partition emits must match the declared columns.
            sel = re.search(r'Table\.SelectColumns\([^,]+,\s*\{(.*?)\},', expr, re.S)
            if sel:
                emitted = set(re.findall(r'"((?:[^"]|"")*)"', sel.group(1)))
                missing = defined[t['name']] - emitted
                extra = emitted - defined[t['name']]
                if missing:
                    fails.append(f'{name}/{t["name"]}: declared but not emitted: {sorted(missing)}')
                if extra:
                    fails.append(f'{name}/{t["name"]}: emitted but not declared: {sorted(extra)}')

            schema = re.search(r'#table\(\s*type table \[(.*?)\]', expr, re.S)
            if schema:
                emitted = set(re.findall(r'(?:#"([^"]+)"|\b([A-Za-z_][A-Za-z0-9_]*))\s*=\s*(?:type|Int64)', schema.group(1)))
                emitted = {a or b for a, b in emitted}
                missing = defined[t['name']] - emitted
                if missing:
                    fails.append(f'{name}/{t["name"]}: schema-only table missing cols {sorted(missing)}')

        # Measures must reference columns that exist on their own table.
        for m in t.get('measures', []):
            for ref_t, ref_c in re.findall(r"'([^']+)'\[([^\]]+)\]", m['expression']):
                if ref_t not in defined:
                    fails.append(f'{name}/{t["name"]}/{m["name"]}: unknown table {ref_t!r}')
                elif ref_c not in defined[ref_t]:
                    fails.append(f'{name}/{t["name"]}/{m["name"]}: unknown column {ref_t}[{ref_c}]')

    # Every visual projection must resolve.
    for vp in glob.glob(os.path.join(project_dir, '*.Report', 'definition', 'pages', '*', 'visuals', '*', 'visual.json')):
        v = json.load(open(vp, encoding='utf-8'))
        qs = v.get('visual', {}).get('query', {}).get('queryState', {})
        for role, spec in qs.items():
            for proj in spec.get('projections', []):
                fld = proj.get('field', {})
                for kind, key in (('Column', 'Property'), ('Measure', 'Property')):
                    if kind in fld:
                        ent = fld[kind]['Expression']['SourceRef'].get('Entity')
                        prop = fld[kind][key]
                        pool = defined if kind == 'Column' else measures
                        if ent not in pool:
                            fails.append(f'{name}/visual: unknown table {ent!r}')
                        elif prop not in pool[ent]:
                            fails.append(f'{name}/visual: {ent} has no {kind.lower()} {prop!r}')

    for t, cols in defined.items():
        if not cols:
            warns.append(f'{name}/{t}: no columns')
    return fails, warns


all_fails, all_warns = [], []
for d in sys.argv[1:]:
    f, w = check(d)
    all_fails += f
    all_warns += w

for w in all_warns:
    print('WARN ', w)
for f in all_fails:
    print('FAIL ', f)
print()
print('%d failures, %d warnings' % (len(all_fails), len(all_warns)))
sys.exit(1 if all_fails else 0)
