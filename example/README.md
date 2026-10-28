# 完整实例：角色「小粉」

这套素材来自同一个角色的 13 段绿幕视频（豆包生成，1280×720 / 24fps），
已经全部转码并配好，可以直接装进插件看效果。

- `source/` — 原始 MP4，转码的输入
- `webm/` — 成品，`VP9 + yuva420p`，直接可用
- `xiaofen-config.json` — 配套配置（JSONC，带注释）

## 13 段素材分别落在哪个池子

| 素材 | 语义 | 配置里的位置 |
|---|---|---|
| 待机 | idle | `animations.idle` |
| 原地走路 / 乘伞滑行 | walk | `moves.actions` |
| 点击 / 问侯 | click / greet | `clicks` |
| 委屈 | drag | `drag`（被拎起来时） |
| 睡觉 / 兔子抱枕堡垒 / 梦境泡泡 | sleep / play | `categories` 的「小动作」（weight 30） |
| 摘星星 / 成功庆祝 / 制造雪花 | play / success | `categories` 的「玩耍」（weight 26） |
| 思考 | think | 随机池 + `events.workStatus[0]` |

权重：`待机 34 + 转向 0 + 走路 10 + 小动作 30 + 玩耍 26 = 100`。
`turn` 留空是因为这批素材里没有转身动作 —— 留空后宠物不会自己翻朝向，右键菜单里也不会出现「转向」组。

## 直接用

```sh
# 只装素材和配置（不重新转码）
mkdir -p ~/.dsh/dsh-pet/pet
cp -r webm ~/.dsh/dsh-pet/pet/xiaofen-animation
cp xiaofen-config.json ~/.dsh/dsh-pet/pet/
```

然后重启 DSH。改 `pets[0].name` / `size` / `position` 就能换成自己的名字、大小、位置；
`whisperPrompt` 是人设（碎碎念和对话的系统提示词）。

## 从头复现

```sh
python ../skills/dsh-pet-pack-builder/scripts/analyze.py --src source --out source-profile.json
python ../skills/dsh-pet-pack-builder/scripts/encode.py --src source --out build/xiaofen-animation \
    --profile source-profile.json
python ../skills/dsh-pet-pack-builder/scripts/check.py verify --out build/xiaofen-animation --src source
```

两段带半透明道具的素材需要额外参数（这段就是专门留下来的坑）：

| 素材 | 现象 | 处理 |
|---|---|---|
| 乘伞滑行、梦境泡泡 | 伞面/泡泡上有灰绿「脏块」 | `encode.py ... --clips 乘伞滑行 梦境泡泡 --deglass` |

（镜子/玻璃板那种「整块被抠空」的情况见主 README 的 `glass_prop.py` 一节；这批素材里没有
镜子片段，所以不在这里演示。）

13 段里角色怀里都抱着白色兔子玩偶，它的**眼睛是青色**的，本来会被当成绿幕抠掉——
`keyer` 默认已救回（判据见主 README 第 10 条踩坑），所以不需要额外参数。

## 效果图

| 文件 | 内容 |
|---|---|
| `preview.png` | 桌面上实际显示大小（500px）的示意 |
| `before-after.png` | 抠像前后对比（绿底 → 透明） |
