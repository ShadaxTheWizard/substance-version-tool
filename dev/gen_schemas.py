#!/usr/bin/env python3
"""Regenerate ../schemas.py from real projects plus the result of probe_fields.py.

    gen_schemas.py ADDED.json FILE_OR_DIR [FILE_OR_DIR ...]

Every .spp / .spt found is read: documents in the base data version (89) must agree with
the DOC_89 table already in schemas.py, documents of older versions are used to check the
derived layouts, documents of a newer version that a target release writes (91: 12.1)
supply its DOC_NEWER table, and the settings blobs (baking.ini, viewersettings2.ini, ...)
supply the AUX tables.  Feed it at least Painter's own resources/ folder.

The run adds to what schemas.py already holds (layout tables, usual values) and stops if
a project disagrees with a layout on record, so it only needs the projects that add
something.
"""
import collections
import glob
import json
import os
import pprint
import sys

import h5py
import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import sppformat as sf                 # noqa: E402
import spp_convert as sc               # noqa: E402
import schemas as old                  # noqa: E402

AUX_KINDS = ('baking.ini', 'viewersettings2.ini', 'iraysettings2.ini', 'posteffects.ini')
DOCS = ('paint/document.bin', 'paint/default_material.bin')
BASE = old.DOC_BASE_VERSION


def visit(root, cb):
    def walk(v):
        if isinstance(v, sf.Obj):
            cb(v.klass, [v.values])
            for fn, _ in v.klass.fields:
                walk(v.values[fn])
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, sf.Arr) and v.items:
            cb(v.klass, v.items)
            for it in v.items:
                for fn, _ in v.klass.fields:
                    walk(it[fn])
    walk(root)


