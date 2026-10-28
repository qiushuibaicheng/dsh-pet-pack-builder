#!/usr/bin/env python3
"""目检 + 体检。

preview  取一帧走完整管线，叠在棋盘格 / 品红上输出 PNG —— 检查绿边、残影、破洞。
verify   扫全部成品：规格、绿溢、清晰度保留率、体积。

用法：
    python inspect.py preview --src <素材目录> --clip 待机 [--t 1.0] [--bg checker|magenta|white] [--out x.png]
    python inspect.py verify  --out <动画目录> [--src <素材目录>] [--size 500] [--frames 6]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import keyer  # noqa: E402
from analyze import grab, probe  # noqa: E402
from encode import bleed, pad_to_16x9  # noqa: E402
from envcheck import ensure_ffmpeg  # noqa: E402


def load_profile(path: str | None) -> tuple[keyer.Profile, dict]:
    if path and Path(path).exists():
        d = json.loads(Path(path).read_text(encoding='utf-8'))
        return keyer.Profile.from_dict(d.get('keyer') or {}), d
    return keyer.Profile(), {}


def to_png(ff: str, rgba: np.ndarray, dst: Path) -> None:
    h, w = rgba.shape[:2]
    subprocess.run([ff, '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgba',
                    '-s', f'{w}x{h}', '-i', '-', str(dst)],
                   input=np.ascontiguousarray(rgba).tobytes(), check=True)


def checker(w: int, h: int, s: int = 16) -> np.ndarray:
    y, x = np.mgrid[0:h, 0:w]
    c = ((x // s + y // s) % 2) * 40 + 175
    return np.dstack([c, c, c]).astype(np.float32)


def bg_canvas(kind: str, w: int, h: int) -> np.ndarray:
    if kind == 'magenta':
        return np.dstack([np.full((h, w), 224), np.full((h, w), 32), np.full((h, w), 144)]).astype(np.float32)
    if kind == 'white':
        return np.full((h, w, 3), 255, np.float32)
    return checker(w, h)


def composite(rgba: np.ndarray, bg: np.ndarray) -> np.ndarray:
    a = rgba[..., 3:4].astype(np.float32) / 255.0
    out = rgba[..., :3].astype(np.float32) * a + bg * (1 - a)
    return np.dstack([out, np.full(rgba.shape[:2], 255)]).clip(0, 255).astype(np.uint8)


def cmd_preview(a) -> int:
    ff = str(ensure_ffmpeg(verbose=False))
    prof, pdata = load_profile(a.profile)
    W, H = pdata.get('width', 1280), pdata.get('height', 720)
    src = Path(a.src).resolve()
    f = next((p for p in src.iterdir() if p.stem == a.clip), None)
    if f is None:
        print(f'[!] 找不到 {a.clip}', file=sys.stderr)
        return 2
    info = probe(ff, f)
    W, H = info['width'] or W, info['height'] or H
    rgb = grab(ff, f, a.t, W, H)
    if rgb is None:
        print('[!] 取帧失败（时间点超出时长？）', file=sys.stderr)
        return 2
    frame = bleed(pad_to_16x9(keyer.key_frame(rgb, prof)))
    h, w = frame.shape[:2]
    img = composite(frame, bg_canvas(a.bg, w, h))
    dst = Path(a.out or f'preview_{a.clip}_{a.t}_{a.bg}.png')
    to_png(ff, img, dst)
    print(dst.resolve())
    return 0


def cmd_verify(a) -> int:
    ff = str(ensure_ffmpeg(verbose=False))
    out = Path(a.out).resolve()
    files = sorted(out.glob('*.webm'))
    if not files:
        print(f'[!] {out} 里没有 webm', file=sys.stderr)
        return 2

    print(f'{"片段":<10}{"时长":>9}{"分辨率":>11}{"fps":>6}{"alpha":>7}{"体积":>10}{"绿溢>15":>9}')
    total = 0
    for f in files:
        txt = subprocess.run([ff, '-hide_banner', '-i', str(f)],
                             capture_output=True, text=True).stderr
        dur = res = fps = alpha = '?'
        for line in txt.splitlines():
            if 'Duration' in line:
                dur = line.split('Duration:')[1].split(',')[0].strip()
            if 'Stream #0:0' in line:
                m = re.search(r'(\d+)x(\d+)', line)
                if m:
                    res = f'{m.group(1)}x{m.group(2)}'
                m = re.search(r'([\d.]+) fps', line)
                if m:
                    fps = m.group(1)
            if 'alpha_mode' in line:
                alpha = line.split('alpha_mode')[1].strip(' :')
        spill = spill_ratio(ff, f)
        sz = f.stat().st_size
        total += sz
        print(f'{f.stem:<10}{dur:>9}{res:>11}{fps:>6}{alpha:>7}{sz/1024:>8.0f}KB{spill:>8.2f}%')
    print(f'\n合计 {len(files)} 段，{total/1048576:.1f} MB')

    if a.src:
        print(f'\n清晰度保留率（按显示宽 {a.size}px 计，相对无损帧）：')
        prof, pdata = load_profile(a.profile)
        for f in files:
            r = clarity(ff, f, Path(a.src).resolve(), prof, a.size, a.frames)
            if r is None:
                print(f'  {f.stem:<10} 取不到源片段，跳过')
            else:
                print(f'  {f.stem:<10} {r*100:5.1f}%' + ('   <= 偏低' if r < 0.9 else ''))
    return 0


def spill_ratio(ff: str, path: Path, max_frames: int = 48) -> float:
    """全帧扫一遍：绿溢 (g - max(r,b)) > 15 的像素占比。>0.5% 说明边缘没处理干净。"""
    w, h = 640, 360
    p = subprocess.Popen([ff, '-loglevel', 'error', '-i', str(path),
                          '-frames:v', str(max_frames), '-vf', f'scale={w}:{h}',
                          '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
                         stdout=subprocess.PIPE)
    fs = w * h * 3
    hi = tot = 0
    for _ in range(max_frames):
        buf = b''
        while len(buf) < fs:
            c = p.stdout.read(fs - len(buf))
            if not c:
                break
            buf += c
        if len(buf) < fs:
            break
        fr = np.frombuffer(buf, np.uint8).reshape(h, w, 3).astype(np.int16)
        r, g, b = fr[..., 0], fr[..., 1], fr[..., 2]
        m = fr.max(axis=2) > 30
        if m.any():
            sp = g - np.maximum(r, b)
            hi += int(((sp > 15) & m).sum())
            tot += int(m.sum())
    p.stdout.read()          # 排空，避免 ffmpeg 写管道时 broken pipe
    p.stdout.close()
    p.wait()
    return hi / max(tot, 1) * 100


def _fit(ff: str, rgba: np.ndarray, w: int, h: int) -> np.ndarray:
    o = subprocess.run([ff, '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgba',
                        '-s', f'{rgba.shape[1]}x{rgba.shape[0]}', '-i', '-',
                        '-vf', f'scale={w}:{h}:flags=lanczos',
                        '-f', 'rawvideo', '-pix_fmt', 'rgba', '-'],
                       input=np.ascontiguousarray(rgba).tobytes(), capture_output=True).stdout
    return np.frombuffer(o, np.uint8)[:w * h * 4].reshape(h, w, 4)


def _grad(img: np.ndarray, mask: np.ndarray) -> float:
    g = img.astype(np.float32).mean(axis=2)
    mx = mask[:, 1:] & mask[:, :-1]
    my = mask[1:, :] & mask[:-1, :]
    gx = np.abs(np.diff(g, axis=1))[mx]
    gy = np.abs(np.diff(g, axis=0))[my]
    if gx.size + gy.size == 0:
        return 0.0
    return float(gx.mean() + gy.mean())


def clarity(ff: str, webm: Path, src: Path, prof: keyer.Profile,
            size: int, frames: int) -> float | None:
    f = next((p for p in src.iterdir() if p.stem == webm.stem), None)
    if f is None:
        return None
    info = probe(ff, f)
    W, H = info['width'], info['height']
    dur = info['duration'] or 2.0
    dw, dh = size, int(round(size * 9 / 16 / 2)) * 2

    refs, ms = [], []
    for k in range(frames):
        t = dur * (k + 0.5) / frames
        rgb = grab(ff, f, t, W, H)
        if rgb is None:
            continue
        fr = _fit(ff, bleed(pad_to_16x9(keyer.key_frame(rgb, prof))), dw, dh)
        a = fr[..., 3:4].astype(np.float32) / 255.0
        refs.append(fr[..., :3].astype(np.float32) * a + 255 * (1 - a))
        ms.append(fr[..., 3] > 250)
    if not refs:
        return None

    es_ref, es_out = [], []
    for k, (ref, m) in enumerate(zip(refs, ms)):
        for _ in range(2):
            m = m & np.roll(m, 1, 0) & np.roll(m, -1, 0) & np.roll(m, 1, 1) & np.roll(m, -1, 1)
        es_ref.append(_grad(ref, m))
        t = dur * (k + 0.5) / frames
        o = subprocess.run([ff, '-loglevel', 'error', '-ss', f'{t:.3f}', '-i', str(webm),
                            '-frames:v', '1', '-vf', f'scale={dw}:{dh}',
                            '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
                           capture_output=True).stdout
        if len(o) < dw * dh * 3:
            continue
        es_out.append(_grad(np.frombuffer(o, np.uint8)[:dw * dh * 3].reshape(dh, dw, 3).astype(np.float32), m))
    if not es_out:
        return None
    e_ref = float(np.mean(es_ref))
    return (np.mean(es_out) / e_ref) if e_ref > 1e-6 else None


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('preview')
    p.add_argument('--src', required=True)
    p.add_argument('--clip', required=True)
    p.add_argument('--t', type=float, default=1.0)
    p.add_argument('--bg', default='checker', choices=['checker', 'magenta', 'white'])
    p.add_argument('--out', default=None)
    p.add_argument('--profile', default=None)

    v = sub.add_parser('verify')
    v.add_argument('--out', required=True)
    v.add_argument('--src', default=None)
    v.add_argument('--profile', default=None)
    v.add_argument('--size', type=int, default=500, help='宠物实际显示宽度 px')
    v.add_argument('--frames', type=int, default=6)

    a = ap.parse_args()
    return cmd_preview(a) if a.cmd == 'preview' else cmd_verify(a)


if __name__ == '__main__':
    raise SystemExit(main())
