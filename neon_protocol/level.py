# -*- coding: utf-8 -*-
"""
NEON PROTOCOL - 程序化关卡生成
==============================

算法：随机房间散布 + L 形走廊连通 + 环形回路 + 承重柱掩体
--------------------------------------------------------
1. **房间阶段**：在 MAP_W x MAP_H 的实心网格里随机投掷矩形房间，
   用 AABB 相交检测拒绝重叠（留 1 格间隔，避免房间粘成大厅）。
2. **连通阶段**：从第 2 个房间开始，依次用 L 形走廊把它连到"上一间"，
   保证整张图 100% 连通（这是生成式关卡的生死线：不能出现孤岛）。
3. **回路阶段**：额外随机补几条走廊形成环路 —— 纯树状地图会让玩家被堵死，
   环路是 FPS 关卡可玩性的关键（永远有退路，也支持绕后）。
4. **掩体阶段**：在大房间里放承重柱，制造掩体与视线遮挡，避免"空旷大厅互射"。

墙面分区
--------
用低频值噪声把整张地图切成若干"数据分区"，每个分区绑定一种墙材质
（电路 / 机柜 / 霓虹 / 全息幕墙），让不同区域有辨识度，玩家能凭视觉定位。
"""

from __future__ import annotations

import math
import random
from collections import deque
from typing import Dict, List, Optional, Sequence, Tuple

from . import config as C


# ===========================================================================
# 低频值噪声
# ===========================================================================
def value_noise(w: int, h: int, cell: int, rng: random.Random) -> List[List[float]]:
    """
    双线性插值的值噪声，返回 [0,1] 的二维场。

    cell 越大，色块越大。用于给地图做"区域划分"。
    """
    gw = w // cell + 3
    gh = h // cell + 3
    grid = [[rng.random() for _ in range(gw)] for _ in range(gh)]

    out = [[0.0] * w for _ in range(h)]
    for y in range(h):
        gy = y / cell
        y0 = int(gy)
        fy = gy - y0
        fy = fy * fy * (3.0 - 2.0 * fy)          # smoothstep，消除插值棱角
        for x in range(w):
            gx = x / cell
            x0 = int(gx)
            fx = gx - x0
            fx = fx * fx * (3.0 - 2.0 * fx)
            v00 = grid[y0][x0]
            v10 = grid[y0][x0 + 1]
            v01 = grid[y0 + 1][x0]
            v11 = grid[y0 + 1][x0 + 1]
            top = v00 + (v10 - v00) * fx
            bot = v01 + (v11 - v01) * fx
            out[y][x] = top + (bot - top) * fy
    return out


# ===========================================================================
# 房间
# ===========================================================================
class Room:
    __slots__ = ("x", "y", "w", "h", "cx", "cy")

    def __init__(self, x: int, y: int, w: int, h: int):
        self.x, self.y, self.w, self.h = x, y, w, h
        self.cx = x + w // 2
        self.cy = y + h // 2

    def intersects(self, o: "Room", pad: int = 1) -> bool:
        return not (self.x - pad >= o.x + o.w or
                    self.x + self.w + pad <= o.x or
                    self.y - pad >= o.y + o.h or
                    self.y + self.h + pad <= o.y)

    def center(self) -> Tuple[float, float]:
        return (self.x + self.w * 0.5, self.y + self.h * 0.5)

    def area(self) -> int:
        return self.w * self.h

    def rand_tile(self, rng: random.Random) -> Tuple[int, int]:
        return (rng.randrange(self.x, self.x + self.w),
                rng.randrange(self.y, self.y + self.h))


