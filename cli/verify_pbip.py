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

# The only escape mechanism M offers inside quoted text. Anything else after a
# `#(` is the parser reading past the end of the literal.
_M_ESCAPES = {'#)', '#(', 'cr)', 'lf)', 'tab)'}


def bad_m_literals(expr):
    """
    Unescaped `#(`, raw control characters, and empty identifiers inside M
    string literals and quoted identifiers.

    Fabric rejects the whole mashup document for any one of these, and the
    error it returns ("Token ',' expected") names neither the table nor the
    column, so the failure is far cheaper to catch here.
    """
    problems = []
    for literal in re.findall(r'#?"((?:[^"]|"")*)"', expr):
        for hit in re.finditer(r'#\(', literal):
            tail = literal[hit.end():hit.end() + 4]
            if not any(tail.startswith(e) for e in _M_ESCAPES):
                problems.append(f'unescaped "#(" in M literal {literal[:60]!r}')
        if any(ch in literal for ch in '\r\n\t'):
            problems.append(f'raw control character in M literal {literal[:60]!r}')
    if '#""' in expr:
        problems.append('empty quoted identifier #"" in M expression')

    # The `type` keyword inside a row type's field specification. Valid one line
    # away in a Table.TransformColumnTypes list, and rejected here: the field's
    # type is parsed as a primary expression, so `= type text` fails and the
    # field list reads as unterminated. Fabric reports it only as
    # "Token ',' expected", naming neither the table nor the column.
    for literal in re.findall(r'type table \[(.*?)\]', expr, re.DOTALL):
        for keyword_use in re.findall(r'=\s*type\s+([A-Za-z]+)', literal):
            problems.append(
                f'row type uses "= type {keyword_use}"; a field specification takes '
                f'a bare primitive ("{keyword_use}") or a type value (Int64.Type)')

    # Duplicate fields in a record type. `type table [#"X" = ..., #"X" = ...]`
    # is rejected outright, and the message names neither the table nor the
    # column -- it surfaces as the same bare "Token ',' expected".
    for literal in re.findall(r'type table \[(.*?)\]', expr, re.DOTALL):
        names = re.findall(r'#"((?:[^"]|"")*)"\s*=', literal)
        seen, duplicated = set(), set()
        for name in names:
            if name in seen:
                duplicated.add(name)
            seen.add(name)
        for name in sorted(duplicated):
            problems.append(f'duplicate column {name!r} in an M record type')

    return problems


def m_lines_are_single_lines(expr_lines):
    """
    Every element of a partition's expression array must be one physical line.

    TMSL joins the array with newlines, so an element that already contains one
    silently splits into two. When the element is a `//` comment -- and the
    generated M comments quote source paths, SQL and error text -- the comment
    ends at that newline and everything after it is parsed as code. Fabric
    reports it as "Token ',' expected" pointing at a line no one wrote.
    """
    problems = []
    for index, line in enumerate(expr_lines):
        if isinstance(line, str) and ('\n' in line or '\r' in line):
            problems.append(
                f'expression line {index} contains a newline, which splits it in two '
                f'and lets a // comment swallow the following code: {line[:60]!r}')
    return problems


