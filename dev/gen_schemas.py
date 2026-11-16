#!/usr/bin/env python3
"""Regenerate ../schemas.py from real projects plus the result of probe_fields.py.

    gen_schemas.py ADDED.json FILE_OR_DIR [FILE_OR_DIR ...]

Every .spp / .spt found is read: documents in the base data version (89) must agree with
the DOC_89 table already in schemas.py, documents of older versions are used to check the
derived layouts, and the settings blobs (baking.ini, viewersettings2.ini, ...) supply the
AUX tables.  Feed it at least Painter's own resources/ folder.
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
    assumed = []
    for n in sorted(doc):
        if n < BASE:
            for c, lay in doc[n].items():
                if c not in doc_base:
                    doc_base[c] = lay
                    assumed.append(c)
    print('classes only seen in older projects (assumed unchanged):', sorted(set(assumed)))
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
            bad = [c for c, lay in doc[n].items() if layouts(n).get(c) != lay and c not in assumed]
            print(f'derived layouts vs real version-{n} projects: {len(doc[n])} classes, mismatches: {bad}')
    for v in sorted(set(added.values())):
        print(f'  {v}: {sorted(k for k in added if added[k] == v)}')

    doc_usual = {k: usual[k].most_common(1)[0][0] for k in sorted(added) if usual.get(k)}
    aux_out = {k: {v: dict(sorted(cl.items())) for v, cl in sorted(vs.items())} for k, vs in sorted(aux.items())}
    auxd = collections.defaultdict(lambda: collections.defaultdict(dict))
    for (base, ver, key), cnt in auxvals.items():
        auxd[base][ver][key] = cnt.most_common(1)[0][0]
    auxd = {k: {v: dict(sorted(d.items())) for v, d in sorted(vs.items())} for k, vs in sorted(auxd.items())}

    with open(os.path.join(REPO, 'schemas.py'), 'w') as out:
        out.write(HEADER % oracle['lo'])
        for name, val in (('DOC_89', dict(sorted(doc_base.items()))), ('DOC_ADDED', dict(sorted(added.items()))),
                          ('DOC_USUAL', doc_usual), ('AUX', aux_out), ('AUX_DEFAULTS', auxd)):
            out.write(f'{name} = ' + pprint.pformat(val, width=110, compact=True, sort_dicts=False) + '\n\n')
    print('schemas.py written')


HEADER = '''"""Class layouts of Substance 3D Painter's serialized project data, per data version.

Generated by dev/gen_schemas.py from real projects and from probing Painter 12.0.1
(see HANDOFF.md, "How the layouts were obtained"); do not edit by hand.

DOC_89       layouts of paint/document.bin in data version 89 (Painter 12.0): the ground
             truth, read from the projects and templates shipped with 12.0.1.
DOC_ADDED    fields of DOC_89 that older data versions do not have, with the data version
             that introduced each.  Everything else is unchanged back to DOC_MIN_VERSION.
DOC_USUAL    the value those fields usually hold (not worth a warning when dropped).
AUX          layouts of the settings blobs (baking.ini, viewersettings2.ini, ...), keyed by
             dataset name and that blob's own data version.
AUX_DEFAULTS usual values of their plain fields, used when a target layout has a field
             the newer source no longer has.
"""

DOC_BASE_VERSION = 89
DOC_MIN_VERSION = %d


def doc_layouts(version):
    """{class: [(field, type id), ...]} of the layer-stack document in a data version."""
    if not DOC_MIN_VERSION <= version <= DOC_BASE_VERSION:
        raise ValueError(f'no document layouts for data version {version}')
    return {c: [(f, t) for f, t in fields if DOC_ADDED.get(f'{c}.{f}', 0) <= version]
            for c, fields in DOC_89.items()}


'''

if __name__ == '__main__':
    main()
