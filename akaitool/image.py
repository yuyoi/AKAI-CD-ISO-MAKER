"""Build Akai sampler CD / disk images (S1000 / S2000 compatible, optional S3000 file types).

The output is a raw 2048-byte-sector image, identical in structure to a ripped commercial Akai sample CD:
burn it with a tool that writes an .iso/.bin image as-is (data CD, mode 1, no ISO 9660 needed).
"""
import struct

import numpy as np

from .akai import (AKAI_SIG, CLUSTER, FAT_AT, HEAD, KG_SIZE, LM_NOLOOP, PART_CLUSTERS, PB_AS_SAMPLE, T_PROG1,
                   T_PROG3, T_SAMP1, T_SAMP3, VOL_AT, enc_name, clean_name)

DIR_TAG = 0x041E            # per-file tag word seen on commercial discs
MAX_FILES_S1000 = 126
MAX_FILES_S3000 = 340

# default program header / keygroup (values from the format notes and commercial discs)
def _prog_template():
    h = bytearray(150)
    h[0] = 1
    h[0x11] = 31                    # polyphony
    h[0x12] = 1                     # priority normal
    h[0x13], h[0x14] = 24, 127      # play range
    h[0x16] = 0xFF                  # individual output off
    h[0x17] = 99                    # stereo level
    h[0x19] = 80                    # loudness
    h[0x1A] = 20                    # velocity > loudness
    h[0x1D] = 0x03
    h[0x21] = 50                    # LFO speed
    h[0x24] = 30                    # modwheel > depth
    h[0x27] = 2                     # bendwheel > pitch
    h[0x38] = 1
    h[0x3E] = h[0x3F] = h[0x40] = 10
    h[0x46] = 50
    return bytes(h)


def _kg_template():
    k = bytearray(KG_SIZE)
    k[0] = 2
    k[3], k[4] = 24, 127
    k[7] = 99
    k[12], k[13], k[14], k[15] = 0, 99, 99, 45
    k[21], k[22], k[23] = 99, 99, 99
    k[0x1F] = 4
    k[0x20:0x22] = b'\xff\xff'
    for i in range(4):
        z = 34 + 24 * i
        k[z:z + 12] = b'\x0a' * 12
        k[z + 20:z + 24] = b'\xff\xff\xff\xff'
    k[0x84] = 0
    k[0x86], k[0x87] = 1, 1
    return bytes(k)


PROG_T = _prog_template()
KG_T = _kg_template()


def _split_tune(t):
    t = int(round(t))
    semi = int(round(t / 100.0))
    return t - 100 * semi, semi


def _sb(v):
    return int(max(-128, min(127, v))) & 0xFF


def sample_bytes(s, ftype, addr=0x100):
    """Sample -> file bytes (header + int16 LE data)"""
    n = s.n
    h = bytearray(HEAD[ftype])
    h[0] = 3
    h[1] = 1 if s.bandwidth else 0
    h[2] = s.root
    h[3:15] = enc_name(s.name)
    h[15] = 0x80
    loops = list(s.loops)[:8]
    h[16] = max(1, len(loops))
    h[17] = 0
    h[19] = s.loop_mode
    h[20:22] = struct.pack('<h', int(round(s.cents * 256 / 100.0)))
    h[22:26] = struct.pack('<I', addr)
    h[26:30] = struct.pack('<I', n)
    h[30:34] = struct.pack('<I', s.start)
    h[34:38] = struct.pack('<I', s.end if s.end else n)
    for i in range(8):
        if i < len(loops):
            lp = loops[i]
            at, ln, tm = lp.end, lp.end - lp.start, lp.time
        else:
            at, ln, tm = n, min(n, 4896), 9999
        h[38 + 12 * i:50 + 12 * i] = struct.pack('<IHIH', at, 0, ln, tm)
    h[0x88:0x8A] = b'\xff\xff'
    h[0x8A:0x8C] = struct.pack('<H', int(min(s.rate, 65535)))
    return bytes(h) + np.ascontiguousarray(s.pcm, '<i2').tobytes()


