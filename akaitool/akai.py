"""Akai S900/S1000/S2000/S3000 sampler CD / disk image: data model, reader and parsers.

Disk layout (reverse-engineered from real CD-ROM images, cross-checked with the public akaitools
S3000 format notes):
  A CD image is a run of Akai *partitions*, 60 MB apart (0x3C00000). The last one may be shorter.
  partition: 0x0000  u16 cluster count (0x1E00 for a full 60 MB one), then a 0xCA-byte header
             0x00CA  volume list, 100 x 16 B: name[12] type u16 (1 S1000, 3 S3000) start-cluster u16
             0x070A  FAT, one u16 per cluster: 0 free, 0x4000 system, 0x8000 2nd dir cluster (S3000),
                     0xC000 end of chain, else next cluster
  cluster size 0x2000, clusters 0-3 are system, cluster 3 = volume 1 directory.
  directory: 126 x 24 B (S1000; 2 clusters for an S3000 volume): name[12] pad[4] type u8 size u24 start u16 tag u16
  names use the Akai charset 0-9 ' ' A-Z # + - .  coded 0..40
"""
import os
import re
import struct
from dataclasses import dataclass, field

import numpy as np

AKAI_CHARS = '0123456789 ABCDEFGHIJKLMNOPQRSTUVWXYZ#+-.'
AKAI_SIG = bytes.fromhex('050d0a1a0f271434')   # bytes 4..11 of every partition header
CLUSTER = 0x2000
PART_STEP = 0x3C00000
PART_CLUSTERS = 0x1E00
FAT_AT = 0x70A
VOL_AT = 0xCA

T_PROG1, T_SAMP1 = 0x70, 0x73       # S1000 / S900-era program and sample
T_PROG3, T_SAMP3 = 0xF0, 0xF3       # S2000 / S3000 program and sample
SAMPLE_TYPES = (T_SAMP1, T_SAMP3)
PROGRAM_TYPES = (T_PROG1, T_PROG3)
HEAD = {T_SAMP1: 150, T_SAMP3: 192, T_PROG1: 150, T_PROG3: 192}
KG_SIZE = 150

# keygroup zone playback modes (zone byte 19 / keygroup 0x35)
PB_AS_SAMPLE, PB_LOOP_REL, PB_LOOP_HOLD, PB_NOLOOP, PB_TO_END = range(5)
# sample header loop modes (byte 0x13)
LM_LOOP_REL, LM_LOOP_HOLD, LM_NOLOOP, LM_TO_END = range(4)


def akai_name(b):
    return ''.join(AKAI_CHARS[c] if c < len(AKAI_CHARS) else '?' for c in b).rstrip()


def clean_name(s, n=12):
    """any string -> valid Akai name (upper case, charset only, <= n chars)"""
    s = s.upper().replace('_', ' ')
    out = ''.join(c if c in AKAI_CHARS else ' ' for c in s)
    out = re.sub(r' +', ' ', out).strip()
    return (out or 'UNNAMED')[:n].rstrip() or 'UNNAMED'


def enc_name(s, n=12):
    """encode as stored (spacing preserved, so names read from a disc round-trip exactly)"""
    s = ''.join(c if c in AKAI_CHARS else ' ' for c in s.upper())[:n].ljust(n)
    return bytes(AKAI_CHARS.index(c) for c in s)


def s8(v):
    return v - 256 if v > 127 else v


# ---------------------------------------------------------------- data model
@dataclass
class Loop:
    start: int
    end: int               # end point (the S1000 stores the end and a length)
    time: int = 9999       # 0 none, 1-9998 ms, 9999 hold


@dataclass
class Sample:
    name: str
    pcm: np.ndarray                      # int16 mono
    rate: int = 44100                    # nominal rate the data was recorded at (base rate is 44.1k/22.05k)
    root: int = 60                       # MIDI note
    cents: float = 0.0                   # pitch offset in cents
    loops: list = field(default_factory=list)
    loop_mode: int = LM_NOLOOP
    start: int = 0
    end: int = 0                         # play end marker (0 = last sample)
    bandwidth: int = 1                   # 1 = 20 kHz (44.1k), 0 = 10 kHz (22.05k)
    source: str = ''

    @property
    def n(self):
        return len(self.pcm)

    @property
    def base_rate(self):
        return 44100 if self.bandwidth else 22050

    @property
    def play_rate(self):
        """the true pitch-reference sample rate: sampler plays at base rate shifted by the tune offset"""
        return self.base_rate * 2 ** (self.cents / 1200.0)

    @property
    def seconds(self):
        return self.n / float(self.play_rate)


