# -*- coding: utf-8 -*-
"""
NEON PROTOCOL - 程序化资源生成
==============================

本模块在启动时用代码"画"出全部美术资源，工程上为零外部依赖：

- 8 张 64x64 墙面纹理（合金板 / 电路 / 霓虹光带 / 机柜 / 核心 / 全息幕墙）
- 地板与天花板的基色渐变（静态部分预计算）
- 4 类敌人的多帧动画精灵（含霓虹辉光）
- 3 种掉落物精灵
- 3 把武器的第一人称持枪图 + 枪口火焰
- 暗角 / 扫描线 后处理查找表

设计取舍
--------
纹理全部用 numpy 直接算，避免逐像素 Python 循环；
精灵用 pygame.draw 绘制后转成 numpy RGBA，由光线投射器统一做 billboard 渲染。
"""

from __future__ import annotations

import math
from typing import Dict, List, Tuple

import numpy as np
import pygame

from . import config as C

S = C.TEX_SIZE


# ===========================================================================
# 通用小工具
# ===========================================================================
def _to_u8(a: np.ndarray) -> np.ndarray:
    return np.clip(a, 0, 255).astype(np.uint8)


def _box_blur(mask: np.ndarray, passes: int = 3) -> np.ndarray:
    """对二维浮点蒙版做 N 次 5 点均值模糊（用 roll 实现，无需 scipy）。"""
    m = mask.astype(np.float32)
    for _ in range(passes):
        m = (m * 2.0
             + np.roll(m, 1, 0) + np.roll(m, -1, 0)
             + np.roll(m, 1, 1) + np.roll(m, -1, 1)) / 6.0
    return m


def _surface_to_rgba(surf: pygame.Surface) -> np.ndarray:
    """
    pygame.Surface(SRCALPHA) -> numpy (h, w, 4) uint8。

    pygame.surfarray 返回 (w, h, c)，这里转置成渲染器需要的行优先布局。
    """
    rgb = pygame.surfarray.array3d(surf)          # (w, h, 3)
    alpha = pygame.surfarray.array_alpha(surf)    # (w, h)
    arr = np.dstack([rgb, alpha])                 # (w, h, 4)
    return np.ascontiguousarray(np.transpose(arr, (1, 0, 2)))


def _apply_glow(surf: pygame.Surface, glow_color: Tuple[int, int, int],
                strength: float = 0.85, passes: int = 3) -> np.ndarray:
    """
    给精灵加霓虹辉光：把 alpha 通道模糊后作为外发光，并叠加到底色 RGB 上。

    这是"低成本 bloom"：不需要真正的 HDR 管线，但赛博朋克味儿很足。
    """
    arr = _surface_to_rgba(surf)
    alpha = arr[:, :, 3].astype(np.float32) / 255.0
    halo = _box_blur(alpha, passes) * strength
    gc = np.array(glow_color, dtype=np.float32)
    rgb = arr[:, :, :3].astype(np.float32)
    rgb = rgb + gc[None, None, :] * halo[:, :, None] * 0.9
    out_a = np.maximum(arr[:, :, 3], (np.clip(halo, 0, 1) * 255.0))
    out = np.dstack([np.clip(rgb, 0, 255), out_a]).astype(np.uint8)
    return out


# ===========================================================================
# 墙面纹理
# ===========================================================================
def _tex_panel(rng: np.random.Generator) -> np.ndarray:
    """标准合金板：拼缝 + 铆钉 + 轻微噪点。"""
    t = np.zeros((S, S, 3), np.float32)
    t[:] = (26, 30, 45)
    t[:, ::16] = (62, 72, 100)          # 竖向拼缝
    t[:, 1::16] = (12, 14, 24)
    t[28:33, :] = (18, 21, 34)          # 横向凹槽
    t[30, :] = (54, 66, 96)
    for y in range(6, S, 16):
        for x in range(6, S, 16):
            t[y:y + 2, x:x + 2] = (104, 118, 152)
    t += rng.normal(0.0, 4.5, (S, S, 1))
    return _to_u8(t)


