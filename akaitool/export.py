"""Export samples and programs: WAV (with root note + loop points), SFZ, SoundFont 2."""
import math
import os
import re
import struct

import numpy as np

from .akai import (LM_NOLOOP, PB_AS_SAMPLE, PB_LOOP_HOLD, PB_LOOP_REL, PB_NOLOOP, PB_TO_END, Sample)


def safe_name(s):
    s = re.sub(r'[^A-Za-z0-9 _.+-]', '_', s).strip()
    return s or '_'


# ---------------------------------------------------------------- WAV
def wav_bytes(s):
    """Sample -> .wav file bytes: 16-bit mono at the sample's true playback rate, with a 'smpl' chunk
    (root note and the first loop) that DAWs and samplers read."""
    rate = int(round(s.play_rate))
    pcm = np.ascontiguousarray(s.pcm, '<i2').tobytes()
    fmt = struct.pack('<HHIIHH', 1, 1, rate, rate * 2, 2, 16)
    chunks = [(b'fmt ', fmt), (b'data', pcm)]
    loops = list(s.loops[:1]) if s.loops and s.loop_mode in (0, 1) else []
    smpl = struct.pack('<9I', 0, 0, int(1e9 / rate), s.root, 0, 0, 0, len(loops), 0)
    for i, lp in enumerate(loops):
        smpl += struct.pack('<6I', i, 0, lp.start, lp.end - 1, 0, 0)
    chunks.append((b'smpl', smpl))
    body = b'WAVE'
    for cid, d in chunks:
        body += cid + struct.pack('<I', len(d)) + d + (b'\x00' if len(d) & 1 else b'')
    return b'RIFF' + struct.pack('<I', len(body)) + body


def export_wav(s, path):
    with open(path, 'wb') as f:
        f.write(wav_bytes(s))


# ---------------------------------------------------------------- envelope approximations
def env_time(v):
    """Akai envelope rate 0-99 -> seconds (approximate: an exponential scale from 3 ms to ~24 s)"""
    return 0.003 * 10 ** (min(99, max(0, v)) / 99.0 * 3.9)


def effective_playback(zone, smp):
    """zone playback mode, resolving 'as sample' via the sample header"""
    if zone.playback != PB_AS_SAMPLE:
        return zone.playback
    return smp.loop_mode + 1 if smp is not None else PB_NOLOOP


# ---------------------------------------------------------------- SFZ
def export_sfz(vm, program, outdir, samples_dir='samples'):
    """write <program>.sfz and the WAVs it uses (shared samples folder). -> sfz path"""
    os.makedirs(os.path.join(outdir, samples_dir), exist_ok=True)
    lines = ['// %s  (exported by Akai CD ISO Maker)' % program.name, '<control>',
             'default_path=%s/' % samples_dir, '']
    done = set()
    for kg in program.keygroups:
        e = kg.env
        for z in kg.zones:
            s = vm.samples.get(z.sample)
            if s is None:
                continue
            if z.sample not in done:
                export_wav(s, os.path.join(outdir, samples_dir, safe_name(z.sample) + '.wav'))
                done.add(z.sample)
            r = ['<region>', 'sample=%s.wav' % safe_name(z.sample), 'lokey=%d' % kg.lo, 'hikey=%d' % kg.hi,
                 'pitch_keycenter=%d' % s.root, 'lovel=%d' % z.lovel, 'hivel=%d' % z.hivel]
            if kg.tune + z.tune:
                r.append('tune=%d' % (kg.tune + z.tune))
            if z.pan:
                r.append('pan=%d' % (z.pan * 2))
            eff = effective_playback(z, s)
            if eff in (PB_LOOP_REL, PB_LOOP_HOLD) and s.loops:
                lp = s.loops[0]
                r += ['loop_mode=%s' % ('loop_continuous' if eff == PB_LOOP_REL else 'loop_sustain'),
                      'loop_start=%d' % lp.start, 'loop_end=%d' % (lp.end - 1)]
            elif eff == PB_TO_END:
                r.append('loop_mode=one_shot')
            else:
                r.append('loop_mode=no_loop')
            if s.start:
                r.append('offset=%d' % s.start)
            if s.end and s.end < s.n:
                r.append('end=%d' % (s.end - 1))
            r += ['ampeg_attack=%.3f' % env_time(e.get('amp_att', 0)) if e.get('amp_att', 0) else 'ampeg_attack=0',
                  'ampeg_decay=%.3f' % env_time(e.get('amp_dec', 99)),
                  'ampeg_sustain=%.1f' % (e.get('amp_sus', 99) * 100.0 / 99.0),
                  'ampeg_release=%.3f' % env_time(e.get('amp_rel', 45))]
            lines.append(' '.join(r))
    path = os.path.join(outdir, safe_name(program.name) + '.sfz')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    return path


# ---------------------------------------------------------------- SF2
def _tc(sec):
    return int(round(1200 * math.log2(max(sec, 0.001))))


def _name20(s):
    return s.encode('ascii', 'replace')[:19].ljust(20, b'\x00')


def _chunk(cid, data):
    return cid + struct.pack('<I', len(data)) + data + (b'\x00' if len(data) & 1 else b'')


def _list(kind, *chunks):
    body = kind + b''.join(chunks)
    return b'LIST' + struct.pack('<I', len(body)) + body


