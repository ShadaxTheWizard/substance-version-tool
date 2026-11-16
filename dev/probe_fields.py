#!/usr/bin/env python3
"""Find the data version that introduced each field of the layer-stack document.

Painter loads a project only if every class has exactly the fields of the stamped data
version (after its own upgrade steps).  So a document stamped N with field F removed
loads if, and only if, an upgrade step after N adds F.  This script removes groups of
fields from real projects of the newest probe-able version, stamps them with each older
version in turn and asks Painter (dev/oracle.py) whether they load.

    probe_fields.py [--lo 73] [--out added.json] PROJECT.spp [PROJECT.spp ...]

The projects must be in the data version of the Painter that is being driven (89 for
12.0.x).  Pick ones that together use as many layer and effect types as possible.
A cache file next to --out makes an interrupted run resumable.
"""
import argparse
import glob
import json
import os
import sys
import tempfile

import h5py

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sppformat as sf                 # noqa: E402
import spp_convert as sc               # noqa: E402
from oracle import Oracle              # noqa: E402

DOCS = ('paint/document.bin', 'paint/default_material.bin')
STRIP = ('texture-cache', 'resources')          # not needed to load the document; keeps probes small


def each_class(root, cb):
    """cb(klass) for every object / struct array, children first."""
    def walk(v):
        if isinstance(v, sf.Obj):
            for fn, _ in list(v.klass.fields):
                if fn in v.values:
                    walk(v.values[fn])
            cb(v.klass)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, sf.Arr) and v.klass:
            for it in v.items:
                for fn, _ in v.klass.fields:
                    if fn in it:
                        walk(it[fn])
            cb(v.klass)
    walk(root)


def write_probe(src, dst, stamp, drop):
    """Copy a project, stamping its documents with a data version and removing fields."""
    drop = set(drop)

    def fn(blob):
        hdr, root = sf.parse(blob)

        def cb(k):
            k.fields = [(a, t) for a, t in k.fields if f'{k.name}.{a}' not in drop]
        each_class(root, cb)
        return sf.write([hdr[0], stamp, hdr[2], 0], root)

    with h5py.File(src, 'r') as f, h5py.File(dst, 'w', libver=('earliest', 'v114')) as g:
        def copy(name, obj):
            if any(name == s or name.startswith(s + '/') for s in STRIP):
                return
            if isinstance(obj, h5py.Group):
                g.require_group(name)
            elif name in DOCS:
                sc._put(g, name, fn(bytes(obj[()])))
            else:
                parent, _, leaf = name.rpartition('/')
                f.copy(obj, g.require_group(parent) if parent else g, name=leaf)
        f.visititems(copy)


def classes_of(path):
    out, version = {}, None
    with h5py.File(path, 'r') as f:
        for d in DOCS:
            hdr, root = sf.parse(bytes(f[d][()]))
            version = hdr[1]
            each_class(root, lambda k: out.setdefault(k.name, [a for a, _ in k.fields]))
    return version, out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('projects', nargs='+')
    ap.add_argument('--lo', type=int, default=73, help='oldest data version to probe')
    ap.add_argument('--out', default='added.json')
    a = ap.parse_args()

    info, top = {}, None
    for p in a.projects:
        v, info[p] = classes_of(p)
        if top not in (None, v):
            sys.exit('all projects must have the same data version')
        top = v
    # Each class is probed in one project: a greedy cover of all classes.
    assigned, remaining = {}, set().union(*[set(c) for c in info.values()])
    while remaining:
        p = max(a.projects, key=lambda p: len(remaining & set(info[p])))
        assigned[p] = sorted(remaining & set(info[p]))
        remaining -= set(assigned[p])

    cache_file = a.out + '.cache'
    cache = json.load(open(cache_file)) if os.path.exists(cache_file) else {}
    oracle = Oracle(a.out + '.log')
    work = tempfile.mkdtemp(prefix='spp-probe-')
    crashed, optional, added, fields = [], [], {}, []

    def loads(base, n, drop):
        """True = Painter loads it, False = rejected (a crash counts as rejected)."""
        key = json.dumps([os.path.basename(base), n, sorted(drop)])
        if key not in cache:
            p = os.path.join(work, 'probe.spp')
            write_probe(base, p, n, drop)
            cache[key] = oracle.test(p)[0]
            for f_ in glob.glob(p + '*'):
                os.unlink(f_)
            json.dump(cache, open(cache_file, 'w'))
        if cache[key] is None and len(drop) == 1 and (n, drop[0]) not in crashed:
            crashed.append((n, drop[0]))
        return bool(cache[key])

    def removable(base, n, cand):
        """The fields of cand that may be absent at stamp n (group testing)."""
        cand = list(cand)
        if not cand:
            return []
        if loads(base, n, cand):
            return cand
        if len(cand) == 1:
            return []
        h = len(cand) // 2
        return removable(base, n, cand[:h]) + removable(base, n, cand[h:])

    for base, classes in assigned.items():
        # uid may always be left out, so it says nothing.
        universe = [f'{c}.{f}' for c in classes for f in info[base][c] if f != 'uid']
        fields += universe
        if not loads(base, a.lo, []):
            sys.exit(f'{base}: does not even load unmodified when stamped {a.lo}')
        cand = removable(base, a.lo, universe)
        print(os.path.basename(base), 'added after', a.lo, ':', cand, flush=True)
        for n in range(a.lo + 1, top):
            if not cand:
                break
            still = removable(base, n, cand)
            for f in cand:
                if f not in still:
                    added[f] = n
                    print('   ', f, 'added in', n, flush=True)
            cand = still
        # What may be absent even at the runtime version is optional, not "added".
        opt = removable(base, top, cand) if cand else []
        for f in cand:
            if f in opt:
                optional.append(f)
            else:
                added[f] = top
                print('   ', f, 'added in', top, flush=True)
    oracle.stop()
    json.dump({'lo': a.lo, 'top': top, 'added': added, 'fields': fields, 'optional': optional,
               'crashed': crashed}, open(a.out, 'w'), indent=1)
    print('probes:', oracle.n, 'crashes:', oracle.crashes)


if __name__ == '__main__':
    main()