def _tex_circuit(rng: np.random.Generator) -> np.ndarray:
    """电路板：随机游走生成的走线 + 青色辉光。"""
    t = np.zeros((S, S, 3), np.float32)
    t[:] = (11, 14, 24)
    mask = np.zeros((S, S), np.float32)
    for _ in range(16):
        x = int(rng.integers(0, S))
        y = int(rng.integers(0, S))
        steps = int(rng.integers(10, 46))
        for _ in range(steps):
            mask[y, x] = 1.0
            mask[y, (x + 1) % S] = 1.0
            if rng.random() < 0.5:
                x = (x + (1 if rng.random() < 0.5 else -1)) % S
            else:
                y = (y + (1 if rng.random() < 0.5 else -1)) % S
    halo = _box_blur(mask, 2)
    t += mask[:, :, None] * np.array([70, 220, 240], np.float32)
    t += halo[:, :, None] * np.array([16, 70, 92], np.float32)
    # 板上的贴片元件
    for _ in range(9):
        x = int(rng.integers(2, S - 6))
        y = int(rng.integers(2, S - 6))
        t[y:y + 4, x:x + 5] = (40, 44, 60)
    t += rng.normal(0.0, 3.0, (S, S, 1))
    return _to_u8(t)


def _tex_neon(rng: np.random.Generator) -> np.ndarray:
    """霓虹光带墙：深底 + 品红色横向光条。"""
    t = np.zeros((S, S, 3), np.float32)
    t[:] = (13, 10, 22)
    for band_y in (10, 30, 50):
        for dy in range(-6, 7):
            f = max(0.0, 1.0 - abs(dy) / 6.0) ** 2.2
            yy = band_y + dy
            if 0 <= yy < S:
                t[yy, :] += np.array([255, 60, 190], np.float32) * f
        t[band_y, :] = np.array([255, 190, 240], np.float32)
    t[:, ::24] = np.maximum(t[:, ::24], np.array([40, 30, 60], np.float32))
    t += rng.normal(0.0, 2.5, (S, S, 1))
    return _to_u8(t)


def _tex_data(rng: np.random.Generator) -> np.ndarray:
    """数据机柜：分层机架 + 随机闪烁指示灯。"""
    t = np.zeros((S, S, 3), np.float32)
    t[:] = (16, 18, 28)
    for row in range(0, S, 16):
        t[row:row + 14, :] += 6
        t[row, :] = (70, 80, 110)
        for col in range(4, S, 8):
            led = rng.random()
            if led < 0.36:
                col_rgb = (60, 240, 150)    # 正常
            elif led < 0.62:
                col_rgb = (90, 210, 255)    # 传输中
            elif led < 0.76:
                col_rgb = (255, 170, 60)    # 告警
            else:
                col_rgb = (120, 130, 160)   # 空闲
            t[row + 5:row + 8, col:col + 3] = col_rgb
    t += rng.normal(0.0, 3.0, (S, S, 1))
    return _to_u8(t)


def _tex_core(rng: np.random.Generator) -> np.ndarray:
    """核心屏障：六边形能量纹路，用于 BOSS / 终点房间。"""
    t = np.zeros((S, S, 3), np.float32)
    yy, xx = np.mgrid[0:S, 0:S].astype(np.float32)
    # 用三角波叠出蜂窝状纹路
    hx = np.abs(((xx / 8.0) % 2.0) - 1.0)
    hy = np.abs(((yy / 12.0) % 2.0) - 1.0)
    cell = np.maximum(hx, hy)
    t[:] = (20, 12, 32)
    t += (1.0 - cell)[:, :, None] * np.array([150, 60, 220], np.float32)
    t[cell > 0.94] = (255, 200, 120)
    t += rng.normal(0.0, 3.0, (S, S, 1))
    return _to_u8(t)


def _tex_glass(rng: np.random.Generator) -> np.ndarray:
    """全息幕墙：高亮青蓝网格 + 数据流。"""
    t = np.zeros((S, S, 3), np.float32)
    t[:] = (10, 22, 34)
    t[::8, :] += np.array([18, 90, 120], np.float32)
    t[:, ::8] += np.array([18, 90, 120], np.float32)
    t[::32, :] = np.array([120, 240, 255], np.float32)
    t[:, ::32] = np.array([90, 200, 235], np.float32)
    for _ in range(5):                       # 数据流竖条
        x = int(rng.integers(0, S))
        y0 = int(rng.integers(0, S - 20))
        t[y0:y0 + 20, x] = np.array([220, 255, 255], np.float32)
    t += rng.normal(0.0, 3.5, (S, S, 1))
    return _to_u8(t)


