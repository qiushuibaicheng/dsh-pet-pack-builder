#!/usr/bin/env python3
"""按动画文件名自动生成 dsh-pet 的 pet pack 配置（JSONC，带注释）。

动作 → 动画池靠关键词猜，猜不出来的都丢进随机小动作分类。猜错了直接改生成的
json 即可（里面有注释说明每个池子的作用），或者用 --map 传一份映射。

用法：
    python make_config.py --anim-dir pet/demo-animation --name 小粉 --id xiaofen
    python make_config.py ... --map mapping.json --persona "性格描述" --install
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

# 关键词 → 语义槽。命中即归类，顺序即优先级
RULES = [
    ('idle',    ['待机', 'idle', '待命', '呼吸', '站立', '站着']),
    ('sleep',   ['睡觉', '睡眠', 'sleep', '困', '打盹', 'zZ']),
    ('think',   ['思考', 'think', '疑问', '好奇', '疑惑', '琢磨']),
    ('drag',    ['委屈', 'drag', '被拎', '挣扎', '难过', '哭', '生气']),
    ('greet',   ['问候', '问侯', '打招呼', 'hello', '挥手', '嗨', '招手']),
    ('click',   ['点击', 'click', 'tap', '回应', '戳', '反馈']),
    ('walk',    ['走路', 'walk', '散步', '移动', '奔跑', '跑', '滑行', '飞', '伞']),
    ('success', ['成功', '庆祝', 'success', '开心', '胜利', '完成', '耶']),
    ('play',    ['玩', 'play', '摘', '星星', '抱枕', '游戏', '摸鱼', '蹦',
                 '雪花', '泡泡', '镜子', '照镜', '魔法', '变']),
]

DEFAULT_PERSONA = (
    '你是主人桌面上的Q版小宠物，性格活泼可爱、有点粘人，心情全写在脸上。'
    '说话口语化、短短一句（20字以内），爱用「呀」「啦」「欸嘿」这种语气词；'
    '说人话、不啰嗦，不解释你自己，不要提你是AI。'
)

DEFAULT_TEXTS = [
    ['让我想想哦…', '脑袋里正在冒泡泡'],
    ['正忙着呢，别催别催~', '小手手正在敲敲敲'],
    ['这一步搞定啦，继续~', '收拾好咯，看下一步'],
    ['等你点头哦~', '这里要你拿主意啦'],
    ['全部搞定，耶！', '完成啦~'],
    ['欸…好像出问题了', '这里卡住了，有点委屈'],
]


def classify(names: list[str]) -> dict:
    """把动画名分配进语义槽。"""
    slots = {k: [] for k, _ in RULES}
    used = set()
    for name in names:
        low = name.lower()
        for slot, kws in RULES:
            if any(kw.lower() in low for kw in kws):
                slots[slot].append(name)
                used.add(name)
                break
    return {'slots': slots, 'leftover': [n for n in names if n not in used]}


def build(name: str, pid: str, prefix: str, names: list[str], size: int,
          persona: str, texts: list[list[str]], display: str,
          corner: str, mx: int, my: int, mapping: dict | None) -> tuple[dict, list[str]]:
    c = classify(names)
    s = c['slots']
    if mapping:
        for k, v in mapping.items():
            if k in s and isinstance(v, list):
                s[k] = v
    warn = []

    def first(*cands):
        for x in cands:
            if x:
                return x[0]
        return names[0]

    idle = first(s['idle'])
    sleep = first(s['sleep'], s['idle'])
    think = first(s['think'], s['idle'])
    drag = first(s['drag'], s['idle'])
    greet = first(s['greet'], s['click'], s['idle'])
    success = first(s['success'], s['idle'])
    play = first(s['play'], s['success'], s['idle'])
    walk = s['walk'][0] if s['walk'] else None

    clicks = (s['click'] + s['greet'])[:3] or [idle]
    cats = []
    rest = s['sleep'] + s['think'] + s['play'] + c['leftover']
    rest = [n for i, n in enumerate(rest) if n not in rest[:i] and n != idle]
    if rest:
        # 均分到两个分类：安静的 / 活泼的
        half = max(1, len(rest) // 2)
        cats = [{'id': '小动作', 'actions': rest[:half]},
                {'id': '玩耍', 'actions': rest[half:]}]
        cats = [x for x in cats if x['actions']]
    if not cats:
        cats = [{'id': '小动作', 'actions': [x for x in names if x != idle][:2] or [idle]}]

    move_w = 10 if walk else 0
    idle_w = 34 if cats else 100 - move_w
    cat_total = 100 - idle_w - move_w
    if cat_total <= 0:
        idle_w, cat_total = 100 - move_w, 0
    per = cat_total // len(cats)
    for i, cat in enumerate(cats):
        cat['weight'] = per if i < len(cats) - 1 else cat_total - per * (len(cats) - 1)

    if not s['idle']:
        warn.append(f'没有识别出「待机」动作，暂用「{idle}」。建议把一个动作改名为 xxx待机xxx')
    if not walk:
        warn.append('没有识别出「走路」动作，宠物不会走动（moves 为空，右键菜单也不会有散步）')

    animations = {
        'idle': [idle],
        'turn': [],
        'drag': [drag],
        'clicks': clicks,
        'moves': {'default': {'minDist': 60, 'maxDist': 220, 'margin': 20,
                              'leadSec': 0.35, 'tailSec': 0.35},
                  'actions': [{'name': walk}] if walk else []},
        'categories': [{'id': x['id'], 'weight': x['weight'], 'actions': x['actions']} for x in cats],
        'events': {
            'balance': [success, greet, idle, think, drag, sleep],
            'whisper': list(dict.fromkeys([think, greet, drag])),
            'workStatus': [think, walk or idle, play, idle, success, drag],
        },
    }
    cfg = {
        'whisperPrompt': persona,
        'chatMemoryRounds': 5,
        'whisperImageEnabled': False,
        'chatImageEnabled': False,
        'workStatusTexts': texts,
        'pets': [{
            'name': name, 'id': pid, 'size': size,
            'balanceEnabled': True, 'whisperEnabled': True, 'workStatusEnabled': True,
            'fixedEnabled': False, 'display': display,
            'position': {'corner': corner, 'marginX': mx, 'marginY': my},
        }],
        'animations': animations,
        'animationWeights': {'idle': idle_w, 'turn': 0, 'move': move_w},
    }
    return cfg, warn


def render(cfg: dict, pid: str) -> str:
    J = lambda v: json.dumps(v, ensure_ascii=False)  # noqa: E731
    a = cfg['animations']
    ev = a['events']
    cats = ',\n'.join(
        f'      {{ "id": "{c["id"]}", "weight": {c["weight"]}, "actions": {J(c["actions"])} }}'
        for c in a['categories'])
    moves = J(a['moves']['actions'])
    weight_desc = ' + '.join([str(cfg['animationWeights']['idle']),
                              '0',
                              str(cfg['animationWeights']['move'])]
                             + [str(c['weight']) for c in a['categories']])
    texts = ',\n'.join('    ' + J(t) for t in cfg['workStatusTexts'])

    return f'''// {cfg["pets"][0]["name"]} —— dsh-pet 额外宠物种类（pet pack）
// 放到 $DSH_HOME/dsh-pet/pet/ 下，与同目录的 {pid}-animation/ 配对；
// 文件名前缀 {pid} = 素材根（动画目录名必须一致），实例 id 不必等于前缀。
{{
  // ===================== 人设（碎碎念 / 对话的系统提示词） =====================
  // 实际发送时会自动追加「你的名字是“{cfg["pets"][0]["name"]}”。」+ 当前时间背景，这里只写性格与说话方式。
  "whisperPrompt": {J(cfg["whisperPrompt"])},

  // 对话上下文档数（1 轮 = 1 问 1 答）
  "chatMemoryRounds": {cfg["chatMemoryRounds"]},

  // 表情包配图：默认关闭（内置图池是主宠物的梗图，与本宠物无关）
  // 想用就把图放进 pet/{pid}-memes/ 后改成 true
  "whisperImageEnabled": false,
  "chatImageEnabled": false,

  // ===================== 工作状态气泡文案（档位顺序必须与 events.workStatus 一致） =====================
  // 0 thinking / 1 working / 2 result / 3 waiting / 4 success / 5 error
  "workStatusTexts": [
{texts}
  ],

  // ===================== 宠物实例 =====================
  // 一个种类可以放多只实例（共享同一套动画），想多养几只就复制一份改 id。
  "pets": [
    {{
      "name": {J(cfg["pets"][0]["name"])},
      "id": {J(pid)},
      "size": {cfg["pets"][0]["size"]},              // 宽度 px，高度自动 = size × 9/16
      "balanceEnabled": true,   // 余额档位用下面的 events.balance
      "whisperEnabled": true,   // 碎碎念（会周期性调用模型，不想花钱就改 false）
      "workStatusEnabled": true,// 监听 DSH 会话，按工作状态切动画 + 气泡
      "fixedEnabled": false,    // true = 不再自己走动/翻朝向，只播原地小动作
      "display": "{cfg["pets"][0]["display"]}",        // web / desktop / both / none
      "position": {{ "corner": "{cfg["pets"][0]["position"]["corner"]}", "marginX": {cfg["pets"][0]["position"]["marginX"]}, "marginY": {cfg["pets"][0]["position"]["marginY"]} }}
    }}
  ],

  // ===================== 动画池 =====================
  // 素材只查本种类的 pet/{pid}-animation/，查不到即 404，绝不回落到主宠物。
  // 所以下面每个动画名都必须有同名 .webm 文件。
  "animations": {{
    "idle": {J(a["idle"])},

    // 转向池留空 = 不会自己翻朝向（没有转身素材）；右键菜单里也不会出现「转向」
    "turn": {J(a["turn"])},

    // 被鼠标抓起悬空时的表情
    "drag": {J(a["drag"])},

    // 点按时的随机回应
    "clicks": {J(a["clicks"])},

    // 走动。leadSec/tailSec = 首尾各多少秒原地不动（走路素材的头尾通常是站立姿势）
    "moves": {{
      "default": {J(a["moves"]["default"])},
      "actions": {moves}
    }},

    // 随机小动作：weight 为该分类在 100 份里占的份额
    "categories": [
{cats}
    ],

    // 事件动画：不进随机链，只由代码按档位触发；数组顺序 = 档位顺序，别在中间插新档
    "events": {{
      // 余额档位（按已用百分比分档）：额度充足时开心，用光就睡过去
      //   index = p === 100 ? 5 : floor(p / 20)
      "balance": {J(ev["balance"])},
      // 碎碎念时随机播一个（配气泡）
      "whisper": {J(ev["whisper"])},
      // 工作状态档位：thinking / working / result / waiting / success / error
      "workStatus": {J(ev["workStatus"])}
    }}
  }},

  // ===================== 动画链权重 =====================
  // idle + turn + move + 各分类 weight 合计 = 100
  // {weight_desc} = 100
  "animationWeights": {{
    "idle": {cfg["animationWeights"]["idle"]},
    "turn": 0,
    "move": {cfg["animationWeights"]["move"]}
  }}
}}
'''


def strip_jsonc(s: str) -> str:
    """去掉 // 与 /* */ 注释和尾逗号，便于用标准 json 校验。"""
    out, i, n = [], 0, len(s)
    ins = esc = False
    while i < n:
        c = s[i]
        if ins:
            out.append(c)
            if esc:
                esc = False
            elif c == '\\':
                esc = True
            elif c == '"':
                ins = False
            i += 1
            continue
        if c == '"':
            ins = True
            out.append(c)
            i += 1
            continue
        if c == '/' and i + 1 < n and s[i + 1] == '/':
            while i < n and s[i] not in '\r\n':
                i += 1
            continue
        if c == '/' and i + 1 < n and s[i + 1] == '*':
            i += 2
            while i + 1 < n and not (s[i] == '*' and s[i + 1] == '/'):
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1
    t = ''.join(out)
    res, i, n, ins, esc = [], 0, len(t), False, False
    while i < n:
        c = t[i]
        if ins:
            res.append(c)
            if esc:
                esc = False
            elif c == '\\':
                esc = True
            elif c == '"':
                ins = False
            i += 1
            continue
        if c == '"':
            ins = True
            res.append(c)
            i += 1
            continue
        if c == ',':
            j = i + 1
            while j < n and t[j] in ' \t\r\n':
                j += 1
            if j < n and t[j] in '}]':
                i += 1
                continue
        res.append(c)
        i += 1
    return ''.join(res)


