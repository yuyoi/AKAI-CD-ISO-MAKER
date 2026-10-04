# Akai CD ISO Maker

> **UNTESTED ON REAL HARDWARE.** Discs written by this tool have not yet been loaded on a real Akai sampler. Reading real CDs is tested; writing is not. Use at your own risk and please report results (issue template: Hardware test report). This banner is removed once a disc is confirmed working.

Made by JunkSmithWizard (JSW) together with Claude (Anthropic's AI). Claude wrote the code and did the format analysis
(partition header and checksum, FAT, directory, sample / program layouts). JSW supplied the idea, the test discs and the
samplers. **Hardware testing of written discs is still to do** (see Status).

Build, read, edit and export **Akai S1000 / S2000 / S3000 sampler CDs** on a modern PC.

- **Make CDs**: import wav / mp3 / flac / ogg / aiff (anything ffmpeg reads), set root notes and loops, auto-map
  into programs, write a disc image the samplers read.
- **Loops**: loop finder (zero-crossing + waveform match), click-to-set loop points, crossfade baking, WAV `smpl` loops imported.
- **Read existing CDs**: open any Akai CD image (S1000 and S3000 volumes), edit it, rebuild it.
- **Export**: WAV (root + loop in `smpl` chunk), **SFZ**, **SoundFont 2**.
- Desktop GUI (Tk) plus a command line.

S1000, S2000 and S3000 share one disc format family, so the default output (S1000 format) loads on all of them.
An experimental S3000 file-type mode is included.

## Run

```
pip install -r requirements.txt
python -m akaitool            # GUI
python -m akaitool make my_samples out.iso -n "MY DISC"
python -m akaitool info "Some Akai CD.iso" -p
python -m akaitool export "Some Akai CD.iso" out_folder -f sfz     # or wav / sf2
```

`ffmpeg` on PATH is used for formats libsndfile cannot decode.

## Burning

The image is a raw Akai disc (no ISO 9660), just like a ripped commercial sample CD. Burn it as an **image**, data CD,
mode 1 (2048 bytes/sector), disc-at-once, finalised: ImgBurn "Write image file to disc", CDBurnerXP "Burn ISO image",
`cdrecord -dao -data image.iso`. Do **not** add the .iso to a normal data-disc project.

## Status

- Reading: tested on 9 real commercial S1000 CD images (all parse; rebuilt volumes read back sample-for-sample identical).
- Writing: structure matches real discs where we could compare (partition header and checksum rule, FAT, directory,
  program / keygroup / sample headers) and round-trips through our reader. **It has not yet been tested on real sampler hardware.**
  If you try it on an S1000 / S2000 / S3000 please report what happens.
- S3000 mode: file types and 2-cluster directories follow the public notes; no S3000 disc was available to compare.
- Not covered: S900 disks, `.akp` programs, S3000-only program parameters (new programs get default filter / LFO values;
  envelopes are kept), effects files, sampler memory limits (warnings are shown for 2 MB / 8 MB).
- Key numbers use C4 = 60 (MIDI standard). Akai displays one octave lower (their middle C is C3). Playable range is key 24-127.
- Sample tune is stored as the sampler does (1/256 semitone). Samples recorded away from the 44.1 kHz base rate are
  exported with the equivalent playback rate so pitch stays correct.

See [docs/FORMAT.md](docs/FORMAT.md) for the disc format notes.

## Credits

License: MIT (see `LICENSE`). Made by JunkSmithWizard (JSW) together with Claude (Anthropic's AI), 2026. See [AUTHORS](AUTHORS).
Format details were reverse engineered from real discs and cross-checked with the public
[akaitools S3000 format notes](https://lsnl.jp/~ohsaki/software/akaitools/S3000-format.html) by Hiroyuki Ohsaki.
Test discs are the author's own and are not distributed.

Not affiliated with or endorsed by Akai Professional / inMusic. This package contains no Akai software or sample data.