def _tex_hex(rng: np.random.Generator) -> np.ndarray:
    """装甲格栅：斜向条纹，用于走廊变体。"""
    t = np.zeros((S, S, 3), np.float32)
    yy, xx = np.mgrid[0:S, 0:S].astype(np.float32)
    stripe = ((xx + yy) % 12) < 5
    t[:] = (19, 23, 36)
    t[stripe] = (30, 37, 56)
    t[((xx + yy) % 12) == 0] = (86, 104, 148)
    t += rng.normal(0.0, 3.0, (S, S, 1))
    return _to_u8(t)


def build_wall_textures() -> List[np.ndarray]:
    """
    返回长度为 WALL_TEXTURES 的列表，索引 = 墙类型 ID。

    索引 0 是空气（不会用到），但保留占位让 grid[y][x] 直接当纹理下标。
    每张纹理为 (S,S,3) uint8，同时提供一份展平版本供渲染器快速索引。
    """
    rng = np.random.default_rng(20261015)
    texs = [
        np.full((S, S, 3), 24, np.uint8),   # 0 占位
        _tex_panel(rng),
        _tex_circuit(rng),
        _tex_neon(rng),
        _tex_data(rng),
        _tex_core(rng),
        _tex_glass(rng),
        _tex_hex(rng),
    ]
    return texs


# ===========================================================================
# 地板 / 天花板基色（静态预计算）
# ===========================================================================
def build_background_table() -> Tuple[np.ndarray, np.ndarray]:
    """
    返回 (bg_rgb, ) —— 形状 (RES_H, RES_W, 3) 的 float32 基色缓冲。

    上半为天花板（深蓝渐变 + 竖直光柱），下半为地板基色。
    地板的透视网格在渲染器里逐帧叠加，这里只提供"打底"的明暗。
    """
    h, w = C.RES_H, C.RES_W
    bg = np.zeros((h, w, 3), np.float32)

    ceil_c = np.array([17, 20, 42], np.float32)
    floor_c = np.array([22, 24, 34], np.float32)
    far_c = np.array(C.FOG_COLOR, np.float32)

    horizon = h // 2
    for y in range(h):
        if y < horizon:
            # 离地平线越远（越靠上）越亮，形成穹顶感
            t = (horizon - y) / max(1, horizon)
            row = far_c + (ceil_c - far_c) * (t ** 0.75)
        else:
            t = (y - horizon) / max(1, h - horizon)
            row = far_c + (floor_c - far_c) * (t ** 0.55)
        bg[y, :, :] = row

    # 天花板光柱：几条竖直的冷光带，增加纵深
    for x in range(0, w, 96):
        band = min(26, w - x)
        if band > 2:
            ramp = np.linspace(1.0, 0.0, band, dtype=np.float32)[None, :, None]
            bg[:horizon, x:x + band, :] += np.array([16, 34, 56], np.float32) * ramp
    return bg


# ===========================================================================
# 后处理查找表
# ===========================================================================
def build_vignette() -> np.ndarray:
    """(RES_H, RES_W) float32 乘数表：中心 1.0，四周压暗。"""
    h, w = C.RES_H, C.RES_W
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    cx, cy = (w - 1) * 0.5, (h - 1) * 0.5
    nx = (xx - cx) / cx
    ny = (yy - cy) / cy
    r = np.sqrt(nx * nx + ny * ny) / 1.4142
    vig = 1.0 - C.VIGNETTE_STRENGTH * (r ** 2.1)
    # 受击时的红边、以及轻微的中心提亮
    return np.clip(vig, 0.15, 1.0).astype(np.float32)


def build_scanline() -> np.ndarray:
    """(RES_H, 1) float32 乘数表：逐行明暗交替，模拟 CRT 扫描线。"""
    h = C.RES_H
    ramp = np.ones((h, 1), np.float32)
    ramp[::2] = 1.0 - C.SCANLINE_STRENGTH
    return ramp


