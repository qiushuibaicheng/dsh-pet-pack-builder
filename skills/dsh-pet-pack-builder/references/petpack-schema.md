# pet pack 配置字段说明

`$DSH_HOME/dsh-pet/pet/<前缀>-config.json` + 同目录 `<前缀>-animation/*.webm`。
插件按 `<前缀>-animation` 解析素材，查不到即 404，**不会回落到内置宠物**。

文件是 JSONC（允许 `//` 注释与尾逗号）。`make_config.py` 生成的版本带完整注释，
这里只列出字段语义，方便改。

## 顶层

| 字段 | 说明 |
|---|---|
| `whisperPrompt` | 碎碎念 / 对话的系统提示词。只写性格与说话方式；实际发送时会自动追加「你的名字是“XX”。」和时间背景 |
| `chatMemoryRounds` | 对话上下文保留轮数（1 轮 = 1 问 1 答） |
| `whisperImageEnabled` | 碎碎念是否配图。内置图池是主宠物的梗图，自定义宠物建议 `false` |
| `chatImageEnabled` | 对话是否配图，同上 |
| `workStatusTexts` | 工作状态气泡文案，**数组长度必须等于 `events.workStatus` 长度**，每项是一个字符串数组（随机取一条） |

## pets

一个种类可以有多个实例，共享同一套动画。

```json
{
  "name": "小粉",
  "id": "xiaofen",
  "size": 500,
  "balanceEnabled": true,
  "whisperEnabled": true,
  "workStatusEnabled": true,
  "fixedEnabled": false,
  "display": "both",
  "position": { "corner": "bottom-left", "marginX": 32, "marginY": 24 }
}
```

- `size`：宽度 px，高度自动 = `size × 9/16`。**素材必须是 16:9**，否则被拉伸。
- `display`：`web` / `desktop` / `both` / `none`。`both` 会在网页和桌面各渲染一份。
- `fixedEnabled`：`true` 表示不再自己走动 / 翻朝向，只播原地小动作。
- `position.corner`：`top-left` / `top-right` / `bottom-left` / `bottom-right`。

## animations

```json
{
  "idle":    ["待机"],
  "turn":    [],
  "drag":    ["委屈"],
  "clicks":  ["点击", "问侯"],
  "moves": {
    "default": { "minDist": 60, "maxDist": 220, "margin": 20,
                 "leadSec": 0.35, "tailSec": 0.35 },
    "actions": [{ "name": "原地走路" }]
  },
  "categories": [
    { "id": "小动作", "weight": 30, "actions": ["睡觉", "兔子抱枕堡垒"] }
  ],
  "events": {
    "balance":    ["成功庆祝", "问侯", "待机", "思考", "委屈", "睡觉"],
    "whisper":    ["思考", "问侯", "委屈"],
    "workStatus": ["思考", "原地走路", "摘星星", "待机", "成功庆祝", "委屈"]
  }
}
```

| 池 | 作用 |
|---|---|
| `idle` | 待机，随机空闲时循环播放。**必须有** |
| `turn` | 转向。没有转身素材就留空数组，右键菜单里也不会出现「转向」 |
| `drag` | 被鼠标抓起悬空时 |
| `clicks` | 点按时随机取一个 |
| `moves.actions` | 自己的散步动作。`leadSec`/`tailSec` = 首尾各多少秒原地不动（走路素材的头尾通常是站立姿势） |
| `categories` | 随机小动作分组，`weight` 是它在 100 份里占的份额 |
| `events.balance` | 余额档位。索引 = `p === 100 ? 5 : floor(p / 20)`，所以固定 6 档，顺序 = 从「额度充足」到「用光」 |
| `events.whisper` | 碎碎念时随机播一个 |
| `events.workStatus` | 工作状态档位，固定 6 档：thinking / working / result / waiting / success / error |

事件池数组的**顺序就是档位顺序**，不要在中间插入或删除，否则整体错位。

## animationWeights

```json
{ "idle": 34, "turn": 0, "move": 10 }
```

约束：`idle + turn + move + Σcategories[].weight == 100`。
`idle` 越大越安静，`move` 越大越爱走动。没有走路动作时 `move` 必须为 0。
