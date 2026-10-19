# -*- coding: utf-8 -*-
"""
NEON PROTOCOL - 全局配置
========================

本文件集中管理所有"魔法数字"，遵循 GDD 规范：每一个数值都必须有存在理由。

设计约束
--------
1. 渲染使用双层分辨率策略：
   - 内部渲染缓冲区（RES_W x RES_H）：所有光线投射、精灵、后处理都在这里做
   - 输出窗口（WINDOW_W x WINDOW_H）：整数倍放大，形成复古 CRT 的锐利像素感
2. 所有平衡数值集中在 BALANCE 段落，便于独立调参而不触碰逻辑代码。
"""

import os

# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(ROOT_DIR)

# ---------------------------------------------------------------------------
# 窗口与渲染
# ---------------------------------------------------------------------------
RES_W = 400                 # 内部渲染宽度（越低越流畅，建议 320~640）
RES_H = 250                 # 内部渲染高度
SCALE = 3                   # 窗口放大倍率（窗口 = RES * SCALE = 1200x750）
WINDOW_W = RES_W * SCALE
WINDOW_H = RES_H * SCALE
TARGET_FPS = 60             # 目标帧率（固定步长物理 + 可变渲染）
MAX_DPR = 1

# 摄像机
FOV_DEG = 78.0              # 水平视场角。78° 是 FPS 的"手感黄金区"
CAM_PLANE = None            # 由 FOV 推导，见 renderer.build_camera_plane

# 渲染距离与雾
MAX_VIEW_DIST = 22.0        # 超过此距离的墙完全被雾吞没
FOG_START = 3.0             # 雾开始生效的距离
FOG_COLOR = (6, 8, 22)      # 雾 / 远景底色（深夜蓝）

# 屏幕震动
SHAKE_DECAY = 6.5           # 震动衰减系数（越大衰减越快）
SHAKE_MAX = 9.0

# 玩家视角
PITCH_LIMIT = 0.42          # 上下视角偏移上限（以屏幕高度比例计）
MOUSE_SENS_X = 0.0026
MOUSE_SENS_Y = 0.0020
BOB_AMP = 2.6               # 走路头部摇晃振幅（像素）
BOB_SPEED = 11.0            # 摇晃频率系数

# ---------------------------------------------------------------------------
# 墙 / 地板纹理
# ---------------------------------------------------------------------------
TEX_SIZE = 64               # 程序化生成纹理的边长（2 的幂，便于位运算）

# 墙面类型 ID -> 纹理索引
WALL_EMPTY = 0
WALL_PANEL = 1              # 标准合金板
WALL_CIRCUIT = 2            # 电路板墙
WALL_NEON = 3               # 霓虹光带墙
WALL_DATA = 4               # 数据服务器机柜
WALL_CORE = 5               # 核心屏障（BOSS 房）
WALL_GLASS = 6              # 全息幕墙（半透感，用高亮实现）
WALL_HEX = 7                # 装甲格栅（承重柱材质）

# 生成期占位 ID：标记"这是承重柱"，_paint_walls 会保留其材质不做噪声重绘
WALL_HEX_SAFE = WALL_HEX

WALL_TEXTURES = 8           # 预生成纹理张数

# 自发光墙：受雾影响更弱，在远处仍保持亮度，形成霓虹纵深
EMISSIVE_WALLS = (WALL_NEON, WALL_GLASS, WALL_CIRCUIT)
EMISSIVE_FOG_POW = 0.34     # 指数越小，穿透雾的能力越强

# ---------------------------------------------------------------------------
# 地图生成
# ---------------------------------------------------------------------------
MAP_W = 42
MAP_H = 34
ROOM_MIN = 5
ROOM_MAX = 12
ROOM_TRIES = 90
CORRIDOR_WIDTH = 2

# ---------------------------------------------------------------------------
# 玩家
# ---------------------------------------------------------------------------
PLAYER_RADIUS = 0.26
PLAYER_MAX_HP = 100.0
PLAYER_MAX_ARMOR = 60.0
PLAYER_MAX_ENERGY = 100.0
PLAYER_ENERGY_REGEN = 2.4       # 能量/秒 自动回充（保证不会因缺弹卡死）
PLAYER_ARMOR_DECAY = 0.0        # 装甲不自然衰减

MOVE_SPEED = 3.05               # 基础移动速度（格/秒）
MOVE_ACCEL = 22.0               # 加速度（用于平滑起步/急停）
DASH_SPEED = 9.6               # 冲刺速度
DASH_TIME = 0.17                # 冲刺持续时间（秒）
DASH_COOLDOWN = 0.85            # 冲刺冷却
DASH_IFRAMES = 0.13             # 冲刺时无敌帧

PICKUP_RADIUS = 0.62            # 拾取判定半径
INTERACT_RADIUS = 1.1

