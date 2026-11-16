#!/usr/bin/env python3
"""Convert Substance 3D Painter projects (.spp) so an older Painter release can open them.

Painter refuses a project whose serializer "data version" is newer than its own, and it
checks every class layout strictly: one field too many or too few and the project does
not load.  This tool rewrites a project in the exact layout of the target release
(Painter 9.0 ... 12.2):

* paint/document.bin and paint/default_material.bin are re-serialized with the target's
  class layouts (schemas.py).  Fields the target does not know are dropped; where the
  representation changed (12.1/12.2: 128-bit channel masks, channel enums, polygon-fill
  hits, single "sourceTransparent" objects) the old representation is rebuilt.
* baking and viewer settings are rewritten in the target's layout when it is known.
* projectsettings.ini gets the target's version stamp (and auto-UV settings block).
* the texture cache is converted to the target's channel keys (or dropped).
* every rewritten dataset gets a correct m3_x64_128 checksum.

Going the other way needs no tool: a newer Painter opens an older project by itself.
The original file is never modified.
"""
import argparse
import json
import os
import re
import struct
import sys
import tempfile
from collections import Counter

import h5py
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sppformat as sf                                   # noqa: E402
import schemas                                           # noqa: E402

__version__ = '0.3.0'

# ---------------------------------------------------------------------------
# Painter releases and what each one reads
# ---------------------------------------------------------------------------
RELEASES = [(9, 0), (9, 1), (10, 0), (10, 1), (11, 0), (11, 1), (12, 0), (12, 1), (12, 2)]
DEFAULT_TARGET = '12.0.1'
NEWEST_DATA_VERSION = 92                       # 12.2
OLDEST_DATA_VERSION = schemas.DOC_MIN_VERSION  # oldest layout this tool can write


class Profile:
    """The file-format versions written for a target release."""

    def __init__(self, doc, aux, anorigami, cache):
        self.doc = doc                # data version of paint/document.bin
        self.aux = aux                # {dataset basename: data version} of the settings blobs
        self.anorigami = anorigami    # version of the auto-UV settings block (None = empty)
        self.cache = cache            # texture-cache header version (None = drop the cache)


# Releases whose formats were read from real projects saved by that release.
PROFILES = {
    (10, 1): Profile(81, {'baking.ini': 20}, None, (3, 2)),
    (11, 0): Profile(82, {'baking.ini': 21, 'viewersettings2.ini': 27, 'iraysettings2.ini': 1,
                          'posteffects.ini': 7}, 6, (3, 2)),
    (12, 0): Profile(89, {'baking.ini': 23, 'viewersettings2.ini': 29, 'iraysettings2.ini': 2,
                          'posteffects.ini': 9}, 7, (3, 2)),
    (12, 2): Profile(92, {'baking.ini': 26, 'viewersettings2.ini': 31, 'iraysettings2.ini': 2,
                          'posteffects.ini': 9}, 8, (3, 3)),
}
# Releases older than the oldest one above (9.0, 9.1, 10.0).  No project saved by them
# was available, so their exact data versions are unknown.  Data version 77 is the one
# that introduced 3D path strokes, the headline feature of Painter 9.0, so 9.0 and later
# read it (and upgrade it themselves).
LEGACY_PROFILE = Profile(77, {}, None, None)


class ConversionError(Exception):
    pass


class Target:
    def __init__(self, spec, data_version=None):
        m = re.fullmatch(r'\s*v?(\d+)(?:\.(\d+))?(?:\.(\d+))?\s*', str(spec))
        if not m:
            raise ConversionError(f'"{spec}" is not a Painter version (examples: 12.0.1, 11.0, 9)')
        self.app = tuple(int(x or 0) for x in m.groups())
        self.release = self.app[:2]
        if self.release not in RELEASES:
            known = ', '.join(f'{a}.{b}' for a, b in RELEASES)
            raise ConversionError(f'Painter {self.release[0]}.{self.release[1]} is not supported '
                                  f'(supported releases: {known})')
        self.label = '.'.join(str(x) for x in (self.app if m.group(3) else self.release))
        self.name = f'{self.release[0]}.{self.release[1]}'
        # Use this release's own format, or else the nearest older release's: Painter
        # opens (and upgrades) anything older than itself.
        self.basis = None
        for rel in reversed(RELEASES[:RELEASES.index(self.release) + 1]):
            if rel in PROFILES:
                self.basis = rel
                break
        self.profile = PROFILES[self.basis] if self.basis else LEGACY_PROFILE
        self.exact = self.basis == self.release
        self.forced = data_version is not None and data_version != self.profile.doc
        if self.forced:
            if not OLDEST_DATA_VERSION <= data_version <= schemas.DOC_BASE_VERSION:
                raise ConversionError(f'--data-version must be between {OLDEST_DATA_VERSION} '
                                      f'and {schemas.DOC_BASE_VERSION}')
            p = self.profile
            self.profile = Profile(data_version, {}, p.anorigami if data_version >= p.doc else None,
                                   p.cache if data_version >= 81 else None)

    def note(self):
        """One line on how sure the output format is, or None when it is the release's own."""
        if self.forced:
            return f'written as data version {self.profile.doc}, as requested'
        if self.exact:
            return None
        if self.basis:
            return (f'written in the Painter {self.basis[0]}.{self.basis[1]} format, which '
                    f'{self.name} opens; settings newer than {self.basis[0]}.{self.basis[1]} are dropped')
        return (f'written as data version {self.profile.doc} (the one that introduced Painter 9.0\'s '
                f'paths). No project saved by Painter {self.name} was available to confirm its '
                f'exact format: if it reports "created with a newer version", retry with '
                f'--data-version {OLDEST_DATA_VERSION}')


