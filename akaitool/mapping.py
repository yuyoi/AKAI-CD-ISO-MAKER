"""Turn loose samples into programs: multisample keyboard map, drum kit, one program per sample."""
import re

from .akai import (LM_LOOP_HOLD, LM_NOLOOP, LM_TO_END, PB_AS_SAMPLE, PB_TO_END, Keygroup, Program, Zone, clean_name)

LOW_KEY, HIGH_KEY = 24, 127          # the key range an S1000-S3000 program can use


def unique_name(name, existing):
    """name <= 12 chars that is not in `existing`"""
    base = clean_name(name)
    if base not in existing:
        return base
    for i in range(2, 1000):
        suf = ' %d' % i if i < 10 else str(i)
        cand = (base[:12 - len(suf)] + suf)
        if cand not in existing:
            return cand
    return base


def add_samples(vm, samples):
    """add Samples to a VolumeModel, renaming on clashes. -> list of the (renamed) samples"""
    out = []
    for s in samples:
        s.name = unique_name(s.name, vm.samples)
        vm.samples[s.name] = s
        out.append(s)
    return out


_LR = re.compile(r'^(.*?)\s*([LR])$')


def voices(samples):
    """group a sample list into voices: a stereo pair 'X L' + 'X R' is one voice, anything else stands alone.
    -> list of lists of Sample"""
    by_base, order, out = {}, [], []
    for s in samples:
        m = _LR.match(s.name)
        if m and len(m.group(1)) >= 1:
            by_base.setdefault(m.group(1), {})[m.group(2)] = s
    used = set()
    for s in samples:
        if id(s) in used:
            continue
        m = _LR.match(s.name)
        pair = by_base.get(m.group(1)) if m else None
        if pair and len(pair) == 2 and pair['L'].n == pair['R'].n:
            out.append([pair['L'], pair['R']])
            used.update((id(pair['L']), id(pair['R'])))
        else:
            out.append([s])
            used.add(id(s))
    return out


def zones_for(voice, lovel=0, hivel=127, playback=PB_AS_SAMPLE):
    if len(voice) == 2:
        return [Zone(voice[0].name, lovel, hivel, 0, 0, 0, -50, playback),
                Zone(voice[1].name, lovel, hivel, 0, 0, 0, 50, playback)]
    return [Zone(voice[0].name, lovel, hivel, 0, 0, 0, 0, playback)]


def _env_for(voice):
    looped = any(s.loops and s.loop_mode in (0, 1) for s in voice)
    return dict(filter=99, amp_att=0, amp_dec=99, amp_sus=99, amp_rel=45 if looped else 30,
                fil_att=0, fil_dec=99, fil_sus=99, fil_rel=99)


def multisample(name, samples, layers=True, key_lo=LOW_KEY, key_hi=HIGH_KEY):
    """spread voices over the keyboard by their root notes (split halfway between neighbours).
    Several voices on one root become velocity layers (if layers) split evenly over 0-127 in name order."""
    vs = voices(samples)
    by_root = {}
    for v in vs:
        by_root.setdefault(v[0].root, []).append(v)
    roots = sorted(by_root)
    kgs = []
    for i, r in enumerate(roots):
        lo = key_lo if i == 0 else (roots[i - 1] + r) // 2 + 1
        hi = key_hi if i == len(roots) - 1 else (r + roots[i + 1]) // 2
        group = sorted(by_root[r], key=lambda v: v[0].name)
        if not layers:
            group = group[:1]
        n = len(group)
        zones = []
        for j, v in enumerate(group):
            lv = round(128 * j / n)
            hv = round(128 * (j + 1) / n) - 1
            zones += zones_for(v, lv, hv)
        if len(zones) > 4:
            zones = zones[:4]
        kgs.append(Keygroup(lo, hi, 0, zones, _env_for(group[0]), 1 if n > 1 else 0))
    return Program(clean_name(name), kgs)


def drumkit(name, samples, start=36):
    """one voice per key, consecutive keys from `start`; every sample's root is set to its key, one-shot playback"""
    vs = voices(samples)
    kgs = []
    for i, v in enumerate(vs):
        key = start + i
        if key > HIGH_KEY:
            break
        for s in v:
            s.root = key
            s.loop_mode = LM_TO_END
        kgs.append(Keygroup(key, key, 0, zones_for(v, 0, 127, PB_TO_END), _env_for(v)))
    return Program(clean_name(name), kgs)


def one_each(samples):
    """one program per voice, sample spread over the whole keyboard"""
    progs = []
    for v in voices(samples):
        base = _LR.sub(r'\1', v[0].name).strip() if len(v) == 2 else v[0].name
        progs.append(Program(clean_name(base), [Keygroup(LOW_KEY, HIGH_KEY, 0, zones_for(v), _env_for(v))]))
    return progs