# ---------------------------------------------------------------------------
# 武器
# ---------------------------------------------------------------------------
# 每种武器：名称 / 伤害 / 弹速 / 冷却 / 能量消耗 / 散布 / 弹丸数 / 穿透 / 颜色
WEAPONS = [
    {
        "key": "pulse",
        "name": "PULSE SMG",
        "cn": "脉冲冲锋枪",
        "damage": 11.0,
        "speed": 17.0,
        "cooldown": 0.098,
        "cost": 1.05,
        "spread": 0.030,
        "pellets": 1,
        "pierce": 0,
        "color": (80, 235, 255),
        "knock": 0.20,
        "auto": True,
    },
    {
        "key": "rail",
        "name": "RAIL LANCE",
        "cn": "磁轨长矛",
        "damage": 62.0,
        "speed": 46.0,
        "cooldown": 0.62,
        "cost": 14.0,
        "spread": 0.002,
        "pellets": 1,
        "pierce": 3,
        "color": (255, 90, 220),
        "knock": 0.9,
        "auto": False,
    },
    {
        "key": "scatter",
        "name": "SCATTER ARRAY",
        "cn": "散射阵列",
        "damage": 9.5,
        "speed": 14.0,
        "cooldown": 0.42,
        "cost": 9.0,
        "spread": 0.115,
        "pellets": 7,
        "pierce": 1,
        "color": (180, 255, 90),
        "knock": 0.55,
        "auto": True,
    },
]
START_WEAPON = 0

# ---------------------------------------------------------------------------
# 敌人原型
# ---------------------------------------------------------------------------
# 速度标定说明（关键平衡约束）
# ----------------------------
# 玩家移动速度 MOVE_SPEED = 3.05 格/秒，敌人速度以此为基准配比：
#   stalker ≈ 1.12x 玩家 —— 近战必须比玩家快，否则永远追不上，形同虚设
#   sentry  ≈ 0.62x 玩家 —— 中速远程，用火力压制而非速度造成威胁
#   phantom ≈ 0.84x 玩家 —— 中高速，靠闪现强行拉近距离
#   warden  ≈ 0.42x 玩家 —— 慢速重装，用高血高伤换取机动性劣势
# 若把速度调得过低，敌人会在半路"摸鱼"，玩家体验到的就是"这局没有敌人"。
#
# hp / speed / 视觉半径 / 碰撞半径 / 伤害 / 攻击间隔 / 行为 / 颜色 / 分数
ENEMY_TYPES = {
    "sentry": {
        "name": "SENTRY",
        "cn": "哨兵",
        "hp": 46.0,
        "speed": 1.90,
        "radius": 0.34,
        "damage": 7.0,
        "attack_cd": 1.45,
        "attack_range": 9.5,
        "keep_dist": 4.2,
        "behavior": "ranged",
        "color": (255, 92, 70),
        "accent": (255, 210, 90),
        "score": 100,
        "glow": 0.55,
    },
    "stalker": {
        "name": "STALKER",
        "cn": "潜行者",
        "hp": 34.0,
        "speed": 3.40,
        "radius": 0.30,
        "damage": 12.0,
        "attack_cd": 0.85,
        "attack_range": 1.15,
        "keep_dist": 0.0,
        "behavior": "melee",
        "color": (70, 240, 200),
        "accent": (230, 255, 250),
        "score": 140,
        "glow": 0.72,
    },
    "warden": {
        "name": "WARDEN",
        "cn": "守望者",
        "hp": 168.0,
        "speed": 1.28,
        "radius": 0.46,
        "damage": 17.0,
        "attack_cd": 1.9,
        "attack_range": 6.8,
        "keep_dist": 2.6,
        "behavior": "heavy",
        "color": (190, 96, 255),
        "accent": (255, 220, 120),
        "score": 320,
        "glow": 0.85,
    },
    "phantom": {
        "name": "PHANTOM",
        "cn": "幻影",
        "hp": 26.0,
        "speed": 2.55,
        "radius": 0.32,
        "damage": 8.0,
        "attack_cd": 1.1,
        "attack_range": 5.6,
        "keep_dist": 3.0,
        "behavior": "blink",
        "color": (120, 255, 140),
        "accent": (245, 255, 200),
        "score": 180,
        "glow": 0.62,
    },
}

# 敌人子弹
ENEMY_BULLET_SPEED = 9.2
ENEMY_BULLET_RADIUS = 0.09

# ---------------------------------------------------------------------------
# 掉落物
# ---------------------------------------------------------------------------
PICKUP_TYPES = {
    "energy": {"color": (90, 235, 255), "amount": 26.0, "label": "ENERGY"},
    "armor": {"color": (255, 214, 96), "amount": 22.0, "label": "ARMOR"},
    "health": {"color": (255, 92, 140), "amount": 24.0, "label": "REPAIR"},
    "shard": {"color": (200, 140, 255), "amount": 1.0, "label": "SHARD"},
}

DROP_TABLE = [
    ("energy", 0.30),
    ("health", 0.15),
    ("armor", 0.13),
    ("shard", 0.42),
]