def default_target(data_version=None):
    return Target(os.environ.get('SPP_TARGET_VERSION') or DEFAULT_TARGET, data_version)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
# Human-readable names for data the target release has no place for.
DROPPED_DESCRIPTIONS = {
    'DataMaterialStack.shaderModel': 'texture-set shader model (12.2 feature)',
    'DataChannel.backgroundColor': 'custom channel background colour',
    'DataChannel.backgroundColorIsOverriden': 'custom channel background colour',
    'DataChannel.colorProfileOverride': 'per-channel colour profile override',
    'DataChannel.shaderModelOverride': 'per-channel shader model override',
    'DataChannel.userDescription': 'channel description text',
    'DataActionDecal.blendingMode': 'polygon fill blending mode',
    'DataActionDecal.opacity': 'polygon fill opacity',
    'DataDecalHitPunctual.restrictToQuads': 'polygon fill "restrict to quads" option',
    'DataDecalHitRect.restrictToQuads': 'polygon fill "restrict to quads" option',
    'DataDecalHitRect.occlusionCulling': 'polygon fill rectangle occlusion culling',
    'DataDecalHitRect.collisionMode': 'polygon fill rectangle collision mode',
    'DataDecalHitRect.paintThrough': 'polygon fill rectangle paint-through',
    'DataDecalHitPunctual.flags': 'polygon fill flags',
    'DataDecalHitRect.flags': 'polygon fill flags',
    'DataGridDeformation.stickyBackupVertices': 'warp projection sticky points',
    'DataGridDeformation.stickyState': 'warp projection sticky points',
    'DataMaterialStack.type': 'layer stack type',
    'DataMaterialStack.typeMetadata': 'layer stack type',
    'DataActionFill.symmetry': 'fill layer symmetry',
    'DataSourceProcedural.resolutionOverride': 'resolution override of a procedural/text/vector source',
    'DataSourceText.resolutionOverride': 'resolution override of a procedural/text/vector source',
    'DataSourceVectorial.resolutionOverride': 'resolution override of a procedural/text/vector source',
    'DataSourceText.letterSpacing': 'text letter / line spacing',
    'DataSourceText.lineSpacing': 'text letter / line spacing',
    'DataLayerColor.explicitPersistentCachePin': 'layer cache pin',
    'DataLayerGroup.explicitPersistentCachePin': 'layer cache pin',
    'DataActionFill.uvTransferChannel': 'fill layer UV transfer channel',
    'DataStroke.label': 'stroke / path names',
    'DataActionPaint.strokes3D': '3D path strokes',
    'DataActionPaintProj.strokes3D': '3D path strokes',
    'DataActionEraser.strokes3D': '3D path strokes',
    'DataActionSmudge.strokes3D': '3D path strokes',
    'DataActionClone.strokes3D': '3D path strokes',
    'Data2DTransformation.physicalSizeBoundedByScaleFactors': 'physical-size options of a projection',
    'DataDocument.usePhysicalSizeAsDefault': 'project option "use physical size by default"',
}

# 12.2 default values of 12.2-only fields: dropping these loses nothing.
KNOWN_DEFAULTS = {
    'DataChannel.backgroundColor': np.array([1, 1, 1, 1], '<f4').tobytes(),
    'DataMaterialStack.shaderModel': struct.pack('<i', 3),
    'DataDecalHitPunctual.restrictToQuads': b'\xff',
    'DataDecalHitRect.restrictToQuads': b'\xff',
    'DataDecalHitRect.occlusionCulling': b'\xff',
    'DataDecalHitRect.collisionMode': struct.pack('<i', 1),
}

# Internal bookkeeping that is not worth a line in the report when it is dropped.
SILENT_FIELDS = {'changeFlags'}

WHITE = np.array([1, 1, 1], '<f4').tobytes()


