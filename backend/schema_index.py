import logging
import re
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Callable, Optional

from config import SCHEMA_CACHE_TTL_S
from db import AnalyticsDatabase, DatabaseError
from schemas import INTERNAL_TABLES, Ambiguity, ColumnContext, SchemaColumn, SchemaTable, TableContext
from store import AppStore

log = logging.getLogger(__name__)

VALUE_CARDINALITY_LIMIT = 50


@dataclass
class Snapshot:
    columns: dict[str, list[tuple[str, str]]]
    fks: list[tuple[str, str, str, str]]
    values: dict[str, list[tuple[str, str, str]]]
    primary_keys: dict[str, set[str]] = field(default_factory=dict)


@dataclass
class Analysis:
    tables: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    ambiguities: list[Ambiguity] = field(default_factory=list)
    value_hits: list[str] = field(default_factory=list)


def _forms(term: str) -> set[str]:
    forms = {term}
    if term.endswith("y") and len(term) > 2:
        forms.add(term[:-1] + "ies")
    elif term.endswith("ies"):
        forms.add(term[:-3] + "y")
    elif term.endswith("s"):
        forms.add(term[:-1])
    else:
        forms |= {term + "s", term + "es"}
    return forms


def _pattern(term: str) -> re.Pattern:
    alts = "|".join(re.escape(f) for f in sorted(_forms(term), key=len, reverse=True))
    return re.compile(rf"(?<![a-z0-9])(?:{alts})(?![a-z0-9])")


def build_snapshot(database: AnalyticsDatabase, internal_tables=INTERNAL_TABLES) -> Snapshot:
    info = database.load_schema()
    columns = {t: [(c.name, c.type) for c in cols] for t, cols in info.tables.items() if t not in internal_tables}
    primary_keys = {t: set(info.primary_keys.get(t, ())) for t in columns}
    fks = [fk for fk in info.foreign_keys if fk[0] in columns and fk[2] in columns]
    values: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for table, cols in columns.items():
        for name, ctype in cols:
            type_up = ctype.upper()
            if name in primary_keys[table] or name.endswith("_id") or not ("CHAR" in type_up or "TEXT" in type_up):
                continue
            try:
                sampled = database.sample_values(table, name, VALUE_CARDINALITY_LIMIT + 1)
            except DatabaseError as e:
                log.warning("Could not sample %s.%s: %s", table, name, e)
                continue
            if len(sampled) > VALUE_CARDINALITY_LIMIT:
                continue
            for v in sampled:
                s = str(v).strip()
                if 3 <= len(s) <= 40 and not s.isdigit() and not re.match(r"^\d{4}-\d{2}-\d{2}", s):
                    values[re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()].append((table, name, s))
    return Snapshot(columns, fks, dict(values), primary_keys)


