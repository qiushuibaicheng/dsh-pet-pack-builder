#!/usr/bin/env python3
"""半透明道具（镜子/玻璃板/窗）的镜面重建。

为什么需要：镜子里的「玻璃」其实是绿幕透过玻璃的绿。抠像后镜框还在、镜面却空了，
成品是一个空心框；镜中的倒影也变半透明。这条对「绿幕前的玻璃」无解——只能重建。

做法：镜框是不透明闭合轮廓 → 形态学闭运算补细缝 → 填洞得到整块镜面区域 →
      每帧在区域里垫一层浅蓝白磨砂玻璃，绿色像素判为背景清掉，倒影（粉/白）留下。

注意：webm 存的是「直通 alpha」，合成出来的颜色要**除以最终 alpha**。少这一步的话
玻璃会被自己的不透明度压暗（alpha 0.55 的玻璃只剩 45% 亮度，深色壁纸上一片灰）。

用法：
    python glass_prop.py --src <素材目录> --clip <片段名> --out <动画目录> \
        --bbox x0,y0,x1,y1 [--glass-alpha 0.72] [--t 1.0] [--encode]

先不加 --encode 跑一次，看生成的 prev_glass_*.png：镜面里还发绿就调 --glass-alpha，
边缘多出玻璃就收 bbox。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import keyer            # noqa: E402
import encode as enc    # noqa: E402
from envcheck import ensure_ffmpeg  # noqa: E402
from analyze import probe  # noqa: E402

TOP = np.array([226, 240, 249], np.float32)
BOT = np.array([186, 211, 232], np.float32)
CLOSE_R = 26


def _dilate(m, r):
    for _ in range(r):
        p = m.copy()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            p |= np.roll(m, (dy, dx), (0, 1))
        m = p
    return m


def _erode(m, r):
    return ~_dilate(~m, r)


def _fill_holes(m):
    free = ~m
    out = np.zeros_like(m)
    out[0, :], out[-1, :] = free[0, :], free[-1, :]
    out[:, 0], out[:, -1] = free[:, 0], free[:, -1]
    out &= free
    for _ in range(3000):
        prev = out.copy()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            out |= np.roll(out, (dy, dx), (0, 1)) & free
        if np.array_equal(out, prev):
            break
    return ~out


def frame_rgb(ff, src, t, W, H):
    p = subprocess.run([ff, '-loglevel', 'error', '-ss', str(t), '-i', str(src),
                        '-frames:v', '1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'],
                       capture_output=True, check=True)
    return np.frombuffer(p.stdout, np.uint8)[:W * H * 3].reshape(H, W, 3)


def build_region(prof, rgb, bbox, W, H):
    a = keyer.key_frame(rgb, prof)[..., 3]
    closed = _erode(_dilate(a > 128, CLOSE_R), CLOSE_R)
    region = _fill_holes(closed)
    keep = np.zeros((H, W), bool)
    x0, y0, x1, y1 = bbox
    keep[y0:y1, x0:x1] = True
    return _erode(region & keep, 2)


def glass_layer(region, alpha, W, H):
    ys = np.where(region.any(1))[0]
    y0, y1 = int(ys.min()), int(ys.max())
    g = np.linspace(0, 1, y1 - y0 + 1, dtype=np.float32)[:, None, None]
    grad = TOP * (1 - g) + BOT * g
    pad = np.zeros((H, W, 3), np.float32)
    pad[y0:y1 + 1] = np.broadcast_to(grad, (y1 - y0 + 1, W, 3))
    return pad, region.astype(np.float32) * alpha


def _greenish(rgba):
    r, g, b = (rgba[..., i].astype(np.int16) for i in range(3))
    return (g > r + 12) & (g > b + 12)


def composite(rgba, region, grgb, ga):
    fg = rgba[..., :3].astype(np.float32)
    fa = rgba[..., 3].astype(np.float32) / 255.0
    fa = np.where(_greenish(rgba) & region, 0.0, fa)      # 镜里的绿＝背景
    out_a = fa + ga * (1 - fa)
    # 直通 alpha（非预乘）：颜色要除以最终的 alpha，否则玻璃会被自己的不透明度压暗
    premul = fg * fa[..., None] + grgb * ga[..., None] * (1 - fa)[..., None]
    out_rgb = premul / np.maximum(out_a, 1e-3)[..., None]
    res = rgba.copy()
    res[..., :3] = np.where(region[..., None], np.clip(out_rgb, 0, 255), fg).astype(np.uint8)
    res[..., 3] = np.where(region, np.clip(out_a * 255, 0, 255), rgba[..., 3]).astype(np.uint8)
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--clip', required=True, help='片段名（不含扩展名）')
    ap.add_argument('--out', required=True, help='动画输出目录')
    ap.add_argument('--bbox', required=True, help='玻璃所在范围 x0,y0,x1,y1')
    ap.add_argument('--profile', default=None)
    ap.add_argument('--t', type=float, default=1.0, help='取哪一帧标定区域')
    ap.add_argument('--glass-alpha', type=float, default=0.72)
    ap.add_argument('--crf', type=int, default=20)
    ap.add_argument('--encode', action='store_true', help='确认无误后加这个才会真正编码')
    a = ap.parse_args()

    ff = str(ensure_ffmpeg(verbose=False))
    src_dir, out = Path(a.src).resolve(), Path(a.out).resolve()
    f = next((p for p in src_dir.iterdir() if p.stem == a.clip), None)
    if f is None:
        print(f'找不到 {a.clip}', file=sys.stderr)
        return 1
    bbox = tuple(int(v) for v in a.bbox.split(','))

    pdata, prof = {}, keyer.Profile()
    if a.profile and Path(a.profile).exists():
        pdata = json.loads(Path(a.profile).read_text('utf-8'))
        prof = keyer.Profile.from_dict(pdata.get('keyer') or {})
    info = probe(ff, f)
    W, H, fps = info['width'], info['height'], info['fps']

    region = build_region(prof, frame_rgb(ff, f, a.t, W, H), bbox, W, H)
    ys, xs = np.where(region)
    print(f'镜面区域 {region.sum()} px, bbox x[{xs.min()},{xs.max()}] y[{ys.min()},{ys.max()}]')
    grgb, ga = glass_layer(region, a.glass_alpha, W, H)

    for t in (a.t, round(a.t + 3, 2)):
        rgb = frame_rgb(ff, f, t, W, H)
        res = enc.bleed(composite(keyer.key_frame(rgb, prof), region, grgb, ga))
        bg = np.full((H, W, 3), 235, np.uint8)
        al = res[..., 3:4].astype(np.float32) / 255
        vis = (res[..., :3] * al + bg * (1 - al)).astype(np.uint8)
        tint = vis.copy()
        tint[region] = (tint[region] * 0.5 + np.array([210, 40, 40]) * 0.5).astype(np.uint8)
        vis = np.concatenate([vis, tint], axis=1)
        out_png = Path(f'prev_glass_{a.clip}_{t}.png')
        subprocess.run([ff, '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
                        '-s', f'{W * 2}x{H}', '-i', '-', str(out_png)],
                       input=vis.tobytes(), check=True)
        print('preview ->', out_png.resolve())

    if not a.encode:
        print('\n目检没问题后加 --encode 重跑才会导出 webm。')
        return 0

    out.mkdir(parents=True, exist_ok=True)
    dst = out / f'{a.clip}.webm'
    n = enc.encode_clip(ff, f, dst, prof, W, H, fps, a.crf, 3, 1280,
                        patch=lambda fr: composite(fr, region, grgb, ga))
    print(f'done {n} frames, {dst.stat().st_size // 1024} KB -> {dst}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