class Report:
    def __init__(self, target):
        self.target = target
        self.info = []
        self.dropped = Counter()         # "Class.field" -> count of non-default values lost
        self.warnings = []
        self.aux_left = False            # settings blobs with no known target layout

    def warn(self, msg):
        if msg not in self.warnings:
            self.warnings.append(msg)


# ---------------------------------------------------------------------------
# "Data" blob conversion (layer stack documents and settings blobs)
# ---------------------------------------------------------------------------
class Unconvertible(Exception):
    """A settings blob that cannot be expressed in the target layout."""


def _is_default(v):
    if v is None:
        return True
    if isinstance(v, (bytes, bytearray)):
        return not any(v)
    if isinstance(v, list):
        return not v
    if isinstance(v, sf.Arr):
        return not v.items
    return False


def _default_for(t):
    if t in sf.FIXED:
        return bytes(sf.FIXED[t])
    return {sf.STRING: b'', sf.ARRAY: sf.Arr(None, []), sf.OBJECT: None,
            sf.OBJLIST: [], sf.EMBED: None}[t]


def fingerprint(v):
    """A comparable summary of a field value; object identities (uid) are left out."""
    if isinstance(v, sf.Obj):
        return (v.klass.name, tuple((fn, fingerprint(v.values[fn]))
                                    for fn, _ in v.klass.fields if fn != 'uid'))
    if isinstance(v, list):
        return tuple(fingerprint(x) for x in v)
    if isinstance(v, sf.Arr):
        if not v.items:
            return ()
        return tuple(tuple((fn, fingerprint(it[fn])) for fn, _ in v.klass.fields if fn != 'uid')
                     for it in v.items)
    return bytes(v) if isinstance(v, (bytes, bytearray)) else v


def _mat(b):
    """Column-major float32[16] -> 4x4 float64 matrix."""
    return np.frombuffer(b, '<f4').reshape(4, 4).T.astype(np.float64)


def _mat_bytes(m):
    return np.ascontiguousarray(m.T, dtype='<f4').tobytes()


