"""Build a disc from generated audio (wav, flac), read it back, export SFZ + SF2 + WAV."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import numpy as np
import soundfile as sf

from akaitool import akai, audio, export, image, mapping


def tone(f, sec=1.5, sr=48000):
    t = np.arange(int(sec * sr)) / sr
    return ((np.sin(2 * np.pi * f * t) + 0.3 * np.sin(4 * np.pi * f * t)) * np.exp(-t * 0.6) * 0.5).astype(np.float32)


def main():
    d = tempfile.mkdtemp()
    sf.write(os.path.join(d, 'Pad_C3.wav'), tone(130.81), 48000)
    sf.write(os.path.join(d, 'Pad_G3.wav'), tone(196.0), 44100)
    sf.write(os.path.join(d, 'Stereo_A3.wav'), np.stack([tone(220), tone(223)], 1), 44100)
    sf.write(os.path.join(d, 'x.flac'), tone(440, 1.0, 22050), 22050)
    vm = akai.VolumeModel('TEST')
    for f in sorted(os.listdir(d)):
        mapping.add_samples(vm, audio.make_samples(os.path.join(d, f)))
    roots = {s.name: s.root for s in vm.samples.values()}
    assert roots['PAD C3'] == 48 and roots['PAD G3'] == 55 and roots['STEREO A3 L'] == 57 and roots['X'] == 69, roots
    s = vm.samples['PAD C3']
    lp, score = audio.find_loop(s.pcm, s.play_rate)
    assert lp is not None and score > 0.9, (lp, score)
    s.loops, s.loop_mode = [lp], akai.LM_LOOP_HOLD
    s.pcm = audio.crossfade_loop(s.pcm, lp, 800)
    vm.programs = [mapping.multisample('PADS', list(vm.samples.values()))]
    assert not [m for lv, m in image.validate([vm]) if lv == 'error']
    iso = os.path.join(d, 't.iso')
    image.write_image(iso, [vm])
    img = akai.Image(iso)
    v = img.volumes[0].load()
    assert set(v.samples) == set(vm.samples) and v.samples['PAD C3'].loops[0].start == lp.start
    assert abs(v.samples['PAD C3'].play_rate - 44100) < 1
    out = os.path.join(d, 'out')
    os.makedirs(out)
    export.export_sfz(v, v.programs[0], out)
    export.export_sf2(v, v.programs, os.path.join(out, 'a.sf2'))
    data, rate = sf.read(os.path.join(out, 'samples', 'PAD C3.wav'))
    assert rate == 44100 and len(data) == s.n
    print('make test OK')


if __name__ == '__main__':
    main()