def build_vignette_red() -> np.ndarray:
    """受击用的红色边缘蒙版 (RES_H, RES_W, 3)。"""
    h, w = C.RES_H, C.RES_W
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    cx, cy = (w - 1) * 0.5, (h - 1) * 0.5
    nx = (xx - cx) / cx
    ny = (yy - cy) / cy
    r = np.clip(np.sqrt(nx * nx + ny * ny) / 1.4142, 0, 1)
    edge = np.clip((r - 0.42) / 0.58, 0, 1) ** 1.5
    out = np.zeros((h, w, 3), np.float32)
    out[:, :, 0] = edge * 165.0
    out[:, :, 1] = edge * 16.0
    out[:, :, 2] = edge * 30.0
    return out


# ===========================================================================
# 敌人精灵
# ===========================================================================
def _new_sprite(w: int, h: int) -> pygame.Surface:
    s = pygame.Surface((w, h), pygame.SRCALPHA, 32)
    s.fill((0, 0, 0, 0))
    return s


def _draw_sentry(frame: int) -> pygame.Surface:
    """哨兵：瘦高双足机器人，持长管步枪，面部有扫描光条。"""
    body = C.ENEMY_TYPES["sentry"]["color"]
    accent = C.ENEMY_TYPES["sentry"]["accent"]
    s = _new_sprite(56, 76)
    bob = (0, -1, 0, 1)[frame % 4]
    leg_swing = (-4, 0, 4, 0)[frame % 4]

    # 腿
    pygame.draw.rect(s, (58, 30, 34), (20, 52, 6, 20 + leg_swing * 0), border_radius=3)
    pygame.draw.rect(s, (58, 30, 34), (30, 52, 6, 20), border_radius=3)
    pygame.draw.line(s, body, (22, 52), (17 + leg_swing, 72), 3)
    pygame.draw.line(s, body, (33, 52), (38 - leg_swing, 72), 3)
    # 躯干
    pygame.draw.rect(s, (46, 24, 30), (17, 24 + bob, 22, 30), border_radius=5)
    pygame.draw.rect(s, body, (19, 27 + bob, 18, 24), border_radius=4)
    pygame.draw.rect(s, (20, 12, 16), (24, 34 + bob, 8, 12), border_radius=2)
    # 胸口核心
    pygame.draw.circle(s, accent, (28, 31 + bob), 3)
    # 头
    pygame.draw.rect(s, (36, 20, 26), (21, 10 + bob, 14, 15), border_radius=4)
    pygame.draw.rect(s, (12, 8, 12), (23, 15 + bob, 10, 5))
    streak = 2 + (frame % 3) * 3
    pygame.draw.rect(s, accent, (23 + (streak % 5), 16 + bob, 4, 3))
    # 手臂 + 步枪
    pygame.draw.line(s, body, (18, 30 + bob), (10, 40 + bob), 4)
    pygame.draw.line(s, body, (38, 30 + bob), (46, 38 + bob), 4)
    pygame.draw.rect(s, (30, 34, 46), (40, 36 + bob, 15, 4))
    pygame.draw.rect(s, accent, (52, 36 + bob, 4, 4))
    # 肩灯
    pygame.draw.circle(s, accent, (17, 27 + bob), 2)
    return s


def _draw_stalker(frame: int) -> pygame.Surface:
    """潜行者：四足掠食者，贴地疾行，青绿色。"""
    body = C.ENEMY_TYPES["stalker"]["color"]
    accent = C.ENEMY_TYPES["stalker"]["accent"]
    s = _new_sprite(64, 52)
    legs = ((0, 3), (5, -2), (-4, 2), (2, -3))
    off = legs[frame % 4]
    body_y = 20 + (1 if frame % 2 else 0)

    # 四条腿
    for i, (dx, dy) in enumerate(legs):
        x0 = 12 + i * 12
        pygame.draw.line(s, (20, 70, 66), (x0, body_y + 8), (x0 + dx - 4, 46 + dy), 3)
    # 身躯
    pygame.draw.ellipse(s, (24, 92, 84), (8, body_y - 6, 44, 18))
    pygame.draw.ellipse(s, body, (10, body_y - 5, 40, 14))
    # 背脊光条
    pygame.draw.line(s, accent, (16, body_y - 4), (46, body_y - 4), 2)
    # 头（前倾）
    pygame.draw.polygon(s, (30, 110, 100), [(46, body_y - 4), (60, body_y + 1), (46, body_y + 8)])
    pygame.draw.circle(s, accent, (50, body_y + 2), 2)
    # 尾
    pygame.draw.line(s, body, (10, body_y), (2, body_y - 6 + frame % 2 * 4), 3)
    return s