class DataConverter:
    """Re-expresses a parsed Data tree in the target class layouts.

    layouts   {class name: [(field, type id), ...]} of the target
    strict    unknown classes raise Unconvertible instead of being kept as they are
    defaults  {"Class.field": raw value} for target fields the source does not have
    usual     {"Class.field": fingerprint} values that are not worth reporting when dropped
    """

    def __init__(self, report, layouts, strict=False, defaults=None, usual=None, quiet=False):
        self.report = report
        self.layouts = layouts
        self.strict = strict
        self.defaults = defaults or {}
        self.usual = usual or {}
        self.quiet = quiet
        self.klasses = {}
        self.decal_color = None

    def klass(self, name, fields):
        k = self.klasses.get(name)
        if k is None:
            k = self.klasses[name] = sf.Klass(name, fields)
        return k

    # -- values -------------------------------------------------------------
    def value(self, v, t_src, t_dst, where):
        if t_src == t_dst:
            if t_src in (sf.OBJECT, sf.EMBED):
                return self.obj(v)
            if t_src == sf.OBJLIST:
                return [self.obj(x) for x in v]
            if t_src == sf.ARRAY:
                return self.arr(v)
            return v
        if t_src == 22 and t_dst == 12:                 # 128-bit -> 64-bit channel mask
            return v[:8]
        if t_src == 9 and t_dst in (21, 12):            # channel index -> one-hot mask
            idx = struct.unpack('<i', v)[0]
            if idx < 0:
                return bytes(8)
            if idx >= 64:
                raise ConversionError(f'{where}: channel type {idx} does not exist in '
                                      f'Painter {self.report.target.name}')
            return (1 << idx).to_bytes(8, 'little')
        if t_src == sf.OBJECT and t_dst == sf.OBJLIST:  # single object -> list
            return [] if v is None else [self.obj(v)]
        if t_src == sf.OBJLIST and t_dst == sf.OBJECT:
            return self.obj(v[0]) if v else None
        if self.strict:
            raise Unconvertible(f'{where}: field type {t_src} -> {t_dst}')
        raise ConversionError(f'{where}: no conversion from field type {t_src} to {t_dst}')

    def arr(self, a):
        if not a.items:
            return sf.Arr(None, [])
        k, items = None, []
        for it in a.items:
            k, vals = self.fields(a.klass, it)
            items.append(vals)
        return sf.Arr(k, items)

    def obj(self, o):
        if o is None:
            return None
        name = o.klass.name
        saved = self.decal_color
        if name == 'DataActionDecal':
            self.decal_color = self._decal_prepare(o)
        try:
            k, vals = self.fields(o.klass, o.values)
        finally:
            self.decal_color = saved
        return sf.Obj(k, vals)

    def fields(self, klass, values):
        name = klass.name
        src_types = dict(klass.fields)
        dst_fields = self.layouts.get(name)
        if dst_fields is None:
            if self.strict:
                raise Unconvertible(f'class {name} is not in the target layout')
            # Not in the layout table: keep the layout, only downgrade field types.
            self.report.warn(f'{name} is not in the layout table for Painter '
                             f'{self.report.target.name}; kept as it is, which Painter may reject')
            dst_fields = [(fn, 12 if ft == 22 else ft) for fn, ft in klass.fields]
        out, consumed = {}, set()
        for fn, t_dst in dst_fields:
            where = f'{name}.{fn}'
            rule = getattr(self, f'_rule_{name}_{fn}', None) or getattr(self, f'_rule_any_{fn}', None)
            if rule is not None:
                res = rule(values, consumed)
                if res is not NotImplemented:
                    out[fn] = res
                    continue
            if fn in values:
                out[fn] = self.value(values[fn], src_types[fn], t_dst, where)
                consumed.add(fn)
            elif where in self.defaults:
                out[fn] = self.defaults[where]
            elif self.strict and t_dst in (sf.OBJECT, sf.EMBED):
                # A settings object the source no longer has cannot be made up.
                raise Unconvertible(f'{where} has no counterpart in the newer format')
            else:
                out[fn] = _default_for(t_dst)
        if not self.quiet:
            for fn in values:
                if fn in consumed or fn in out or fn in SILENT_FIELDS:
                    continue
                key, v = f'{name}.{fn}', values[fn]
                if _is_default(v) or KNOWN_DEFAULTS.get(key) == v:
                    continue
                if key in self.usual and fingerprint(v) == self.usual[key]:
                    continue
                self.report.dropped[key] += 1
        return self.klass(name, dst_fields), out

    # -- special cases ------------------------------------------------------
    def _rule_DataChannel_userIsColorManaged(self, values, consumed):
        if 'userIsColor' in values:
            consumed.add('userIsColor')
            return values['userIsColor']
        return NotImplemented

    def _decal_prepare(self, o):
        """12.2 polygon fills carry their fill as a list of sources; older releases store
        a colour on each hit.  Use the (uniform) source colour for every hit."""
        v = o.values
        if 'sources' not in v:
            return None
        sources = [s for s in v['sources'] if s is not None]
        color = WHITE
        if sources and sources[0].klass.name == 'DataSourceUniform':
            color = sources[0].values.get('color', WHITE)
            src_op = np.frombuffer(sources[0].values.get('opacity', b'\0\0\x80\x3f'), '<f4')[0]
            if abs(src_op - 1.0) > 1e-6:
                self.report.dropped['DataSourceUniform.opacity (polygon fill)'] += 1
        elif sources:
            self.report.warn('A polygon fill uses a non-uniform source (material/bitmap); '
                             'it is converted as a plain white fill.')
        if len(sources) > 1:
            self.report.warn('A polygon fill has several sources; only the first colour is kept.')
        bm = struct.unpack('<i', v.get('blendingMode', b'\2\0\0\0'))[0]
        op = np.frombuffer(v.get('opacity', b'\0\0\x80\x3f'), '<f4')[0]
        if bm != 2:
            self.report.dropped['DataActionDecal.blendingMode'] += 1
        if abs(op - 1.0) > 1e-6:
            self.report.dropped['DataActionDecal.opacity'] += 1
        return color

    def _rule_DataActionDecal_hits(self, values, consumed):
        for fn in ('sources', 'blendingMode', 'opacity'):
            if fn in values:
                consumed.add(fn)
        return NotImplemented          # convert the hits normally (after the bookkeeping)

    def _hit_color(self, values, consumed):
        if 'color' in values:
            return NotImplemented
        return self.decal_color if self.decal_color is not None else WHITE

    _rule_DataDecalHitPunctual_color = _hit_color
    _rule_DataDecalHitRect_color = _hit_color

    def _hit_matrix(self, values, consumed):
        if 'projViewInvMatrix' in values or 'projectionMatrix' not in values:
            return NotImplemented
        consumed.update(('projectionMatrix', 'viewMatrix'))
        P, V = _mat(values['projectionMatrix']), _mat(values['viewMatrix'])
        if not P.any() or not V.any():
            return bytes(64)
        try:
            inv = np.linalg.inv(P @ V)
        except np.linalg.LinAlgError:
            self.report.warn('A polygon fill hit had a degenerate camera; it may not re-apply correctly.')
            return bytes(64)
        return _mat_bytes(inv)

    _rule_DataDecalHitPunctual_projViewInvMatrix = _hit_matrix
    _rule_DataDecalHitRect_projViewInvMatrix = _hit_matrix


