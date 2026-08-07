import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

"""
QVF Extractor - Universal Qlik Sense .qvf File Extraction Tool
================================================================
Extracts load scripts, data models, chart definitions, variables,
and metadata from any Qlik Sense .qvf file.

Usage:
    python qvf_extractor.py <path_to_qvf_file> [--output <output_dir>]

Example:
    python qvf_extractor.py my_app.qvf
    python qvf_extractor.py my_app.qvf --output ./extracted
    python qvf_extractor.py my_app.qvf --format json
"""

import argparse
import json
import os
import re
import sys
import zlib
import base64
from datetime import datetime
from pathlib import Path


# ============================================================
# CORE EXTRACTION ENGINE
# ============================================================

class QVFExtractor:
    """
    Extracts and parses all components from a Qlik Sense .qvf file.
    
    A .qvf file uses a proprietary binary format with zlib-compressed
    JSON blobs for metadata, scripts, sheet definitions, and object
    properties. The actual row-level data is stored in Qlik's binary
    columnar format which requires the Qlik Engine to decode.
    
    This tool extracts everything that is stored as structured JSON.
    """

    # Zlib magic bytes that indicate the start of a compressed stream
    ZLIB_MAGIC_BYTES = [b'\x78\x9c', b'\x78\x01', b'\x78\xda']

    # Maximum bytes to attempt decompressing from any single offset
    MAX_DECOMPRESS_BYTES = 2_000_000

    def __init__(self, qvf_path: str):
        self.qvf_path = Path(qvf_path)
        if not self.qvf_path.exists():
            raise FileNotFoundError(f"QVF file not found: {qvf_path}")
        if not self.qvf_path.suffix.lower() == '.qvf':
            raise ValueError(f"Expected a .qvf file, got: {self.qvf_path.suffix}")

        self.raw_data: bytes = b''
        self.file_header: dict = {}
        self.app_properties: dict = {}
        self.load_script: str = ''
        self.data_model: dict = {}
        self.sheets: list = []
        self.variables: list = []
        self.fields: list = []
        self.tables: list = []
        self.base64_metadata: list = []
        self.all_objects: list = []  # All decompressed JSON objects
        # Library dimensions/measures, keyed by qId, that charts reference by
        # qLibraryId rather than defining inline.
        self.master_dimensions: dict = {}
        self.master_measures: dict = {}
        self._zlib_streams: list = []

    # ----------------------------------------------------------
    # STEP 1: Read the raw binary data
    # ----------------------------------------------------------
    def _read_file(self):
        """Read the entire .qvf file into memory."""
        self.raw_data = self.qvf_path.read_bytes()
        print(f"  [OK] Read {len(self.raw_data):,} bytes from {self.qvf_path.name}")

    # ----------------------------------------------------------
    # STEP 2: Extract the QVF file header
    # ----------------------------------------------------------
    def _extract_file_header(self):
        """
        Extract the QVF file header which contains format version,
        build number, and compression settings.
        """
        # Clean the data by removing QVF framing bytes (\x01\x00\x00\x00)
        cleaned = self.raw_data.replace(b'\x01\x00\x00\x00', b'')
        pattern = rb'\{[^{}]*"QVFFileVersion"[^{}]*\}'
        match = re.search(pattern, cleaned)
        if match:
            try:
                self.file_header = json.loads(match.group().decode('utf-8', errors='replace'))
                print(f"  [OK] Extracted file header (QVF v{self.file_header.get('QVFFileVersion', '?')})")
            except json.JSONDecodeError:
                print("  [WARN] Found header but could not parse JSON")
        else:
            print("  [WARN] No QVF file header found")

    # ----------------------------------------------------------
    # STEP 3: Find and decompress all zlib streams
    # ----------------------------------------------------------
    def _extract_zlib_streams(self):
        """
        Scan the binary data for zlib-compressed streams (magic bytes
        78 9C, 78 01, or 78 DA), decompress them, and attempt to
        parse as UTF-8 text / JSON.
        """
        results = []
        for magic in self.ZLIB_MAGIC_BYTES:
            pos = 0
            while True:
                idx = self.raw_data.find(magic, pos)
                if idx == -1:
                    break
                # Try to decompress
                decompressed = self._try_decompress(idx)
                if decompressed is not None:
                    results.append((idx, decompressed))
                pos = idx + 1

        # Sort by offset
        results.sort(key=lambda x: x[0])
        self._zlib_streams = results
        print(f"  [OK] Found {len(results)} valid zlib streams")

    def _try_decompress(self, offset: int) -> bytes | None:
        """Attempt zlib decompression at the given offset."""
        chunk = self.raw_data[offset:offset + self.MAX_DECOMPRESS_BYTES]
        # Standard zlib
        try:
            return zlib.decompress(chunk)
        except zlib.error:
            pass
        # Try different wbits
        for wbits in [15, -15, 31]:
            try:
                dec = zlib.decompressobj(wbits)
                return dec.decompress(chunk)
            except zlib.error:
                pass
        return None

    # ----------------------------------------------------------
    # STEP 4: Classify each decompressed stream
    # ----------------------------------------------------------
    def _classify_streams(self):
        """
        Parse each decompressed stream as JSON and classify it
        into the appropriate category (script, sheet, metadata, etc).
        """
        for offset, raw_bytes in self._zlib_streams:
            # Try to decode as UTF-8 text
            try:
                text = raw_bytes.decode('utf-8').rstrip('\x00')
            except UnicodeDecodeError:
                # Binary data (likely columnar row data) - skip
                continue

            # Try to parse as JSON
            try:
                obj = json.loads(text)
            except json.JSONDecodeError:
                continue

            # Store the raw object
            self.all_objects.append({'offset': offset, 'data': obj})

        # Master dimensions and measures are resolved before any sheet is
        # parsed: a chart references them by qLibraryId, and the streams arrive
        # in no particular order, so a sheet parsed first would find nothing.
        self._collect_master_items()

        for entry in self.all_objects:
            obj = entry['data']

            # Classify based on known keys
            if 'qScript' in obj:
                self._parse_script(obj)
            elif 'qMetaData' in obj and obj.get('qMetaData', {}).get('qType') == 'sheet':
                self._parse_sheet(obj)
            elif 'qreload_meta' in obj:
                self._parse_data_model(obj)
            elif 'qTitle' in obj and 'qLastReloadTime' in obj:
                self._parse_app_properties(obj)
            elif 'qEntryList' in obj and 'qId' in obj:
                self._parse_variables(obj)

        print(f"  [OK] Classified {len(self.all_objects)} JSON objects")

    # ----------------------------------------------------------
    # STEP 5: Parse individual components
    # ----------------------------------------------------------
    def _collect_master_items(self):
        """
        Index the app's master dimensions and measures by their library id.

        Qlik charts rarely inline their fields. A dimension or measure defined
        once in the library is referenced from every chart that uses it via
        `qLibraryId`, leaving the chart's own `qDef` empty. Without this index
        those charts look like they have no dimensions and no measures at all.
        """
        for entry in self.all_objects:
            obj = entry['data']
            info = obj.get('qInfo') or {}
            qid = info.get('qId')
            if not qid:
                continue

            if info.get('qType') == 'dimension' and 'qDim' in obj:
                dim = obj['qDim'] or {}
                field_defs = dim.get('qFieldDefs') or []
                labels = dim.get('qFieldLabels') or []
                self.master_dimensions[qid] = {
                    'fields': list(field_defs),
                    'field': field_defs[0] if field_defs else '',
                    'label': (labels[0] if labels else '') or dim.get('title', ''),
                    'title': (obj.get('qMetaDef') or {}).get('title', ''),
                }
            elif info.get('qType') == 'measure' and 'qMeasure' in obj:
                meas = obj['qMeasure'] or {}
                self.master_measures[qid] = {
                    'expression': meas.get('qDef', ''),
                    'label': meas.get('qLabel', '') or (obj.get('qMetaDef') or {}).get('title', ''),
                    'format': meas.get('qNumFormat', {}),
                }

        if self.master_dimensions or self.master_measures:
            print("  [OK] Indexed %d master dimension(s) and %d master measure(s)"
                  % (len(self.master_dimensions), len(self.master_measures)))

    def _parse_script(self, obj: dict):
        """Extract the Qlik load script."""
        raw_script = obj.get('qScript', '')
        # Unescape the script (stored with \\r\\n)
        self.load_script = raw_script.replace('\r\n', '\n')
        print(f"  [OK] Extracted load script ({len(self.load_script)} chars)")

    def _parse_sheet(self, obj: dict):
        """Extract sheet definition with all chart objects."""
        root = obj.get('qRoot', {})
        prop = root.get('qProperty', {})
        meta = prop.get('qMetaDef', {})
        children = root.get('qChildren', [])

        sheet_info = {
            'id': prop.get('qInfo', {}).get('qId', ''),
            'title': meta.get('title', 'Untitled Sheet'),
            'description': meta.get('description', ''),
            'rank': prop.get('rank', 0),
            'created': prop.get('creationDate', ''),
            'layout': {
                'columns': prop.get('columns', 0),
                'rows': prop.get('rows', 0),
            },
            'cells': prop.get('cells', []),
            'charts': [],
        }

        # Parse each child chart object
        for child in children:
            chart = self._parse_chart_object(child)
            if chart:
                sheet_info['charts'].append(chart)
                # Recursively parse nested children (e.g., KPIs in containers)
                for nested_child in child.get('qChildren', []):
                    nested_chart = self._parse_chart_object(nested_child)
                    if nested_chart:
                        nested_chart['parent'] = chart['id']
                        sheet_info['charts'].append(nested_chart)

        self.sheets.append(sheet_info)
        print(f"  [OK] Extracted sheet: \"{sheet_info['title']}\" ({len(sheet_info['charts'])} charts)")

    def _parse_chart_object(self, child: dict) -> dict | None:
        """Parse a single chart/visualization object."""
        prop = child.get('qProperty', {})
        if not prop:
            return None

        info = prop.get('qInfo', {})
        hypercube = prop.get('qHyperCubeDef', {})

        chart = {
            'id': info.get('qId', ''),
            'type': info.get('qType', ''),
            'visualization': prop.get('visualization', info.get('qType', '')),
            'title': prop.get('title', ''),
            'subtitle': prop.get('subtitle', ''),
            'footnote': prop.get('footnote', ''),
            'showTitles': prop.get('showTitles', False),
            'dimensions': [],
            'measures': [],
            'settings': {},
        }

        # Extract dimensions, resolving library references against the master
        # items indexed earlier. An inline definition always wins over the
        # library entry, because a chart may override what it borrowed.
        for dim in hypercube.get('qDimensions', []):
            dim_def = dim.get('qDef', {}) or {}
            master = self.master_dimensions.get(dim.get('qLibraryId'), {})
            field_defs = dim_def.get('qFieldDefs') or []
            labels = dim_def.get('qFieldLabels') or []
            chart['dimensions'].append({
                'field': (field_defs[0] if field_defs else '') or master.get('field', ''),
                'label': (labels[0] if labels else '') or master.get('label', ''),
                'sort': dim_def.get('qSortCriterias', []),
                'fromLibrary': bool(master and not field_defs),
            })

        # A listbox is a single-field selector and keeps its field in
        # qListObjectDef rather than in a hypercube. Without this it looks like
        # an object with no dimensions and cannot become a slicer.
        list_object = prop.get('qListObjectDef')
        if list_object and not chart['dimensions']:
            list_def = list_object.get('qDef', {}) or {}
            master = self.master_dimensions.get(list_object.get('qLibraryId'), {})
            field_defs = list_def.get('qFieldDefs') or []
            labels = list_def.get('qFieldLabels') or []
            field = (field_defs[0] if field_defs else '') or master.get('field', '')
            label = ((labels[0] if labels else '')
                     or list_def.get('qLabel', '')
                     or master.get('label', ''))
            # Older listboxes name the field only in the object's title.
            if not field:
                field = label or (prop.get('title') if isinstance(prop.get('title'), str) else '')
            if field:
                chart['dimensions'].append({
                    'field': field,
                    'label': label or field,
                    'sort': list_def.get('qSortCriterias', []),
                    'fromLibrary': bool(master and not field_defs),
                })

        # Extract measures, same precedence.
        for meas in hypercube.get('qMeasures', []):
            meas_def = meas.get('qDef', {}) or {}
            master = self.master_measures.get(meas.get('qLibraryId'), {})
            chart['measures'].append({
                'expression': meas_def.get('qDef', '') or master.get('expression', ''),
                'label': meas_def.get('qLabel', '') or master.get('label', ''),
                'format': meas_def.get('qNumFormat', {}) or master.get('format', {}),
                'fromLibrary': bool(master and not meas_def.get('qDef')),
            })

        # Extract chart-specific settings
        for key in ['color', 'legend', 'orientation', 'gridLine', 'dataPoint',
                     'dimensionAxis', 'measureAxis', 'tooltip', 'barGrouping',
                     'lineType', 'donut', 'scrollbar', 'version']:
            if key in prop:
                chart['settings'][key] = prop[key]

        return chart

    def _parse_data_model(self, obj: dict):
        """Extract data model metadata (fields, tables, reload info)."""
        self.data_model = {
            'reload_meta': obj.get('qreload_meta', {}),
            'static_byte_size': obj.get('qstatic_byte_size', 0),
            'usage': obj.get('qusage', ''),
        }

        # Extract field info
        for field in obj.get('qfields', []):
            if field.get('qis_system') or field.get('qis_hidden'):
                continue
            self.fields.append({
                'name': field.get('qname', ''),
                'source_table': field.get('qsrc_tables', []),
                'cardinality': field.get('qcardinal', 0),
                'total_count': field.get('qtotal_count', 0),
                'is_numeric': field.get('qis_numeric', False),
                'tags': field.get('qtags', []),
                'byte_size': field.get('qbyte_size', 0),
            })

        # Extract table info
        for table in obj.get('qtables', []):
            if table.get('qis_system'):
                continue
            self.tables.append({
                'name': table.get('qname', ''),
                'rows': table.get('qno_of_rows', 0),
                'fields': table.get('qno_of_fields', 0),
                'key_fields': table.get('qno_of_key_fields', 0),
                'byte_size': table.get('qbyte_size', 0),
            })

        print(f"  [OK] Extracted data model: {len(self.fields)} fields, {len(self.tables)} table(s)")

    def _parse_app_properties(self, obj: dict):
        """Extract app-level properties."""
        self.app_properties = {
            'title': obj.get('qTitle', ''),
            'last_reload': obj.get('qLastReloadTime', ''),
            'saved_in_version': obj.get('qSavedInProductVersion', ''),
            'usage': obj.get('qUsage', ''),
            'thumbnail': obj.get('qThumbnail', {}),
            'owner': obj.get('owner', ''),
        }
        print(f"  [OK] Extracted app properties: \"{self.app_properties['title']}\"")

    def _parse_variables(self, obj: dict):
        """Extract variables defined in the app."""
        var_list_id = obj.get('qId', '')
        for entry in obj.get('qEntryList', []):
            props = entry.get('qProperties', {})
            self.variables.append({
                'list_id': var_list_id,
                'name': props.get('qName', ''),
                'definition': props.get('qDefinition', ''),
                'is_script_created': entry.get('qIsScriptCreated', False),
            })
        if self.variables:
            print(f"  [OK] Extracted {len(self.variables)} variable(s)")

    # ----------------------------------------------------------
    # STEP 6: Extract base64-encoded metadata
    # ----------------------------------------------------------
    def _extract_base64_metadata(self):
        """Decode SecurityMetaAsBase64 fields found in the binary."""
        cleaned = self.raw_data.replace(b'\x01\x00\x00\x00', b'')
        pattern = rb'SecurityMetaAsBase64":"([A-Za-z0-9+/=]{20,})"'
        for m in re.finditer(pattern, cleaned):
            try:
                decoded = base64.b64decode(m.group(1))
                text = decoded.decode('utf-8', errors='replace').rstrip('\x00')
                parsed = json.loads(text)
                self.base64_metadata.append(parsed)
            except Exception:
                pass
        if self.base64_metadata:
            print(f"  [OK] Decoded {len(self.base64_metadata)} base64 metadata entries")

    # ----------------------------------------------------------
    # STEP 7: Extract field names from binary region
    # ----------------------------------------------------------
    def _extract_field_names_from_binary(self):
        """
        Fallback: extract field names from the binary data region or load script.
        """
        if len(self.fields) >= 4 and all(len(f.get('name', '')) > 2 for f in self.fields):
            return  # Already extracted cleanly from metadata

        # Search in load script first (most reliable for QVD apps)
        if self.load_script:
            blocks = re.findall(r'LOAD\s+([\s\S]+?)\s+(?:FROM|RESIDENT|;|\Z)', self.load_script, re.IGNORECASE)
            extracted_names = []
            for b in blocks:
                lines = [line.strip(' ,\r\n') for line in re.split(r'[,]', b)]
                for line in lines:
                    if not line or line.startswith('//') or len(line) > 60:
                        continue
                    if ' as ' in line.lower():
                        col = re.split(r'\s+as\s+', line, flags=re.IGNORECASE)[-1].strip(' \'\"[]')
                    else:
                        col = line.strip(' \'\"[]')
                    if re.match(r'^[a-zA-Z0-9_ &.#/-]+$', col):
                        col_lower = col.lower()
                        if col_lower not in [n.lower() for n in extracted_names] and not any(k in col_lower for k in ['resident', 'drop', 'load', 'where', 'left join', 'distinct', 'if(']):
                            extracted_names.append(col)
            
            if extracted_names:
                self.fields = []
                for name in extracted_names:
                    self.fields.append({
                        'name': name,
                        'source_table': [],
                        'cardinality': 0,
                        'total_count': 0,
                        'is_numeric': False,
                        'tags': [],
                        'byte_size': 0,
                    })
                print(f"  [OK] Extracted {len(self.fields)} field names from load script")
                return

        # Fallback to binary pattern search
        for test_field in [b'postcode', b'address', b'id']:
            idx = self.raw_data.find(test_field)
            if idx != -1:
                region = self.raw_data[idx:idx + 1000]
                parts = re.split(rb'[\x00-\x1f]+', region)
                names = [
                    p.decode('utf-8', errors='replace')
                    for p in parts
                    if len(p) > 1 and all(32 <= b <= 126 for b in p)
                ]
                for name in names:
                    self.fields.append({
                        'name': name,
                        'source_table': [],
                        'cardinality': 0,
                        'total_count': 0,
                        'is_numeric': False,
                        'tags': [],
                        'byte_size': 0,
                    })
                if self.fields:
                    print(f"  [OK] Extracted {len(self.fields)} field names from binary region")
                break

    # ----------------------------------------------------------
    # PUBLIC API: Run the full extraction
    # ----------------------------------------------------------
    def extract(self) -> dict:
        """
        Run the full extraction pipeline and return all results
        as a structured dictionary.
        """
        print(f"\n{'='*60}")
        print(f"  QVF EXTRACTOR — {self.qvf_path.name}")
        print(f"{'='*60}\n")

        print("[1/7] Reading file...")
        self._read_file()

        print("[2/7] Extracting file header...")
        self._extract_file_header()

        print("[3/7] Finding zlib streams...")
        self._extract_zlib_streams()

        print("[4/7] Classifying JSON objects...")
        self._classify_streams()

        print("[5/7] Extracting field names (fallback)...")
        self._extract_field_names_from_binary()

        print("[6/7] Decoding base64 metadata...")
        self._extract_base64_metadata()

        print("[7/7] Building results...\n")

        results = {
            'file': {
                'name': self.qvf_path.name,
                'path': str(self.qvf_path.absolute()),
                'size_bytes': len(self.raw_data),
                'extracted_at': datetime.now().isoformat(),
            },
            'header': self.file_header,
            'app_properties': self.app_properties,
            'load_script': self.load_script,
            'data_model': {
                'tables': self.tables,
                'fields': self.fields,
                'meta': self.data_model,
            },
            'sheets': self.sheets,
            'variables': self.variables,
            'security_metadata': self.base64_metadata,
        }

        return results


