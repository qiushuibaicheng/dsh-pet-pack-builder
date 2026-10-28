---
name: dsh-pet-pack-builder
description: 把绿幕拍摄或 AI 生成的 MP4 转成 dsh-pet 桌面宠物所需的透明 webm 动画，并生成可直接加载的 pet pack 配置。当用户提供一段或多段绿幕角色视频、要求制作桌面宠物 / 桌宠 / 宠物包 / pet pack，或要求把 mp4 转成带 alpha 的 webm 时使用。
agent_created: true
---

# dsh-pet 宠物包生成器

把「绿幕 + 角色」的 MP4 批量转成 dsh-pet 认的素材（VP9 + Alpha 的 webm），
再自动生成配置，产出可直接放进 `$DSH_HOME/dsh-pet/pet/` 的宠物包。

## 产出结构

```
<pet>/
├── <前缀>-config.json           # JSONC，带注释
└── <前缀>-animation/
    ├── 待机.webm                # 1280x720 / 源帧率 / VP9 yuva420p
    ├── 点击.webm
    └── ...
```

目录名必须与配置文件名前缀一致（插件按 `<前缀>-animation` 找素材，查不到直接 404，
**不会**回落到内置素材）。文件名就是动画名，可以用中文。

## 流程

所有命令都从 skill 根目录执行，`<素材目录>` 里放 mp4。

### 1. 准备环境（只需一次）

```bash
python scripts/envcheck.py
```

自动装 numpy，并在本机没有 ffmpeg 时从 npm 镜像拉一个静态构建到
`~/.workbuddy/tools/petpack/ffmpeg/`。若已有 ffmpeg，设 `FFMPEG=<路径>` 即可复用。

### 2. 扫描素材

```bash
python scripts/analyze.py --src <素材目录> --out source-profile.json
```

输出分辨率、帧率、绿幕色相、角色包围盒、是否检测到水印，以及自动标定后的抠像参数。
把它当诊断报告读：绿幕色相应是 70~185°；「触边片段」说明有特效碰到画面边缘（正常现象，
但要确认不是角色被裁）。

### 3. 目检抠像（必做，不要跳）

```bash
python scripts/check.py preview --src <素材目录> --clip 待机 --t 1.0 --bg checker --profile source-profile.json
python scripts/check.py preview --src <素材目录> --clip 待机 --t 1.0 --bg magenta --profile source-profile.json
```

- `--bg checker`：棋盘格，看角色内部有没有被误挖的透明洞、地面阴影清没清、水印遮罩有没有留灰斑。
- `--bg magenta`：品红，专门看轮廓外有没有灰白光晕或绿边——棋盘格会把这类瑕疵藏起来。

至少抽查「待机」和一段带道具/特效的动作。发现问题按下面的「参数调优」处理，改完重跑到干净为止。

### 4. 转码

```bash
python scripts/encode.py --src <素材目录> --out <pet>/<前缀>-animation \
    --profile source-profile.json
```

- 默认全量转。只重跑某几段用 `--clips 待机 点击`。
- 每段约 2 分钟（720p / 100 帧 / cpu-used 3）。批量时用后台任务跑，别卡在前台。
- 清晰度与体积的旋钮是 `--crf`（默认 20）。18 更清晰更大，26 省四成体积、肉眼几乎无差。
- 素材没有右下角水印时加 `--no-watermark`。
- 素材里有**半透明道具**（伞面、泡泡、玻璃杯）时加 `--deglass`，见下。

**道具上有灰绿脏块 → `--deglass`**

绿幕透过半透明塑料/玻璃的绿，饱和度低（看着发灰），抠像不会被判成绿幕，
成品里就是一块块像脏污的灰绿。这类像素的本质仍是背景（G 略高于 R/B、低饱和、亮度不低），
`--deglass` 把它们 alpha 归零，颜色由外渗补上邻近的塑料色，只对该片段生效。

**镜子/玻璃板整块被抠空 → `scripts/glass_prop.py`**

