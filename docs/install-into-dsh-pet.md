# 怎么把素材放进 dsh-pet 插件

这篇只讲「文件放哪里、名字怎么起、为什么放了还是不动」。转码怎么跑见根目录 README。

---

## 一、目录长这样

```
$DSH_HOME/                       # Windows 默认 C:\Users\<你>\.dsh，macOS/Linux 是 ~/.dsh
└─ dsh-pet/
   ├─ main-config.jsonc          # 内置主宠物（鲸鱼娘）的配置，别动
   ├─ main-animation/
   │  └─ webm/                   # 主宠物素材，注意这一层有 webm 子目录
   └─ pet/                       # ← 你自己的宠物都放这里，可以放多个
      ├─ xiaofen-config.json     # 配置（JSONC，允许注释）
      └─ xiaofen-animation/      # 素材目录，名字 = 配置前缀 + "-animation"
         ├─ 待机.webm
         ├─ 点击.webm
         └─ …
```

三条对应关系，错一条就 404：

| 位置 | 规则 |
|---|---|
| `xiaofen-config.json` | 文件名前缀 `xiaofen` 是**素材根**，决定插件去哪个目录找素材 |
| `xiaofen-animation/` | 必须叫 `<素材根>-animation`，**扁平**放 webm，没有 `webm/` 子目录 |
| `待机.webm` | 文件名就是**动画名**，配置里引用的每个名字都要有同名文件 |

> 素材目录和主宠物不一样：主宠物是 `main-animation/webm/xxx.webm`，pet pack 是
> `pet/<前缀>-animation/xxx.webm`。两者的查找链是各自独立的，查不到也不会互相回落。

实例 `id` 与素材根**不必相同**：同一个种类可以配多只实例（复制 `pets` 数组里的对象改 `id`），
它们共享同一套动画。

---

## 二、手装（3 条命令）

```sh
# 1. 进到仓库根目录
cd dsh-pet-pack-builder

# 2. 建目录、拷素材、拷配置
mkdir -p ~/.dsh/dsh-pet/pet
cp -r example/webm ~/.dsh/dsh-pet/pet/xiaofen-animation
cp example/xiaofen-config.json ~/.dsh/dsh-pet/pet/

# 3. 重启 DSH（或刷新页面）
```

Windows Git Bash / PowerShell 都能跑；`~` 不可用时换成 `C:\Users\<你>\.dsh`。

### 或者一条命令自动化

```sh
python skills/dsh-pet-pack-builder/scripts/make_config.py \
    --anim-dir build/xiaofen-animation \
    --name 小粉 --id xiaofen \
    --install
```

`--install` 做的事就是上面第 2 步：把 `<id>-animation/` 和 `<id>-config.json` 复制到
`$DSH_HOME/dsh-pet/pet/`。设置 `DSH_HOME` 环境变量可以指到别的位置。

---

## 三、配置怎么写

`example/xiaofen-config.json` 是一份带注释的完整示例，关键就三段：

```jsonc
{
  "whisperPrompt": "你是主人桌面上的Q版粉发小姑娘……",   // 人设：碎碎念 / 对话的系统提示词

  "pets": [
    {
      "name": "小粉", "id": "xiaofen", "size": 500,      // 宽度 px，高度自动 = size × 9/16
      "display": "both",                                 // web / desktop / both / none
      "position": { "corner": "bottom-left", "marginX": 32, "marginY": 24 }
    }
  ],

  "animations": {
    "idle": ["待机"],                                    // 必须有，且只能引用存在的动画
    "turn": [],                                          // 没有转身素材就留空
    "drag": ["委屈"],                                    // 被拎起来时
    "clicks": ["点击", "问侯"],                          // 点按时的随机回应
    "moves": {
      "default": { "minDist": 60, "maxDist": 220, "leadSec": 0.35, "tailSec": 0.35 },
      "actions": [{ "name": "原地走路" }, { "name": "乘伞滑行" }]
    },
    "categories": [                                      // 随机小动作，weight 合计 + idle + move = 100
      { "id": "小动作", "weight": 30, "actions": ["睡觉", "兔子抱枕堡垒", "梦境泡泡"] },
      { "id": "玩耍",   "weight": 26, "actions": ["摘星星", "成功庆祝", "制造雪花"] }
    ],
    "events": {                                          // 事件档位，数组顺序 = 档位顺序
      "balance": ["成功庆祝", "问侯", "待机", "思考", "委屈", "睡觉"],
      "whisper": ["思考", "问侯", "委屈"],
      "workStatus": ["思考", "原地走路", "摘星星", "待机", "成功庆祝", "委屈"]
    }
  },

  "animationWeights": { "idle": 34, "turn": 0, "move": 10 }
}
```