def _check_layouts(blob, layouts, version, label):
    """Re-parse a written blob and make sure every class uses the target layout."""
    hdr, root = sf.parse(blob)
    bad = set()

    def check(k):
        want = layouts.get(k.name)
        if want is not None and [tuple(f) for f in k.fields] != [tuple(f) for f in want]:
            bad.add(k.name)
    sf.walk_classes(root, check)
    if bad or hdr[1] != version:
        raise ConversionError(f'{label}: self-check failed for {sorted(bad)}')


def convert_document(blob, report, label):
    """Layer-stack document -> target layout, or None when it is old enough already."""
    hdr, root = sf.parse(blob)
    version = report.target.profile.doc
    if hdr[1] <= version:
        return None
    layouts = schemas.doc_layouts(version)
    conv = DataConverter(report, layouts, usual=schemas.DOC_USUAL)
    out = sf.write([hdr[0], version, hdr[2], 0], conv.obj(root))
    _check_layouts(out, layouts, version, label)
    return out


AUX_NAMES = {'baking.ini': 'baking settings', 'viewersettings2.ini': 'viewer and shader settings',
             'iraysettings2.ini': 'Iray settings', 'posteffects.ini': 'post-effects settings'}


def convert_aux(blob, kind, report):
    """Settings blob -> target layout.  Returns None when nothing has to change or when
    it cannot be expressed in the target layout (Painter then falls back to its defaults)."""
    version = report.target.profile.aux.get(kind)
    have = sf.data_version(blob)
    if version is None:
        if kind in AUX_NAMES:
            report.aux_left = True
        return None
    if have is None or have <= version:
        return None
    layouts = schemas.AUX.get(kind, {}).get(version)
    try:
        if layouts is None:
            raise Unconvertible('no layout table')
        hdr, root = sf.parse(blob)
        conv = DataConverter(report, layouts, strict=True, quiet=True,
                             defaults=schemas.AUX_DEFAULTS.get(kind, {}).get(version))
        out = sf.write([hdr[0], version, hdr[2], 0], conv.obj(root))
        _check_layouts(out, layouts, version, kind)
    except (Unconvertible, sf.FormatError) as e:
        report.info.append(f'{AUX_NAMES.get(kind, kind)} could not be converted ({e}); '
                           f'Painter {report.target.name} will use its defaults')
        return None
    report.info.append(f'{AUX_NAMES.get(kind, kind)} converted')
    return out


# ---------------------------------------------------------------------------
# Qt settings blobs
# ---------------------------------------------------------------------------
# Auto-UV ("anorigami") settings block, by block version.
ANORIGAMI_KEYS = {
    7: {'createFewerSeamsEnabled', 'enabled', 'keepIslandOrientationsEnabled', 'loadUVsFromMesh',
        'maxNbUVTiles', 'minTexelDensity', 'nbIslandRotations', 'nbUVTilesPolicy',
        'referenceTextureResolution', 'segmentElongatedChartsEnabled', 'uvIslandsMargin', 'version'},
    6: {'createFewerSeamsEnabled', 'enabled', 'keepIslandOrientationsEnabled', 'loadUVsFromMesh',
        'maxNbUVTiles', 'minTexelDensity', 'nbIslandRotations', 'referenceTextureResolution',
        'segmentElongatedChartsEnabled', 'useTexelDensityEnabled', 'uvIslandsMargin', 'version'},
}
ANORIGAMI_NEW_KEYS = {'useTexelDensityEnabled': False, 'nbUVTilesPolicy': 0}
ANORIGAMI_EMPTY = b'{\n}\n'


def _convert_anorigami(raw, version):
    try:
        js = json.loads(raw)
    except ValueError:
        return raw
    if not isinstance(js, dict) or not js:
        return raw
    if version is None:
        return ANORIGAMI_EMPTY
    if js.get('version', 0) <= version:
        return raw
    keys = ANORIGAMI_KEYS[version]
    js = {k: js.get(k, ANORIGAMI_NEW_KEYS.get(k)) for k in keys}
    js['version'] = version
    return json.dumps(js, indent=4, sort_keys=True).encode()


def saved_with(entries):
    """(major, minor, patch) of the Painter that last saved the project, or None."""
    d = dict(entries)
    try:
        return tuple(d[f'versionAtLastSave/{p}'][2] for p in ('major', 'minor', 'patch'))
    except KeyError:
        return None


