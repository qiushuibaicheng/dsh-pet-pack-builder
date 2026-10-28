"""绿幕帧抠像核心：rgb24 → rgba（带真实 alpha）。

处理顺序（每一步都对应一个踩过的坑，改动前先读 references/pipeline.md）：
  1. HSV 判定绿幕：饱和度是主判据，色相只是护栏（白衣服 sat 低，不会被吃）
  2. 地面阴影补判：绿幕压暗后色相漂到 180~210°，普通绿区规则只给半透明，会留灰蓝雾
  3. 水印逐像素遮罩：右下角「豆包AI生成」那种静态白字
  4. alpha 收缩：软阈值，去掉半透明残留
  5. 绿溢压制：按「离轮廓多远」定权重，不能用 alpha 阈值（否则描边被 core 保护圈豁免）
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import numpy as np

BASE_H = 720  # 所有 px 级参数按 720p 调好，其它分辨率等比缩放

ASSETS = Path(__file__).resolve().parent.parent / 'assets'
WM_MASK_FILE = ASSETS / 'wm_mask_1280x720.npz'


@dataclass
class Profile:
    """抠像参数。默认值针对「AI 生成的灰绿幕 + 右下角水印」这一类素材调好。"""

    # ── 绿幕判定 ──────────────────────────────────────────────
    hue_lo: float = 60.0        # 色相下界（度）。低于此值偏红/橙/黄
    hue_hi: float = 188.0       # 色相上界。高于此值偏蓝
    feather: float = 10.0       # 色相边界羽化宽度
    sat_min: float = 0.085      # 饱和度下限：主判据
    val_min: float = 0.06       # 明度下限：纯黑不算背景

    # ── 地面阴影（压暗的绿幕偏青）────────────────────────────
    floor_y: float = 0.828      # 画面下方这条带才算「地面」，比例
    shadow_hue: tuple = (148.0, 218.0)
    shadow_sat_min: float = 0.12
    shadow_val_max: float = 0.80

    # ── 掩膜半径（720p 像素，运行时按 H/720 缩放）──────────
    core_guard: int = 11        # 保护环：只用于「阴影/水印别误伤角色」
    core_force: int = 2         # 内芯：强制不透明，防止头发/裙子里被挖洞
    rim_px: int = 5             # 绿溢带宽

    # ── 角色内部的青色小件（玩偶眼睛、发饰）──────────────────
    protect_teal: bool = True
    teal_sat_max: float = 0.36  # 青色小件饱和度上限（再高就是绿幕了）
    teal_ring: int = 14         # 判定「在角色内部」的环形半径
    teal_inside: float = 0.78   # 环内不透明像素比例超过此值才算内部

    # ── alpha 软阈值 ────────────────────────────────────────
    alpha_offset: float = 0.28
    alpha_scale: float = 0.50

    # ── 水印 ────────────────────────────────────────────────
    watermark: bool = True
    wm_mask: Optional[str] = None   # 自定义遮罩 .npz，None = 用内置 1280x720 那张

    extra: dict = field(default_factory=dict)

    def scaled(self, h: int) -> 'Profile':
        """按目标高度缩放所有 px 级参数，返回新对象。"""
        if h == BASE_H:
            return self
        k = h / BASE_H
        d = asdict(self)
        for key in ('core_guard', 'core_force', 'rim_px', 'teal_ring'):
            d[key] = max(1, int(round(d[key] * k)))
        return Profile(**d)

    @classmethod
    def from_dict(cls, d: dict) -> 'Profile':
        if not d:
            return cls()
        known = {f for f in cls.__dataclass_fields__}
        kw = {k: v for k, v in d.items() if k in known}
        if 'shadow_hue' in kw:
            kw['shadow_hue'] = tuple(kw['shadow_hue'])
        p = cls(**kw)
        p.extra = {k: v for k, v in d.items() if k not in known}
        return p


# ────────────────────────────── 基础工具 ──────────────────────────────

def hsv(rgb: np.ndarray):
    """rgb uint8 (H,W,3) → hue(度) / sat / val，全部 float32。"""
    f = rgb.astype(np.float32) / 255.0
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    delta = mx - mn
    nz = delta > 1e-6
    d = np.where(nz, delta, 1.0)
    hue = np.where(
        (mx == r) & nz, 60.0 * (((g - b) / d) % 6.0),
        np.where((mx == g) & nz, 60.0 * (((b - r) / d) + 2.0),
                 np.where((mx == b) & nz, 60.0 * (((r - g) / d) + 4.0), 0.0)),
    )
    sat = np.where(mx > 0, delta / np.maximum(mx, 1e-6), 0.0)
    return hue.astype(np.float32), sat.astype(np.float32), mx.astype(np.float32)


def box_blur(mask: np.ndarray, r: int) -> np.ndarray:
    """可分离盒式模糊（单通道浮点），用于羽化 / 膨胀 / 近似距离变换。"""
    if r < 1:
        return mask
    k = 2 * r + 1
    p = np.pad(mask, ((r, r), (0, 0)), mode='edge')
    c = np.cumsum(p, axis=0, dtype=np.float64)
    c = np.vstack([np.zeros((1, mask.shape[1])), c])
    out = (c[k:] - c[:-k]) / k
    p = np.pad(out, ((0, 0), (r, r)), mode='edge')
    c = np.cumsum(p, axis=1, dtype=np.float64)
    c = np.hstack([np.zeros((mask.shape[0], 1)), c])
    return ((c[:, k:] - c[:, :-k]) / k).astype(np.float32)


def _resize_nearest(mask: np.ndarray, h: int, w: int) -> np.ndarray:
    sh, sw = mask.shape[:2]
    if (sh, sw) == (h, w):
        return mask.astype(bool)
    m = mask.astype(np.uint8)
    # 缩小时先膨胀，否则水印的细笔画会被抽掉
    if h < sh or w < sw:
        r = max(1, int(np.ceil(max(sh / h, sw / w))))
        m = (box_blur(m.astype(np.float32), r) > 0.001)
    yi = np.clip((np.arange(h) * sh / h).astype(int), 0, sh - 1)
    xi = np.clip((np.arange(w) * sw / w).astype(int), 0, sw - 1)
    return m[yi][:, xi].astype(bool)


def load_wm_mask(h: int, w: int, path: Optional[str] = None,
                 dilate: int = 3) -> Optional[np.ndarray]:
    """载入水印逐像素遮罩，缩放到 (h,w) 并向外膨胀一点。

    膨胀是为了吃掉水印字形的抗锯齿边缘：那圈灰绿过渡像素饱和度刚好卡在
    sat_min 之上，不膨胀就会在右下角留一小片灰斑。
    """
    p = Path(path) if path else WM_MASK_FILE
    if not p.exists():
        return None
    try:
        if p.suffix == '.npz':
            m = np.load(p)['mask']
        else:
            m = np.load(p)
        m = _resize_nearest(m, h, w)
        if dilate > 0:
            m = box_blur(m.astype(np.float32), dilate) > 0.0005
        return m.astype(bool)
    except Exception:
        return None


# ────────────────────────────── 主流程 ──────────────────────────────

def key_frame(rgb: np.ndarray, prof: Optional[Profile] = None) -> np.ndarray:
    """rgb24 (H,W,3) uint8 → rgba (H,W,4) uint8。透明区 RGB 归零，颜色外渗交给 bleed()。"""
    prof = prof or Profile()
    h, w = rgb.shape[:2]
    p = prof.scaled(h)

    hue, sat, val = hsv(rgb)
    lo, hi = p.hue_lo - p.feather, p.hue_hi + p.feather
    in_hue = np.clip((hue - lo) / p.feather, 0, 1) * np.clip((hi - hue) / p.feather, 0, 1)
    bg = (sat >= p.sat_min) & (val >= p.val_min) & (hue >= lo) & (hue <= hi)
    alpha = np.where(bg, 1.0 - in_hue, 1.0).astype(np.float32)

    # 角色主体种子：必须排除「暗青」（残留阴影）和水印，否则它们会把自己认成主体而自我豁免
    dark_teal = (hue >= 148.0) & (hue <= 226.0) & (sat >= 0.10) & (val < 0.62)
    seed = (alpha > 0.97) & ~dark_teal

    wm = load_wm_mask(h, w, p.wm_mask) if p.watermark else None
    wm_zone = washed = greenish = None
    if wm is not None:
        # 水印附近（遮罩 + 20px 余量：遮罩取自参考视频，和实际素材可能差几像素）
        wm_zone = box_blur(wm.astype(np.float32), max(8, int(round(20 * h / BASE_H)))) > 0.0005
        r8, g8, b8 = (rgb[..., i].astype(np.int16) for i in range(3))
        washed = (sat < 0.20) & (val > 0.32)                        # 灰白笔画
        greenish = (g8 > r8 + 6) & (g8 > b8 + 6) & (val > 0.30)     # 压在绿幕上的边缘：HSV 饱和度很低，但绿偏色明显
        # 水印区里「灰白/发绿」的东西不许当主体，否则它会给自己撑起 guard/solid 保护圈，
        # 结果就是右下角留一圈随帧闪烁的残影（这是待机动画闪烁的根因）。
        seed &= ~(wm_zone & (washed | greenish))
        seed &= ~wm

    guard = box_blur(seed.astype(np.float32), p.core_guard) > 0.35   # 大保护环，只管别误伤
    solid = box_blur(seed.astype(np.float32), p.core_force) > 0.35   # 小内芯，强制不透明

    # 地面投影
    floor = np.zeros((h, w), bool)
    floor[int(h * p.floor_y):, :] = True
    shadow_bg = (
        floor & ~guard
        & (hue >= p.shadow_hue[0]) & (hue <= p.shadow_hue[1])
        & (sat >= p.shadow_sat_min) & (val < p.shadow_val_max)
    )
    alpha = np.where(shadow_bg, 0.0, alpha)

    # 水印
    if wm is not None:
        alpha = np.where(wm & ~guard, 0.0, alpha)

    # 边缘收缩（软阈值）：去掉最后半像素的绿边
    soft = box_blur(alpha, max(1, int(round(2 * h / BASE_H))))
    alpha = np.clip((soft - p.alpha_offset) / max(p.alpha_scale, 1e-3), 0.0, 1.0)
    alpha = np.where(solid, 1.0, alpha)

    # 水印残影最后一刀：放在 solid 之后，否则会被「内芯强制不透明」重新提起来。
    # 只清「薄 + 灰白/发绿」的，厚实的白衣服、镜框由 thick 保住。
    # thick 必须用「去完阴影之后」的 alpha 算：地面投影是一整片不透明，算进去的话
    # 整个画面下沿都会被判成实体，宏块级的灰绿噪声就清不掉了。
    if wm is not None:
        r_th = max(4, int(round(12 * h / BASE_H)))
        core = box_blur((alpha > 0.9).astype(np.float32), r_th) > 0.9995
        thick = box_blur(core.astype(np.float32), r_th) > 0.0005
        # 画面最外一圈（24px）无条件清：那里本来就是背景，偶尔整块宏块闪灰绿，
        # 这种块又方又厚会被 thick 误判成实体。
        b = max(6, int(round(24 * h / BASE_H)))
        border = np.zeros((h, w), bool)
        border[:, -b:] = True
        border[-b:, :] = True
        alpha = np.where(wm_zone & (washed | greenish) & (~thick | border), 0.0, alpha)

    # 青色小件保护：玩偶眼睛、发饰这类「偏青的小色块」正好落在绿幕色相窗口里，
    # 会被正常抠掉——成品里就成了一个能看见背后内容的透明小洞（角色怀里兔子玩偶的
    # 眼睛就是这么丢的）。只在两条证据同时成立时才救回来：
    #   1) 颜色偏青（蓝不比绿低多少）——绿幕是黄绿，蓝低得多（实测背景 b-g ≈ -35）；
    #   2) 周围一圈基本不透明——说明它在角色内部，不是背景。
    # 只看颜色会误伤：有的素材背景色相到 156°，本身就偏青，直接按颜色保护会糊成一片。
    if p.protect_teal:
        r8, g8, b8 = (rgb[..., i].astype(np.int16) for i in range(3))
        teal = (b8 >= g8 - 12) & (g8 > 120) & (g8 > r8 + 10) & (sat < p.teal_sat_max)
        if teal.any():
            ring = box_blur((alpha > 0.5).astype(np.float32), max(5, p.teal_ring))
            protect = teal & (ring >= p.teal_inside)
            if protect.any():
                alpha = np.where(protect, 1.0, alpha)

    out = np.empty((h, w, 4), np.uint8)
    out[..., :3] = rgb
    out[..., 3] = (alpha * 255).astype(np.uint8)

    # ── 绿溢压制 ──────────────────────────────────────────────
    # 按「离轮廓多远」定权重。用 alpha 阈值判定边缘是错的：带绿溢的描边是不透明像素，
    # 而它恰好落在 core 保护圈里，永远进不了处理条件。
    outside = (alpha < 0.02).astype(np.float32)
    frac = box_blur(outside, max(1, int(round(p.rim_px * h / BASE_H))))
    weight = np.clip(frac * 2.6, 0.0, 1.0)
    weight[alpha <= 0] = 0.0
    px = out[..., :3].astype(np.float32)
    r, g, b = px[..., 0], px[..., 1], px[..., 2]
    excess = g - np.maximum(r, b)
    take = np.clip(excess, 0.0, None) * weight
    if take.any():
        px[..., 1] = g - take
        out[..., :3] = px.clip(0, 255).astype(np.uint8)

    # 透明像素不保留绿幕色（否则 4:2:0 色度下采样会把绿混回边缘）
    out = np.where(alpha[..., None] > 0.02, out, 0).astype(np.uint8)
    return out


# ────────────────────────────── 背景自标定 ──────────────────────────────

def detect_background(frames) -> dict:
    """从若干帧的边框像素估计绿幕色相范围。只会在默认值基础上「放宽」，不会收窄。"""
    hs, ss, vs = [], [], []
    for f in frames:
        h, w = f.shape[:2]
        band = max(2, int(h * 0.04))
        parts = [f[:band].reshape(-1, 3), f[-band:].reshape(-1, 3),
                 f[:, :band].reshape(-1, 3), f[:, -band:].reshape(-1, 3)]
        px = np.concatenate(parts, 0)
        hue, sat, val = hsv(px[None, ...])
        hs.append(hue.ravel()); ss.append(sat.ravel()); vs.append(val.ravel())
    hue = np.concatenate(hs); sat = np.concatenate(ss); val = np.concatenate(vs)
    m = (sat >= 0.05) & (val >= 0.05)
    info = {'green_screen': False, 'hue_center': None, 'sat_median': None, 'rgb': None}
    if m.sum() < 500:
        return info
    hh, ssv = hue[m], sat[m]
    ang = np.deg2rad(hh)
    center = float(np.rad2deg(np.arctan2(np.sin(ang).mean(), np.cos(ang).mean())) % 360)
    info['hue_center'] = round(center, 1)
    info['sat_median'] = round(float(np.median(ssv)), 3)
    info['green_screen'] = 70.0 <= center <= 185.0
    return info