# ============================================================
# OUTPUT FORMATTERS
# ============================================================

def save_json(results: dict, output_dir: Path):
    """Save the full extraction as a single JSON file."""
    out_file = output_dir / 'extraction_result.json'
    with open(out_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"  Saved: {out_file}")


def save_load_script(results: dict, output_dir: Path):
    """Save the load script as a standalone .qvs file."""
    script = results.get('load_script', '')
    if script:
        out_file = output_dir / 'load_script.qvs'
        with open(out_file, 'w', encoding='utf-8') as f:
            f.write(script)
        print(f"  Saved: {out_file}")


def save_sheets(results: dict, output_dir: Path):
    """Save each sheet definition as a separate JSON file."""
    sheets_dir = output_dir / 'sheets'
    sheets_dir.mkdir(exist_ok=True)
    for sheet in results.get('sheets', []):
        safe_name = re.sub(r'[^\w\s-]', '', sheet.get('title', 'untitled')).strip().replace(' ', '_')
        out_file = sheets_dir / f"{safe_name}.json"
        with open(out_file, 'w', encoding='utf-8') as f:
            json.dump(sheet, f, indent=2, ensure_ascii=False)
        print(f"  Saved: {out_file}")


def save_data_model(results: dict, output_dir: Path):
    """Save the data model as a separate JSON file."""
    dm = results.get('data_model', {})
    if dm.get('fields') or dm.get('tables'):
        out_file = output_dir / 'data_model.json'
        with open(out_file, 'w', encoding='utf-8') as f:
            json.dump(dm, f, indent=2, ensure_ascii=False)
        print(f"  Saved: {out_file}")