def main():
    oracle = json.load(open(sys.argv[1]))
    files = []
    for a in sys.argv[2:]:
        if os.path.isdir(a):
            for ext in ('spp', 'spt'):
                files += glob.glob(os.path.join(a, '**', '*.' + ext), recursive=True)
        else:
            files.append(a)
    added = dict(oracle['added'])
    probed = {f.split('.')[0] for f in oracle['fields']}

    doc = collections.defaultdict(dict)                              # version -> class -> layout
    aux = collections.defaultdict(lambda: collections.defaultdict(dict))
    usual = collections.defaultdict(collections.Counter)
    auxvals = collections.defaultdict(collections.Counter)
    for p in sorted(files):
        with h5py.File(p, 'r') as f:
            def scan(name, obj):
                if not (isinstance(obj, h5py.Dataset) and obj.dtype == np.uint8 and obj.ndim == 1
                        and obj.shape[0] >= 20 and bytes(obj[:4]) == sf.MAGIC):
                    return
                base = name.rpartition('/')[2]
                isdoc = name in DOCS
                if not isdoc and base not in AUX_KINDS:
                    return
                hdr, root = sf.parse(bytes(obj[()]))
                ver = hdr[1]
                saved = None
                if 'projectsettings.ini' in f:
                    saved = sc.saved_with(sf.QStream(bytes(f['projectsettings.ini'][()])).variant_map())

                def cb(k, items):
                    table = doc[ver] if isdoc else aux[base][ver]
                    lay = [tuple(x) for x in k.fields]
                    if table.setdefault(k.name, lay) != lay:
                        sys.exit(f'{p}: {name}: two layouts for {k.name} in version {ver}')
                    for it in items:
                        for fn, ft in k.fields:
                            key = f'{k.name}.{fn}'
                            if isdoc:
                                if ver == BASE:
                                    usual[key][sc.fingerprint(it[fn])] += 1
                            elif ft in sf.FIXED or ft == sf.STRING:
                                auxvals[(base, ver, key)][bytes(it[fn])] += 1
                visit(root, cb)
            f.visititems(scan)

    doc_base = {k: [tuple(x) for x in v] for k, v in old.DOC_89.items()}
    for c, lay in doc[BASE].items():
        if doc_base.setdefault(c, lay) != lay:
            sys.exit(f'{c}: layout in the projects differs from schemas.DOC_89')
    # A class seen only in older projects is taken as unchanged up to BASE, unless a newer
    # project shows it with another layout: then BASE's layout is unknown, and the older
    # layout is kept for its own data version only (DOC_OLDER_SEEN).
    later = collections.defaultdict(set)
    for n, table in list(doc.items()) + list(getattr(old, 'DOC_NEWER', {}).items()):
        if n > BASE:
            for c, lay in table.items():
                later[c].add(tuple(tuple(x) for x in lay))
    older_seen = {n: dict(t) for n, t in getattr(old, 'DOC_OLDER_SEEN', {}).items()}
    assumed = []
    for n in sorted(doc):
        if n < BASE:
            for c, lay in doc[n].items():
                if c in doc_base:
                    continue
                if later[c] and later[c] != {tuple(lay)}:
                    if older_seen.setdefault(n, {}).setdefault(c, lay) != lay:
                        sys.exit(f'version {n}: layout of {c} differs from the one in schemas.py')
                    continue
                doc_base[c] = lay
                assumed.append(c)
    print('classes only seen in older projects (assumed unchanged):', sorted(set(assumed)))
    print('classes kept for their own version only:', {n: sorted(t) for n, t in older_seen.items()})
    # Classes the oracle could not probe inherit the dates of same-named fields in their family.
    for c in sorted(set(doc_base) - probed):
        for fn, _ in doc_base[c]:
            fam = [k for k in oracle['added'] if k.split('.')[1] == fn and k[:10] == c[:10]]
            vs = {oracle['added'][k] for k in fam}
            if len(vs) == 1:
                added[f'{c}.{fn}'] = vs.pop()
                print(f'  {c}.{fn}: taken as added in {added[c + "." + fn]} (like {fam[0]})')

    def layouts(n):
        return {c: [(a, t) for a, t in lay if added.get(f'{c}.{a}', 0) <= n] for c, lay in doc_base.items()}
    for n in sorted(doc):
        if oracle['lo'] <= n < BASE:
            bad = [c for c, lay in doc[n].items() if layouts(n).get(c, older_seen.get(n, {}).get(c)) != lay
                   and c not in assumed]
            print(f'derived layouts vs real version-{n} projects: {len(doc[n])} classes, mismatches: {bad}')
    for v in sorted(set(added.values())):
        print(f'  {v}: {sorted(k for k in added if added[k] == v)}')

    newer, newer_assumed = newer_tables(doc, doc_base)

    # Tables already in schemas.py are kept, so a run only needs the projects that add
    # something; a layout that disagrees with the one on record stops the run.
    doc_usual = dict(old.DOC_USUAL)
    for k in sorted(added):
        if usual.get(k) and k not in doc_usual:
            doc_usual[k] = usual[k].most_common(1)[0][0]
    aux_out = {k: {v: dict(cl) for v, cl in vs.items()} for k, vs in old.AUX.items()}
    for k, vs in aux.items():
        for v, cl in vs.items():
            have = aux_out.setdefault(k, {}).setdefault(v, {})
            for c, lay in cl.items():
                if have.setdefault(c, lay) != lay:
                    sys.exit(f'{k} version {v}: layout of {c} differs from the one in schemas.py')
    aux_out = {k: {v: dict(sorted(cl.items())) for v, cl in sorted(vs.items())} for k, vs in sorted(aux_out.items())}
    auxd = {k: {v: dict(d) for v, d in vs.items()} for k, vs in old.AUX_DEFAULTS.items()}
    for (base, ver, key), cnt in auxvals.items():
        auxd.setdefault(base, {}).setdefault(ver, {}).setdefault(key, cnt.most_common(1)[0][0])
    auxd = {k: {v: dict(sorted(d.items())) for v, d in sorted(vs.items())} for k, vs in sorted(auxd.items())}

    with open(os.path.join(REPO, 'schemas.py'), 'w') as out:
        out.write(HEADER % oracle['lo'])
        for name, val in (('DOC_89', dict(sorted(doc_base.items()))), ('DOC_ADDED', dict(sorted(added.items()))),
                          ('DOC_USUAL', doc_usual), ('DOC_OLDER_SEEN', dict(sorted(older_seen.items()))),
                          ('DOC_NEWER', newer), ('DOC_NEWER_ASSUMED', newer_assumed),
                          ('AUX', aux_out), ('AUX_DEFAULTS', auxd)):
            out.write(f'{name} = ' + pprint.pformat(val, width=110, compact=True, sort_dicts=False) + '\n\n')
    print('schemas.py written')