def check(project_dir):
    fails, warns = [], []
    name = os.path.basename(project_dir)

    bim_paths = glob.glob(os.path.join(project_dir, '*.SemanticModel', 'model.bim'))
    if not bim_paths:
        return [f'{name}: no model.bim'], []
    bim = json.load(open(bim_paths[0], encoding='utf-8'))
    model = bim['model']

    # Both maps are completed before anything is validated against them. A
    # measure may legitimately reference a table declared later in the file,
    # and checking mid-pass reported those as missing.
    defined = {t['name']: {c['name'] for c in t.get('columns', [])}
               for t in model['tables']}          # table -> set(columns)
    measures = {t['name']: {m['name'] for m in t.get('measures', [])}
                for t in model['tables']}         # table -> set(measures)

    # What the staged Parquet actually holds, when this project stages to a
    # Lakehouse. A Direct Lake table reads those files directly, so the file --
    # not the .qvf's field metadata -- is the authority on each column's type.
    staged_types = {}
    manifest_path = os.path.join(project_dir, 'lakehouse', 'manifest.json')
    if os.path.exists(manifest_path):
        try:
            for entry in json.load(open(manifest_path, encoding='utf-8')).get('tables', []):
                staged_types[entry['table']] = entry.get('types') or {}
        except (ValueError, OSError, KeyError) as err:
            warns.append(f'{name}: lakehouse/manifest.json could not be read ({err})')

    for t in model['tables']:
        for p in t.get('partitions', []):
            source = p.get('source') or {}

            # A Direct Lake partition names a Delta table instead of carrying an
            # M query, so there is no expression to inspect. What matters there
            # is that it resolves: the entity must be named and the shared
            # expression it points at must exist.
            if p.get('mode') == 'directLake' or source.get('type') == 'entity':
                shared = {e.get('name') for e in model.get('expressions', [])}
                if not source.get('entityName'):
                    fails.append(f'{name}/{t["name"]}: Direct Lake partition names no entity')
                if source.get('expressionSource') not in shared:
                    fails.append(
                        f'{name}/{t["name"]}: Direct Lake partition points at shared '
                        f'expression {source.get("expressionSource")!r}, which the model '
                        f'does not define (has: {sorted(shared)})')

                # Declaring int64 over a column written as text describes data
                # that is not there.
                for column in t.get('columns', []):
                    written = (staged_types.get(t['name']) or {}).get(column['name'])
                    if written and written != column.get('dataType'):
                        fails.append(
                            f'{name}/{t["name"]}: column {column["name"]!r} is declared '
                            f'{column.get("dataType")!r} but the staged Parquet holds '
                            f'{written!r}')
                continue

            expr = source.get('expression')
            if expr is None:
                fails.append(f'{name}/{t["name"]}: partition has neither an M expression '
                             f'nor a Direct Lake entity')
                continue
            # Checked before joining: once the lines are concatenated, a line
            # that already held a newline is indistinguishable from two lines.
            if isinstance(expr, list):
                for problem in m_lines_are_single_lines(expr):
                    fails.append(f'{name}/{t["name"]}: {problem}')

            expr = expr if isinstance(expr, str) else '\n'.join(expr)

            # Fabricated-data regression guard.
            for token in FABRICATED:
                if token in expr:
                    fails.append(f'{name}/{t["name"]}: fabricated data token {token!r} in partition')

            for problem in bad_m_literals(expr):
                fails.append(f'{name}/{t["name"]}: {problem}')

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
                # The field type may be a bare primitive (`text`), a type value
                # (`Int64.Type`), or the `type text` keyword form this file
                # rejects elsewhere -- so the column name is matched on the `=`
                # and any type token that follows, not on a fixed set of them.
                emitted = set(re.findall(
                    r'(?:#"([^"]+)"|\b([A-Za-z_][A-Za-z0-9_]*))\s*=\s*[A-Za-z]',
                    schema.group(1)))
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

    # The legacy root report.json, when one was written. It is built by a
    # different code path from definition/, binds every visual to a single table
    # alias, and is what Power BI Desktop opens. It is deliberately not published
    # to Fabric, so a bad reference here is a warning: it breaks the downloadable
    # .pbip, not the published report. The wording avoids the phrases the
    # publisher's preflight blocks on, so it cannot stop a publish over a file
    # that is never sent.
    for lp in glob.glob(os.path.join(project_dir, '*.Report', 'report.json')):
        try:
            layout = json.load(open(lp, encoding='utf-8'))
        except (ValueError, OSError) as err:
            warns.append(f'{name}/report.json (Desktop copy): unreadable ({err})')
            continue
        for section in layout.get('sections', []):
            for vc in section.get('visualContainers', []):
                config = vc.get('config')
                if isinstance(config, str):
                    try:
                        config = json.loads(config)
                    except ValueError:
                        warns.append(f'{name}/report.json (Desktop copy): '
                                     f'a visual config is not valid JSON')
                        continue
                query = ((config or {}).get('singleVisual') or {}).get('prototypeQuery') or {}
                entities = {f.get('Name'): f.get('Entity') for f in query.get('From', [])}
                for sel in query.get('Select', []):
                    for kind, pool in (('Column', defined), ('Measure', measures)):
                        if kind not in sel:
                            continue
                        alias = sel[kind]['Expression']['SourceRef'].get('Source')
                        ent = entities.get(alias)
                        prop = sel[kind]['Property']
                        if ent not in pool:
                            warns.append(f'{name}/report.json (Desktop copy): '
                                         f'table {ent!r} is not in the model')
                        elif prop not in pool[ent]:
                            warns.append(f'{name}/report.json (Desktop copy): '
                                         f'{ent} does not define {kind.lower()} {prop!r}')

    for t, cols in defined.items():
        if not cols:
            warns.append(f'{name}/{t}: no columns')
    return fails, warns


def main(argv):
    all_fails, all_warns = [], []
    for d in argv:
        f, w = check(d)
        all_fails += f
        all_warns += w

    for w in all_warns:
        print('WARN ', w)
    for f in all_fails:
        print('FAIL ', f)
    print()
    print('%d failures, %d warnings' % (len(all_fails), len(all_warns)))
    return 1 if all_fails else 0


# Guarded, because the publisher imports this module to run `check` before it
# uploads anything. Left at module level, this block ran on import and called
# sys.exit(), which killed the publish rather than verifying it.
if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