# ---------------------------------------------------------------------------
# 波次与难度曲线
# ---------------------------------------------------------------------------
TOTAL_WAVES = 8
WAVE_BASE_COUNT = 4             # 第 1 波敌人数量
WAVE_COUNT_GROWTH = 2.3         # 每波新增敌人数量
WAVE_HP_SCALE = 0.16            # 每波敌人血量成长系数
WAVE_DMG_SCALE = 0.075          # 每波敌人伤害成长系数
WAVE_SPAWN_INTERVAL = 0.85      # 同一波内敌人出生间隔
WAVE_BREAK_TIME = 0.9           # 波次清空 -> 结算界面的延迟
MAX_ALIVE_ENEMIES = 26          # 同时存活上限（性能与手感双重考虑）

# 敌人构成随波次解锁（波次下限 -> 权重表）
ENEMY_UNLOCK = {
    1: {"sentry": 1.0},
    2: {"sentry": 0.72, "stalker": 0.28},
    3: {"sentry": 0.52, "stalker": 0.34, "phantom": 0.14},
    5: {"sentry": 0.40, "stalker": 0.28, "phantom": 0.16, "warden": 0.16},
    7: {"sentry": 0.32, "stalker": 0.26, "phantom": 0.20, "warden": 0.22},
}

# ---------------------------------------------------------------------------
# 局内升级（协议补丁）
# ---------------------------------------------------------------------------
UPGRADE_POOL = [
    {
        "id": "hp_up",
        "name": "BIOWARE PATCH",
        "cn": "生物补丁",
        "desc": "最大结构完整度 +25，并立即修复同量",
        "apply": "max_hp", "value": 25.0,
    },
    {
        "id": "dmg_up",
        "name": "OVERCHARGE",
        "cn": "过载芯片",
        "desc": "所有武器伤害 +18%",
        "apply": "damage_mul", "value": 0.18,
    },
    {
        "id": "rate_up",
        "name": "PULSE ACCEL",
        "cn": "脉冲加速",
        "desc": "射速 +14%，能量消耗 -8%",
        "apply": "rate_mul", "value": 0.14,
    },
    {
        "id": "speed_up",
        "name": "MYOMER GEAR",
        "cn": "肌纤维齿轮",
        "desc": "移动速度 +10%，冲刺冷却 -18%",
        "apply": "speed_mul", "value": 0.10,
    },
    {
        "id": "armor_up",
        "name": "AEGIS LAYER",
        "cn": "神盾涂层",
        "desc": "最大护盾 +35，并立即充满",
        "apply": "max_armor", "value": 35.0,
    },
    {
        "id": "pierce_up",
        "name": "PHASE ROUNDS",
        "cn": "相位弹头",
        "desc": "所有子弹穿透 +1",
        "apply": "pierce", "value": 1,
    },
    {
        "id": "energy_up",
        "name": "FUSION CELL",
        "cn": "聚变电芯",
        "desc": "最大能量 +40，回充速度 +45%",
        "apply": "energy", "value": 40.0,
    },
    {
        "id": "dash_up",
        "name": "BLINK DRIVE",
        "cn": "闪现驱动",
        "desc": "冲刺冷却 -25%，无敌帧 +60%",
        "apply": "dash", "value": 0.25,
    },
    {
        "id": "crit_up",
        "name": "WEAKPOINT DAEMON",
        "cn": "弱点守护进程",
        "desc": "暴击率 +15%，暴击伤害 2.2 倍",
        "apply": "crit", "value": 0.15,
    },
    {
        "id": "vamp_up",
        "name": "LEECH PROTOCOL",
        "cn": "汲取协议",
        "desc": "击杀回复 4 点结构完整度",
        "apply": "leech", "value": 4.0,
    },
]

CRIT_MULT = 2.2

# ---------------------------------------------------------------------------
# 视觉
# ---------------------------------------------------------------------------
NEON = {
    "cyan": (72, 240, 255),
    "magenta": (255, 78, 200),
    "lime": (168, 255, 96),
    "amber": (255, 196, 92),
    "violet": (178, 120, 255),
    "red": (255, 78, 92),
    "white": (236, 248, 255),
    "dark": (10, 12, 24),
    "panel": (18, 22, 38),
    "grid": (34, 44, 74),
}

SCANLINE_STRENGTH = 0.09        # 扫描线明暗差
VIGNETTE_STRENGTH = 0.55        # 暗角强度
GLITCH_CHANCE = 0.010           # 每帧触发横向撕裂的概率
HIT_FLASH_DECAY = 5.5

# 粒子
MAX_PARTICLES = 420
PARTICLE_GRAVITY = 0.9
PARTICLE_DRAG = 1.9

# ---------------------------------------------------------------------------
# 音频
# ---------------------------------------------------------------------------
AUDIO_ENABLED = True
AUDIO_RATE = 44100
SFX_VOLUME = 0.36
MAX_SFX_CHANNELS = 24

# ---------------------------------------------------------------------------
# 调色板（窗口 UI 用）
# ---------------------------------------------------------------------------
UI_BG = (8, 9, 18)
UI_FG = (226, 238, 255)
UI_DIM = (128, 148, 186)

TITLE = "NEON PROTOCOL"
SUBTITLE = "霓虹协议 · 数据要塞入侵"

SEED = None                 # None = 每次随机；固定值可复现关卡