def print_summary(results: dict):
    """Print a human-readable summary to the console."""
    print(f"\n{'='*60}")
    print(f"  EXTRACTION SUMMARY")
    print(f"{'='*60}")

    # File info
    f = results['file']
    print(f"\n  File:    {f['name']}")
    print(f"  Size:    {f['size_bytes']:,} bytes")

    # Header
    h = results.get('header', {})
    if h:
        print(f"  Version: QVF v{h.get('QVFFileVersion', '?')} (build {h.get('buildno', '?')})")

    # App properties
    ap = results.get('app_properties', {})
    if ap:
        print(f"  Title:   {ap.get('title', 'N/A')}")
        print(f"  Reload:  {ap.get('last_reload', 'N/A')}")

    # Data model
    dm = results.get('data_model', {})
    tables = dm.get('tables', [])
    fields = dm.get('fields', [])
    print(f"\n  DATA MODEL")
    print(f"  {'-'*40}")
    if tables:
        for t in tables:
            print(f"  Table: {t['name']} ({t['rows']:,} rows x {t['fields']} fields)")
    if fields:
        print(f"\n  Fields ({len(fields)}):")
        # Print as a formatted table
        max_name = max(len(f['name']) for f in fields)
        for f in fields:
            src = ', '.join(f.get('source_table', []))
            card = f.get('cardinality', 0)
            total = f.get('total_count', 0)
            print(f"    {f['name']:<{max_name}}  cardinality={card:<8} rows={total}")

    # Load script
    script = results.get('load_script', '')
    if script:
        print(f"\n  LOAD SCRIPT")
        print(f"  {'-'*40}")
        # Show first 30 lines
        lines = script.split('\n')
        for line in lines[:30]:
            print(f"  {line.rstrip()}")
        if len(lines) > 30:
            print(f"  ... ({len(lines) - 30} more lines)")

    # Sheets
    sheets = results.get('sheets', [])
    if sheets:
        print(f"\n  SHEETS & CHARTS")
        print(f"  {'-'*40}")
        for sheet in sheets:
            print(f"\n  Sheet: \"{sheet['title']}\"")
            for chart in sheet.get('charts', []):
                dims = [d['field'] for d in chart.get('dimensions', []) if d['field']]
                meass = [m['expression'] for m in chart.get('measures', []) if m['expression']]
                parent = f" (in {chart['parent']})" if chart.get('parent') else ''
                print(f"    [{chart['type']}] \"{chart.get('title', '')}\"")
                if dims:
                    print(f"      Dimensions: {', '.join(dims)}")
                if meass:
                    print(f"      Measures:   {', '.join(meass)}")
                if parent:
                    print(f"      Parent:     {parent}")

    # Variables
    variables = results.get('variables', [])
    if variables:
        print(f"\n  VARIABLES ({len(variables)})")
        print(f"  {'-'*40}")
        for v in variables:
            print(f"    {v['name']} = {v['definition']}")

    print(f"\n{'='*60}")
    print(f"  Extraction complete!")
    print(f"{'='*60}\n")