def program_bytes(p, ftype):
    """Program -> file bytes (header + keygroups)"""
    hl = HEAD[ftype]
    nkg = len(p.keygroups)
    h = bytearray(PROG_T) + bytearray(hl - len(PROG_T))
    h[1:3] = struct.pack('<H', hl)
    h[3:15] = enc_name(p.name)
    h[15] = p.midi_prog & 0x7F
    h[0x11] = p.polyphony
    h[0x19] = p.loudness
    h[0x2A] = nkg
    out = bytearray(h)
    for i, kg in enumerate(p.keygroups):
        k = bytearray(KG_T)
        nxt = hl + (i + 1) * KG_SIZE if i + 1 < nkg else 0
        k[1:3] = struct.pack('<H', nxt)
        k[3], k[4] = kg.lo, kg.hi
        k[5], k[6] = _sb(_split_tune(kg.tune)[0]), _sb(_split_tune(kg.tune)[1])
        e = kg.env
        k[7] = e.get('filter', 99)
        k[12], k[13], k[14], k[15] = e.get('amp_att', 0), e.get('amp_dec', 99), e.get('amp_sus', 99), e.get('amp_rel', 45)
        k[20], k[21], k[22], k[23] = e.get('fil_att', 0), e.get('fil_dec', 99), e.get('fil_sus', 99), e.get('fil_rel', 99)
        k[30] = 1 if kg.xfade else 0
        for j, z in enumerate(kg.zones[:4]):
            o = 34 + 24 * j
            k[o:o + 12] = enc_name(z.sample)
            k[o + 12], k[o + 13] = z.lovel, z.hivel
            c, sm = _split_tune(z.tune)
            k[o + 14], k[o + 15] = _sb(c), _sb(sm)
            k[o + 16], k[o + 17], k[o + 18] = _sb(z.loud), _sb(z.filt), _sb(z.pan)
            k[o + 19] = z.playback
        out += k
    return bytes(out)


def _header(count):
    h = bytearray(VOL_AT)
    h[0:2] = struct.pack('<H', count)
    v = 0
    for i in range(4, 0xC6, 2):
        v = (v + 0x0D05) & 0xFFFF
        h[i:i + 2] = struct.pack('<H', v)
    h[0xC6:0xC8] = struct.pack('<H', (0xB9D5 + count) & 0xFFFF)
    h[0xC8:0xCA] = b'\x2f\x00'
    assert h[4:12] == AKAI_SIG
    return h


def _nclusters(nbytes):
    return (nbytes + CLUSTER - 1) // CLUSTER


def plan(volumes, mode='S1000'):
    """Lay the volumes out. -> list of partitions; each is dict(vols=[dict(name, vtype, dir, files)], n)
    files: dict(name, type, data, start, size). Raises ValueError if something cannot fit."""
    s3 = mode == 'S3000'
    vtype = 3 if s3 else 1
    ft_s, ft_p = (T_SAMP3, T_PROG3) if s3 else (T_SAMP1, T_PROG1)
    dir_clu = 2 if s3 else 1
    maxfiles = MAX_FILES_S3000 if s3 else MAX_FILES_S1000
    parts = []
    for vm in volumes:
        files = []
        used_names = set()
        for p in vm.programs:
            if p.keygroups:
                files.append(dict(name=p.name, type=ft_p, size=HEAD[ft_p] + KG_SIZE * len(p.keygroups),
                                  make=lambda p=p, t=ft_p: program_bytes(p, t)))
        addr = 0x100
        for nm, smp in vm.samples.items():
            files.append(dict(name=nm, type=ft_s, size=HEAD[ft_s] + 2 * smp.n,
                              make=lambda smp=smp, a=addr, t=ft_s: sample_bytes(smp, t, a)))
            addr += (len(smp.pcm) * 2 + 0x1FF) & ~0x1FF
        if len(files) > maxfiles:
            raise ValueError('volume %s has %d files, the format allows %d' % (vm.name, len(files), maxfiles))
        body = sum(_nclusters(f['size']) for f in files)
        need = dir_clu + body
        if need > PART_CLUSTERS - 3:
            raise ValueError('volume %s needs %.1f MB, more than one 60 MB partition' % (vm.name, need * 8192 / 1e6))
        for pt in parts:
            if len(pt['vols']) < 100 and pt['used'] + need <= PART_CLUSTERS:
                break
        else:
            pt = dict(vols=[], used=3)
            parts.append(pt)
        v = dict(name=vm.name, vtype=vtype, dir=pt['used'], files=files, dir_clu=dir_clu)
        pt['used'] += dir_clu
        for f in files:
            f['start'] = pt['used']
            pt['used'] += _nclusters(f['size'])
        pt['vols'].append(v)
    return parts


def image_size(volumes, mode='S1000'):
    """size in bytes of the image (without building it)"""
    parts = plan(volumes, mode)
    if not parts:
        return 0
    return (len(parts) - 1) * PART_CLUSTERS * CLUSTER + parts[-1]['used'] * CLUSTER


