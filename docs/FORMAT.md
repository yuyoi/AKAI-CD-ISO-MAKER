# Akai disc format notes (S1000 / S2000 / S3000 CD-ROM images)

All integers little endian. Verified on commercial S1000 CDs; S3000 items from the public akaitools notes.

## Image
Run of **partitions**, 0x3C00000 bytes (60 MB, 0x1E00 clusters) apart; only the last may be shorter.
Some rips (.nrg) have a 0x4B000 byte lead-in before partition A. Cluster = 0x2000 bytes. Files never span partitions.

## Partition header (clusters 0..2)
| offset | |
|---|---|
| 0x0000 | u16 cluster count (0x1E00 full) |
| 0x0004 | 97 x u16: running sum of 0x0D05 (0x0D05, 0x1A0A, ...), up to 0x00C5 |
| 0x00C6 | u16 = (0xB9D5 + cluster count) & 0xFFFF |
| 0x00C8 | 2F 00 |
| 0x00CA | volume list, 100 x 16 B: name[12], u16 type (1 = S1000, 3 = S3000), u16 first cluster (directory) |
| 0x070A | FAT, u16 per cluster: 0 free, 0x4000 system/directory, 0x8000 2nd directory cluster (S3000), 0xC000 end of file, else next cluster |

Clusters 0-3 are system; the first volume's directory is cluster 3, its first file starts at cluster 4.
Volumes are packed one after another inside a partition.

## Directory
S1000: one cluster, 126 x 24 B. S3000: two clusters, up to ~512 entries.
`name[12] 20 20 20 20 type u8 size u24 start u16 tag u16` (tag is a per-disc constant, e.g. 0x041E).
Types: 0x70 S1000 program, 0x73 S1000 sample, 0xF0 / 0xF3 S3000 program / sample, 0x64 drum, 0x71 QL, 0x78 effects.
Names use the charset `0-9`, space, `A-Z`, `#+-.` coded 0..40 (so a space is 10).

## Sample (0x73 / 0xF3)
150 byte header (S3000: 192) then int16 PCM.

| offset | field |
|---|---|
| 0 | 3 |
| 1 | bandwidth (1 = 44.1k base rate, 0 = 22.05k) |
| 2 | root note (MIDI) |
| 3 | name[12] |
| 0x0F | 0x80 (rate valid) |
| 0x10 / 0x11 | active loop count / first loop |
| 0x13 | loop mode: 0 loop in release, 1 loop until release, 2 no loop, 3 play to end |
| 0x14 | s16: pitch offset in 1/256 semitone (48 kHz material carries +1.5 semitones, compensating the fixed 44.1k playback) |
| 0x16 | u32 RAM address (ignored) |
| 0x1A / 0x1E / 0x22 | u32 length (samples) / play start / play end |
| 0x26 | 8 loops x 12 B: u32 loop end ("at"), u16 fraction, u32 loop length, u16 time (9999 = hold) |
| 0x88 / 0x8A | u16 stereo partner (0xFFFF) / u16 nominal rate |

## Program (0x70 / 0xF0)
Header 150 B (S3000 192): byte 0 = 1, 1..2 offset of first keygroup, 3 name[12], 0x0F MIDI program, 0x11 polyphony,
0x19 loudness, 0x2A keygroup count. Then keygroups of 150 B back to back.

Keygroup: 0 = 2, 1..2 offset of next, 3/4 key low/high, 5/6 tune (cents, semitones), 7 filter, 12-15 amp ADSR (0-99),
20-23 filter ADSR, 30 velocity crossfade, 31 zone count (4), then four 24 B zones from byte 34:
`name[12] lovel hivel tune_cents tune_semitones loud filter pan playback(0 as sample .. 4 play to end) xfade[2] 0xFFFF`.