@dataclass
class Zone:
    sample: str = ''
    lovel: int = 0
    hivel: int = 127
    tune: int = 0          # cents
    loud: int = 0
    filt: int = 0
    pan: int = 0           # -50..50
    playback: int = PB_AS_SAMPLE


ENV_DEFAULT = dict(filter=99, amp_att=0, amp_dec=99, amp_sus=99, amp_rel=45,
                   fil_att=0, fil_dec=99, fil_sus=99, fil_rel=99)


@dataclass
class Keygroup:
    lo: int = 24
    hi: int = 127
    tune: int = 0
    zones: list = field(default_factory=list)
    env: dict = field(default_factory=lambda: dict(ENV_DEFAULT))
    xfade: int = 0
    raw: bytes = b''


@dataclass
class Program:
    name: str
    keygroups: list = field(default_factory=list)
    midi_prog: int = 0
    polyphony: int = 31
    loudness: int = 80
    raw: bytes = b''


@dataclass
class VolumeModel:
    name: str
    samples: dict = field(default_factory=dict)       # name -> Sample, insertion ordered
    programs: list = field(default_factory=list)


# ---------------------------------------------------------------- container reading
class Partition:
    def __init__(self, fh, base, letter):
        self.fh, self.base, self.letter = fh, base, letter
        fh.seek(base)
        head = fh.read(0x8000)
        self.n_clusters = struct.unpack('<H', head[:2])[0]
        if not (4 < self.n_clusters <= PART_CLUSTERS):
            raise ValueError('not an Akai partition header')
        self.fat = struct.unpack('<%dH' % self.n_clusters, head[FAT_AT:FAT_AT + 2 * self.n_clusters])
        self.volumes = []
        for k in range(100):
            e = head[VOL_AT + 16 * k:VOL_AT + 16 * k + 16]
            typ, start = struct.unpack('<HH', e[12:16])
            if typ and 3 <= start < self.n_clusters and e[:12] != b'\x00' * 12:
                self.volumes.append((akai_name(e[:12]), typ, start))

    def chain(self, start, size):
        out, c = [], start
        need = (size + CLUSTER - 1) // CLUSTER
        while c and c < self.n_clusters and len(out) < need:
            out.append(c)
            nxt = self.fat[c]
            if nxt in (0xC000, 0x4000, 0):
                break
            c = nxt
        return out

    def read(self, start, size):
        buf = bytearray()
        for c in self.chain(start, size):
            self.fh.seek(self.base + c * CLUSTER)
            buf += self.fh.read(CLUSTER)
        return bytes(buf[:size])

    def directory(self, vol_start, vol_type=1):
        nclu = 2 if vol_type == 3 else 1
        nent = (nclu * CLUSTER) // 24
        self.fh.seek(self.base + vol_start * CLUSTER)
        d = self.fh.read(nent * 24)
        files = []
        for i in range(len(d) // 24):
            e = d[i * 24:i * 24 + 24]
            if e[:12] == b'\x00' * 12:
                continue
            size = e[17] | e[18] << 8 | e[19] << 16
            start = e[20] | e[21] << 8
            if size == 0 or start < 4 or start >= self.n_clusters:
                continue
            files.append(dict(name=akai_name(e[:12]), type=e[16], size=size, start=start))
        return files


def open_image(path):
    """list of Partition for an Akai CD / disk image, or [] if it is not one"""
    fh = open(path, 'rb')
    size = os.fstat(fh.fileno()).st_size
    first = fh.read(0x100000)
    org = 0
    if first[4:12] != AKAI_SIG:
        # Nero .nrg rips carry a 150-sector (0x4B000) lead-in before the first partition
        i = first.find(AKAI_SIG)
        if i < 4 or (i - 4) % 2048:
            fh.close()
            return []
        org = i - 4
    parts = []
    for i, base in enumerate(range(org, size, PART_STEP)):
        try:
            parts.append(Partition(fh, base, chr(65 + i) if i < 26 else str(i)))
        except (ValueError, struct.error):
            break
    return parts


# ---------------------------------------------------------------- sample / program parsing
def read_sample(data, ftype=T_SAMP1, name=None):
    """sample file bytes -> Sample (or None)"""
    hl = HEAD.get(ftype, 150)
    if len(data) < hl + 4 or data[0] not in (1, 3):
        return None
    n = struct.unpack('<I', data[26:30])[0]
    n = min(n, (len(data) - hl) // 2)
    if n < 8:
        return None
    pcm = np.frombuffer(data, '<i2', n, hl).copy()
    bw = 1 if data[1] else 0
    v = struct.unpack('<h', data[20:22])[0]            # semitones in 1/256 steps
    cents = v * 100.0 / 256.0
    start, end = struct.unpack('<II', data[30:38])
    nact = min(data[16], 8)
    first = min(data[17], 7)
    sel = []
    for li in range(first, min(first + nact, 8)):
        at, fine, ln, tm = struct.unpack('<IHIH', data[38 + 12 * li:38 + 12 * li + 12])
        ls = at - ln
        if at == n and ln == min(n, 4896):
            continue                  # the sampler's placeholder loop on samples that were never looped
        if ln >= 2 and 0 <= ls < at <= n:
            sel.append(Loop(ls, at, tm))
    return Sample(name=name or akai_name(data[3:15]), pcm=pcm, rate=int(round(
        (44100 if bw else 22050) * 2 ** (cents / 1200.0))), root=data[2], cents=cents, loops=sel,
        loop_mode=data[19] if data[19] < 4 else LM_NOLOOP, start=min(start, max(n - 1, 0)),
        end=min(max(end, 0), n), bandwidth=bw)


def read_program(data, ftype=T_PROG1):
    """program file bytes -> Program (or None)"""
    hl = HEAD.get(ftype, 150)
    if len(data) < hl or data[0] != 1:
        return None
    kgs = []
    for addr in range(hl, len(data) - KG_SIZE + 1, KG_SIZE):
        kg = data[addr:addr + KG_SIZE]
        if kg[0] != 2:
            break
        zones = []
        for i in range(4):
            z = kg[34 + 24 * i:58 + 24 * i]
            nm = akai_name(z[:12])
            if not nm:
                continue
            zones.append(Zone(nm, z[12], z[13], 100 * s8(z[15]) + s8(z[14]), s8(z[16]), s8(z[17]),
                              s8(z[18]), z[19] if z[19] < 5 else 0))
        env = dict(filter=kg[7], amp_att=kg[12], amp_dec=kg[13], amp_sus=kg[14], amp_rel=kg[15],
                   fil_att=kg[20], fil_dec=kg[21], fil_sus=kg[22], fil_rel=kg[23])
        kgs.append(Keygroup(kg[3], kg[4], 100 * s8(kg[6]) + s8(kg[5]), zones, env, kg[30], kg))
    return Program(akai_name(data[3:15]), kgs, data[15], data[17], data[25], data[:hl])


class Volume:
    """one volume of a disc: file directory + lazy loading"""

    def __init__(self, part, name, vtype, start):
        self.part, self.name, self.vtype, self.start = part, name, vtype, start
        self.files = part.directory(start, vtype)
        self.sample_files = {f['name']: f for f in self.files if f['type'] in SAMPLE_TYPES}
        self.program_files = [f for f in self.files if f['type'] in PROGRAM_TYPES]

    def sample(self, name):
        f = self.sample_files.get(name)
        if f is None:
            return None
        return read_sample(self.part.read(f['start'], f['size']), f['type'], name)

    def program(self, f):
        return read_program(self.part.read(f['start'], f['size']), f['type'])

    def load(self, progress=None):
        """fully load into a VolumeModel"""
        vm = VolumeModel(self.name)
        for i, f in enumerate(self.program_files):
            p = self.program(f)
            if p:
                p.name = f['name']
                vm.programs.append(p)
        for i, (nm, f) in enumerate(self.sample_files.items()):
            s = self.sample(nm)
            if s:
                vm.samples[nm] = s
            if progress:
                progress(i + 1, len(self.sample_files))
        return vm


class Image:
    """an Akai CD / disk image: .volumes = [Volume]"""

    def __init__(self, path):
        self.path = path
        self.parts = open_image(path)
        if not self.parts:
            raise ValueError('not an Akai sampler disk image (no Akai partition found)')
        self.volumes = []
        for p in self.parts:
            for name, typ, start in p.volumes:
                try:
                    v = Volume(p, name, typ, start)
                except (struct.error, OSError):
                    continue
                if v.program_files or v.sample_files:
                    self.volumes.append(v)

    def close(self):
        for p in self.parts:
            p.fh.close()
