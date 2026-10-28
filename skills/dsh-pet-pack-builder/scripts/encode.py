#!/usr/bin/env python3
"""绿幕 MP4 → VP9 + Alpha 的 webm（dsh-pet 宠物包素材）。

每帧流程：抠像 → 颜色外渗 → （必要时补透明边到 16:9）→ VP9 编码。

三条不能改的约束（都是踩出来的）：
  1. 不做缩放。输出保持源分辨率，让浏览器下采样。先缩小再放大是两层损失。
  2. 颜色外渗只写透明区。不透明像素必须逐像素保留原值——用「模糊结果 ÷ 模糊 alpha」
     反算会顺带把整个角色模糊一遍（细节掉 58%，这是「糊」的真正原因）。
  3. yuva420p + libvpx-vp9，alpha 才真正写进 webm。

用法：
    python encode.py --src <素材目录> --out <动画目录> [--profile source-profile.json]
                     [--clips 待机 点击] [--crf 20] [--max-width 1280] [--no-watermark]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import keyer  # noqa: E402
from envcheck import ensure_ffmpeg  # noqa: E402
from analyze import probe  # noqa: E402


def box_blur_nd(x: np.ndarray, r: int) -> np.ndarray:
    """任意通道数的可分离盒式模糊。"""
    if r <= 0:
        return x
    k = 2 * r + 1
    for axis in (0, 1):
        n = x.shape[axis]
        pad = [(0, 0)] * x.ndim
        pad[axis] = (r, r)
        p = np.pad(x, pad, mode='edge')
        c = np.cumsum(p, axis=axis, dtype=np.float32)
        zs = list(c.shape)
        zs[axis] = 1
        c = np.concatenate([np.zeros(zs, np.float32), c], axis=axis)
        hi = [slice(None)] * c.ndim
        lo = [slice(None)] * c.ndim
        hi[axis] = slice(k, k + n)
        lo[axis] = slice(0, n)
        x = (c[tuple(hi)] - c[tuple(lo)]) / k
    return x


def bleed(rgba: np.ndarray, iters: int = 5, r: int = 2) -> np.ndarray:
    """把不透明像素的颜色扩散进透明区，且**只改透明区**。

    VP9 的 alpha 是 4:2:0：色度平面不参与 alpha，会跟邻近透明像素平均。透明区若留黑或
    绿幕色，轮廓外就会混出一圈暗边/绿边。扩散之后色度下采样吃到的都是角色自己的颜色。
    """
    a = rgba[..., 3].astype(np.float32) / 255.0
    acc = rgba[..., :3].astype(np.float32) * a[..., None]
    w = a.copy()
    for _ in range(iters):
        acc = np.dstack([box_blur_nd(acc[..., i], r) for i in range(3)])
        w = box_blur_nd(w, r)
    filled = acc / np.maximum(w, 1e-3)[..., None]

    t = np.clip((a - 0.12) / 0.60, 0.0, 1.0)[..., None]     # a>=0.72 原样, a<=0.12 全用外渗色
    rgb = filled * (1.0 - t) + rgba[..., :3].astype(np.float32) * t
    rgb = np.where((w < 1e-3)[..., None], rgba[..., :3], rgb)  # 够不到的远处保持原值

    out = rgba.copy()
    out[..., :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    return out


def deglass(rgba: np.ndarray) -> np.ndarray:
    """去掉「半透明道具上的灰绿脏块」（伞面、泡泡、玻璃板上的透过绿幕的绿）。

    这类像素的共同点：G 略高于 R/B、饱和很低（看着是灰的）、亮度不低——抠像时因为
    饱和度没到绿幕阈值被留下来，成品里就是一块块像脏污的灰绿。它们的本质仍是背景，
    直接归零 alpha，颜色由外渗补上邻近的塑料/玻璃色。
    """
    r, g, b = (rgba[..., i].astype(np.int16) for i in range(3))
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1), 0.0)
    val = mx / 255.0
    m = (g > r + 7) & (g > b + 7) & (sat < 0.13) & (val > 0.42)
    out = rgba.copy()
    out[..., 3] = np.where(m, 0, out[..., 3])
    return out


def pad_to_16x9(rgba: np.ndarray) -> np.ndarray:
    """补透明边到 16:9（宠物按 宽:高 = 16:9 显示，比例不对会被拉伸）。"""
    h, w = rgba.shape[:2]
    target = 16 / 9
    if abs(w / h - target) < 0.01:
        return rgba
    if w / h > target:
        nh, nw = int(round(w / target / 2)) * 2, w
    else:
        nh, nw = h, int(round(h * target / 2)) * 2
    canvas = np.zeros((nh, nw, 4), np.uint8)
    y0, x0 = (nh - h) // 2, (nw - w) // 2
    canvas[y0:y0 + h, x0:x0 + w] = rgba
    return canvas


def encode_clip(ff: str, src: Path, dst: Path, prof: keyer.Profile, W: int, H: int,
                fps: float, crf: int, cpu_used: int, max_width: int | None,
                patch=None, deglass_on: bool = False) -> int:
    """patch(frame_rgba) -> frame_rgba：抠像后、编码前的额外处理（如镜面玻璃重建）。"""
    read = [ff, '-loglevel', 'error', '-i', str(src),
            '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-']
    p_in = subprocess.Popen(read, stdout=subprocess.PIPE)

    # 先按源帧抠像，再决定输出几何（补边 / 限宽）
    canvas_w, canvas_h = W, H
    if abs(W / H - 16 / 9) >= 0.01:
        canvas_w, canvas_h = (int(round(H * 16 / 9 / 2)) * 2, H) if W / H < 16 / 9 else (W, int(round(W * 9 / 16 / 2)) * 2)
    out_w, out_h = canvas_w, canvas_h
    if max_width and canvas_w > max_width:
        out_w = max_width
        out_h = int(round(canvas_h * max_width / canvas_w / 2)) * 2

    vf = []
    if (out_w, out_h) != (canvas_w, canvas_h):
        vf.append(f'scale={out_w}:{out_h}:flags=area')

    cmd = [ff, '-y', '-loglevel', 'error',
           '-f', 'rawvideo', '-pix_fmt', 'rgba', '-s', f'{canvas_w}x{canvas_h}',
           '-r', str(fps), '-i', '-',
           '-an', '-c:v', 'libvpx-vp9', '-pix_fmt', 'yuva420p',
           '-b:v', '0', '-crf', str(crf),
           '-deadline', 'good', '-cpu-used', str(cpu_used),
           '-row-mt', '1', '-tile-columns', '2',
           '-lag-in-frames', '25', '-auto-alt-ref', '1']
    if vf:
        cmd += ['-vf', ','.join(vf)]
    cmd.append(str(dst))
    p_out = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    fs = W * H * 3
    n, t0 = 0, time.time()
    while True:
        buf = b''
        while len(buf) < fs:
            chunk = p_in.stdout.read(fs - len(buf))
            if not chunk:
                break
            buf += chunk
        if len(buf) < fs:
            break
        rgb = np.frombuffer(buf, np.uint8).reshape(H, W, 3)
        frame = keyer.key_frame(rgb, prof)
        if deglass_on:
            frame = deglass(frame)
        if patch is not None:
            frame = patch(frame)
        frame = pad_to_16x9(frame)
        p_out.stdin.write(bleed(frame).tobytes())
        n += 1
        if n % 24 == 0:
            print(f'    {n} frames  {time.time() - t0:.0f}s', flush=True)

    p_in.stdout.close()
    p_out.stdin.close()
    rc = p_in.wait() + p_out.wait()
    if rc != 0:
        raise RuntimeError(f'编码失败：{src.name}')
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--out', required=True, help='输出动画目录，如 pet/demo-animation')
    ap.add_argument('--profile', default=None, help='analyze.py 产出的 source-profile.json')
    ap.add_argument('--clips', nargs='*', default=None, help='只处理这些片段（默认全部）')
    ap.add_argument('--crf', type=int, default=20, help='18~24 之间，越小越清晰越大')
    ap.add_argument('--cpu-used', type=int, default=3, help='0 最慢最好，5 最快')
    ap.add_argument('--max-width', type=int, default=1280, help='超过此宽度会等比缩小，0=不限')
    ap.add_argument('--no-watermark', action='store_true', help='素材没有右下角水印时加这个')
    ap.add_argument('--deglass', action='store_true',
                    help='素材里有半透明道具（伞面/泡泡/玻璃），成品出现灰绿脏块时加这个')
    a = ap.parse_args()

    ff = str(ensure_ffmpeg(verbose=False))
    src, out = Path(a.src).resolve(), Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    pdata, prof = {}, keyer.Profile()
    if a.profile and Path(a.profile).exists():
        pdata = json.loads(Path(a.profile).read_text(encoding='utf-8'))
        prof = keyer.Profile.from_dict(pdata.get('keyer') or {})
    if a.no_watermark:
        prof.watermark = False
    if prof.watermark and keyer.load_wm_mask(10, 10, prof.wm_mask) is None:
        print('[!] 没找到水印遮罩，按无水印处理', file=sys.stderr)
        prof.watermark = False

    W = pdata.get('width') or 1280
    H = pdata.get('height') or 720
    fps = pdata.get('fps') or 24.0

    clips = a.clips or [c['name'] for c in pdata.get('clips', [])]
    if not clips:
        from analyze import VIDEO_EXT
        clips = [p.stem for p in sorted(src.iterdir()) if p.suffix.lower() in VIDEO_EXT]

    total = len(clips)
    for i, name in enumerate(clips, 1):
        f = next((p for p in src.iterdir() if p.stem == name), None)
        if f is None:
            print(f'[!] 找不到 {name}，跳过', file=sys.stderr)
            continue
        info = probe(ff, f)
        cw, ch = info['width'] or W, info['height'] or H
        cfps = info['fps'] or fps
        dst = out / f'{name}.webm'
        print(f'[{i}/{total}] {f.name} ({cw}x{ch} @ {cfps}) -> {dst.name}', flush=True)
        n = encode_clip(ff, f, dst, prof, cw, ch, cfps, a.crf, a.cpu_used,
                        a.max_width or None, deglass_on=a.deglass)
        print(f'    done {n} frames, {dst.stat().st_size // 1024} KB', flush=True)

    files = sorted(out.glob('*.webm'))
    mb = sum(p.stat().st_size for p in files) / 1048576
    print(f'\n完成 {len(files)} 段，共 {mb:.1f} MB -> {out}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