def _draw_warden(frame: int) -> pygame.Surface:
    """守望者：重装炮台型单位，宽肩 + 中央能量核心。"""
    body = C.ENEMY_TYPES["warden"]["color"]
    accent = C.ENEMY_TYPES["warden"]["accent"]
    s = _new_sprite(80, 84)
    bob = (0, -1, -2, -1)[frame % 4]

    # 下肢支撑
    pygame.draw.rect(s, (40, 26, 54), (18, 58, 12, 24), border_radius=3)
    pygame.draw.rect(s, (40, 26, 54), (50, 58, 12, 24), border_radius=3)
    pygame.draw.rect(s, (28, 18, 40), (14, 78, 20, 5), border_radius=2)
    pygame.draw.rect(s, (28, 18, 40), (46, 78, 20, 5), border_radius=2)
    # 躯干
    pygame.draw.rect(s, (36, 22, 52), (16, 22 + bob, 48, 40), border_radius=8)
    pygame.draw.rect(s, body, (19, 25 + bob, 42, 34), border_radius=7)
    # 能量核心
    pr = 9 + (frame % 3)
    pygame.draw.circle(s, (255, 240, 200), (40, 42 + bob), max(4, pr - 3))
    pygame.draw.circle(s, accent, (40, 42 + bob), 5)
    # 肩甲
    pygame.draw.rect(s, (30, 18, 44), (4, 26 + bob, 16, 16), border_radius=5)
    pygame.draw.rect(s, (30, 18, 44), (60, 26 + bob, 16, 16), border_radius=5)
    pygame.draw.line(s, body, (10, 24 + bob), (66, 24 + bob), 3)
    # 头
    pygame.draw.rect(s, (26, 16, 38), (32, 10 + bob, 16, 14), border_radius=4)
    pygame.draw.rect(s, accent, (35, 15 + bob, 10, 4))
    # 双臂炮
    pygame.draw.rect(s, (44, 30, 58), (0, 42 + bob, 16, 9), border_radius=3)
    pygame.draw.rect(s, (44, 30, 58), (64, 42 + bob, 16, 9), border_radius=3)
    pygame.draw.rect(s, accent, (0, 44 + bob, 5, 5))
    pygame.draw.rect(s, accent, (75, 44 + bob, 5, 5))
    return s


def _draw_phantom(frame: int) -> pygame.Surface:
    """幻影：漂浮斗篷形态，下摆破碎波动，半透明。"""
    body = C.ENEMY_TYPES["phantom"]["color"]
    accent = C.ENEMY_TYPES["phantom"]["accent"]
    w, h = 52, 74
    s = _new_sprite(w, h)
    bob = (0, -2, -3, -1)[frame % 4]
    top = 10 + bob

    # 斗篷主体
    pygame.draw.polygon(s, (16, 52, 34), [
        (26, top), (42, top + 16), (38, top + 44), (30, top + 40),
        (26, top + 50), (20, top + 40), (12, top + 45), (14, top + 16),
    ])
    pygame.draw.polygon(s, (30, 96, 60), [
        (26, top + 2), (38, top + 18), (34, top + 40), (26, top + 44), (16, top + 38), (13, top + 18),
    ])
    # 兜帽内的空洞 + 双眼
    pygame.draw.ellipse(s, (6, 20, 16), (19, top + 6, 14, 16))
    pygame.draw.circle(s, accent, (23, top + 13), 2 + (frame % 2))
    pygame.draw.circle(s, accent, (29, top + 13), 2 + ((frame + 1) % 2))
    # 下摆能量须
    for i in range(5):
        x = 14 + i * 6
        ln = 6 + ((frame + i) % 4) * 3
        pygame.draw.line(s, body, (x, top + 44), (x + (i % 2 * 2 - 1) * 3, top + 44 + ln), 2)
    return s


_ENEMY_DRAWERS = {
    "sentry": (_draw_sentry, 1.02, 0.75),
    "stalker": (_draw_stalker, 0.62, 1.24),
    "warden": (_draw_warden, 1.16, 0.96),
    "phantom": (_draw_phantom, 1.00, 0.71),
}
ENEMY_FRAMES = 4


