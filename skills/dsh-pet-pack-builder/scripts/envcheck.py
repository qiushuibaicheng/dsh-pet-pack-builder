#!/usr/bin/env python3
"""确保 ffmpeg（含 libvpx-vp9）与 numpy 可用，并把 ffmpeg 路径打印出来。

环境里通常没有 ffmpeg。这里的做法是：优先用现成的，没有再通过 npm 镜像
拉一个静态构建解压到本地缓存目录（不装系统包、不改 PATH）。

用法：
    python envcheck.py            # 人读的输出
    python envcheck.py --json     # 机读，{"ffmpeg": "...", "ok": true}
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

CACHE = Path(os.environ.get('PETPACK_HOME')
             or (Path.home() / '.workbuddy' / 'tools' / 'petpack'))
MIRRORS = ('https://registry.npmmirror.com', 'https://registry.npmjs.org')
PIP_MIRRORS = ('https://pypi.tuna.tsinghua.edu.cn/simple', 'https://pypi.org/simple')


def _pkg_for_this_os() -> str:
    s = platform.system().lower()
    m = platform.machine().lower()
    arch = 'arm64' if m in ('arm64', 'aarch64') else 'x64'
    if s == 'windows':
        return f'@ffmpeg-installer/win32-{arch}'
    if s == 'darwin':
        return f'@ffmpeg-installer/darwin-{arch}'
    return f'@ffmpeg-installer/linux-{arch}'


def _get_json(url: str, timeout: int = 30) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))


def _download(url: str, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=300) as r, open(dst, 'wb') as f:
        shutil.copyfileobj(r, f)


def _probe(ff: Path) -> bool:
    """能不能跑，且带 libvpx-vp9。"""
    try:
        out = subprocess.run([str(ff), '-hide_banner', '-encoders'],
                             capture_output=True, text=True, timeout=60).stdout
        return 'libvpx-vp9' in out
    except Exception:
        return False


def ensure_ffmpeg(verbose: bool = True) -> Path:
    env = os.environ.get('FFMPEG')
    if env and Path(env).exists() and _probe(Path(env)):
        return Path(env)

    found = shutil.which('ffmpeg')
    if found and _probe(Path(found)):
        return Path(found)

    cached = CACHE / 'ffmpeg'
    exe = 'ffmpeg.exe' if platform.system().lower() == 'windows' else 'ffmpeg'
    hit = cached / exe
    if hit.exists() and _probe(hit):
        return hit

    pkg = _pkg_for_this_os()
    if verbose:
        print(f'[env] 未找到可用的 ffmpeg，从 npm 镜像下载 {pkg} …', flush=True)
    last = None
    for mirror in MIRRORS:
        try:
            meta = _get_json(f'{mirror}/{pkg}')
            latest = meta['dist-tags']['latest']
            url = meta['versions'][latest]['dist']['tarball']
            tgz = CACHE / 'ff.tgz'
            _download(url, tgz)
            out_dir = CACHE / 'ff'
            out_dir.mkdir(parents=True, exist_ok=True)
            with tarfile.open(tgz, 'r:gz') as tf:
                try:
                    tf.extractall(out_dir, filter='data')
                except TypeError:      # Python < 3.12 没有 filter 参数
                    tf.extractall(out_dir)
            hit.parent.mkdir(parents=True, exist_ok=True)
            for p in out_dir.rglob(exe):
                shutil.copy2(p, hit)
            hit.chmod(0o755)
            if _probe(hit):
                if verbose:
                    print(f'[env] ffmpeg 就位：{hit}', flush=True)
                return hit
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(
        f'无法自动准备 ffmpeg（{last}）。请手动安装 ffmpeg 并设置环境变量 FFMPEG=<路径>，'
        f'或把 ffmpeg 可执行文件放到 {hit}'
    )


def ensure_numpy() -> None:
    try:
        import numpy  # noqa: F401
        return
    except ImportError:
        pass
    print('[env] 安装 numpy …', flush=True)
    for src in PIP_MIRRORS:
        r = subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', 'numpy', '-i', src],
                           capture_output=True, text=True)
        if r.returncode == 0:
            return
    raise RuntimeError('numpy 安装失败，请手动 pip install numpy')


def main() -> int:
    as_json = '--json' in sys.argv
    ensure_numpy()
    ff = ensure_ffmpeg(verbose=not as_json)
    if as_json:
        print(json.dumps({'ok': True, 'ffmpeg': str(ff)}))
    else:
        v = subprocess.run([str(ff), '-hide_banner', '-version'],
                           capture_output=True, text=True).stdout.splitlines()
        print(f'ffmpeg : {ff}')
        print(f'version: {v[0] if v else "?"}')
        import numpy
        print(f'numpy  : {numpy.__version__}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
