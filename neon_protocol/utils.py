# -*- coding: utf-8 -*-
"""
NEON PROTOCOL - 数学与通用工具
==============================

这里只放"不依赖 pygame 渲染上下文"的纯计算逻辑，方便单独测试。
包含：
- 向量数学（2D）
- 角度插值、阻尼
- 网格射线（用于 AI 视线判定）
- 简易随机工具（带权抽样）
- 颜色处理
"""

from __future__ import annotations

import math
import random
from typing import Iterable, List, Sequence, Tuple


# ---------------------------------------------------------------------------
# 基础数学
# ---------------------------------------------------------------------------
def clamp(v: float, lo: float, hi: float) -> float:
    """把 v 夹在 [lo, hi] 区间内。"""
    return lo if v < lo else (hi if v > hi else v)


def lerp(a: float, b: float, t: float) -> float:
    """线性插值。t=0 返回 a，t=1 返回 b。"""
    return a + (b - a) * t


def inv_lerp(a: float, b: float, v: float) -> float:
    """lerp 的反函数，返回 v 在 [a,b] 中的归一化位置。"""
    if abs(b - a) < 1e-9:
        return 0.0
    return clamp((v - a) / (b - a), 0.0, 1.0)


def damp(current: float, target: float, rate: float, dt: float) -> float:
    """
    帧率无关的指数阻尼。用于摄像机抖动、准心收缩等"平滑跟随"场景。
    比 lerp(cur, target, 0.1) 更稳定，因为结果不依赖单帧时长。
    """
    if rate <= 0.0:
        return target
    return target + (current - target) * math.exp(-rate * dt)


def approach(current: float, target: float, max_delta: float) -> float:
    """以固定步长逼近目标，不会过冲。"""
    if current < target:
        return min(current + max_delta, target)
    return max(current - max_delta, target)


def signed_angle_diff(a: float, b: float) -> float:
    """返回 a 到 b 的最短角差，落在 (-pi, pi]。"""
    d = (b - a) % (2.0 * math.pi)
    if d > math.pi:
        d -= 2.0 * math.pi
    return d


def rotate_towards(a: float, b: float, max_step: float) -> float:
    """把角度 a 朝 b 转动最多 max_step 弧度。"""
    d = signed_angle_diff(a, b)
    if abs(d) <= max_step:
        return b % (2.0 * math.pi)
    return (a + math.copysign(max_step, d)) % (2.0 * math.pi)


# ---------------------------------------------------------------------------
# 2D 向量（纯函数式，避免对象分配开销）
# ---------------------------------------------------------------------------
def norm(x: float, y: float) -> Tuple[float, float]:
    """归一化 2D 向量，零向量返回 (0,0)。"""
    m = math.hypot(x, y)
    if m < 1e-9:
        return 0.0, 0.0
    return x / m, y / m


def dist(ax: float, ay: float, bx: float, by: float) -> float:
    return math.hypot(bx - ax, by - ay)


def dist_sq(ax: float, ay: float, bx: float, by: float) -> float:
    dx = bx - ax
    dy = by - ay
    return dx * dx + dy * dy


def angle_to(ax: float, ay: float, bx: float, by: float) -> float:
    """从 a 指向 b 的弧度角。"""
    return math.atan2(by - ay, bx - ax)


def circle_rect_overlap(cx: float, cy: float, r: float,
                        rx: float, ry: float, rw: float, rh: float) -> bool:
    """圆与轴对齐矩形的相交测试（用于像素级 UI 碰撞）。"""
    nx = clamp(cx, rx, rx + rw)
    ny = clamp(cy, ry, ry + rh)
    return dist_sq(cx, cy, nx, ny) <= r * r


