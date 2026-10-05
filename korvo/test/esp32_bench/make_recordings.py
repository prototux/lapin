#!/usr/bin/env python3
"""Packs the WAV files of a directory into main/data/recordings.bin for the
benchmark (u32 count, then u32 length + WAV bytes each)."""
import glob, os, struct, sys
fs = sorted(glob.glob(os.path.join(sys.argv[1], "*.wav")))
os.makedirs(os.path.join(os.path.dirname(__file__), "main", "data"), exist_ok=True)
with open(os.path.join(os.path.dirname(__file__), "main", "data", "recordings.bin"), "wb") as o:
    o.write(struct.pack("<I", len(fs)))
    for f in fs:
        d = open(f, "rb").read()
        o.write(struct.pack("<I", len(d)) + d)
print("%d recordings packed" % len(fs))