def build_enemy_sprites() -> Dict[str, Dict]:
    """
    生成敌人精灵表。

    返回 {type: {"frames": [rgba...], "h_world": 世界高度, "aspect": w/h}}
    h_world 以"墙高 = 1.0"为基准，所以数值都在 0.6~1.2 之间。
    """
    out: Dict[str, Dict] = {}
    for key, (drawer, h_world, aspect) in _ENEMY_DRAWERS.items():
        glow = C.ENEMY_TYPES[key]["color"]
        frames = [_apply_glow(drawer(i), glow, strength=0.75, passes=3)
                  for i in range(ENEMY_FRAMES)]
        out[key] = {
            "frames": frames,
            "h_world": h_world,
            "aspect": aspect,
            "glow": glow,
        }
    return out


# ===========================================================================
# 掉落物精灵
# ===========================================================================
def build_pickup_sprites() -> Dict[str, np.ndarray]:
    """每种掉落物一张 28x28 的发光图标。"""
    out: Dict[str, np.ndarray] = {}
    for key, info in C.PICKUP_TYPES.items():
        col = info["color"]
        s = _new_sprite(28, 28)
        if key == "energy":
            pygame.draw.polygon(s, (18, 40, 60), [(14, 2), (25, 14), (14, 26), (3, 14)])
            pygame.draw.polygon(s, col, [(14, 6), (21, 14), (14, 22), (7, 14)])
            pygame.draw.circle(s, (240, 255, 255), (14, 14), 3)
        elif key == "armor":
            pygame.draw.polygon(s, (44, 36, 16), [(14, 2), (25, 9), (25, 19), (14, 26), (3, 19), (3, 9)])
            pygame.draw.polygon(s, col, [(14, 6), (22, 11), (22, 18), (14, 23), (6, 18), (6, 11)])
            pygame.draw.line(s, (255, 250, 220), (14, 7), (14, 22), 2)
        elif key == "health":
            pygame.draw.rect(s, (60, 16, 30), (3, 3, 22, 22), border_radius=5)
            pygame.draw.rect(s, (100, 24, 46), (5, 5, 18, 18), border_radius=4)
            pygame.draw.rect(s, col, (11, 7, 6, 14))
            pygame.draw.rect(s, col, (7, 11, 14, 6))
        else:  # shard
            pygame.draw.polygon(s, (28, 18, 44), [(14, 2), (23, 12), (14, 26), (5, 12)])
            pygame.draw.polygon(s, col, [(14, 6), (20, 12), (14, 22), (8, 12)])
        out[key] = _apply_glow(s, col, strength=1.0, passes=2)
    return out


# ===========================================================================
# 武器（第一人称视角模型）
# ===========================================================================
def _gun_pulse() -> pygame.Surface:
    """脉冲冲锋枪：紧凑方形枪身 + 青色弹匣灯。"""
    s = _new_sprite(190, 150)
    c = C.WEAPONS[0]["color"]
    pygame.draw.polygon(s, (62, 74, 102), [(20, 96), (120, 62), (176, 84), (168, 112), (36, 132)])
    pygame.draw.polygon(s, (92, 106, 138), [(26, 94), (118, 68), (166, 86), (162, 104), (40, 124)])
    pygame.draw.rect(s, (56, 66, 90), (92, 40, 76, 16), border_radius=5)   # 枪管
    pygame.draw.rect(s, c, (58, 80, 54, 8), border_radius=3)               # 能量轨道
    for i in range(4):
        pygame.draw.rect(s, (200, 250, 255), (64 + i * 13, 82, 5, 4))
    pygame.draw.rect(s, (74, 88, 116), (44, 108, 34, 40), border_radius=6)  # 握把
    pygame.draw.circle(s, c, (150, 94), 5)
    pygame.draw.rect(s, (52, 62, 86), (152, 42, 12, 22))                   # 准星
    return s


