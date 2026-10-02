#!/usr/bin/env python3
"""Темп трека по 30-секундному превью Deezer — для светомузыки (packages/light_music.yaml).

У Deezer bpm почти у всех треков 0, а превью (mp3) есть. ffmpeg и numpy уже
есть в контейнере HA. Печатает темп (BPM) или 0, если превью нет.
  light_music_bpm.py <deezer track id>
  light_music_bpm.py --selftest
"""
import json, subprocess, sys, urllib.request
import numpy as np

SR, HOP, WIN = 11025, 128, 1024


def tempo(pcm):
    """BPM по моно-сигналу SR Гц: спектральный поток → автокорреляция."""
    n = (len(pcm) - WIN) // HOP
    if n < 200:
        return 0.0
    frames = np.lib.stride_tricks.as_strided(pcm, (n, WIN), (pcm.strides[0] * HOP, pcm.strides[0]))
    spec = np.log1p(100 * np.abs(np.fft.rfft(frames * np.hanning(WIN), axis=1)))
    flux = np.maximum(0, np.diff(spec, axis=0)).sum(axis=1)
    flux -= np.convolve(flux, np.ones(32) / 32, "same")
    flux = np.maximum(0, flux)
    ac = np.correlate(flux, flux, "full")[len(flux) - 1:]
    fps = SR / HOP
    lags = np.arange(int(fps * 60 / 200), int(fps * 60 / 50) + 1)
    bpm = 60 * fps / lags
    # Prior around 120 BPM: an octave off (60 or 240) loses to the main beat.
    score = ac[lags] * np.exp(-0.5 * np.log2(bpm / 120) ** 2)
    k = int(np.argmax(score))
    if 0 < k < len(lags) - 1:   # parabolic refine between lags
        a, b, c = score[k - 1:k + 2]
        k += 0.5 * (a - c) / (a - 2 * b + c) if a - 2 * b + c else 0
    return round(60 * fps / (lags[0] + k), 1)


def main(tid):
    t = json.load(urllib.request.urlopen(f"https://api.deezer.com/track/{int(tid)}", timeout=10))
    if not t.get("preview"):
        return 0.0
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", t["preview"], "-ac", "1", "-ar", str(SR),
                          "-f", "s16le", "-"], capture_output=True, timeout=30, check=True).stdout
    return tempo(np.frombuffer(raw, np.int16).astype(np.float32) / 32768)


def selftest():
    for bpm in (72, 110, 150):   # клик на каждый удар + шум
        t = np.arange(SR * 20) / SR
        x = 0.05 * np.random.default_rng(1).standard_normal(len(t))
        for b in np.arange(0, 20, 60 / bpm):
            i = int(b * SR); x[i:i + 200] += np.sin(np.arange(200) / 3)
        got = tempo(x.astype(np.float32))
        assert abs(got - bpm) < 2, (bpm, got)
    print("selftest ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        selftest()
    else:
        try:
            print(main(sys.argv[1]))
        except Exception as e:   # нет сети, нет превью — светомузыка возьмёт темп по жанру
            print(0.0); print(repr(e), file=sys.stderr)
