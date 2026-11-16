"""Readers/writers for the binary formats found inside Substance 3D Painter .spp files.

An .spp file is an HDF5 container. Every dataset is a 1-D uint8 blob carrying an
``m3_x64_128`` attribute (MurmurHash3_x64_128 of the blob, see ``content_hash``).
Two blob formats matter for down-conversion:

* The "Data" serializer (paint/document.bin, paint/default_material.bin, ...):
  little-endian, self-describing.  Header = magic, 1, data_version, 18, class_count,
  followed by the root object.  An object is ``u32 end_offset`` (absolute, 0 = null),
  a class reference (``0xffffffff`` + inline definition, or index of an earlier
  definition) and then each field's value in definition order.

* Qt ``QDataStream`` QVariantMaps (projectsettings.ini, texture-cache/header, ...):
  big-endian.
"""
import struct

import mmh3
import numpy as np

# ---------------------------------------------------------------------------
# Hash used for the per-dataset m3_x64_128 attribute
# ---------------------------------------------------------------------------
HASH_SEED = 0xF13A0239


def content_hash(data: bytes) -> np.ndarray:
    h = mmh3.hash128(data, seed=HASH_SEED, x64arch=True, signed=False)
    return np.array([h & 0xFFFFFFFFFFFFFFFF, h >> 64], dtype='>u8')


# ---------------------------------------------------------------------------
# "Data" serializer
# ---------------------------------------------------------------------------
MAGIC = b'\xdd\x2f\x7c\x1b'

# Field type ids -> fixed byte size (others are variable / composite).
#  1 float  2 vec2  3 vec3  4 vec4  5 int  6 ivec2  9 enum  10 bool  11 uint32
# 12 uint64  13 mat3  14 mat4  15 selection id  21 uint64 one-hot channel (<=12.0)
# 22 uint128 channel mask (12.2+)
FIXED = {1: 4, 2: 8, 3: 12, 4: 16, 5: 4, 6: 8, 9: 4, 10: 1, 11: 4, 12: 8,
         13: 36, 14: 64, 15: 8, 21: 8, 22: 16}
STRING, ARRAY, OBJECT, OBJLIST, EMBED = 16, 17, 18, 19, 20


class FormatError(Exception):
    pass


class Klass:
    __slots__ = ('name', 'fields')

    def __init__(self, name, fields):
        self.name = name
        self.fields = list(fields)        # [(field_name, type_id), ...]

    def __repr__(self):
        return f'Klass({self.name})'


class Obj:
    __slots__ = ('klass', 'values')

    def __init__(self, klass, values):
        self.klass = klass
        self.values = values              # {field_name: value}

    def __repr__(self):
        return f'<{self.klass.name}>'


class Arr:
    """Type 17: homogeneous inline struct array (one class ref, no per-item offsets)."""
    __slots__ = ('klass', 'items')

    def __init__(self, klass, items):
        self.klass = klass
        self.items = items                # [{field_name: value}, ...]