def _gun_rail() -> pygame.Surface:
    """磁轨长矛：细长枪管 + 品红充能环。"""
    s = _new_sprite(190, 150)
    c = C.WEAPONS[1]["color"]
    pygame.draw.polygon(s, (70, 56, 94), [(16, 104), (126, 70), (186, 92), (176, 118), (30, 138)])
    pygame.draw.polygon(s, (102, 80, 132), [(24, 102), (124, 76), (178, 94), (170, 112), (36, 130)])
    pygame.draw.rect(s, (58, 48, 80), (86, 44, 104, 14), border_radius=4)
    for i in range(5):                                                     # 充能环
        x = 96 + i * 20
        pygame.draw.rect(s, c, (x, 40, 4, 22))
        pygame.draw.rect(s, (255, 220, 255), (x + 1, 46, 2, 10))
    pygame.draw.rect(s, c, (30, 84, 130, 6), border_radius=3)
    pygame.draw.rect(s, (66, 54, 90), (52, 116, 30, 34), border_radius=5)
    pygame.draw.circle(s, c, (168, 100), 6)
    return s


def _gun_scatter() -> pygame.Surface:
    """散射阵列：双联粗管 + 柠檬绿散热片。"""
    s = _new_sprite(200, 150)
    c = C.WEAPONS[2]["color"]
    pygame.draw.polygon(s, (66, 80, 62), [(14, 100), (130, 66), (192, 88), (182, 122), (26, 138)])
    pygame.draw.polygon(s, (98, 114, 88), [(22, 98), (128, 72), (184, 90), (176, 116), (32, 130)])
    pygame.draw.rect(s, (56, 66, 52), (96, 34, 44, 20), border_radius=6)   # 左管
    pygame.draw.rect(s, (56, 66, 52), (140, 44, 44, 20), border_radius=6)  # 右管
    pygame.draw.ellipse(s, c, (94, 36, 48, 16))
    pygame.draw.ellipse(s, c, (138, 46, 48, 16))
    for i in range(6):                                                     # 散热片
        pygame.draw.rect(s, (140, 210, 90), (52 + i * 11, 96, 6, 16))
    pygame.draw.rect(s, (62, 74, 58), (44, 118, 34, 32), border_radius=5)
    return s


def build_weapon_sprites() -> List[Dict]:
    """返回每把武器的持枪图与枪口火焰（坐标在屏幕右下）。"""
    makers = [_gun_pulse, _gun_rail, _gun_scatter]
    out = []
    for i, mk in enumerate(makers):
        gun = mk()
        c = C.WEAPONS[i]["color"]
        flash = _new_sprite(120, 120)
        pygame.draw.polygon(flash, (255, 255, 255), [(60, 10), (74, 56), (60, 74), (46, 56)])
        pygame.draw.polygon(flash, c, [(60, 0), (84, 52), (60, 92), (36, 52)])
        pygame.draw.circle(flash, (255, 240, 255), (60, 46), 16)
        out.append({
            "gun": gun,
            "flash": _apply_glow(flash, c, strength=1.0, passes=2),
        })
    return out


# ===========================================================================
# 敌人投射物
# ===========================================================================
def build_bullet_sprites() -> Dict[str, np.ndarray]:
    """玩家 / 敌人子弹的发光弹体。"""
    out = {}
    for key, col, size in (("player", (140, 250, 255), 22),
                           ("enemy", (255, 120, 90), 20),
                           ("rail", (255, 120, 230), 26)):
        s = _new_sprite(size, size)
        pygame.draw.circle(s, (255, 255, 255), (size // 2, size // 2), size // 6)
        pygame.draw.circle(s, col, (size // 2, size // 2), size // 3, 2)
        out[key] = _apply_glow(s, col, strength=1.0, passes=2)
    return out


def build_muzzle_flash_texture() -> np.ndarray:
    """枪口照亮的全屏渐变（开火时短暂叠加一层暖光）。"""
    h, w = C.RES_H, C.RES_W
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    cy, cx = h * 0.62, w * 0.68
    r = np.sqrt(((xx - cx) / w) ** 2 + ((yy - cy) / h) ** 2)
    f = np.clip(1.0 - r * 2.6, 0, 1) ** 1.6
    out = np.zeros((h, w, 3), np.float32)
    out[:, :, 0] = f * 90.0
    out[:, :, 1] = f * 70.0
    out[:, :, 2] = f * 55.0
    return out


def build_damage_numbers_font(size: int = 14) -> pygame.font.Font:
    """伤害飘字用的等宽字体。"""
    for name in ("consolas", "couriernew", "dejavusansmono"):
        try:
            f = pygame.font.SysFont(name, size, bold=True)
            if f:
                return f
        except Exception:
            continue
    return pygame.font.Font(None, size)