def write_image(path, volumes, mode='S1000', progress=None):
    """write the image; returns (size_bytes, n_partitions)"""
    parts = plan(volumes, mode)
    if not parts:
        raise ValueError('nothing to write: add at least one program with keygroups')
    with open(path, 'wb') as fh:
        for pi, pt in enumerate(parts):
            last = pi == len(parts) - 1
            count = pt['used'] if last else PART_CLUSTERS
            base = pi * PART_CLUSTERS * CLUSTER
            fat = [0] * count
            for c in range(4):
                fat[c] = 0x4000
            head = bytearray(0x3 * CLUSTER)
            head[:VOL_AT] = _header(count)
            for k, v in enumerate(pt['vols']):
                e = enc_name(v['name']) + struct.pack('<HH', v['vtype'], v['dir'])
                head[VOL_AT + 16 * k:VOL_AT + 16 * k + 16] = e
                fat[v['dir']] = 0x4000
                if v['dir_clu'] == 2:
                    fat[v['dir'] + 1] = 0x8000
                d = bytearray(v['dir_clu'] * CLUSTER)
                for i, f in enumerate(v['files']):
                    sz = f['size']
                    d[i * 24:i * 24 + 24] = (enc_name(f['name']) + b'\x20\x20\x20\x20' + bytes([f['type']]) +
                                             sz.to_bytes(3, 'little') + struct.pack('<HH', f['start'], DIR_TAG))
                    n = _nclusters(sz)
                    for c in range(n):
                        fat[f['start'] + c] = f['start'] + c + 1 if c + 1 < n else 0xC000
                    fh.seek(base + f['start'] * CLUSTER)
                    fh.write(f['make']())
                fh.seek(base + v['dir'] * CLUSTER)
                fh.write(d)
                if progress:
                    progress(v['name'])
            head[FAT_AT:FAT_AT + 2 * count] = struct.pack('<%dH' % count, *fat)
            # clusters 0-2 hold header + FAT; write after the data so the partition always starts with it
            fh.seek(base)
            fh.write(head)
        end = (len(parts) - 1) * PART_CLUSTERS * CLUSTER + parts[-1]['used'] * CLUSTER
        fh.seek(end - 1)
        cur = fh.tell()
        fh.write(b'\x00') if cur == end - 1 else None
        fh.truncate(end)
    return end, len(parts)


def validate(volumes, mode='S1000'):
    """-> list of (level, message), level 'error' (cannot build correctly) or 'warn'"""
    out = []
    maxfiles = MAX_FILES_S3000 if mode == 'S3000' else MAX_FILES_S1000
    if not volumes:
        out.append(('error', 'The disc has no volumes.'))
    seen_vol = set()
    for vm in volumes:
        if vm.name in seen_vol:
            out.append(('warn', 'Two volumes are called %s.' % vm.name))
        seen_vol.add(vm.name)
        if len(vm.programs) + len(vm.samples) > maxfiles:
            out.append(('error', '%s: %d files, limit is %d per volume.' % (vm.name, len(vm.programs) + len(vm.samples), maxfiles)))
        names = set()
        for p in vm.programs:
            if p.name in names:
                out.append(('warn', '%s: program name %s used twice.' % (vm.name, p.name)))
            names.add(p.name)
            if not p.keygroups:
                out.append(('warn', '%s / %s: program has no keygroups (it is skipped).' % (vm.name, p.name)))
            ram = 0
            used = set()
            for i, kg in enumerate(p.keygroups):
                if kg.lo < 24:
                    out.append(('warn', '%s / %s: keygroup %d starts below key 24, the samplers cannot play lower.' % (vm.name, p.name, i + 1)))
                if not kg.zones:
                    out.append(('error', '%s / %s: keygroup %d has no sample.' % (vm.name, p.name, i + 1)))
                for z in kg.zones:
                    if z.sample not in vm.samples:
                        out.append(('error', '%s / %s: keygroup %d uses missing sample %s.' % (vm.name, p.name, i + 1, z.sample)))
                    elif z.sample not in used:
                        used.add(z.sample)
                        ram += vm.samples[z.sample].n * 2
            if len(p.keygroups) > 99:
                out.append(('error', '%s / %s: %d keygroups, limit is 99.' % (vm.name, p.name, len(p.keygroups))))
            if ram > 8 * 1024 * 1024:
                out.append(('warn', '%s / %s: samples total %.1f MB, more than an 8 MB sampler holds.' % (vm.name, p.name, ram / 1048576.0)))
            elif ram > 2 * 1024 * 1024:
                out.append(('warn', '%s / %s: samples total %.1f MB, a stock 2 MB sampler cannot load it.' % (vm.name, p.name, ram / 1048576.0)))
    return out
