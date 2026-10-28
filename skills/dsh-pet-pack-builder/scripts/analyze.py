#!/usr/bin/env python3
"""扫描素材目录：分辨率 / 帧率 / 时长 / 绿幕色 / 角色包围盒 / 水印，写出 source-profile.json。

这份 profile 是后面 encode 的输入，里面已经包含自动标定过的抠像参数。

用法：
    python analyze.py --src <素材目录> [--out source-profile.json] [--samples 4]
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
from envcheck import ensure_ffmpeg  # noqa: E402

VIDEO_EXT = ('.mp4', '.mov', '.mkv', '.webm', '.m4v', '.avi')


def probe(ff: str, path: Path) -> dict:
    txt = subprocess.run([ff, '-hide_banner', '-i', str(path)],
                         capture_output=True, text=True).stderr
    info = {'duration': None, 'fps': None, 'width': None, 'height': None}
    for line in txt.splitlines():
        if 'Duration:' in line:
            t = line.split('Duration:')[1].split(',')[0].strip()
            try:
                hh, mm, ss = t.split(':')
                info['duration'] = round(int(hh) * 3600 + int(mm) * 60 + float(ss), 3)
            except ValueError:
                pass
        if 'Stream #0:0' in line and 'Video' in line:
            m = re.search(r'(\d{2,5})x(\d{2,5})', line)
            if m:
                info['width'], info['height'] = int(m.group(1)), int(m.group(2))
            m = re.search(r'([\d.]+) fps', line) or re.search(r'([\d.]+) tbr', line)
            if m:
                info['fps'] = round(float(m.group(1)), 3)
    return info


def grab(ff: str, path: Path, t: float, w: int, h: int) -> np.ndarray:
    out = subprocess.run([ff, '-loglevel', 'error', '-ss', f'{t:.3f}', '-i', str(path),
                          '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
                         capture_output=True).stdout
    need = w * h * 3
    if len(out) < need:
        return None
    return np.frombuffer(out, np.uint8)[:need].reshape(h, w, 3)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True, help='素材目录（放 mp4）')
    ap.add_argument('--out', default='source-profile.json')
    ap.add_argument('--samples', type=int, default=4, help='每段取几帧做统计')
    a = ap.parse_args()

    ff = str(ensure_ffmpeg(verbose=False))
    src = Path(a.src).resolve()
    files = sorted(p for p in src.iterdir() if p.suffix.lower() in VIDEO_EXT)
    if not files:
        print(f'[!] {src} 里没有找到视频文件', file=sys.stderr)
        return 2

    clips, first = [], None
    for f in files:
        i = probe(ff, f)
        if not i['width']:
            print(f'[!] 读不出规格，跳过：{f.name}', file=sys.stderr)
            continue
        frames = int(round(i['duration'] * i['fps'])) if i['duration'] and i['fps'] else None
        clips.append({'name': f.stem, 'file': f.name, 'duration': i['duration'],
                      'fps': i['fps'], 'frames': frames,
                      'width': i['width'], 'height': i['height']})
        first = first or i

    sizes = {(c['width'], c['height']) for c in clips}
    if len(sizes) > 1:
        print(f'[!] 素材分辨率不一致：{sizes}，将按第一段处理', file=sys.stderr)
    W, H = clips[0]['width'], clips[0]['height']

    # ── 采样：绿幕色 + 包围盒 + 水印 ───────────────────────────
    bg_frames, masks = [], []
    prof0 = keyer.Profile()
    for c in clips:
        path = src / c['file']
        dur = c['duration'] or 2.0
        ts = [dur * (k + 0.5) / a.samples for k in range(max(1, a.samples))]
        got = []
        for t in ts:
            fr = grab(ff, path, t, W, H)
            if fr is None:
                continue
            bg_frames.append(fr)
            m = keyer.key_frame(fr, prof0)[..., 3] > 160
            got.append(m)
            if len(masks) < 40:
                masks.append(m)
        if got:
            u = np.zeros((H, W), bool)
            for m in got:
                u |= m
            ys, xs = np.where(u)
            if len(xs):
                c['bbox'] = [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]
                c['touches_edge'] = bool(c['bbox'][0] <= 2 or c['bbox'][1] <= 2
                                         or c['bbox'][2] >= W - 3 or c['bbox'][3] >= H - 3)

    bg = keyer.detect_background(bg_frames)

    # 抠像参数：默认值只在「检测到的绿幕超出默认窗口」时放宽，绝不收窄
    over: dict = {}
    if bg['hue_center'] is not None:
        lo = (bg['hue_center'] - 55) % 360
        hi = (bg['hue_center'] + 55) % 360
        if lo < 200 and hi < 200:
            over['hue_lo'] = round(min(60.0, max(40.0, lo)), 1)
            over['hue_hi'] = round(max(188.0, min(215.0, hi)), 1)
        if bg['sat_median'] and bg['sat_median'] < 0.12:
            over['sat_min'] = round(max(0.05, bg['sat_median'] * 0.7), 3)
    over = {k: v for k, v in over.items() if v != getattr(keyer.Profile(), k)}

    boxes = [c['bbox'] for c in clips if c.get('bbox')]
    if boxes:
        bbox = [min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes)]
    else:
        bbox = None
    edge_clips = [c['name'] for c in clips if c.get('touches_edge')]

    # 水印：内置遮罩区域在这些帧里是否稳定存在「亮且几乎无饱和」的像素（白字）
    wm = keyer.load_wm_mask(H, W)
    wm_hit = 0.0
    if wm is not None and bg_frames:
        hits = []
        for fr in bg_frames[:12]:
            hue, sat, val = keyer.hsv(fr)
            cand = (sat < 0.18) & (val > 0.50) & wm
            hits.append(cand.sum() / max(int(wm.sum()), 1))
        wm_hit = float(np.median(hits))

    out = {
        'generated_by': 'analyze.py',
        'src': str(src),
        'width': W,
        'height': H,
        'fps': clips[0]['fps'],
        'clips': clips,
        'background': bg,
        'bbox_union': bbox,
        'edge_clips': edge_clips,
        'watermark_detected': wm_hit > 0.35,
        'watermark_fill_ratio': round(wm_hit, 3),
        'keyer': over,
    }
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')

    print(f'素材目录 : {src}')
    print(f'片段      : {len(clips)} 段')
    print(f'规格      : {W}x{H} @ {clips[0]["fps"]} fps')
    print(f'绿幕色相  : {bg["hue_center"]}°  饱和度中位 {bg["sat_median"]}')
    print(f'包围盒并集: {bbox}')
    if edge_clips:
        print(f'触边片段  : {", ".join(edge_clips)}（特效/道具碰到画面边缘，不是抠像错误）')
    print(f'水印      : {"检测到" if out["watermark_detected"] else "未检测到"}'
          f'（区域内亮不饱和像素占比 {wm_hit:.0%}）')
    print(f'抠像参数  : {over or "全部默认"}')
    print(f'已写入    : {Path(a.out).resolve()}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
