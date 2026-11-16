"""Drive a real Painter as a format oracle: ask it to open a project, learn whether it loaded.

Painter is started once with the probe plugin (oracle_plugin/startup/spp_oracle.py) and is
then fed one project after another through a small request/reply directory.  A crash or
hang counts as its own result and Painter is restarted for the next probe.

    PAINTER_DIR=/path/to/Painter  (default: ~/Documents/Substance 3D Painter 2026)
"""
import json
import os
import subprocess
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PAINTER_DIR = os.environ.get('PAINTER_DIR', os.path.expanduser('~/Documents/Substance 3D Painter 2026'))


class Oracle:
    def __init__(self, logfile=None):
        self.proc = None
        self.n = 0
        self.crashes = 0
        self.last = {}
        self.queue = tempfile.mkdtemp(prefix='spp-oracle-')
        self.logf = open(logfile, 'a') if logfile else None

    def log(self, *a):
        if self.logf:
            print(*a, file=self.logf, flush=True)

    def start(self):
        for f in os.listdir(self.queue):
            os.unlink(os.path.join(self.queue, f))
        env = dict(os.environ, SPP_EXP_QUEUE=self.queue,
                   SUBSTANCE_PAINTER_PLUGINS_PATH=os.path.join(HERE, 'oracle_plugin'))
        self.proc = subprocess.Popen(
            ['./Adobe Substance 3D Painter', '--disable-version-checking', '--disable-modal-popup'],
            cwd=PAINTER_DIR, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        t = time.time()
        while not os.path.exists(os.path.join(self.queue, 'ready')):
            if self.proc.poll() is not None:
                raise RuntimeError('Painter exited during startup')
            if time.time() - t > 180:
                self.proc.kill()
                raise RuntimeError('Painter startup timeout')
            time.sleep(0.5)
        time.sleep(3)

    def stop(self):
        if self.proc and self.proc.poll() is None:
            with open(os.path.join(self.queue, 'req.json'), 'w') as f:
                json.dump({'quit': True}, f)
            try:
                self.proc.wait(20)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        # Painter's crash reporter stays open after a crash.
        subprocess.run(['pkill', '-f', 'AdobeCrashReportWindow'], capture_output=True)
        self.proc = None

    def test(self, path, timeout=150, describe=False):
        """Open a project. -> (ok, log lines); ok is True, False (rejected) or None (crash/hang).
        With describe=True, self.last['desc'] holds the layer tree of a project that loaded."""
        if self.proc is None or self.proc.poll() is not None:
            self.stop()
            self.start()
        self.n += 1
        rp = os.path.join(self.queue, 'res.json')
        if os.path.exists(rp):
            os.unlink(rp)
        tmp = os.path.join(self.queue, 'req.tmp')
        with open(tmp, 'w') as f:
            json.dump({'id': self.n, 'path': os.path.abspath(path), 'describe': describe}, f)
        os.replace(tmp, os.path.join(self.queue, 'req.json'))
        t = time.time()
        while True:
            if os.path.exists(rp):
                try:
                    r = json.load(open(rp))
                except ValueError:
                    time.sleep(0.05)
                    continue
                if r['id'] == self.n:
                    self.last = r
                    self.log('TEST', os.path.basename(path), r['ok'], [l for l in r['log'] if 'ERR' in l][:3])
                    return r['ok'], r['log']
            if self.proc.poll() is not None:
                self.crashes += 1
                self.log('TEST', os.path.basename(path), 'CRASH')
                self.stop()
                return None, ['CRASH']
            if time.time() - t > timeout:
                self.log('TEST', os.path.basename(path), 'HANG')
                self.proc.kill()
                self.stop()
                return None, ['HANG']
            time.sleep(0.1)