# ===========================================================================
# 关卡
# ===========================================================================
class Level:
    """
    一张数据要塞地图。

    属性
    ----
    grid      : List[List[int]]  墙类型 ID（0 = 可通行）
    rooms     : List[Room]
    spawn     : (x, y) 玩家出生点（浮点格子坐标）
    open_cells: List[(x, y)] 所有可通行格子，供出生点抽样
    dist      : List[List[int]] 从玩家出生点出发的 BFS 步数
    explored  : List[List[bool]] 战争迷雾（小地图用）
    """

    def __init__(self, rng: random.Random, wave_index: int = 0):
        self.rng = rng
        self.w = C.MAP_W
        self.h = C.MAP_H
        self.grid: List[List[int]] = [[C.WALL_PANEL] * self.w for _ in range(self.h)]
        self.rooms: List[Room] = []
        self.spawn: Tuple[float, float] = (2.5, 2.5)
        self.open_cells: List[Tuple[int, int]] = []
        self.dist: List[List[int]] = [[-1] * self.w for _ in range(self.h)]
        self.explored: List[List[bool]] = [[False] * self.w for _ in range(self.h)]
        self.complexity = 1.0          # 统计用：房间数 / 面积

        self._carve_rooms()
        self._connect_rooms()
        self._add_loops(wave_index)
        self._add_pillars()
        self._paint_walls()
        self._collect_open()
        self._pick_spawn()
        self._compute_distance()
        # 交给渲染器的快速索引副本：把 Python 嵌套 list 变成 numpy 数组，
        # 光线投射里就能用 grid_np[map_y, map_x] 一次性取完所有列，避免逐列循环。
        import numpy as _np
        self.grid_np = _np.array(self.grid, dtype=_np.int32)

    # ------------------------------------------------------------------
    # 查询接口
    # ------------------------------------------------------------------
    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.w and 0 <= y < self.h

    def is_wall(self, x: int, y: int) -> bool:
        """格子坐标是否为墙（越界视为墙）。"""
        if not self.in_bounds(x, y):
            return True
        return self.grid[y][x] != 0

    def is_wall_f(self, fx: float, fy: float) -> bool:
        """浮点坐标是否为墙。"""
        return self.is_wall(int(fx), int(fy))

    def cell(self, x: int, y: int) -> int:
        if not self.in_bounds(x, y):
            return C.WALL_PANEL
        return self.grid[y][x]

    def free_neighbors(self, x: int, y: int) -> int:
        """四邻域中可通行格数量，用于判断某个格子是否"贴墙"。"""
        n = 0
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            if not self.is_wall(x + dx, y + dy):
                n += 1
        return n

    def mark_explored(self, px: float, py: float, radius: int = 4) -> None:
        """把玩家周围区域标记为已探索（小地图淡出）。"""
        cx, cy = int(px), int(py)
        for dy in range(-radius, radius + 1):
            yy = cy + dy
            if yy < 0 or yy >= self.h:
                continue
            row = self.explored[yy]
            for dx in range(-radius, radius + 1):
                xx = cx + dx
                if 0 <= xx < self.w and dx * dx + dy * dy <= radius * radius:
                    row[xx] = True

    # ------------------------------------------------------------------
    # 生成步骤
    # ------------------------------------------------------------------
    def _carve_rooms(self) -> None:
        """随机投掷矩形房间，拒绝重叠。"""
        tries = 0
        while tries < C.ROOM_TRIES:
            tries += 1
            rw = self.rng.randint(C.ROOM_MIN, C.ROOM_MAX)
            rh = self.rng.randint(C.ROOM_MIN, C.ROOM_MAX)
            rx = self.rng.randint(2, self.w - rw - 3)
            ry = self.rng.randint(2, self.h - rh - 3)
            room = Room(rx, ry, rw, rh)
            if any(room.intersects(r, 1) for r in self.rooms):
                continue
            # 越大的房间越少：面积加权拒绝，避免地图被巨型房间填满
            if rng_reject(self.rng, room.area()):
                continue
            self.rooms.append(room)
            self._fill(rx, ry, rw, rh, 0)

        if not self.rooms:                       # 极端兜底
            r = Room(self.w // 2 - 4, self.h // 2 - 4, 8, 8)
            self.rooms.append(r)
            self._fill(r.x, r.y, r.w, r.h, 0)

    def _fill(self, x: int, y: int, w: int, h: int, v: int) -> None:
        for yy in range(max(1, y), min(self.h - 1, y + h)):
            row = self.grid[yy]
            for xx in range(max(1, x), min(self.w - 1, x + w)):
                row[xx] = v

    def _corridor(self, x1: int, y1: int, x2: int, y2: int) -> None:
        """L 形走廊：先横后竖（或反之），带随机宽度。"""
        wdt = C.CORRIDOR_WIDTH if self.rng.random() < 0.78 else 1
        if self.rng.random() < 0.5:
            self._fill(min(x1, x2), y1, abs(x2 - x1) + 1, wdt, 0)
            self._fill(x2, min(y1, y2), wdt, abs(y2 - y1) + 1, 0)
        else:
            self._fill(x1, min(y1, y2), wdt, abs(y2 - y1) + 1, 0)
            self._fill(min(x1, x2), y2, abs(x2 - x1) + 1, wdt, 0)

    def _connect_rooms(self) -> None:
        """顺序连通所有房间（等价于最小生成树的一条生成路径）。"""
        for i in range(1, len(self.rooms)):
            a = self.rooms[i - 1]
            b = self.rooms[i]
            self._corridor(a.cx, a.cy, b.cx, b.cy)

    def _add_loops(self, wave_index: int) -> None:
        """
        补环路。环路数量随波次略微增加：后期地图更"通透"，
        给玩家更多战术走位空间，同时敌人也更容易包抄。
        """
        extra = int(2 + self.rng.random() * 3 + wave_index * 0.35)
        for _ in range(extra):
            if len(self.rooms) < 2:
                break
            a, b = self.rng.sample(self.rooms, 2)
            self._corridor(a.cx, a.cy, b.cx, b.cy)

    def _add_pillars(self) -> None:
        """大房间里加承重柱：制造掩体，打破空旷。"""
        for room in self.rooms:
            if room.area() < 64:
                continue
            count = self.rng.randint(1, 3)
            for _ in range(count):
                px, py = room.rand_tile(self.rng)
                # 离房间边界至少 1 格，避免堵住门口
                if not (room.x + 1 < px < room.x + room.w - 2 and
                        room.y + 1 < py < room.y + room.h - 2):
                    continue
                if self.rng.random() < 0.5:
                    self.grid[py][px] = C.WALL_HEX_SAFE
                else:
                    for dx in range(2):
                        for dy in range(2):
                            if self.in_bounds(px + dx, py + dy):
                                self.grid[py + dy][px + dx] = C.WALL_HEX_SAFE

    def _paint_walls(self) -> None:
        """按低频噪声给墙面分区着色。"""
        noise = value_noise(self.w, self.h, 9, self.rng)
        palette = [
            C.WALL_PANEL, C.WALL_PANEL, C.WALL_HEX_SAFE,
            C.WALL_CIRCUIT, C.WALL_DATA, C.WALL_NEON, C.WALL_GLASS,
        ]
        for y in range(self.h):
            row = self.grid[y]
            nrow = noise[y]
            for x in range(self.w):
                if row[x] == 0:
                    continue
                if row[x] == C.WALL_HEX_SAFE:        # 柱子保持金属色
                    row[x] = C.WALL_HEX_SAFE
                    continue
                v = nrow[x]
                idx = int(v * len(palette))
                idx = max(0, min(len(palette) - 1, idx))
                # 边缘实心墙统一用暗色（反正看不到），降低无谓绘制
                if self.free_neighbors(x, y) == 0:
                    row[x] = C.WALL_PANEL
                else:
                    row[x] = palette[idx]

    def _collect_open(self) -> None:
        self.open_cells = []
        for y in range(self.h):
            row = self.grid[y]
            for x in range(self.w):
                if row[x] == 0:
                    self.open_cells.append((x, y))
        self.complexity = len(self.rooms) / max(1.0, len(self.open_cells) / 40.0)

    # ------------------------------------------------------------------
    def _pick_spawn(self) -> None:
        """
        玩家出生在最宽敞的房间正中。

        这里必须校验"中心格是否可通行" —— 承重柱有可能正好落在房间正中，
        一旦玩家出生在柱子里，就会永久卡死、整局报废。
        """
        if not self.rooms:
            self.spawn = (self.w * 0.5, self.h * 0.5)
            return
        best = max(self.rooms, key=lambda r: r.area())
        self.player_room = best
        cx, cy = best.center()
        ix, iy = int(cx), int(cy)
        if self.grid[iy][ix] == 0:
            self.spawn = (cx, cy)
            return
        # 就近螺旋搜索一个可通行格
        for r in range(1, max(best.w, best.h)):
            for dy in range(-r, r + 1):
                for dx in range(-r, r + 1):
                    nx, ny = ix + dx, iy + dy
                    if (best.x <= nx < best.x + best.w and best.y <= ny < best.y + best.h
                            and self.grid[ny][nx] == 0):
                        self.spawn = (nx + 0.5, ny + 0.5)
                        return
        # 极端兜底：任取一个可通行格
        for y in range(self.h):
            for x in range(self.w):
                if self.grid[y][x] == 0:
                    self.spawn = (x + 0.5, y + 0.5)
                    return
        self.spawn = (cx, cy)

    def _compute_distance(self) -> None:
        """从出生点做 BFS，得到每格步数（用于敌人出生点抽样与导航退化）。"""
        sx, sy = int(self.spawn[0]), int(self.spawn[1])
        self.dist = [[-1] * self.w for _ in range(self.h)]
        if self.is_wall(sx, sy):
            return
        self.dist[sy][sx] = 0
        q = deque([(sx, sy)])
        while q:
            x, y = q.popleft()
            d = self.dist[y][x] + 1
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if self.in_bounds(nx, ny) and self.dist[ny][nx] < 0 and self.grid[ny][nx] == 0:
                    self.dist[ny][nx] = d
                    q.append((nx, ny))

    # ------------------------------------------------------------------
    # 采样接口
    # ------------------------------------------------------------------
    def spawn_points(self, px: float, py: float, min_dist: float, count: int) -> List[Tuple[float, float]]:
        """
        抽取离玩家至少 min_dist 格、且相互间隔开的一批出生点。

        策略：先在候选池里按距离加权抽样，再确保彼此不重叠，
        避免整波敌人叠在同一个像素点上。
        """
        pool: List[Tuple[int, int]] = []
        md2 = min_dist * min_dist
        for (x, y) in self.open_cells:
            dx = x + 0.5 - px
            dy = y + 0.5 - py
            if dx * dx + dy * dy >= md2:
                # 只选离墙略远的格子，避免敌人卡在墙缝里
                if self.free_neighbors(x, y) >= 2:
                    pool.append((x, y))
        if not pool:
            pool = list(self.open_cells)

        picked: List[Tuple[float, float]] = []
        guard = 0
        while len(picked) < count and guard < count * 60 and pool:
            guard += 1
            x, y = self.rng.choice(pool)
            fx, fy = x + 0.5, y + 0.5
            ok = True
            for (ox, oy) in picked:
                if (ox - fx) ** 2 + (oy - fy) ** 2 < 4.0:
                    ok = False
                    break
            if ok:
                picked.append((fx, fy))
        # 数量不足时补齐（放宽间距限制）
        while len(picked) < count and pool:
            x, y = self.rng.choice(pool)
            picked.append((x + 0.5, y + 0.5))
        return picked

    def random_point(self) -> Tuple[float, float]:
        """任意可通行点（掉落物随机散布用）。"""
        if not self.open_cells:
            return self.spawn
        x, y = self.rng.choice(self.open_cells)
        return (x + 0.5, y + 0.5)

    def room_centers(self) -> List[Tuple[float, float]]:
        return [r.center() for r in self.rooms]

    # ------------------------------------------------------------------
    def render_minimap(self, scale: int = 2) -> Tuple[bytes, int, int]:
        """
        生成小地图的静态底图（未探索区域为黑）。

        返回 (rgb_bytes, w, h) —— 由 HUD 层包成 pygame 纹理缓存，
        避免每帧重画上千个矩形。
        """
        w, h = self.w * scale, self.h * scale
        buf = bytearray(w * h * 3)
        for y in range(self.h):
            for x in range(self.w):
                c = self.grid[y][x]
                if c == 0:
                    rgb = (26, 30, 48)
                elif c == C.WALL_HEX_SAFE:
                    rgb = (78, 88, 124)
                elif c == C.WALL_NEON:
                    rgb = (120, 40, 96)
                elif c == C.WALL_GLASS:
                    rgb = (30, 96, 118)
                elif c == C.WALL_CIRCUIT:
                    rgb = (30, 76, 86)
                elif c == C.WALL_DATA:
                    rgb = (46, 60, 92)
                else:
                    rgb = (44, 50, 76)
                for sy in range(scale):
                    base = ((y * scale + sy) * w + x * scale) * 3
                    for sx in range(scale):
                        o = base + sx * 3
                        buf[o] = rgb[0]
                        buf[o + 1] = rgb[1]
                        buf[o + 2] = rgb[2]
        return bytes(buf), w, h


# ---------------------------------------------------------------------------
def rng_reject(rng: random.Random, area: int) -> bool:
    """
    面积加权拒绝：面积超过 70 的房间只有 25% 概率通过。

    目的：让地图以中小房间为主，少量大房间做"战场"，
    避免出现连续几个全屏大厅导致节奏拖沓。
    """
    if area > 70:
        return rng.random() > 0.25
    if area > 50:
        return rng.random() > 0.55
    return False