绿幕前的镜面玻璃抠完只剩框、倒影变半透明，这是「玻璃后面是绿幕」的无解情况，只能重建：

```bash
python scripts/glass_prop.py --src <素材目录> --clip <片段名> --out <pet>/<前缀>-animation \
    --bbox x0,y0,x1,y1              # 玻璃所在范围
```

先不加 `--encode` 跑一次，读生成的 `prev_glass_*.png`（左边成品、右边红区＝识别到的镜面）：
镜面里还发绿就调 `--glass-alpha`（默认 0.72，深色壁纸下更实），边缘多出玻璃就收 `--bbox`。
满意了加 `--encode` 才真正导出。原理是闭运算补上镜框细缝 → 填洞取整块镜面 → 每帧垫一层
浅蓝白玻璃，绿色判为背景清掉、倒影（粉/白）原样保留。

**玩偶眼睛变成透明洞 → 默认已修，不用开关**

角色怀里抱的玩偶，眼睛常画成**青色/薄荷绿**（实测 RGB 141,183,179、色相 175°、饱和度 0.14），
色相正好落在绿幕窗口里 → 被正常抠掉 → 成品里是个能看见背后内容的小洞。
`keyer` 默认救回这类像素，判据是「两条证据同时成立」：

1. 颜色偏青（`b >= g-12` 且 `g > r+10` 且 `g > 120`）——绿幕是黄绿，蓝低得多（实测 `b-g ≈ -35`）；
2. 周围一圈基本不透明（半径 14px 内 ≥78% 不透明）——说明它在角色内部，不是背景。

只看颜色会误伤：有的素材背景色相到 156°，本身就很青（实测单帧 4500 个像素会被误保护）。
所以要关掉就用 `Profile(protect_teal=False)`；误伤了就往紧调 `teal_inside`（0.78 → 0.85）。

### 5. 体检

```bash
python scripts/check.py verify --out <pet>/<前缀>-animation --src <素材目录> --profile source-profile.json
```

合格线：

| 指标 | 期望 |
|---|---|
| `alpha` | `1`（有真实透明通道） |
| 分辨率 | 1280x720（或源分辨率，只要 ≥ 显示宽度） |
| 绿溢 >15 占比 | < 0.5% |
| 清晰度保留率 | > 90% |

清晰度掉到 60% 以下说明角色主体被模糊了，检查是否改动过 `encode.bleed()`。

### 6. 生成配置

```bash
python scripts/make_config.py --anim-dir <pet>/<前缀>-animation \
    --name 小粉 --id xiaofen --persona "性格描述…"
```

按文件名关键词自动分池（见下），校验权重合计 = 100、所有引用的动画都有对应 webm，
然后写 `<前缀>-config.json`。生成后会自己解析一遍，解析不过会直接报错。

想精确控制就传 `--map mapping.json`：

```json
{"idle": ["待机"], "walk": ["原地走路"], "drag": ["委屈"],
 "click": ["点击"], "greet": ["问侯"], "sleep": ["睡觉"],
 "think": ["思考"], "success": ["成功庆祝"], "play": ["摘星星"]}
```

### 7. 安装（可选）

```bash
python scripts/make_config.py ... --install
```

复制到 `$DSH_HOME/dsh-pet/pet/`（默认 `~/.dsh/dsh-pet/pet/`），与内置宠物并存。

## 动作命名约定

自动分池靠文件名关键词，命中即归类：

| 语义 | 关键词 | 落到哪个池 |
|---|---|---|
| idle | 待机 / idle / 呼吸 / 待命 | `animations.idle`（必须有一个） |
| walk | 走路 / walk / 散步 / 移动 / 滑行 / 伞 | `moves.actions` |
| drag | 委屈 / drag / 挣扎 / 生气 | `drag`（被拎起来时） |
| click | 点击 / click / 戳 | `clicks` |
| greet | 问候 / 打招呼 / 挥手 | `clicks` |
| sleep | 睡觉 / 困 / 打盹 | 随机小动作 + `events.balance` 末档 |
| think | 思考 / 疑问 / 好奇 | 随机小动作 + `events.workStatus[0]` |
| success | 成功 / 庆祝 / 开心 | 随机小动作 + `events.workStatus[4]` |
| play | 玩 / 摘 / 星星 / 抱枕 / 雪花 / 泡泡 / 照镜 | 随机小动作 |
| 其它 | 未命中 | 并入随机小动作分类 |