def convert_projectsettings(blob, report):
    qs = sf.QStream(blob)
    entries = qs.variant_map()
    if qs.o != len(blob):
        raise ConversionError('projectsettings.ini: unexpected trailing data')
    target = report.target
    d = dict(entries)

    def ver(prefix):
        try:
            return tuple(d[f'{prefix}/{p}'][2] for p in ('major', 'minor', 'patch'))
        except KeyError:
            return None
    created, saved = ver('versionAtCreation'), ver('versionAtLastSave')
    out = []
    for k, (t, n, v) in entries:
        for prefix, cur in (('versionAtLastSave', saved), ('versionAtCreation', created)):
            if k.startswith(prefix + '/') and cur is not None and cur > target.app:
                v = target.app[('major', 'minor', 'patch').index(k.split('/')[1])]
        if k == 'anorigami' and t == sf.Q_BYTEARRAY and v:
            v = _convert_anorigami(v, target.profile.anorigami)
        out.append((k, (t, n, v)))
    return sf.variant_map_bytes(out)


# ---------------------------------------------------------------------------
# Texture cache (final channel composites, keyed by channel)
# ---------------------------------------------------------------------------
def _cache_header_parse(blob):
    qs = sf.QStream(blob)
    meta = qs.variant_map()
    entries = [(qs.qstring(), qs.qbytes(8)) for _ in range(qs.u32())]
    if qs.o != len(blob):
        raise sf.FormatError('texture-cache header: trailing data')
    return meta, entries


def _cache_header_bytes(meta, entries):
    out = bytearray(sf.variant_map_bytes(meta)) + struct.pack('>I', len(entries))
    for path, tag in entries:
        out += sf.qstring_bytes(path) + tag
    return bytes(out)


def _channel_key(index_str):
    i = int(index_str)
    if not 0 <= i < 64:
        raise sf.FormatError(f'texture-cache channel {i} has no equivalent before Painter 12.1')
    return str(1 << i)


def plan_texture_cache(f, drop, report):
    """Return None/False (leave the cache alone) or
    (header_bytes, {old_path: None (remove) | new_path | (path, new_bytes)})."""
    if 'texture-cache' not in f or 'header' not in f['texture-cache']:
        return None
    empty = _cache_header_bytes([('version', (3, 0, 3)), ('version.minor', (3, 0, 2))], [])
    tc = f['texture-cache']
    everything = {}

    def collect(name, obj):
        if isinstance(obj, h5py.Dataset) and name != 'header':
            everything['texture-cache/' + name] = None
    tc.visititems(collect)
    if drop or report.target.profile.cache is None:
        report.info.append('texture cache dropped (Painter rebuilds it on open)')
        return empty, everything
    try:
        meta, entries = _cache_header_parse(bytes(tc['header'][()]))
        md = {k: v[2] for k, v in meta}
        if (md.get('version'), md.get('version.minor')) == (3, 2):
            return False                                  # already the old layout
        if (md.get('version'), md.get('version.minor')) != (3, 3):
            raise sf.FormatError(f'unknown texture-cache version {md}')
        new_meta = [(k, (t, n, 2 if k == 'version.minor' else v)) for k, (t, n, v) in meta]
        new_entries = []
        for path, tag in entries:
            head, _, last = path.rpartition('/')
            new_entries.append((f'{head}/{_channel_key(last)}', tag))
        renames = {}
        for path in everything:
            head, _, last = path.rpartition('/')
            if last.isdigit():
                renames[path] = f'{head}/{_channel_key(last)}'
            elif last == 'list':
                raw = bytes(f[path][()])
                if len(raw) % 16:
                    raise sf.FormatError('unexpected texture-cache list size')
                conv = bytearray()
                for i in range(0, len(raw), 16):
                    idx = struct.unpack_from('>I', raw, i + 12)[0]
                    if idx >= 64:
                        raise sf.FormatError(f'texture-cache channel {idx} has no older equivalent')
                    conv += raw[i:i + 12] + struct.pack('>Q', 1 << idx)
                renames[path] = (path, bytes(conv))
            else:
                renames[path] = path
        report.info.append('texture cache converted')
        return _cache_header_bytes(new_meta, new_entries), renames
    except (sf.FormatError, ValueError, struct.error, KeyError) as e:
        report.warn(f'Texture cache could not be converted ({e}); dropped instead. '
                    'Painter will rebuild it when the project opens.')
        return empty, everything


# ---------------------------------------------------------------------------
# HDF5 container
# ---------------------------------------------------------------------------
def _put(dst, path, data):
    arr = np.frombuffer(data, np.uint8)
    kw = dict(chunks=(65536,), maxshape=(None,)) if len(arr) >= 65536 else {}
    ds = dst.create_dataset(path, data=arr, **kw)
    ds.attrs.create('m3_x64_128', sf.content_hash(data))


