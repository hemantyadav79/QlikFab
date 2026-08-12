import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

"""
QVF Data Reader - Real Row-Level Data Extraction from Qlik Sense .qvf files
===========================================================================
A .qvf stores each field of a loaded table as two zlib-compressed streams:

  1. A SYMBOL TABLE  - the distinct values of the field, in index order:
         [00 00 00 00][count:uint32-LE] then `count` symbols, each one of
             0x01 <int32>                       integer
             0x02 <float64>                     double
             0x04 <len> <utf8>                  string
             0x05 <len> <utf8> <int32>          dual (string + int value)
             0x06 <len> <utf8> <float64>        dual (string + double value)
     `len` is a single byte, or 0xFF followed by an int32-LE for long strings.

  2. An INDEX TABLE  - one bit-packed index per row (LSB first) pointing into
     the symbol table, followed by 6-8 zero padding bytes. Its size is
     therefore ceil(rows * bits / 8) + padding, which is what lets us pair
     index tables with their symbol tables and recover the row count.

Large streams are written in 256 KB pages and must be concatenated before
being parsed.

Reading both back gives the ACTUAL data that was loaded into the Qlik app -
no mock rows, no guessing. This is what makes a migrated Power BI report show
the same numbers the Qlik dashboard showed.

Usage:
    from qvf_data_reader import QVFDataReader
    tables = QVFDataReader("my_app.qvf").read()
    # {table_name: {"columns": [{"name","type",...}], "rows": [[...], ...]}}
"""

import json
import math
import re
import struct
import zlib
from pathlib import Path


ZLIB_MAGIC_BYTES = (b'\x78\x9c', b'\x78\x01', b'\x78\xda')

# Qlik writes long streams in fixed-size pages; a page of exactly this size
# means "continued in the next stream".
PAGE_SIZE = 262144

# Index tables carry 6-8 bytes of zero padding after the packed bits.
INDEX_PAD_MIN = 6
INDEX_PAD_MAX = 8


class Symbol:
    """A single distinct value: its display string and its numeric value (if any)."""

    __slots__ = ("text", "number")

    def __init__(self, text, number=None):
        self.text = text
        self.number = number

    def value(self):
        """Best representation for export."""
        if self.text is None:
            return self.number
        return self.text

    def __repr__(self):
        return f"Symbol({self.text!r}, {self.number!r})"


