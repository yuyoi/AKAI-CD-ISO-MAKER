"""Audio import (wav / mp3 / flac / ogg / aiff / anything ffmpeg reads), resampling, root-note detection,
loop finding and crossfaded loops."""
import math
import os
import re
import shutil
import struct
import subprocess
import tempfile

import numpy as np

from .akai import LM_LOOP_HOLD, LM_NOLOOP, Loop, Sample, clean_name

AUDIO_EXT = ('.wav', '.mp3', '.flac', '.ogg', '.oga', '.aif', '.aiff', '.aifc', '.m4a', '.aac', '.wma', '.opus')
NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
_NOTE_RE = re.compile(r'(?<![A-Za-z])([A-Ga-g])([#bB]?)(-?\d)(?![0-9])')


def note_name(n):
    """MIDI note -> name, C4 = 60"""
    return '%s%d' % (NOTE_NAMES[n % 12], n // 12 - 1)


def parse_note(s):
    """note name in a file name ('Piano_C#3', 'a4 sus') -> MIDI note (C4 = 60) or None"""
    m = _NOTE_RE.search(s)
    if not m:
        return None
    base = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}[m.group(1).upper()]
    acc = {'#': 1, 'b': -1, 'B': -1, '': 0}[m.group(2)]
    n = 12 * (int(m.group(3)) + 1) + base + acc
    return n if 0 <= n <= 127 else None


# ---------------------------------------------------------------- loading
def _wav_smpl(path):
    """read the 'smpl' chunk of a RIFF/WAVE file -> dict(root, cents, loops=[(start, end_exclusive)]) or {}"""
    try:
        with open(path, 'rb') as f:
            d = f.read(1 << 22) if os.path.getsize(path) < (1 << 22) else None
            if d is None:
                f.seek(0)
                head = f.read(12)
                if head[:4] != b'RIFF':
                    return {}
                out = {}
                while True:
                    h = f.read(8)
                    if len(h) < 8:
                        break
                    cid, sz = struct.unpack('<4sI', h)
                    if cid == b'smpl':
                        return _parse_smpl(f.read(sz))
                    f.seek(sz + (sz & 1), 1)
                return out
        if d[:4] != b'RIFF' or d[8:12] != b'WAVE':
            return {}
        i = 12
        while i + 8 <= len(d):
            cid, sz = struct.unpack('<4sI', d[i:i + 8])
            if cid == b'smpl':
                return _parse_smpl(d[i + 8:i + 8 + sz])
            i += 8 + sz + (sz & 1)
    except OSError:
        pass
    return {}


def _parse_smpl(b):
    if len(b) < 36:
        return {}
    _, _, _, root, frac, _, _, nloops, _ = struct.unpack('<9I', b[:36])
    loops = []
    for k in range(min(nloops, 8)):
        o = 36 + 24 * k
        if len(b) < o + 24:
            break
        _, _, s, e, _, _ = struct.unpack('<6I', b[o:o + 24])
        loops.append((s, e + 1))
    cents = (frac / 4294967296.0) * 100.0
    if cents > 50:
        cents -= 100
    return dict(root=root if 0 < root < 128 else None, cents=cents, loops=loops)


def load_audio(path):
    """-> (float32 array (n, ch), rate, meta)  meta: root / cents / loops from the file, if any"""
    import soundfile as sf
    meta = _wav_smpl(path) if path.lower().endswith('.wav') else {}
    try:
        data, rate = sf.read(path, dtype='float32', always_2d=True)
    except Exception:
        ff = shutil.which('ffmpeg')
        if not ff:
            raise ValueError('cannot decode %s (install ffmpeg for this format)' % os.path.basename(path))
        tmp = os.path.join(tempfile.mkdtemp(), 'x.wav')
        r = subprocess.run([ff, '-y', '-v', 'error', '-i', path, '-c:a', 'pcm_f32le', tmp],
                           capture_output=True)
        if r.returncode:
            raise ValueError('ffmpeg could not decode %s: %s' % (os.path.basename(path), r.stderr.decode(errors='replace')[:200]))
        data, rate = sf.read(tmp, dtype='float32', always_2d=True)
        shutil.rmtree(os.path.dirname(tmp), ignore_errors=True)
    return data, int(rate), meta