# ============================================================
# CLI ENTRY POINT
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description='QVF Extractor — Extract scripts, charts, and metadata from Qlik Sense .qvf files',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python qvf_extractor.py my_app.qvf
  python qvf_extractor.py my_app.qvf --output ./extracted
  python qvf_extractor.py my_app.qvf --format json
  python qvf_extractor.py my_app.qvf --format all
        """
    )
    parser.add_argument('qvf_file', help='Path to the .qvf file to extract')
    parser.add_argument('--output', '-o', help='Output directory (default: <qvf_name>_extracted/)',
                        default=None)
    parser.add_argument('--format', '-f', choices=['summary', 'json', 'all'],
                        default='all',
                        help='Output format: summary (console only), json (JSON file), all (everything)')

    args = parser.parse_args()

    # Resolve paths
    qvf_path = Path(args.qvf_file)
    if args.output:
        output_dir = Path(args.output)
    else:
        output_dir = qvf_path.parent / f"{qvf_path.stem}_extracted"

    # Run extraction
    try:
        extractor = QVFExtractor(str(qvf_path))
        results = extractor.extract()
    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    # Print summary to console
    print_summary(results)

    # Save files
    if args.format in ('json', 'all'):
        output_dir.mkdir(parents=True, exist_ok=True)
        print(f"\nSaving files to: {output_dir}")
        save_json(results, output_dir)
        save_load_script(results, output_dir)
        save_sheets(results, output_dir)
        save_data_model(results, output_dir)
        print(f"\nAll files saved to: {output_dir}")


if __name__ == '__main__':
    main()