class _Reader:
    def __init__(self, b):
        self.b, self.o, self.classes = b, 0, []

    def u32(self):
        if self.o + 4 > len(self.b):
            raise FormatError(f'unexpected end of data at {self.o}')
        v, = struct.unpack_from('<I', self.b, self.o)
        self.o += 4
        return v

    def raw(self, n):
        if self.o + n > len(self.b):
            raise FormatError(f'unexpected end of data reading {n} bytes at {self.o}')
        v = self.b[self.o:self.o + n]
        self.o += n
        return v

    def classref(self):
        idx = self.u32()
        if idx != 0xFFFFFFFF:
            if idx >= len(self.classes):
                raise FormatError(f'bad class index {idx} at {self.o - 4}')
            return self.classes[idx]
        name = self.raw(self.u32()).decode('ascii')
        n = self.u32()
        if n > 1000:
            raise FormatError(f'implausible field count {n} for {name}')
        fields = []
        for _ in range(n):
            fname = self.raw(self.u32()).decode('ascii')
            fields.append((fname, self.u32()))
        k = Klass(name, fields)
        self.classes.append(k)
        return k

    def fields(self, k):
        return {fn: self.value(ft, k.name, fn) for fn, ft in k.fields}

    def obj(self):
        start = self.o
        end = self.u32()
        if end == 0:
            return None
        k = self.classref()
        vals = self.fields(k)
        if self.o != end:
            raise FormatError(f'{k.name} at {start}: ended at {self.o}, header says {end}')
        return Obj(k, vals)

    def value(self, t, cname, fname):
        if t in FIXED:
            return self.raw(FIXED[t])
        if t == STRING:
            return self.raw(self.u32())
        if t in (OBJECT, EMBED):
            return self.obj()
        if t == OBJLIST:
            return [self.obj() for _ in range(self.u32())]
        if t == ARRAY:
            n = self.u32()
            if n == 0:
                return Arr(None, [])
            k = self.classref()
            return Arr(k, [self.fields(k) for _ in range(n)])
        raise FormatError(f'unknown field type {t} ({cname}.{fname}) at {self.o}')


def data_version(blob: bytes):
    """Return the serializer data version of a Data blob, or None if not one."""
    if len(blob) < 20 or blob[:4] != MAGIC:
        return None
    return struct.unpack_from('<I', blob, 8)[0]


def parse(blob: bytes):
    """Parse a Data blob -> (header [4 ints], root Obj)."""
    if blob[:4] != MAGIC:
        raise FormatError('not a Painter data blob')
    r = _Reader(blob)
    r.o = 4
    hdr = [r.u32() for _ in range(4)]
    root = r.obj()
    if r.o != len(blob):
        raise FormatError(f'trailing data: parsed {r.o} of {len(blob)} bytes')
    if hdr[3] != len(r.classes):
        raise FormatError(f'header class count {hdr[3]} != {len(r.classes)} defined')
    return hdr, root


class _Writer:
    def __init__(self):
        self.buf = bytearray()
        self.index = {}

    def u32(self, v):
        self.buf += struct.pack('<I', v)

    def classref(self, k):
        if id(k) in self.index:
            self.u32(self.index[id(k)])
            return
        self.index[id(k)] = len(self.index)
        self.u32(0xFFFFFFFF)
        n = k.name.encode('ascii')
        self.u32(len(n))
        self.buf += n
        self.u32(len(k.fields))
        for fn, ft in k.fields:
            e = fn.encode('ascii')
            self.u32(len(e))
            self.buf += e
            self.u32(ft)

    def fields(self, k, vals):
        for fn, ft in k.fields:
            self.value(ft, vals[fn])

    def obj(self, o):
        if o is None:
            self.u32(0)
            return
        pos = len(self.buf)
        self.u32(0)
        self.classref(o.klass)
        self.fields(o.klass, o.values)
        struct.pack_into('<I', self.buf, pos, len(self.buf))

    def value(self, t, v):
        if t in FIXED:
            if len(v) != FIXED[t]:
                raise FormatError(f'type {t} value has {len(v)} bytes, expected {FIXED[t]}')
            self.buf += v
        elif t == STRING:
            self.u32(len(v))
            self.buf += v
        elif t in (OBJECT, EMBED):
            self.obj(v)
        elif t == OBJLIST:
            self.u32(len(v))
            for o in v:
                self.obj(o)
        elif t == ARRAY:
            self.u32(len(v.items))
            if v.items:
                self.classref(v.klass)
                for it in v.items:
                    self.fields(v.klass, it)
        else:
            raise FormatError(f'cannot write field type {t}')


def write(hdr, root) -> bytes:
    w = _Writer()
    w.buf += MAGIC
    for h in hdr:
        w.u32(h)
    w.obj(root)
    struct.pack_into('<I', w.buf, 16, len(w.index))   # class count
    return bytes(w.buf)