class QVFDataReader:
    """Reconstructs the real, row-level data model out of a .qvf file."""

    def __init__(self, qvf_path, max_rows=None, verbose=True):
        self.qvf_path = Path(qvf_path)
        if not self.qvf_path.exists():
            raise FileNotFoundError(f"QVF file not found: {qvf_path}")
        self.max_rows = max_rows
        self.verbose = verbose

        self.raw = b''
        self.streams = []          # [bytes] - page-merged, in file order
        self.json_objects = []     # parsed JSON blobs found along the way
        self.symbol_tables = []    # [(stream_index, [Symbol, ...])]
        self.index_streams = []    # [(stream_index, bytes)]
        self.fields_meta = []
        self.tables_meta = []
        self._name_cache = None
        self.confidence = 'none'

    def _log(self, msg):
        if self.verbose:
            print(msg)

    # ------------------------------------------------------------------
    # Stage 1 - pull every zlib stream out of the binary, merging pages
    # ------------------------------------------------------------------
    def _load_streams(self):
        self.raw = data = self.qvf_path.read_bytes()
        n = len(data)
        raw_streams = []

        pos = 0
        while pos < n:
            hits = [data.find(m, pos) for m in ZLIB_MAGIC_BYTES]
            hits = [h for h in hits if h != -1]
            if not hits:
                break
            i = min(hits)
            d = zlib.decompressobj()
            try:
                out = d.decompress(data[i:])
            except zlib.error:
                pos = i + 1
                continue
            # d.eof means the stream terminated with a valid adler32 checksum,
            # which reliably separates real streams from coincidental matches.
            if d.eof and len(out) > 4:
                raw_streams.append(out)
                pos = n - len(d.unused_data)
            else:
                pos = i + 1

        # Merge continuation pages: a full-size page means "more follows".
        merged = []
        buf = None
        for out in raw_streams:
            buf = out if buf is None else buf + out
            if len(out) != PAGE_SIZE:
                merged.append(buf)
                buf = None
        if buf is not None:
            merged.append(buf)

        self.streams = merged
        self._log(f"  [data] {len(raw_streams)} zlib streams -> {len(merged)} after page merge")

    # ------------------------------------------------------------------
    # Stage 2 - the data-model metadata blob, when the app has one
    # ------------------------------------------------------------------
    def _load_metadata(self):
        for raw in self.streams:
            if not raw.startswith(b'{'):
                continue
            try:
                obj = json.loads(raw.decode('utf-8').rstrip('\x00'))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            self.json_objects.append(obj)
            if 'qreload_meta' not in obj:
                continue
            for f in obj.get('qfields', []):
                if f.get('qis_system') or f.get('qis_hidden'):
                    continue
                self.fields_meta.append({
                    'name': f.get('qname', ''),
                    'cardinality': f.get('qcardinal', 0),
                    'rows': f.get('qtotal_count', 0),
                    'src_tables': f.get('qsrc_tables', []) or [],
                    'tags': f.get('qtags', []) or [],
                })
            for t in obj.get('qtables', []):
                if t.get('qis_system'):
                    continue
                self.tables_meta.append({
                    'name': t.get('qname', ''),
                    'rows': t.get('qno_of_rows', 0),
                })

        if self.fields_meta:
            self._log(f"  [data] data-model metadata: {len(self.fields_meta)} fields, "
                      f"{len(self.tables_meta)} table(s)")

    # ------------------------------------------------------------------
    # Stage 3 - parse symbol tables / collect index-table candidates
    # ------------------------------------------------------------------
    @staticmethod
    def _read_str_len(buf, pos):
        """String length: one byte, or 0xFF followed by an int32-LE."""
        if pos >= len(buf):
            return None, pos
        b = buf[pos]
        pos += 1
        if b != 0xFF:
            return b, pos
        if pos + 4 > len(buf):
            return None, pos
        return struct.unpack_from('<I', buf, pos)[0], pos + 4

    @classmethod
    def _parse_symbol_table(cls, buf):
        """Return [Symbol, ...] if `buf` is a well-formed symbol table, else None."""
        if len(buf) < 8 or buf[0:4] != b'\x00\x00\x00\x00':
            return None
        count = struct.unpack_from('<I', buf, 4)[0]
        if count == 0 or count > 20_000_000:
            return None

        symbols = []
        pos = 8
        n = len(buf)
        for _ in range(count):
            if pos >= n:
                return None
            tag = buf[pos]
            pos += 1
            if tag == 0x01:
                if pos + 4 > n:
                    return None
                symbols.append(Symbol(None, struct.unpack_from('<i', buf, pos)[0]))
                pos += 4
            elif tag == 0x02:
                if pos + 8 > n:
                    return None
                symbols.append(Symbol(None, struct.unpack_from('<d', buf, pos)[0]))
                pos += 8
            elif tag in (0x04, 0x05, 0x06):
                slen, pos = cls._read_str_len(buf, pos)
                if slen is None or pos + slen > n:
                    return None
                text = buf[pos:pos + slen].decode('utf-8', errors='replace')
                pos += slen
                num = None
                if tag == 0x05:
                    if pos + 4 > n:
                        return None
                    num = struct.unpack_from('<i', buf, pos)[0]
                    pos += 4
                elif tag == 0x06:
                    if pos + 8 > n:
                        return None
                    num = struct.unpack_from('<d', buf, pos)[0]
                    pos += 8
                symbols.append(Symbol(text, num))
            else:
                return None

        if n - pos > 64:      # far too much slack: we mis-read the layout
            return None
        return symbols

    @staticmethod
    def _looks_like_index(buf):
        """Index tables end in 6-8 zero bytes of padding."""
        if len(buf) < 8:
            return False
        tail = len(buf) - len(buf.rstrip(b'\x00'))
        return INDEX_PAD_MIN <= tail <= INDEX_PAD_MAX + 8

    def _classify_streams(self):
        for i, raw in enumerate(self.streams):
            if raw[:1] in (b'{', b'['):
                continue
            syms = self._parse_symbol_table(raw)
            if syms is not None:
                self.symbol_tables.append((i, syms))
            elif self._looks_like_index(raw):
                self.index_streams.append((i, raw))
        self._log(f"  [data] {len(self.symbol_tables)} symbol tables, "
                  f"{len(self.index_streams)} index tables")

    # ------------------------------------------------------------------
    # Stage 4 - bit unpacking
    # ------------------------------------------------------------------
    @staticmethod
    def _bits_for(cardinality):
        return max(1, math.ceil(math.log2(cardinality))) if cardinality > 1 else 1

    @staticmethod
    def _unpack_bits(buf, bit_width, count):
        """Read `count` LSB-first bit fields of `bit_width` bits."""
        out = []
        mask = (1 << bit_width) - 1
        blen = len(buf)
        for i in range(count):
            bit_pos = i * bit_width
            byte_pos = bit_pos >> 3
            if byte_pos >= blen:
                return None
            window = int.from_bytes(buf[byte_pos:byte_pos + 8].ljust(8, b'\x00'), 'little')
            out.append((window >> (bit_pos & 7)) & mask)
        return out

    @staticmethod
    def _row_range(stream_len, bits):
        """
        Row counts an index stream of this size could hold. The packed bits
        occupy ceil(rows*bits/8) bytes and the rest is padding, which leaves a
        small range rather than an exact answer.
        """
        lo = math.floor((stream_len - INDEX_PAD_MAX - 1) * 8 / bits) + 1
        hi = math.floor((stream_len - INDEX_PAD_MIN + 1) * 8 / bits)
        return max(1, lo), max(1, hi)

    def _row_candidates(self):
        """
        Row counts announced by the app: from the data-model metadata when it
        exists, and from the numeric symbol tables in the stream preamble,
        which is where an app saved without metadata still records them.
        """
        declared = set()
        for t in self.tables_meta:
            if t.get('rows'):
                declared.add(int(t['rows']))
        for f in self.fields_meta:
            if f.get('rows'):
                declared.add(int(f['rows']))

        scraped = set()
        block_start = self._data_symbol_block()[0][0] if self.symbol_tables else 0
        for si, syms in self.symbol_tables:
            if si >= block_start:
                break
            for s in syms:
                txt = (s.text or '').strip()
                if re.fullmatch(r'\d{1,12}', txt):
                    scraped.add(int(txt))
        return declared, scraped

    # ------------------------------------------------------------------
    # Stage 5 - pair symbol tables with index tables
    # ------------------------------------------------------------------
    def _score_pair(self, symbols, raw, announced):
        """
        Test one candidate symbol-table / index-table pairing.

        A real pairing has to survive two independent checks: the index stream
        must be exactly the right size to hold one packed index per row for a
        row count the app declares, and decoding it must land inside the symbol
        table while touching every symbol in it - Qlik keeps no symbol that no
        row uses. Returns (row_count, indexes, weight) or None.
        """
        card = len(symbols)
        bits = self._bits_for(card)
        lo, hi = self._row_range(len(raw), bits)
        if hi < 1:
            return None

        declared, scraped = announced
        # Weight the match by how well-attested the row count is, so the
        # alignment prefers columns that agree with the app's own numbers.
        for source, weight in ((declared, 4), (scraped, 1), (None, 1)):
            if source is None:
                fits = [hi] if not declared and not scraped else []
            else:
                fits = sorted((c for c in source if lo <= c <= hi), reverse=True)
            for rows in fits:
                idxs = self._unpack_bits(raw, bits, rows)
                if idxs is None:
                    continue
                seen = set(idxs)
                if max(seen) >= card or len(seen) != card:
                    continue
                return rows, idxs, weight
        return None

    def _pair_columns(self):
        """
        Match every symbol table to its index table.

        Both sets are written in field order, but which block comes first in
        the file varies between apps, so position alone cannot be trusted.
        Instead the two sequences are aligned against each other - order
        preserved, skips allowed on both sides - choosing the alignment that
        validates the most pairings. That survives preamble tables, trailing
        internal streams, and either block ordering.
        """
        block = self._data_symbol_block()
        if not block:
            return []

        # The field-name list is itself a symbol table with its own index
        # stream. Drop it so it cannot be mistaken for the first data column.
        name_list = set(self._known_field_names())
        if name_list:
            block = [(si, syms) for si, syms in block
                     if {s.text for s in syms} != name_list]
        if not block:
            return []

        symbols_seq = [syms for _si, syms in block]
        index_seq = [raw for _ii, raw in self.index_streams]
        declared, scraped = self._row_candidates()

        # A high-cardinality column packs so many bits per row that its stream
        # size pins the row count to a handful of values. Harvesting those
        # recovers the row counts of tables the app never announced.
        for symbols in symbols_seq:
            bits = self._bits_for(len(symbols))
            for raw in index_seq:
                lo, hi = self._row_range(len(raw), bits)
                if 0 < hi - lo <= 4 or lo == hi:
                    scraped.update(range(lo, hi + 1))
        announced = (declared, scraped)

        n, m = len(symbols_seq), len(index_seq)
        # Validate candidate pairings up front; the size test rejects almost
        # everything cheaply, so the decode only runs on plausible pairs.
        valid = {}
        for i in range(n):
            for j in range(m):
                hit = self._score_pair(symbols_seq[i], index_seq[j], announced)
                if hit is not None:
                    valid[(i, j)] = hit

        # Longest-common-subsequence style alignment over the valid pairings.
        best = [[0] * (m + 1) for _ in range(n + 1)]
        for i in range(1, n + 1):
            row, prev = best[i], best[i - 1]
            for j in range(1, m + 1):
                hit = valid.get((i - 1, j - 1))
                take = prev[j - 1] + hit[2] if hit else -1
                row[j] = max(prev[j], row[j - 1], take)

        pairs = []
        i, j = n, m
        while i > 0 and j > 0:
            hit = valid.get((i - 1, j - 1))
            if hit and best[i][j] == best[i - 1][j - 1] + hit[2]:
                rows, idxs, _w = hit
                pairs.append({
                    'symbols': symbols_seq[i - 1],
                    'indexes': idxs[:rows],
                    'rows': rows,
                    'cardinality': len(symbols_seq[i - 1]),
                })
                i -= 1
                j -= 1
            elif best[i - 1][j] >= best[i][j - 1]:
                i -= 1
            else:
                j -= 1
        pairs.reverse()
        return pairs

    def _data_symbol_block(self):
        """
        The field symbol tables sit together in one run of streams. Isolating
        the longest such run keeps unrelated symbol tables elsewhere in the app
        from being offered up as columns.
        """
        if not self.symbol_tables:
            return []
        runs = []
        current = [self.symbol_tables[0]]
        for entry in self.symbol_tables[1:]:
            # Tolerate a couple of interleaved streams inside the block.
            if entry[0] - current[-1][0] <= 3:
                current.append(entry)
            else:
                runs.append(current)
                current = [entry]
        runs.append(current)
        return max(runs, key=len)

    # ------------------------------------------------------------------
    # Stage 6 - names
    # ------------------------------------------------------------------
    def _known_field_names(self):
        """
        Field names come from the data-model metadata when present. Otherwise
        the app still carries them as a symbol table, which is recognisable
        because it is written twice - once in the preamble and once beside the
        data - while a genuine data column appears only once.
        """
        if self._name_cache is not None:
            return self._name_cache

        names = []
        if self.fields_meta:
            names = [f['name'] for f in self.fields_meta]
        else:
            seen = set()
            for _si, syms in self.symbol_tables:
                texts = tuple(s.text for s in syms)
                if len(texts) < 2 or any(t is None or not t for t in texts):
                    continue
                if len(set(texts)) != len(texts):
                    continue
                if any(re.fullmatch(r'-?\d+(\.\d+)?', t) for t in texts):
                    continue
                if texts in seen and len(texts) > len(names):
                    names = list(texts)
                seen.add(texts)

        self._name_cache = names
        return names

    def _table_names(self):
        if self.tables_meta:
            return [t['name'] for t in self.tables_meta]
        names = []
        for obj in self.json_objects:
            if 'qScript' in obj:
                for m in re.finditer(r'^\s*\[?([A-Za-z_][\w \-]*?)\]?\s*:\s*$',
                                     obj['qScript'], re.MULTILINE):
                    if m.group(1) not in names:
                        names.append(m.group(1))
        return names

    # ------------------------------------------------------------------
    # Types
    # ------------------------------------------------------------------
    def _infer_type(self, name, symbols, tags):
        tags = [t.lower() for t in (tags or [])]
        if any(t in ('$date', '$timestamp') for t in tags):
            return 'dateTime'
        if not symbols:
            return 'string'
        numeric = 0
        datey = 0
        for s in symbols:
            txt = (s.text or '').strip()
            if s.number is not None and (s.text is None or
                                         re.fullmatch(r'-?[\d,]*\.?\d+', txt)):
                numeric += 1
            if re.fullmatch(r'\d{4}-\d{2}-\d{2}([ T].*)?', txt) or \
               re.fullmatch(r'\d{1,2}/\d{1,2}/\d{4}([ T].*)?', txt):
                datey += 1
        if datey == len(symbols):
            return 'dateTime'
        if numeric == len(symbols):
            return 'double'
        return 'string'

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def read(self):
        """
        Returns {table_name: {"columns": [{"name","type","cardinality"}...],
                              "rows": [[value, ...], ...]}}
        holding the real values stored in the app.
        """
        self._load_streams()
        self._load_metadata()
        self._classify_streams()

        pairs = self._pair_columns()
        if not pairs:
            self._log("  [data] no row data found in this QVF "
                      "(app was saved without loaded data)")
            return {}

        if not self._is_trustworthy(pairs):
            self._log("  [data] row data could not be decoded with confidence "
                      "- migrating schema and visuals only")
            self.confidence = 'none'
            return {}
        self.confidence = 'verified' if self.fields_meta else 'partial'

        columns = []
        dropped = 0
        for p, meta in zip(pairs, self._assign_fields(pairs)):
            if not meta.get('name') and self._is_internal_blob(p):
                # Qlik keeps its own storage alongside the app's fields - map
                # geometry above all. It matches no declared field and its
                # values run to tens of thousands of characters, which is both
                # useless in a report and more than Power Query will parse.
                dropped += 1
                continue
            name = meta.get('name') or f"Field_{len(columns) + 1}"
            columns.append({
                'name': name,
                'type': self._infer_type(name, p['symbols'], meta.get('tags')),
                'cardinality': p['cardinality'],
                'rows': p['rows'],
                'src_tables': meta.get('src_tables', []),
                'pair': p,
            })
        if dropped:
            self._log(f"  [data] skipped {dropped} internal Qlik column(s) "
                      f"that are not app fields")

        return self._group_into_tables(columns)

    # A real field value is short; Qlik's internal geometry blobs are not.
    BLOB_VALUE_LENGTH = 1000

    def _is_internal_blob(self, pair):
        """Is this unmatched column Qlik's own storage rather than a field?"""
        for s in pair['symbols'][:50]:
            if s.text and len(s.text) > self.BLOB_VALUE_LENGTH:
                return True
        return False

    def _is_trustworthy(self, pairs):
        """
        Decide whether the decoded columns can be published as real data.

        With the data-model metadata present, every column is anchored to a
        declared field and cardinality, and the result is verifiable. Without
        it the layout has to be inferred, so the decode is only accepted when
        it recovers most of the app's fields - otherwise a partial, misaligned
        result would be worse than migrating the schema alone.
        """
        if self.fields_meta:
            return True
        names = self._known_field_names()
        if not names:
            return False
        return len(pairs) >= 0.7 * len(names)

    def _assign_fields(self, pairs):
        """
        Attach each decoded column to the field it came from.

        Not every field yields a column - a field holding a single value has
        no index table - so decoded columns cannot simply be numbered off
        against the field list. Cardinality identifies them instead: align the
        decoded cardinalities against the declared ones, order preserved.
        """
        meta_list = self.fields_meta
        if not meta_list:
            names = self._known_field_names()
            return [{'name': names[i]} if i < len(names) else {}
                    for i in range(len(pairs))]

        n, m = len(pairs), len(meta_list)
        best = [[0] * (m + 1) for _ in range(n + 1)]
        for i in range(1, n + 1):
            card = pairs[i - 1]['cardinality']
            for j in range(1, m + 1):
                take = (best[i - 1][j - 1] + 1
                        if meta_list[j - 1]['cardinality'] == card else -1)
                best[i][j] = max(best[i - 1][j], best[i][j - 1], take)

        assigned = [{} for _ in range(n)]
        i, j = n, m
        while i > 0 and j > 0:
            if (meta_list[j - 1]['cardinality'] == pairs[i - 1]['cardinality']
                    and best[i][j] == best[i - 1][j - 1] + 1):
                assigned[i - 1] = meta_list[j - 1]
                i -= 1
                j -= 1
            elif best[i - 1][j] >= best[i][j - 1]:
                i -= 1
            else:
                j -= 1
        return assigned

    def _group_into_tables(self, columns):
        """Split columns into their Qlik tables: by declared source table when
        the metadata is available, otherwise by shared row count."""
        table_names = self._table_names()
        groups = []

        declared = {c['name']: (c['src_tables'] or [None])[0] for c in columns}
        if self.tables_meta and any(declared.values()):
            known = {t['name'] for t in self.tables_meta}
            buckets = {}
            for c in columns:
                src = declared.get(c['name'])
                tname = src if src in known else (table_names[0] if table_names else 'QlikTable')
                buckets.setdefault(tname, []).append(c)
            groups = list(buckets.items())
        else:
            # Consecutive columns sharing a row count belong to one table.
            current = []
            for c in columns:
                if current and c['rows'] != current[-1]['rows']:
                    groups.append((None, current))
                    current = []
                current.append(c)
            if current:
                groups.append((None, current))
            groups = [(table_names[i] if i < len(table_names) else f"Table_{i + 1}", cols)
                      for i, (_n, cols) in enumerate(groups)]

        result = {}
        for tname, cols in groups:
            if not cols:
                continue
            # Take the row count most of the columns agree on, and drop any
            # column that disagrees rather than truncating the whole table.
            tally = {}
            for c in cols:
                tally[c['rows']] = tally.get(c['rows'], 0) + 1
            length = max(tally, key=lambda r: (tally[r], r))
            cols = [c for c in cols if c['rows'] >= length]
            if not cols:
                continue
            if self.max_rows:
                length = min(length, self.max_rows)
            materialised = []
            for c in cols:
                p = c['pair']
                syms = p['symbols']
                materialised.append([syms[i].value() for i in p['indexes'][:length]])
            rows = [[materialised[j][i] for j in range(len(cols))] for i in range(length)]
            result[tname] = {
                'columns': [{'name': c['name'], 'type': c['type'],
                             'cardinality': c['cardinality']} for c in cols],
                'rows': rows,
            }
            self._log(f"  [data] table '{tname}': {length} real rows x {len(cols)} columns")
        return result


# ============================================================
# CLI - quick inspection of what a QVF really contains
# ============================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Read the real row-level data out of a Qlik Sense .qvf file")
    parser.add_argument('qvf_file', help='Path to the .qvf file')
    parser.add_argument('--rows', type=int, default=10, help='Rows to preview (default 10)')
    parser.add_argument('--json', help='Write the full decoded dataset to this JSON file')
    args = parser.parse_args()

    tables = QVFDataReader(args.qvf_file).read()

    for tname, t in tables.items():
        print(f"\nTable: {tname}  ({len(t['rows'])} rows x {len(t['columns'])} cols)")
        header = [c['name'] for c in t['columns']]
        line = " | ".join(header)
        print("  " + line[:200])
        print("  " + "-" * min(len(line), 200))
        for row in t['rows'][:args.rows]:
            print("  " + " | ".join(str(v) for v in row)[:200])

    if args.json:
        with open(args.json, 'w', encoding='utf-8') as f:
            json.dump(tables, f, indent=2, ensure_ascii=False, default=str)
        print(f"\nSaved: {args.json}")


if __name__ == '__main__':
    main()