class SchemaIndex:
    def __init__(self, store: AppStore, database: AnalyticsDatabase, ttl_s: float = SCHEMA_CACHE_TTL_S,
                 clock: Callable[[], float] = time.monotonic):
        self.store = store
        self.database = database
        self.ttl_s = ttl_s
        self._clock = clock
        self._snap: Optional[Snapshot] = None
        self._loaded_at = 0.0
        self._lock = threading.Lock()

    def snapshot(self) -> Snapshot:
        with self._lock:
            now = self._clock()
            if self._snap is None or now - self._loaded_at >= self.ttl_s:
                try:
                    self._snap = build_snapshot(self.database)
                except DatabaseError:
                    if self._snap is None:
                        raise
                    log.warning("Schema refresh failed; serving the previous snapshot", exc_info=True)
                self._loaded_at = now
            return self._snap

    def refresh(self) -> Snapshot:
        with self._lock:
            self._snap = build_snapshot(self.database)
            self._loaded_at = self._clock()
            return self._snap

    def table_columns(self) -> dict[str, set[str]]:
        return {t: {c.lower() for c, _ in cols} for t, cols in self.snapshot().columns.items()}

    def _meta(self):
        return {(m.table_name.lower(), m.column_name.lower()): m for m in self.store.list_column_metadata()}

    def analyze(self, nl_query: str, choices: Optional[dict[str, str]] = None) -> Analysis:
        snap, meta = self.snapshot(), self._meta()
        choices = {k.lower(): v.lower() for k, v in (choices or {}).items()}
        sensitive = {k for k, m in meta.items() if m.is_sensitive}

        terms: dict[str, list[tuple]] = defaultdict(list)
        for t in snap.columns:
            terms[t.replace("_", " ")].append(("table", t))
        table_forms = set().union(*(_forms(t) for t in list(terms)))
        for t, cols in snap.columns.items():
            for c, _ in cols:
                if not (c.endswith("_id") or c == "id"):
                    terms[c.replace("_", " ")].append(("column", t, c))
                m = meta.get((t.lower(), c.lower()))
                for syn in (m.synonyms if m else []):
                    s = re.sub(r"[^a-z0-9]+", " ", syn.lower()).strip()
                    if s and not (_forms(s) & table_forms):
                        terms[s].append(("column", t, c))
        for key, targets in snap.values.items():
            for t, c, original in targets:
                if (t.lower(), c.lower()) not in sensitive:
                    terms[key].append(("value", t, c, original))

        text = " " + re.sub(r"[^a-z0-9]+", " ", nl_query.lower()) + " "
        hits: dict[str, list[tuple]] = {}
        for term in sorted(terms, key=lambda x: (-len(x), x)):
            pat = _pattern(term)
            if pat.search(text):
                targets = terms[term]
                tbl = [x for x in targets if x[0] == "table"]
                hits[term] = tbl or list(dict.fromkeys(targets))
                text = pat.sub(" ", text)

        table_hits = {x[1] for ts in hits.values() for x in ts if x[0] == "table"}
        resolved: dict[tuple, str] = {}
        pending: dict[str, list[tuple]] = {}
        for term, ts in hits.items():
            non_table = [x for x in ts if x[0]!= "table"]
            if len(non_table) == 1:
                resolved[non_table[0][:3]] = non_table[0][3] if non_table[0][0] == "value" else ""
            elif len(non_table) > 1:
                pending[term] = non_table

        ambiguities, unresolved = [], dict(pending)
        for _ in range(3):
            mentioned = table_hits | {k[1] for k in resolved}
            progressed = False
            for term, cands in list(unresolved.items()):
                keys = {f"{c[1]}.{c[2]}".lower(): c for c in cands}
                chosen = keys.get(choices.get(term, ""))
                narrowed = [c for c in cands if c[1] in mentioned]
                pick = chosen or (narrowed[0] if len(narrowed) == 1 else None)
                if pick:
                    resolved[pick[:3]] = pick[3] if pick[0] == "value" else ""
                    del unresolved[term]
                    progressed = True
            if not progressed:
                break
        mentioned = table_hits | {k[1] for k in resolved}
        for term, cands in unresolved.items():
            narrowed = [c for c in cands if c[1] in mentioned] or cands
            names = list(dict.fromkeys(f"{c[1]}.{c[2]}" for c in narrowed))
            if len(names) > 1:
                opts = " or ".join(f"`{n}`" for n in names)
                ambiguities.append(Ambiguity(term=term, candidates=names, message=f"'{term}' is ambiguous. Did you mean {opts}?"))

        columns, excluded, value_hits = [], [], []
        for (kind, t, c), original in resolved.items():
            name = f"{t}.{c}"
            if (t.lower(), c.lower()) in sensitive:
                excluded.append(name)
                continue
            columns.append(name)
            if kind == "value":
                value_hits.append(f"{name} = '{original}'")
        tables = [t for t in snap.columns if t in table_hits or any(c.startswith(t + ".") for c in columns)]
        return Analysis(tables, list(dict.fromkeys(columns)), excluded, ambiguities, value_hits)

    def describe(self) -> list[SchemaTable]:
        snap, meta = self.snapshot(), self._meta()
        out = []
        for t, cols in snap.columns.items():
            columns = []
            for name, ctype in cols:
                m = meta.get((t.lower(), name.lower()))
                columns.append(SchemaColumn(name=name, type=ctype, primary_key=name in snap.primary_keys.get(t, ()),
                                            description=m.description if m else "", synonyms=m.synonyms if m else [],
                                            is_sensitive=bool(m and m.is_sensitive)))
            out.append(SchemaTable(table_name=t, columns=columns))
        return out

    def _join_path(self, tables: list[str]) -> list[str]:
        snap = self.snapshot()
        graph = defaultdict(set)
        for t, _, rt, _ in snap.fks:
            graph[t].add(rt)
            graph[rt].add(t)
        keep = set(tables)
        for i, a in enumerate(tables):
            for b in tables[i + 1:]:
                dist, queue = {a: 0}, deque([a])
                while queue:
                    cur = queue.popleft()
                    for nxt in graph[cur]:
                        if nxt not in dist:
                            dist[nxt] = dist[cur] + 1
                            queue.append(nxt)
                if b in dist:
                    self._collect_paths(graph, a, b, dist[b] + 1, [a], keep)
        return [t for t in snap.columns if t in keep]

    def _collect_paths(self, graph, cur: str, target: str, budget: int, path: list[str], keep: set[str]) -> None:
        if cur == target:
            keep.update(path)
            return
        if budget == 0:
            return
        for nxt in graph[cur]:
            if nxt not in path:
                self._collect_paths(graph, nxt, target, budget - 1, path + [nxt], keep)

    def context(self, tables: list[str]) -> list[TableContext]:
        snap, meta = self.snapshot(), self._meta()
        selected = self._join_path(tables) if len(tables) > 1 else (tables or list(snap.columns))
        out = []
        for t in selected:
            cols = []
            for name, ctype in snap.columns[t]:
                m = meta.get((t.lower(), name.lower()))
                if m and m.is_sensitive:
                    continue
                cols.append(ColumnContext(name=name, type=ctype, description=m.description if m else "",
                                          synonyms=m.synonyms if m else []))
            fks = [f"{t}.{c} -> {rt}.{rc}" for ft, c, rt, rc in snap.fks if ft == t]
            out.append(TableContext(table=t, columns=cols, foreign_keys=fks))
        return out
