"""Round trip: parse a real disc volume, rebuild it with our writer, read it back and compare."""
import os, sys, tempfile, glob
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import numpy as np
from akaitool import akai, image

EXT = ('.iso', '.img', '.bin', '.tao', '.nrg')


def find_disc():
    """AKAI_TEST_ISO, then argv[1] (file or folder), then a search of ~/Downloads and ./, then ask in a dialog"""
    cand = os.environ.get('AKAI_TEST_ISO') or (sys.argv[1] if len(sys.argv) > 1 else None)
    roots = [cand] if cand else [os.path.join(os.path.expanduser('~'), 'Downloads'), os.getcwd()]
    for _ in range(2):
        for r in roots:
            if os.path.isfile(r):
                return r
            for dp, _dn, fn in os.walk(r):
                for f in sorted(fn):
                    p = os.path.join(dp, f)
                    if f.lower().endswith(EXT) and os.path.getsize(p) > 1 << 20:
                        try:
                            im = akai.Image(p)
                            im.close()
                            return p
                        except ValueError:
                            pass
        if cand or not sys.stdin.isatty():
            return None
        try:                                    # nothing found: ask for a folder
            import tkinter
            from tkinter import filedialog
            tkinter.Tk().withdraw()
            d = filedialog.askdirectory(title='Folder with an Akai CD image (.iso)')
        except Exception:
            return None
        if not d:
            return None
        roots, cand = [d], d
    return None


def main():
    DISC = find_disc()
    if not DISC:
        print('SKIPPED: no Akai disc image found (set AKAI_TEST_ISO or pass a file/folder)')
        return
    print('using', DISC)
    img = akai.Image(DISC)
    v = img.volumes[0]
    vm = v.load()
    print('source volume', vm.name, len(vm.programs), 'programs', len(vm.samples), 'samples')
    for mode in ('S1000', 'S3000'):
        out = os.path.join(tempfile.gettempdir(), 'rt_%s.iso' % mode)
        size, nparts = image.write_image(out, [vm], mode)
        print(mode, 'wrote', size, 'bytes', nparts, 'partitions')
        img2 = akai.Image(out)
        v2 = img2.volumes[0]
        vm2 = v2.load()
        assert vm2.name == vm.name, (vm2.name, vm.name)
        assert len(vm2.programs) == len(vm.programs) and set(vm2.samples) == set(vm.samples)
        for nm, s in vm.samples.items():
            t = vm2.samples[nm]
            assert np.array_equal(s.pcm, t.pcm), nm
            assert (s.root, s.loop_mode, s.bandwidth) == (t.root, t.loop_mode, t.bandwidth), nm
            assert abs(s.cents - t.cents) < 0.5, (nm, s.cents, t.cents)
            assert [(l.start, l.end) for l in s.loops] == [(l.start, l.end) for l in t.loops], nm
        for p, q in zip(vm.programs, vm2.programs):
            assert p.name == q.name and len(p.keygroups) == len(q.keygroups), p.name
            for a, b in zip(p.keygroups, q.keygroups):
                assert (a.lo, a.hi, a.tune) == (b.lo, b.hi, b.tune)
                assert [(z.sample, z.lovel, z.hivel, z.tune, z.pan, z.playback) for z in a.zones] == \
                       [(z.sample, z.lovel, z.hivel, z.tune, z.pan, z.playback) for z in b.zones], (p.name,)
        print(mode, 'round trip OK')
        img2.close()
    img.close()


if __name__ == '__main__':
    main()