# ---------------------------------------------------------------------------
# 网格射线（Bresenham / DDA on grid）
# ---------------------------------------------------------------------------
def grid_ray_hit(grid: Sequence[Sequence[int]], x0: float, y0: float,
                 x1: float, y1: float, max_steps: int = 128) -> bool:
    """
    在整数网格上做 DDA 步进，判断 (x0,y0) -> (x1,y1) 之间是否被墙阻断。

    用于敌人 AI 的"视线判定"：只有真正看得见玩家，才进入追击/攻击状态。
    这避免了敌人隔着墙开枪的经典 bug。

    返回 True 表示被墙挡住（无视线）。
    """
    h = len(grid)
    if h == 0:
        return False
    w = len(grid[0])

    dx = x1 - x0
    dy = y1 - y0
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return False

    # 每前进 0.25 格采样一次：足够精确，且比纯 DDA 更快读懂
    step = 0.25
    n = int(length / step) + 1
    if n > max_steps * 4:
        n = max_steps * 4

    ux = dx / n
    uy = dy / n
    px, py = x0, y0
    for _ in range(n):
        px += ux
        py += uy
        mx = int(px)
        my = int(py)
        if mx < 0 or my < 0 or mx >= w or my >= h:
            return True
        if grid[my][mx] != 0:
            return True
    return False


def ray_cast_distance(grid: Sequence[Sequence[int]], x0: float, y0: float,
                      ang: float, max_dist: float, step: float = 0.12) -> float:
    """
    从 (x0,y0) 沿 ang 方向投射线，返回撞墙前的距离（最大 max_dist）。

    用于：AI 的避障探测（触须算法）、瞬移落点校验。
    """
    h = len(grid)
    w = len(grid[0]) if h else 0
    dx = math.cos(ang) * step
    dy = math.sin(ang) * step
    px, py = x0, y0
    travelled = 0.0
    while travelled < max_dist:
        px += dx
        py += dy
        travelled += step
        mx = int(px)
        my = int(py)
        if mx < 0 or my < 0 or mx >= w or my >= h or grid[my][mx] != 0:
            return travelled
    return max_dist


# ---------------------------------------------------------------------------
# 随机工具
# ---------------------------------------------------------------------------
def weighted_choice(rng: random.Random, table: Iterable[Tuple[str, float]]) -> str:
    """按权重抽样。table 为 [(key, weight), ...]。"""
    items = [(k, max(0.0, float(w))) for k, w in table]
    total = sum(w for _, w in items)
    if total <= 0.0:
        return items[0][0] if items else ""
    r = rng.random() * total
    acc = 0.0
    for k, w in items:
        acc += w
        if r <= acc:
            return k
    return items[-1][0]


def jitter(rng: random.Random, amount: float) -> float:
    """[-amount, amount) 之间的均匀随机抖动。"""
    return (rng.random() * 2.0 - 1.0) * amount


# ---------------------------------------------------------------------------
# 颜色
# ---------------------------------------------------------------------------
def mix(c1: Sequence[int], c2: Sequence[int], t: float) -> Tuple[int, int, int]:
    """两色线性混合，t=0 返回 c1。"""
    t = clamp(t, 0.0, 1.0)
    return (
        int(c1[0] + (c2[0] - c1[0]) * t),
        int(c1[1] + (c2[1] - c1[1]) * t),
        int(c1[2] + (c2[2] - c1[2]) * t),
    )


def shade(c: Sequence[int], f: float) -> Tuple[int, int, int]:
    """整体亮度缩放，结果自动夹在 0-255。"""
    return (
        int(clamp(c[0] * f, 0, 255)),
        int(clamp(c[1] * f, 0, 255)),
        int(clamp(c[2] * f, 0, 255)),
    )


def with_alpha(c: Sequence[int], a: int) -> Tuple[int, int, int, int]:
    return (int(c[0]), int(c[1]), int(c[2]), int(clamp(a, 0, 255)))


def hex_to_rgb(s: str) -> Tuple[int, int, int]:
    s = s.lstrip("#")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


# ---------------------------------------------------------------------------
# 轻量环形缓冲：用于 FPS 统计
# ---------------------------------------------------------------------------
class Ring:
    """固定容量环形缓冲，O(1) 均值，用来算平滑帧时间。"""

    def __init__(self, size: int = 60):
        self.size = size
        self.data: List[float] = [0.0] * size
        self.idx = 0
        self.filled = 0

    def push(self, v: float) -> None:
        self.data[self.idx] = v
        self.idx = (self.idx + 1) % self.size
        if self.filled < self.size:
            self.filled += 1

    def mean(self) -> float:
        if self.filled == 0:
            return 0.0
        return sum(self.data[:self.filled]) / self.filled