class ProjectInfo:
    """What a project file says about the Painter that saved it."""

    def __init__(self, path):
        with h5py.File(path, 'r') as f:
            if 'paint/document.bin' not in f:
                raise ConversionError('no paint/document.bin - is this a Painter project?')
            self.data_version = sf.data_version(bytes(f['paint/document.bin'][:20]))
            if self.data_version is None:
                raise ConversionError('paint/document.bin is not in a known format')
            self.saved_with = None
            if 'projectsettings.ini' in f:
                try:
                    self.saved_with = saved_with(
                        sf.QStream(bytes(f['projectsettings.ini'][()])).variant_map())
                except (sf.FormatError, struct.error, IndexError, UnicodeDecodeError):
                    pass

    def saved_label(self):
        return '.'.join(map(str, self.saved_with)) if self.saved_with else 'an unknown version'

    def opens_in(self, target):
        """True when the target release reads the project without conversion."""
        if self.data_version <= target.profile.doc:
            return True
        # Saved by the target release (or an older one) whose format this tool has no table for.
        return self.saved_with is not None and self.saved_with[:2] <= target.release


def convert(src, dst, target=None, drop_cache=False, force=False):
    target = target or default_target()
    report = Report(target)
    if os.path.abspath(src) == os.path.abspath(dst):
        raise ConversionError('output must be a different file than the input')
    info = ProjectInfo(src)
    if info.opens_in(target):
        raise ConversionError(f'saved with Painter {info.saved_label()} (data version '
                              f'{info.data_version}): Painter {target.name} opens it already')
    if info.data_version > NEWEST_DATA_VERSION:
        msg = (f'project data version {info.data_version} is newer than this tool was built for '
               f'({NEWEST_DATA_VERSION}, Painter 12.2).')
        if not force:
            raise ConversionError(msg + ' Use --force to try anyway.')
        report.warn(msg)
    report.info.append(f'saved with Painter {info.saved_label()} (data version {info.data_version})')
    if target.note():
        report.info.append(target.note())
    with h5py.File(src, 'r') as f:
        replaced = {}            # path -> new bytes
        renamed = {}             # path -> new path (content unchanged)
        removed = set()
        cache = plan_texture_cache(f, drop_cache, report)
        if cache:
            header, plan = cache
            replaced['texture-cache/header'] = header
            for p, t in plan.items():
                if t is None:
                    removed.add(p)
                elif isinstance(t, tuple):
                    replaced[p] = t[1]
                elif t != p:
                    renamed[p] = t

        def scan(name, obj):
            if not isinstance(obj, h5py.Dataset) or name.startswith('texture-cache/'):
                return
            if name == 'projectsettings.ini':
                replaced[name] = convert_projectsettings(bytes(obj[()]), report)
                return
            if obj.dtype != np.uint8 or obj.ndim != 1 or obj.shape[0] < 20:
                return
            if bytes(obj[:4]) != sf.MAGIC:
                return
            base = name.rpartition('/')[2]
            if name.startswith(('editor/', 'baking/')):
                out = convert_aux(bytes(obj[()]), base, report)
            elif name.startswith('paint/') and sf.data_version(bytes(obj[:20])) >= 60:
                # Layer-stack documents share the project data version.
                out = convert_document(bytes(obj[()]), report, name)
            else:
                return
            if out is not None:
                replaced[name] = out
        f.visititems(scan)
        if report.aux_left:
            report.info.append(f'viewer and baking settings are left as saved; Painter {target.name} '
                               f'falls back to its defaults for the ones it cannot read')

        out_dir = os.path.dirname(os.path.abspath(dst))
        fd, tmp = tempfile.mkstemp(suffix='.spp.tmp', dir=out_dir)
        os.close(fd)
        try:
            with h5py.File(tmp, 'w', libver=('earliest', 'v114')) as g:
                for k in f.attrs:
                    g.attrs[k] = f.attrs[k]

                cache_dropped = bool(cache) and all(t is None for t in cache[1].values())

                def copy(name, obj):
                    if isinstance(obj, h5py.Group):
                        if cache_dropped and name.startswith('texture-cache/'):
                            return
                        grp = g.require_group(name)
                        for k in obj.attrs:
                            grp.attrs[k] = obj.attrs[k]
                        return
                    if name in removed:
                        return
                    if name in replaced:
                        _put(g, name, replaced[name])
                        return
                    dest = renamed.get(name, name)
                    parent, _, leaf = dest.rpartition('/')
                    f.copy(obj, g.require_group(parent) if parent else g, name=leaf)
                f.visititems(copy)
                if 'texture-cache/header' in replaced and 'texture-cache/header' not in g:
                    _put(g, 'texture-cache/header', replaced['texture-cache/header'])
            umask = os.umask(0)
            os.umask(umask)
            os.chmod(tmp, 0o666 & ~umask)
            os.replace(tmp, dst)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
    # Final verification of the written file.
    with h5py.File(dst, 'r') as g:
        for name in ('paint/document.bin', 'paint/default_material.bin'):
            if name in g:
                b = bytes(g[name][()])
                sf.parse(b)
                if sf.data_version(b) > target.profile.doc:
                    raise ConversionError(f'{name} still has a newer data version')
        bad = []

        def verify(name, obj):
            if isinstance(obj, h5py.Dataset) and 'm3_x64_128' in obj.attrs and name in replaced:
                if list(obj.attrs['m3_x64_128']) != list(sf.content_hash(bytes(obj[()]))):
                    bad.append(name)
        g.visititems(verify)
        if bad:
            raise ConversionError(f'checksum verification failed for {bad}')
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def default_output(src, target):
    stem, ext = os.path.splitext(src)
    return f'{stem}_{target.label}{ext or ".spp"}'