校验规则（`make_config.py` 会替你查一遍）：

- `animationWeights.idle + turn + move + Σcategories[].weight === 100`
- 每个被引用的动画名，都能在 `<前缀>-animation/` 里找到同名 `.webm`
- `idle` 必须有；`turn` 可以空（空 = 不会自己翻朝向）；`moves.actions` 空 = 不会走动
- 文件是 JSONC，允许 `//` 注释和尾逗号，插件用 jsonc 解析

字段全表见 `skills/dsh-pet-pack-builder/references/petpack-schema.md`。

---

## 四、装完怎么验

1. **重启 DSH**（桌面端要重启进程，网页端强刷）。日志里不该出现 `404` 或 `unknown-animation`。
2. 宠物出现在 `position.corner` 指定的角落；点它、拖它、等它自己走，看有没有动画切错。
3. 需要点名验证某一段时，可以直接调接口让它播（端口以启动日志里的 `dsh web: http://127.0.0.1:<端口>` 为准）：

```sh
TOKEN=<启动日志里的 token>
curl -X POST "http://127.0.0.1:3080/dsh-pet-7340/anim?pet=xiaofen&surface=desktop&token=$TOKEN" \
     -H "content-type: application/json" -d '{"name":"待机"}'
# {"ok":true,"playbackId":"pb-..."} → 桌宠立刻播这一段
```

`surface` 可选 `web` / `desktop` / `both`。返回 `ok:false, reason:"unknown-animation"` 说明
配置里没这个名字；返回 `ok:true` 但画面上没反应，多半是 webm 文件缺失或路径不对（渲染端 404）。

---

## 五、改了素材不动？先清缓存

插件给素材写死 `cache-control: max-age=3600`，且没有 ETag / Last-Modified ——
浏览器会老老实实用一小时前的旧文件。

| 端 | 怎么清 |
|---|---|
| 桌面端（Electron 小窗） | 关掉 DSH，删 `%APPDATA%\dsh-pet-electron-helper\` 下的 `Cache`、`Code Cache`、`GPUCache`（还有 `DawnGraphiteCache`、`DawnWebGPUCache` 一起删更保险），再启动 |
| 网页端 | `Ctrl+Shift+R` 强制刷新（macOS `Cmd+Shift+R`） |

PowerShell 一行：

```powershell
$p = Join-Path $env:APPDATA 'dsh-pet-electron-helper'
'Cache','Code Cache','GPUCache','DawnGraphiteCache','DawnWebGPUCache' |
  ForEach-Object { Remove-Item (Join-Path $p $_) -Recurse -Force -ErrorAction SilentlyContinue }
```

---

## 六、故障对照表

| 现象 | 原因 |
|---|---|
| 宠物根本不出来 | `pet/<前缀>-config.json` 名字和素材目录对不上；或 `display` 是 `none` |
| 宠物出来了但只有待机 | `moves.actions` 为空，或 `pets[0].fixedEnabled: true` |
| 带黑底方块 | webm 不是 `yuva420p`，或源帧 alpha 全 255（抠像没生效） |
| 被拉扁 / 拉长 | 素材不是 16:9，且没走补边（`encode.py` 会自动补，手搓的素材要自己补） |
| 换了几段动画没生效 | 缓存（见上） |
| 某一段 404 | 文件名和配置里的名字不一致：中文全半角、空格、繁简都要一致 |
| 点右键没有「转向」 | `animations.turn` 是空数组，正常行为 |
| 换脸之后气泡文案对不上 | `workStatusTexts` 的档位顺序必须和 `events.workStatus` 一致（0 thinking … 5 error） |

---

## 七、卸载 / 多只宠物

- 卸载：删掉 `pet/xiaofen-config.json` 和 `pet/xiaofen-animation/` 即可，内置主宠物不受影响。
- 多只：同一种类复制 `pets` 数组里的对象、改 `id`（不能重复）和位置；不同种类就再放一份
  `<另一个前缀>-config.json` + `<另一个前缀>-animation/`，各自独立。