def resample(x, r_from, r_to):
    if r_from == r_to or len(x) == 0:
        return x
    from math import gcd
    from scipy.signal import resample_poly
    g = gcd(int(r_from), int(r_to))
    return resample_poly(x, int(r_to) // g, int(r_from) // g, axis=0).astype(np.float32)


def to_int16(x, normalize=False, headroom_db=1.0):
    x = np.asarray(x, np.float32)
    if normalize:
        pk = float(np.max(np.abs(x))) if len(x) else 0
        if pk > 1e-6:
            x = x * (10 ** (-headroom_db / 20.0) / pk)
    return np.clip(np.round(x * 32767.0), -32768, 32767).astype(np.int16)


# ---------------------------------------------------------------- pitch / loops
def detect_pitch(x, rate):
    """rough monophonic pitch: -> (midi_float, confidence 0..1) or (None, 0)"""
    x = np.asarray(x, np.float32)
    if x.ndim > 1:
        x = x.mean(axis=1)
    n = len(x)
    if n < 2048:
        return None, 0.0
    seg_len = min(n, 16384)
    # use the loudest part of the first 2 s
    lim = min(n, int(rate * 2))
    env = np.convolve(np.abs(x[:lim]), np.ones(1024) / 1024, 'same')
    c = int(np.argmax(env))
    a = max(0, min(c - seg_len // 2, n - seg_len))
    s = x[a:a + seg_len] * np.hanning(seg_len)
    f = np.fft.rfft(s, 2 * seg_len)
    ac = np.fft.irfft(f * np.conj(f))[:seg_len]
    if ac[0] <= 1e-9:
        return None, 0.0
    ac = ac / ac[0]
    lo, hi = int(rate / 2000), min(int(rate / 30), seg_len - 2)
    if hi <= lo + 2:
        return None, 0.0
    # first strong peak after the first zero crossing region
    seg = ac[lo:hi]
    peaks = np.where((seg[1:-1] > seg[:-2]) & (seg[1:-1] >= seg[2:]) & (seg[1:-1] > 0.3))[0] + 1
    if len(peaks) == 0:
        return None, 0.0
    best = peaks[np.argmax(seg[peaks] > 0.9 * seg[peaks].max())]
    k = best + lo
    y0, y1, y2 = ac[k - 1], ac[k], ac[k + 1]
    den = y0 - 2 * y1 + y2
    off = 0.5 * (y0 - y2) / den if abs(den) > 1e-12 else 0.0
    f0 = rate / (k + off)
    return 69 + 12 * math.log2(f0 / 440.0), float(ac[k])


def _zero_cross_up(x, a, b):
    """indices in [a, b) where x crosses zero going up"""
    seg = x[a:b]
    idx = np.where((seg[:-1] <= 0) & (seg[1:] > 0))[0] + a
    return idx


def find_loop(pcm, rate, min_seconds=0.15, window=2048):
    """Search a seamless sustain loop in the second half of the sample.
    -> (Loop, score 0..1) or (None, 0). Picks the loop end whose surrounding waveform best matches the loop start."""
    x = np.asarray(pcm, np.float32)
    n = len(x)
    if n < rate * 0.4:
        return None, 0.0
    x = x / (np.max(np.abs(x)) + 1e-9)
    W = min(window, n // 8)
    min_len = int(rate * min_seconds)
    a0, a1 = int(n * 0.30), int(n * 0.55)
    best = (0.0, None)
    # candidate starts: a few rising zero crossings
    zs = _zero_cross_up(x, a0, a1)
    if len(zs) == 0:
        return None, 0.0
    starts = zs[np.linspace(0, len(zs) - 1, min(len(zs), 12)).astype(int)]
    e0, e1 = max(a1, int(n * 0.6)), n - W - 2
    if e1 <= e0:
        return None, 0.0
    ze = _zero_cross_up(x, e0, e1)
    if len(ze) == 0:
        return None, 0.0
    # pre-compute window energy for ends
    for s in starts:
        ref = x[s:s + W]
        rn = np.linalg.norm(ref) + 1e-9
        # candidate ends restricted to enough distance
        ends = ze[ze - s >= min_len]
        if len(ends) == 0:
            continue
        if len(ends) > 600:
            ends = ends[np.linspace(0, len(ends) - 1, 600).astype(int)]
        idx = ends[:, None] + np.arange(W)[None, :]
        wins = x[idx]
        sc = (wins @ ref) / (np.linalg.norm(wins, axis=1) * rn + 1e-9)
        k = int(np.argmax(sc))
        if sc[k] > best[0]:
            best = (float(sc[k]), (int(s), int(ends[k])))
    if best[1] is None:
        return None, 0.0
    s, e = best[1]
    return Loop(s, e, 9999), best[0]


def crossfade_loop(pcm, loop, xf):
    """Blend the audio before the loop start into the tail before the loop end so the wrap is seamless.
    Returns a new int16 array (the loop points stay the same)."""
    x = pcm.astype(np.float32).copy()
    s, e = loop.start, loop.end
    L = int(min(xf, s, (e - s) // 2))
    if L < 8:
        return pcm
    t = np.linspace(0, math.pi / 2, L, endpoint=False)
    fo, fi = np.cos(t), np.sin(t)
    x[e - L:e] = x[e - L:e] * fo + x[s - L:s] * fi
    return np.clip(np.round(x), -32768, 32767).astype(np.int16)


def wrap_discontinuity(pcm, loop):
    """0 = perfect, 1 = full-scale jump at the loop wrap"""
    x = pcm.astype(np.float32) / 32768.0
    a = x[loop.end - 1] if loop.end - 1 < len(x) else 0.0
    b = x[loop.start]
    return float(abs(a - b))


# ---------------------------------------------------------------- build Samples
def make_samples(path, name=None, target_rate=44100, stereo='split', normalize=False, root=None,
                 detect=True, keep_loops=True):
    """audio file -> [Sample] (1 for mono / mixed, 2 for a split stereo pair named ...L / ...R).
    target_rate: 44100 or 22050 (the two playback rates the samplers have)."""
    data, rate, meta = load_audio(path)
    base = name or os.path.splitext(os.path.basename(path))[0]
    ch = data.shape[1]
    r_note = root if root is not None else meta.get('root') or parse_note(base)
    if r_note is None and detect:
        m, conf = detect_pitch(data.mean(axis=1), rate)
        if m is not None and conf > 0.6:
            r_note = int(round(m))
    if r_note is None:
        r_note = 60
    chans = []
    if ch == 1:
        chans = [(data[:, 0], '')]
    elif stereo == 'split':
        chans = [(data[:, 0], ' L'), (data[:, 1], ' R')]
    else:
        chans = [(data.mean(axis=1), '')]
    out = []
    ratio = target_rate / float(rate)
    for x, suffix in chans:
        y = resample(x.reshape(-1, 1), rate, target_rate)[:, 0]
        pcm = to_int16(y, normalize)
        nm = clean_name(base, 12 - len(suffix)) + suffix if suffix else clean_name(base)
        s = Sample(nm, pcm, rate=target_rate, root=int(min(127, max(24, r_note))),
                   bandwidth=1 if target_rate >= 32000 else 0, source=path)
        if keep_loops and meta.get('loops'):
            ls, le = meta['loops'][0]
            ls, le = int(ls * ratio), int(le * ratio)
            if 0 <= ls < le <= len(pcm):
                s.loops = [Loop(ls, le, 9999)]
                s.loop_mode = LM_LOOP_HOLD
        out.append(s)
    return out
