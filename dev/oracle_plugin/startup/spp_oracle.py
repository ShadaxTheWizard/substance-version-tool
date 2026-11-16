"""Probe server: opens the project named in queue/req.json and reports whether it loaded."""
import json, os, traceback
from PySide6 import QtCore
import substance_painter.project as P

Q = os.environ['SPP_EXP_QUEUE']
LOG = os.path.expanduser('~/.local/share/Adobe/Adobe Substance 3D Painter/log.txt')
st = {'phase': 'idle', 'ticks': 0, 'logpos': 0, 'req': None}
timer = None

def logdelta():
    try:
        with open(LOG, errors='replace') as f:
            f.seek(st['logpos'])
            txt = f.read()
            st['logpos'] = f.tell()
        return [l[:300] for l in txt.splitlines() if 'Paint Document>' in l or 'TRANSLATION' in l or 'Baking Document' in l or 'iewer settings' in l or 'more recent than' in l or ('ERR' in l and ('Post' in l or 'Iray' in l or 'ettings' in l))]
    except Exception as e:
        return [repr(e)]

def tree(nodes, depth=0):
    out = []
    for n in nodes:
        try:
            t = n.get_type().name
        except Exception:
            t = '?'
        try:
            vis = n.is_visible()
        except Exception:
            vis = None
        out.append([depth, t, n.get_name(), vis])
        for attr in ('sub_layers', 'content_effects', 'mask_effects'):
            try:
                out += tree(getattr(n, attr)(), depth + 1)
            except Exception:
                pass
    return out


def describe():
    import substance_painter.textureset as TS
    import substance_painter.layerstack as LS
    d = {}
    for ts in TS.all_texture_sets():
        for stack in ts.all_stacks():
            key = f'{ts.name()}/{stack.name()}'
            try:
                d[key] = {'res': str(ts.get_resolution()),
                          'channels': sorted(str(c) for c in stack.all_channels()),
                          'nodes': tree(LS.get_root_layer_nodes(stack))}
            except Exception as e:
                d[key] = repr(e)
    return d


def reply(ok, lines):
    r = {'id': st['req']['id'], 'ok': ok, 'log': lines}
    if ok and st['req'].get('describe'):
        try:
            r['desc'] = describe()
        except Exception:
            r['desc'] = traceback.format_exc()
    tmp = os.path.join(Q, 'res.tmp')
    with open(tmp, 'w') as f:
        json.dump(r, f)
    os.replace(tmp, os.path.join(Q, 'res.json'))
    st['req'] = None
    st['phase'] = 'closing'
    st['ticks'] = 0

def tick():
    try:
        step()
    except Exception:
        with open(os.path.join(Q, 'fatal.txt'), 'a') as f:
            f.write(traceback.format_exc())

def step():
    st['ticks'] += 1
    ph = st['phase']
    if ph == 'closing':
        if P.is_open():
            if not P.is_busy():
                try:
                    P.close()
                except Exception:
                    pass
            return
        st['phase'] = 'idle'
    elif ph == 'idle':
        rp = os.path.join(Q, 'req.json')
        if not os.path.exists(rp):
            return
        with open(rp) as f:
            st['req'] = json.load(f)
        os.unlink(rp)
        if st['req'].get('quit'):
            os._exit(0)
        try:
            st['logpos'] = os.path.getsize(LOG)
        except OSError:
            pass
        st['acc'] = []
        try:
            P.open(st['req']['path'])
        except Exception as e:
            reply(False, [repr(e)] + logdelta())
            return
        st['phase'] = 'wait'
        st['ticks'] = 0
    elif ph == 'wait':
        st['acc'] += logdelta()
        err = [l for l in st['acc'] if 'ERR' in l and 'Paint Document' in l]
        if err or (not P.is_open() and st['ticks'] > 25):
            reply(False, st['acc'])
        elif P.is_open() and P.is_in_edition_state() and not P.is_busy():
            st['acc'] += logdelta()
            err = [l for l in st['acc'] if 'ERR' in l and 'Paint Document' in l]
            reply(not err, st['acc'])
        elif st['ticks'] > 600:
            reply(False, st['acc'] + ['TIMEOUT'])

def start_plugin():
    global timer
    timer = QtCore.QTimer()
    timer.setInterval(200)
    timer.timeout.connect(tick)
    timer.start()
    open(os.path.join(Q, 'ready'), 'w').close()

def close_plugin():
    global timer
    if timer is not None:
        timer.stop(); timer = None

if __name__ == '__main__':
    start_plugin()