def sf2_bytes(vm, programs, title='AKAI EXPORT'):
    """SoundFont 2.01 with one preset per program"""
    # samples used
    used = []
    for p in programs:
        for kg in p.keygroups:
            for z in kg.zones:
                if z.sample in vm.samples and z.sample not in used:
                    used.append(z.sample)
    smpl = bytearray()
    shdr = bytearray()
    sidx = {}
    for nm in used:
        s = vm.samples[nm]
        start = len(smpl) // 2
        smpl += np.ascontiguousarray(s.pcm, '<i2').tobytes() + b'\x00' * 92          # 46 pad points
        end = start + s.n
        if s.loops and s.loop_mode < 2:
            sl, el = start + s.loops[0].start, start + s.loops[0].end
        else:
            sl = el = start
        shdr += (_name20(nm) + struct.pack('<IIIII', start, end, sl, el, int(round(s.play_rate))) +
                 struct.pack('<BbHH', s.root, 0, 0, 1))
        sidx[nm] = len(sidx)
    shdr += _name20('EOS') + struct.pack('<IIIII', 0, 0, 0, 0, 0) + struct.pack('<BbHH', 0, 0, 0, 0)

    phdr, pbag, pgen, inst, ibag, igen = bytearray(), bytearray(), bytearray(), bytearray(), bytearray(), bytearray()
    ngen_i = 0
    nbag_i = 0
    for pi, p in enumerate(programs):
        inst += _name20(p.name) + struct.pack('<H', nbag_i)
        for kg in p.keygroups:
            for z in kg.zones:
                s = vm.samples.get(z.sample)
                if s is None:
                    continue
                e = kg.env
                eff = effective_playback(z, s)
                g = [(43, struct.pack('<BB', kg.lo, kg.hi)), (44, struct.pack('<BB', z.lovel, z.hivel))]
                if z.pan:
                    g.append((17, struct.pack('<h', z.pan * 10)))
                tune = kg.tune + z.tune
                if tune:
                    semi, cents = int(tune / 100), tune - 100 * int(tune / 100)
                    if semi:
                        g.append((51, struct.pack('<h', semi)))
                    if cents:
                        g.append((52, struct.pack('<h', cents)))
                if e.get('amp_att', 0):
                    g.append((34, struct.pack('<h', _tc(env_time(e['amp_att'])))))
                if e.get('amp_dec', 99) < 99:
                    g.append((36, struct.pack('<h', _tc(env_time(e['amp_dec'])))))
                sus = e.get('amp_sus', 99)
                if sus < 99:
                    cb = 1440 if sus <= 0 else min(1440, int(round(200 * math.log10(99.0 / sus))))
                    g.append((37, struct.pack('<h', cb)))
                g.append((38, struct.pack('<h', _tc(env_time(e.get('amp_rel', 45))))))
                if eff in (PB_LOOP_REL, PB_LOOP_HOLD) and s.loops:
                    g.append((54, struct.pack('<h', 1 if eff == PB_LOOP_REL else 3)))
                g.append((58, struct.pack('<h', s.root)))
                g.append((53, struct.pack('<H', sidx[z.sample])))
                ibag += struct.pack('<HH', ngen_i, 0)
                nbag_i += 1
                for op, amt in g:
                    igen += struct.pack('<H', op) + amt
                ngen_i += 1 * len(g)
        # preset
        phdr += _name20(p.name) + struct.pack('<HHHIII', pi, 0, pi, 0, 0, 0)
        pbag += struct.pack('<HH', pi, 0)
        pgen += struct.pack('<HH', 41, pi)
    phdr += _name20('EOP') + struct.pack('<HHHIII', 0, 0, len(programs), 0, 0, 0)
    pbag += struct.pack('<HH', len(programs), 0)
    pgen += struct.pack('<HH', 0, 0)
    inst += _name20('EOI') + struct.pack('<H', nbag_i)
    ibag += struct.pack('<HH', ngen_i, 0)
    igen += struct.pack('<HH', 0, 0)
    pmod = b'\x00' * 10
    imod = b'\x00' * 10

    info = _list(b'INFO', _chunk(b'ifil', struct.pack('<HH', 2, 1)), _chunk(b'isng', b'EMU8000\x00'),
                 _chunk(b'INAM', title.encode('ascii', 'replace')[:60] + b'\x00'),
                 _chunk(b'ISFT', b'Akai CD ISO Maker\x00'))
    sdta = _list(b'sdta', _chunk(b'smpl', bytes(smpl)))
    pdta = _list(b'pdta', _chunk(b'phdr', bytes(phdr)), _chunk(b'pbag', bytes(pbag)), _chunk(b'pmod', pmod),
                 _chunk(b'pgen', bytes(pgen)), _chunk(b'inst', bytes(inst)), _chunk(b'ibag', bytes(ibag)),
                 _chunk(b'imod', imod), _chunk(b'igen', bytes(igen)), _chunk(b'shdr', bytes(shdr)))
    body = b'sfbk' + info + sdta + pdta
    return b'RIFF' + struct.pack('<I', len(body)) + body


def export_sf2(vm, programs, path, title=None):
    with open(path, 'wb') as f:
        f.write(sf2_bytes(vm, programs, title or vm.name))
    return path