def print_report(report, src, dst, out=sys.stdout):
    print(f'Converted: {src}\n       -> {dst}', file=out)
    for line in report.info:
        print(f'  - {line}', file=out)
    if report.dropped:
        seen = Counter()
        for key, n in report.dropped.items():
            seen[DROPPED_DESCRIPTIONS.get(key, key)] += n
        print(f'  Settings that Painter {report.target.name} cannot store (ignored):', file=out)
        for desc, n in sorted(seen.items()):
            print(f'      {desc} ({n}x)', file=out)
    for w in report.warnings:
        print(f'  ! {w}', file=out)


def cmd_launch(path, target, drop_cache):
    """For the desktop launcher: print the file Painter should open."""
    try:
        if ProjectInfo(path).opens_in(target):
            print(path)
            return 0
    except Exception:
        print(path)
        return 0
    dst = default_output(path, target)
    if not os.access(os.path.dirname(os.path.abspath(path)), os.W_OK):
        cache_dir = os.path.join(os.environ.get('XDG_CACHE_HOME', os.path.expanduser('~/.cache')),
                                 'spp-downgrade')
        os.makedirs(cache_dir, exist_ok=True)
        dst = os.path.join(cache_dir, os.path.basename(dst))
    if os.path.exists(dst) and os.path.getmtime(dst) >= os.path.getmtime(path):
        print(dst)
        return 0
    report = convert(path, dst, target, drop_cache=drop_cache)
    print_report(report, path, dst, out=sys.stderr)
    print(dst)
    return 0


def main(argv=None):
    releases = ', '.join(f'{a}.{b}' for a, b in RELEASES)
    ap = argparse.ArgumentParser(
        prog='substance-version-tool',
        description='Make a Substance 3D Painter project open in an older Painter release. '
                    'Writes a new file; the original is left untouched.',
        epilog=f'Supported releases: {releases}. A newer Painter opens older projects by '
               f'itself, so only conversions to an older release exist.')
    ap.add_argument('files', nargs='+', metavar='FILE.spp')
    ap.add_argument('-t', '--to', metavar='VERSION',
                    help=f'Painter version to convert for, e.g. 11.0 or 12.0.1 '
                         f'(default: $SPP_TARGET_VERSION, else {DEFAULT_TARGET})')
    ap.add_argument('-o', '--output', help='output file (only with a single input). '
                    'Default: <name>_<version>.spp next to the input')
    ap.add_argument('--drop-cache', action='store_true',
                    help='do not carry over the texture cache (Painter recomputes it on open)')
    ap.add_argument('--force', action='store_true',
                    help='try projects newer than 12.2 too')
    ap.add_argument('--data-version', type=int, metavar='N',
                    help=f'advanced: write this document data version ({OLDEST_DATA_VERSION}-'
                         f'{schemas.DOC_BASE_VERSION}) instead of the one chosen for the target')
    ap.add_argument('--check', action='store_true',
                    help='only report which Painter saved each file and whether it needs converting')
    ap.add_argument('--launch', action='store_true', help=argparse.SUPPRESS)
    ap.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    a = ap.parse_args(argv)

    try:
        target = Target(a.to, a.data_version) if a.to else default_target(a.data_version)
    except ConversionError as e:
        ap.error(str(e))
    if a.launch:
        return cmd_launch(a.files[0], target, a.drop_cache)
    if a.output and len(a.files) != 1:
        ap.error('--output needs exactly one input file')
    status = 0
    for src in a.files:
        try:
            if a.check:
                info = ProjectInfo(src)
                ok = 'opens as it is' if info.opens_in(target) else 'needs conversion'
                print(f'{src}: saved with Painter {info.saved_label()}, data version '
                      f'{info.data_version} - {ok} for Painter {target.label}')
                continue
            dst = a.output or default_output(src, target)
            report = convert(src, dst, target, drop_cache=a.drop_cache, force=a.force)
            print_report(report, src, dst)
        except (ConversionError, sf.FormatError, OSError) as e:
            print(f'{src}: ERROR: {e}', file=sys.stderr)
            status = 1
    return status


if __name__ == '__main__':
    sys.exit(main())
