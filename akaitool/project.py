"""Save / load an editable disc project (.akproj = zip: manifest.json + one raw int16 file per sample)."""
import json
import zipfile
from dataclasses import asdict

import numpy as np

from .akai import Keygroup, Loop, Program, Sample, VolumeModel, Zone

VERSION = 1


def save_project(path, volumes):
    man = dict(version=VERSION, volumes=[])
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        for vi, vm in enumerate(volumes):
            v = dict(name=vm.name, samples=[], programs=[])
            for si, (nm, s) in enumerate(vm.samples.items()):
                d = dict(name=nm, rate=s.rate, root=s.root, cents=s.cents, loop_mode=s.loop_mode, start=s.start,
                         end=s.end, bandwidth=s.bandwidth, source=s.source,
                         loops=[asdict(l) for l in s.loops], file='pcm/%d_%d.raw' % (vi, si))
                z.writestr(d['file'], np.ascontiguousarray(s.pcm, '<i2').tobytes())
                v['samples'].append(d)
            for p in vm.programs:
                v['programs'].append(dict(name=p.name, midi_prog=p.midi_prog, polyphony=p.polyphony, loudness=p.loudness,
                                          keygroups=[dict(lo=k.lo, hi=k.hi, tune=k.tune, env=k.env, xfade=k.xfade,
                                                          zones=[asdict(zz) for zz in k.zones]) for k in p.keygroups]))
            man['volumes'].append(v)
        z.writestr('manifest.json', json.dumps(man, indent=1))


def load_project(path):
    vols = []
    with zipfile.ZipFile(path) as z:
        man = json.loads(z.read('manifest.json'))
        for v in man['volumes']:
            vm = VolumeModel(v['name'])
            for d in v['samples']:
                pcm = np.frombuffer(z.read(d['file']), '<i2').copy()
                vm.samples[d['name']] = Sample(d['name'], pcm, d['rate'], d['root'], d['cents'],
                                               [Loop(**l) for l in d['loops']], d['loop_mode'], d['start'], d['end'],
                                               d['bandwidth'], d.get('source', ''))
            for p in v['programs']:
                kgs = [Keygroup(k['lo'], k['hi'], k['tune'], [Zone(**zz) for zz in k['zones']], k['env'], k['xfade'])
                       for k in p['keygroups']]
                vm.programs.append(Program(p['name'], kgs, p['midi_prog'], p['polyphony'], p['loudness']))
            vols.append(vm)
    return vols