def walk(v, cb):
    """Call cb(obj) for every Obj reachable from v."""
    if isinstance(v, Obj):
        cb(v)
        for fn, _ in v.klass.fields:
            walk(v.values[fn], cb)
    elif isinstance(v, list):
        for x in v:
            walk(x, cb)
    elif isinstance(v, Arr):
        for it in v.items:
            for fn, _ in v.klass.fields:
                walk(it[fn], cb)


def walk_classes(v, cb):
    """Call cb(klass) for the class of every object and struct array reachable from v."""
    if isinstance(v, Obj):
        cb(v.klass)
        for fn, _ in v.klass.fields:
            walk_classes(v.values[fn], cb)
    elif isinstance(v, list):
        for x in v:
            walk_classes(x, cb)
    elif isinstance(v, Arr) and v.items:
        cb(v.klass)
        for it in v.items:
            for fn, _ in v.klass.fields:
                walk_classes(it[fn], cb)


# ---------------------------------------------------------------------------
# QDataStream QVariantMap (big-endian, Qt 6 stream format)
# ---------------------------------------------------------------------------
_QFIXED = {1: '>?', 2: '>i', 3: '>I', 4: '>q', 5: '>Q', 6: '>d'}
Q_MAP, Q_LIST, Q_STRING, Q_STRINGLIST, Q_BYTEARRAY, Q_UUID = 8, 9, 10, 11, 12, 30


class QStream:
    def __init__(self, b, o=0):
        self.b, self.o = b, o

    def u32(self):
        v, = struct.unpack_from('>I', self.b, self.o)
        self.o += 4
        return v

    def qstring(self):
        n = self.u32()
        if n == 0xFFFFFFFF:
            return None
        s = self.b[self.o:self.o + n].decode('utf-16-be')
        self.o += n
        return s

    def qbytes(self, n):
        v = self.b[self.o:self.o + n]
        self.o += n
        return v

    def variant(self):
        t = self.u32()
        is_null = self.b[self.o]
        self.o += 1
        if t in _QFIXED:
            fmt = _QFIXED[t]
            v, = struct.unpack_from(fmt, self.b, self.o)
            self.o += struct.calcsize(fmt)
        elif t == Q_STRING:
            v = self.qstring()
        elif t == Q_BYTEARRAY:
            n = self.u32()
            v = None if n == 0xFFFFFFFF else self.qbytes(n)
        elif t == Q_UUID:
            v = self.qbytes(16)
        elif t == Q_MAP:
            v = self.variant_map()
        elif t == Q_LIST:
            v = [self.variant() for _ in range(self.u32())]
        elif t == Q_STRINGLIST:
            v = [self.qstring() for _ in range(self.u32())]
        else:
            raise FormatError(f'unsupported QVariant type {t}')
        return (t, is_null, v)

    def variant_map(self):
        return [(self.qstring(), self.variant()) for _ in range(self.u32())]


def qstring_bytes(s):
    if s is None:
        return struct.pack('>I', 0xFFFFFFFF)
    e = s.encode('utf-16-be')
    return struct.pack('>I', len(e)) + e


def qvariant_bytes(var):
    t, is_null, v = var
    out = struct.pack('>IB', t, is_null)
    if t in _QFIXED:
        return out + struct.pack(_QFIXED[t], v)
    if t == Q_STRING:
        return out + qstring_bytes(v)
    if t == Q_BYTEARRAY:
        return out + (struct.pack('>I', 0xFFFFFFFF) if v is None else struct.pack('>I', len(v)) + v)
    if t == Q_UUID:
        return out + v
    if t == Q_MAP:
        return out + variant_map_bytes(v)
    if t == Q_LIST:
        return out + struct.pack('>I', len(v)) + b''.join(qvariant_bytes(x) for x in v)
    if t == Q_STRINGLIST:
        return out + struct.pack('>I', len(v)) + b''.join(qstring_bytes(x) for x in v)
    raise FormatError(f'unsupported QVariant type {t}')


def variant_map_bytes(entries):
    out = bytearray(struct.pack('>I', len(entries)))
    for k, var in entries:
        out += qstring_bytes(k) + qvariant_bytes(var)
    return bytes(out)