def validate(cfg: dict, anim_dir: Path) -> list[str]:
    errs = []
    files = {p.stem for p in anim_dir.glob('*.webm')}
    a = cfg['animations']
    used = set(a['idle']) | set(a['turn']) | set(a['drag']) | set(a['clicks'])
    used |= {m['name'] for m in a['moves']['actions']}
    for c in a['categories']:
        used |= set(c['actions'])
    for v in a['events'].values():
        used |= set(v)
    missing = used - files
    if missing:
        errs.append(f'配置引用了不存在的动画：{sorted(missing)}')
    w = cfg['animationWeights']
    total = w['idle'] + w['turn'] + w['move'] + sum(c['weight'] for c in a['categories'])
    if total != 100:
        errs.append(f'权重合计 {total}，应为 100')
    if len(cfg['workStatusTexts']) != len(a['events']['workStatus']):
        errs.append('workStatusTexts 数量与 events.workStatus 不一致')
    for k, v in a['events'].items():
        if not v:
            errs.append(f'events.{k} 为空')
    return errs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--anim-dir', required=True)
    ap.add_argument('--name', required=True, help='宠物显示名，如 小粉')
    ap.add_argument('--id', default='pet', help='实例 id 与目录前缀（ASCII 更稳），如 xiaofen')
    ap.add_argument('--prefix', default=None, help='目录前缀，默认同 --id')
    ap.add_argument('--size', type=int, default=500)
    ap.add_argument('--persona', default=DEFAULT_PERSONA)
    ap.add_argument('--persona-file', default=None)
    ap.add_argument('--texts', default=None, help='workStatusTexts 的 JSON 文件')
    ap.add_argument('--display', default='both', choices=['web', 'desktop', 'both', 'none'])
    ap.add_argument('--corner', default='bottom-left',
                    choices=['top-left', 'top-right', 'bottom-left', 'bottom-right'])
    ap.add_argument('--margin-x', type=int, default=32)
    ap.add_argument('--margin-y', type=int, default=24)
    ap.add_argument('--map', default=None, help='映射 JSON：{"idle":["a"],"walk":["b"]}')
    ap.add_argument('--install', action='store_true', help='顺便装到 $DSH_HOME/dsh-pet/pet/')
    a = ap.parse_args()

    anim = Path(a.anim_dir).resolve()
    if not anim.is_dir():
        print(f'[!] 目录不存在：{anim}', file=sys.stderr)
        return 2
    prefix = a.prefix or a.id
    want = f'{prefix}-animation'
    if anim.name != want:
        target = anim.parent / want
        if target.exists():
            print(f'[!] {target} 已存在，无法改名，请手动整理', file=sys.stderr)
            return 2
        anim.rename(target)
        print(f'动画目录改名为 {want}（插件要求 <前缀>-animation）')
        anim = target

    names = sorted(p.stem for p in anim.glob('*.webm'))
    if not names:
        print(f'[!] {anim} 里没有 webm', file=sys.stderr)
        return 2

    mapping = json.loads(Path(a.map).read_text(encoding='utf-8')) if a.map else None
    persona = (Path(a.persona_file).read_text(encoding='utf-8').strip()
               if a.persona_file else a.persona)
    texts = (json.loads(Path(a.texts).read_text(encoding='utf-8'))
             if a.texts else DEFAULT_TEXTS)

    cfg, warn = build(a.name, a.id, prefix, names, a.size, persona, texts,
                      a.display, a.corner, a.margin_x, a.margin_y, mapping)
    errs = validate(cfg, anim)
    if errs:
        print('[!] 配置校验失败：', file=sys.stderr)
        for e in errs:
            print('    ' + e, file=sys.stderr)
        return 2

    dst = anim.parent / f'{prefix}-config.json'
    text = render(cfg, prefix)
    try:
        back = json.loads(strip_jsonc(text))
        if back['animationWeights']['idle'] + back['animationWeights']['move'] \
                + sum(c['weight'] for c in back['animations']['categories']) != 100:
            raise ValueError('权重合计不是 100')
    except Exception as e:  # noqa: BLE001
        print(f'[!] 生成的配置无法解析：{e}', file=sys.stderr)
        return 2
    dst.write_text(text, encoding='utf-8')
    print(f'配置已写入：{dst}')
    print(f'  宠物      : {a.name} (id={a.id})  size={a.size}  display={a.display}')
    print(f'  动画池    : idle={cfg["animations"]["idle"]}  '
          f'clicks={cfg["animations"]["clicks"]}  drag={cfg["animations"]["drag"]}')
    print(f'  分类      : ' + ', '.join(f'{c["id"]}({c["weight"]})={c["actions"]}'
                                       for c in cfg['animations']['categories']))
    for w in warn:
        print(f'  [提示] {w}')

    if a.install:
        home = Path(os.environ.get('DSH_HOME') or (Path.home() / '.dsh'))
        target = home / 'dsh-pet' / 'pet'
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(dst, target / dst.name)
        tdir = target / anim.name
        if tdir.exists():
            shutil.rmtree(tdir)
        shutil.copytree(anim, tdir)
        print(f'已安装到：{target}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