命名越贴近上表，生成的配置越像样。缺「待机」会告警并借用其它动作；缺「走路」则宠物不会走动。

## 参数调优

改 `source-profile.json` 的 `keyer` 段，再重跑 encode：

| 症状 | 改什么 |
|---|---|
| 背景没抠干净，残留大片绿 | 调大 `hue_lo`/`hue_hi` 覆盖范围，或降低 `sat_min` |
| 角色身上出现透明洞（白衣、白发被吃） | 调大 `core_force`，或降低 `sat_min` 的严格度 |
| 地面一圈灰蓝雾 | 调整 `floor_y`（地面起始比例）、放宽 `shadow_hue` |
| 轮廓外一圈绿边 | 调大 `rim_px`；确认没有用 alpha 阈值判定边缘 |
| 右下角残留灰斑 | 素材水印位置与内置遮罩不符，改 `wm_mask` 指向自定义 `.npz`，或 `--no-watermark` |

`core_guard` / `core_force` / `rim_px` 都按 720p 调好，其它分辨率会按高度等比缩放，不用手改。

## 硬约束（改动前先读 references/pipeline.md）

1. **不缩放**。输出保持源分辨率，让浏览器下采样。先缩小再放大是两层损失，成品会发虚。
2. **颜色外渗只写透明区**。不透明像素必须逐像素保留原值，否则整个角色被模糊一遍。
3. **必须 16:9**。插件按 `宽度 × 9/16` 定高度，比例不对会被拉伸；脚本会自动补透明边。
4. **必须 yuva420p + libvpx-vp9**，否则 webm 里没有 alpha，宠物会带黑底。

## 生效与缓存

装好后重启 DSH 或刷新页面即可看到新宠物。

换素材后**必须**处理缓存，否则看到的还是旧文件：插件给素材写死
`cache-control: max-age=3600` 且没有 ETag。

- 桌面端：删 `%APPDATA%/dsh-pet-electron-helper/` 下的 `Cache`、`Code Cache`、`GPUCache`，再重启。
- 网页端：Ctrl+Shift+R 强制刷新。

## 常见故障

| 现象 | 原因 |
|---|---|
| 宠物带黑底方块 | 编码没用 `yuva420p`，或源帧 alpha 全是 255（抠像没生效） |
| 宠物被拉伸变形 | 素材不是 16:9，且没走 `pad_to_16x9` |
| 动画 404 / 宠物不出现 | 目录名与配置前缀不一致，或动画名与文件名不一致 |
| 宠物不动、只有待机 | `moves.actions` 为空（没识别出走路），或 `fixedEnabled: true` |
| 伞面/泡泡上有灰绿脏块 | 绿幕透过半透明材质的绿，加 `--deglass` 重编该片段 |
| 右下角有随帧闪烁的灰白残影 | 水印字形被当成主体、给自己撑起了保护圈。keyer 已内置「薄＋灰白/发绿」清理；若仍有，检查 `sat_min` 是否过低、或换一份遮罩 |
| 镜子里是空心框、倒影发灰 | 镜面玻璃被抠空，用 `scripts/glass_prop.py` 重建镜面 |
| 玩偶眼睛/发饰变成透明洞（能看见背后） | 青色小件被当成绿幕。keyer 默认已救回；仍丢就调低 `teal_inside`（0.78→0.72），误保护就调高 |
| 报 `inspect` 模块错误 | 有文件叫 `inspect.py` 覆盖了标准库，本 skill 的脚本已改名为 `check.py` |

## 参考

- `references/pipeline.md` — 每一步为什么这么写，以及踩过的坑
- `references/petpack-schema.md` — pet pack 配置字段完整说明