def newer_tables(doc, doc_base):
    """Layouts of the data versions after BASE that a target release writes.

    Classes seen in projects of that version are taken as they are.  Every other class of
    DOC_89 gets the field type changes that the seen classes show consistently (the same
    field name and old type always becomes the same new type), and is listed in
    DOC_NEWER_ASSUMED.  A class whose fields changed inconsistently, or that a project of
    the newest version shows with a different layout, is left out, so the converter
    warns instead of guessing."""
    newest = sc.NEWEST_DATA_VERSION
    tables = {v: dict(t) for v, t in getattr(old, 'DOC_NEWER', {}).items()}
    assumed_old = {v: set(cs) for v, cs in getattr(old, 'DOC_NEWER_ASSUMED', {}).items()}
    assumed_out = {}
    for n in sorted(set(doc) | set(tables)):
        if not BASE < n < newest:
            continue
        seen = {c: lay for c, lay in tables.get(n, {}).items() if c not in assumed_old.get(n, set())}
        for c, lay in doc.get(n, {}).items():
            if seen.setdefault(c, lay) != lay:
                sys.exit(f'version {n}: layout of {c} differs from the one in schemas.py')
        change = collections.defaultdict(set)              # (field, old type) -> new types
        for c, lay in seen.items():
            if c in doc_base:
                new = dict(lay)
                for fn, t in doc_base[c]:
                    if fn in new:
                        change[(fn, t)].add(new[fn])
        table, assumed = dict(seen), []
        for c, lay in doc_base.items():
            if c in seen:
                continue
            derived, ok = [], True
            for fn, t in lay:
                to = change.get((fn, t), {t})
                if len(to) != 1:
                    ok = False
                    break
                derived.append((fn, next(iter(to))))
            later = doc.get(newest, {}).get(c)
            if later is not None and later != derived:
                ok = False                                 # changed again later: unknown here
            if ok:
                table[c] = derived
                assumed.append(c)
            else:
                print(f'version {n}: no layout for {c} (not seen, changes unclear)')
        print(f'version {n}: {len(seen)} classes seen, assumed from DOC_89: {sorted(assumed)}')
        tables[n] = dict(sorted(table.items()))
        assumed_out[n] = sorted(assumed)
    return dict(sorted(tables.items())), assumed_out


HEADER = '''"""Class layouts of Substance 3D Painter's serialized project data, per data version.

Generated by dev/gen_schemas.py from real projects and from probing Painter 12.0.1
(see HANDOFF.md, "How the layouts were obtained"); do not edit by hand.

DOC_89       layouts of paint/document.bin in data version 89 (Painter 12.0): the ground
             truth, read from the projects and templates shipped with 12.0.1.
DOC_ADDED    fields of DOC_89 that older data versions do not have, with the data version
             that introduced each.  Everything else is unchanged back to DOC_MIN_VERSION.
DOC_USUAL    the value those fields usually hold (not worth a warning when dropped).
DOC_OLDER_SEEN  classes missing from DOC_89 whose layout is known for one older data
             version only (a newer version shows them changed, so 89's layout is unknown).
DOC_NEWER    complete layout tables of data versions after 89 that a target release
             writes (91: Painter 12.1), read from projects saved by that release.
DOC_NEWER_ASSUMED  classes of DOC_NEWER that no such project contained: DOC_89's layout
             with the field type changes every seen class shows (gen_schemas.newer_tables).
AUX          layouts of the settings blobs (baking.ini, viewersettings2.ini, ...), keyed by
             dataset name and that blob's own data version.
AUX_DEFAULTS usual values of their plain fields, used when a target layout has a field
             the newer source no longer has.
"""

DOC_BASE_VERSION = 89
DOC_MIN_VERSION = %d


def doc_layouts(version):
    """{class: [(field, type id), ...]} of the layer-stack document in a data version."""
    if version in DOC_NEWER:
        return DOC_NEWER[version]
    if not DOC_MIN_VERSION <= version <= DOC_BASE_VERSION:
        raise ValueError(f'no document layouts for data version {version}')
    out = {c: [(f, t) for f, t in fields if DOC_ADDED.get(f'{c}.{f}', 0) <= version]
           for c, fields in DOC_89.items()}
    out.update(DOC_OLDER_SEEN.get(version, {}))
    return out


'''

if __name__ == '__main__':
    main()
