"""Command line: list / export / make."""
import argparse
import os
import sys

from . import akai, audio, export, image, mapping


def cmd_info(a):
    img = akai.Image(a.image)
    for i, v in enumerate(img.volumes):
        print('[%d] %s  (%s, partition %s): %d programs, %d samples' % (
            i, v.name, 'S3000' if v.vtype == 3 else 'S1000', v.part.letter, len(v.program_files), len(v.sample_files)))
        if a.programs:
            for f in v.program_files:
                print('      P %s' % f['name'])
    img.close()


def cmd_export(a):
    img = akai.Image(a.image)
    vols = [v for i, v in enumerate(img.volumes) if a.volume is None or i == a.volume]
    for v in vols:
        vm = v.load()
        out = os.path.join(a.out, export.safe_name(vm.name))
        os.makedirs(out, exist_ok=True)
        if a.format == 'wav':
            for nm, s in vm.samples.items():
                export.export_wav(s, os.path.join(out, export.safe_name(nm) + '.wav'))
        elif a.format == 'sfz':
            for p in vm.programs:
                export.export_sfz(vm, p, out)
        else:
            export.export_sf2(vm, vm.programs, os.path.join(out, export.safe_name(vm.name) + '.sf2'))
        print('exported', vm.name, '->', out)
    img.close()


def cmd_make(a):
    files = []
    for root, _, names in os.walk(a.folder):
        for n in sorted(names):
            if n.lower().endswith(audio.AUDIO_EXT):
                files.append(os.path.join(root, n))
    if not files:
        sys.exit('no audio files found in ' + a.folder)
    vm = akai.VolumeModel(akai.clean_name(a.name or os.path.basename(os.path.abspath(a.folder))))
    for f in files:
        ss = audio.make_samples(f, target_rate=a.rate, stereo=a.stereo, normalize=a.normalize)
        mapping.add_samples(vm, ss)
        print('  +', os.path.basename(f), '->', ', '.join('%s (root %s)' % (s.name, audio.note_name(s.root)) for s in ss))
    smp = list(vm.samples.values())
    if a.layout == 'multi':
        vm.programs = [mapping.multisample(vm.name, smp)]
    elif a.layout == 'drums':
        vm.programs = [mapping.drumkit(vm.name, smp)]
    else:
        vm.programs = mapping.one_each(smp)
    size, parts = image.write_image(a.out, [vm], a.mode)
    print('wrote %s: %.1f MB, %d partition(s)' % (a.out, size / 1e6, parts))


def main(argv=None):
    ap = argparse.ArgumentParser(prog='akaitool', description='Akai S1000/S2000/S3000 sampler CD tool')
    sub = ap.add_subparsers(dest='cmd')
    p = sub.add_parser('info', help='list volumes of a disc image')
    p.add_argument('image'); p.add_argument('-p', '--programs', action='store_true'); p.set_defaults(fn=cmd_info)
    p = sub.add_parser('export', help='export a disc image to wav / sfz / sf2')
    p.add_argument('image'); p.add_argument('out'); p.add_argument('-f', '--format', choices=('wav', 'sfz', 'sf2'), default='sfz')
    p.add_argument('-v', '--volume', type=int); p.set_defaults(fn=cmd_export)
    p = sub.add_parser('make', help='build an Akai disc image from a folder of audio files')
    p.add_argument('folder'); p.add_argument('out'); p.add_argument('-n', '--name')
    p.add_argument('-l', '--layout', choices=('multi', 'drums', 'each'), default='multi')
    p.add_argument('-m', '--mode', choices=('S1000', 'S3000'), default='S1000')
    p.add_argument('-r', '--rate', type=int, choices=(44100, 22050), default=44100)
    p.add_argument('-s', '--stereo', choices=('split', 'mono'), default='split')
    p.add_argument('--normalize', action='store_true'); p.set_defaults(fn=cmd_make)
    a = ap.parse_args(argv)
    if not getattr(a, 'fn', None):
        from . import gui
        return gui.main()
    a.fn(a)


if __name__ == '__main__':
    main()
