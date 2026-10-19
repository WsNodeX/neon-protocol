# -*- coding: utf-8 -*-
"""
NEON PROTOCOL - 光线投射渲染器
==============================

这是整个项目的技术核心：一个完全用 NumPy 向量化实现的伪 3D 渲染管线。
不使用 OpenGL / 着色器，所有像素都在 CPU 上用数组运算算出来。

管线顺序（每帧）
----------------
1. **背景层**：预计算的天空/地面渐变基色（静态，零开销）
2. **透视网格地面**：按行距离公式反算地板世界坐标，画出跟随相机移动的霓虹网格
3. **墙面层**：DDA 光线投射，把所有列的光线并行推进（向量化），采样纹理
   - 同时输出 z-buffer（每列到墙的距离），供精灵遮挡判定
4. **精灵层**：敌人/掉落物/子弹/粒子，按变换矩阵投影成公告板，逐像素 z 测试混合
5. **后处理**：枪口补光 -> 暗角 -> 扫描线 -> 受击红边 -> 信号撕裂 -> 输出

为什么这样写
------------
纯 Python 逐像素循环跑不动 480x300；NumPy 把"每列一条光线"变成一次数组运算，
把"每像素一次取样"变成一次 fancy indexing，从而在 CPU 上稳定 60 FPS。

关键数学
--------
- 相机平面：plane = P * perpendicular(dir)，P = tan(FOV/2)，控制水平视场
- 墙高投影：line_h = W / (2 * P * dist)   （像素/世界单位，眼睛高度 = 0.5 格）
- 地板行距离：dist = (0.5 * W / (2P)) / (y - horizon)
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pygame

from . import config as C
from . import textures as T
from . import utils as U


class Renderer:
    """把世界渲染到一张 RES_W x RES_H 的低分辨率缓冲面上。"""

    def __init__(self) -> None:
        self.w = C.RES_W
        self.h = C.RES_H
        self.S = C.TEX_SIZE

        # ---- 纹理 ----
        self.wall_tex = T.build_wall_textures()
        # 展开成 (8*S*S, 3)，用一维下标一次性取色，比逐张索引快得多
        self.wall_flat = np.concatenate(
            [t.reshape(-1, 3) for t in self.wall_tex], axis=0
        )
        self.enemy_sprites = T.build_enemy_sprites()
        self.pickup_sprites = T.build_pickup_sprites()
        self.bullet_sprites = T.build_bullet_sprites()

        # ---- 静态图层 ----
        self.bg = T.build_background_table()              # (H, W, 3) float32 基础色
        self.vignette = T.build_vignette()                # (H, W)
        self.scanline = T.build_scanline()                # (H, 1)
        # 预乘：暗角 x 扫描线 = 唯一的后处理乘数表
        self.post_mul = (self.vignette * self.scanline).astype(np.float32)
        self.red_edge = T.build_vignette_red()            # (H, W, 3)
        self.muzzle_glow = T.build_muzzle_flash_texture()  # (H, W, 3)

        # ---- 工作缓冲（复用，避免每帧分配几十 MB）----
        self.frame = np.zeros((self.h, self.w, 3), np.float32)
        self.out_u8 = np.zeros((self.w, self.h, 3), np.uint8)   # 输出用（已是转置布局）
        self.grid_mask = np.zeros((self.h, self.w), np.float32)
        self.zbuf = np.zeros(self.w, np.float32)
        # 24 位 Surface：与 (w,h,3) 数组深度匹配，surfarray 直接可用
        self.buffer = pygame.Surface((self.w, self.h), 0, 24)

        # ---- 网格线的逐行颜色（上半=天花板，下半=地板）----
        horizon = self.h // 2
        self.grid_color_rows = np.zeros((self.h, 3), np.float32)
        self.grid_color_rows[:horizon] = np.array([30, 14, 46], np.float32)      # 天花板：品紫
        self.grid_color_rows[horizon:] = np.array([16, 46, 62], np.float32)      # 地板：青蓝

        # 列方向预计算
        self.cols = np.arange(self.w, dtype=np.float32)
        self.cam_x = (2.0 * self.cols / self.w - 1.0).astype(np.float32)

        # 地板网格采样的列（每 2 像素一列，横向复制，省一半计算量）
        self.grid_cols = np.arange(0, self.w, 2, dtype=np.float32)
        self.grid_cam_x = (2.0 * self.grid_cols / self.w - 1.0).astype(np.float32)

        # 屏幕空间飘字（每帧由精灵层追加，HUD 层消费）
        self.float_texts: List[Tuple[float, float, str, Tuple[int, int, int], float]] = []

        # 调试统计
        self.stats: Dict[str, float] = {}

    # ==================================================================
    # 主入口
    # ==================================================================
    def render(self, world, dt: float) -> pygame.Surface:
        """
        渲染一帧。

        dt 用于驱动扫描线与撕裂等时间相关效果；世界状态通过 world 读取。
        """
        p = world.player
        level = world.level

        # —— 相机基向量 ——
        dir_x = math.cos(p.facing)
        dir_y = math.sin(p.facing)
        P = math.tan(math.radians(C.FOV_DEG) * 0.5)
        plane_x = -dir_y * P
        plane_y = dir_x * P

        # —— 抖动 / 俯仰 ——
        shx, shy = world.shake_offset()
        bob = math.sin(p.bob_phase) * C.BOB_AMP * p.bob_amount
        bob2 = abs(math.cos(p.bob_phase)) * C.BOB_AMP * 0.55 * p.bob_amount
        pitch_px = p.pitch * self.h + bob

        # 1) 背景
        np.copyto(self.frame, self.bg)

        # 2) 透视网格（同时画地板与天花板）
        self._draw_perspective_grid(p.x, p.y, dir_x, dir_y, plane_x, plane_y, pitch_px)

        # 3) 墙面 + z-buffer
        ppu = self.w / (2.0 * P)          # 距离 1 处 1 个世界单位占多少像素
        self._draw_walls(p.x, p.y, dir_x, dir_y, plane_x, plane_y, pitch_px, ppu, level)

        # 4) 精灵
        self._draw_sprites(world, dir_x, dir_y, plane_x, plane_y, pitch_px, ppu,
                           shx, shy)

        # 5) 后处理
        self._post_process(world, dt, shx, shy)

        # 6) 输出：截断到 0-255 -> uint8 -> 转置成 pygame 的 (w,h,c) 布局
        #    用预分配缓冲，避免每帧申请 400KB+ 的临时内存
        np.clip(self.frame, 0.0, 255.0, out=self.frame)
        np.copyto(self.out_u8, self.frame.astype(np.uint8).transpose(1, 0, 2))
        pygame.surfarray.blit_array(self.buffer, self.out_u8)
        return self.buffer

    # ==================================================================
    # 2) 透视网格地面
    # ==================================================================
    def _draw_perspective_grid(self, pos_x: float, pos_y: float,
                               dir_x: float, dir_y: float,
                               plane_x: float, plane_y: float,
                               pitch_px: float) -> None:
        """
        反投影地板：对地平线以下每一行，求出该行对应的世界距离，
        再乘上每个像素列的光线方向，得到地板上的世界坐标，据此画网格。

        天花板用同一组距离镜像复用（几何上完全等价），计算量减半。
        """
        horizon = self.h * 0.5 + pitch_px + 0.0
        ppu = self.w / (2.0 * math.tan(math.radians(C.FOV_DEG) * 0.5))

        # 与地平线的像素距离
        offs = np.arange(1, self.h, dtype=np.float32)
        d = (0.5 * ppu) / offs
        np.clip(d, 0.0, C.MAX_VIEW_DIST, out=d)

        # 采样列的光线方向
        rdx = dir_x + plane_x * self.grid_cam_x
        rdy = dir_y + plane_y * self.grid_cam_x

        fx = pos_x + rdx[None, :] * d[:, None]
        fy = pos_y + rdy[None, :] * d[:, None]

        # 到最近整数网格线的距离 -> 线宽（乘法代替除法，省一次逐元素除法）
        ax = np.abs(fx - np.rint(fx))
        ay = np.abs(fy - np.rint(fy))
        line = np.minimum(ax, ay)
        line *= -1.0 / 0.055          # line = -dist/0.055
        line += 1.0                   # line = 1 - dist/0.055
        np.clip(line, 0.0, 1.0, out=line)
        line *= line                  # 平方，让线条更锐利

        # 距离衰减：远处淡出，避免摩尔纹
        fade = np.clip(1.0 - d / (C.MAX_VIEW_DIST * 0.72), 0.0, 1.0)
        line *= (fade * fade)[:, None]

        # 横向 2 倍上采样到整屏宽
        full = np.repeat(line, 2, axis=1)
        if full.shape[1] < self.w:
            full = np.pad(full, ((0, 0), (0, self.w - full.shape[1])))

        # 分配到镜像行对：天花板 y = horizon - k，地板 y = horizon + k
        ci = np.rint(horizon - offs).astype(np.int32)
        fi = np.rint(horizon + offs).astype(np.int32)
        mask = self.grid_mask
        mask.fill(0.0)
        mc = (ci >= 0) & (ci < self.h)
        mf = (fi >= 0) & (fi < self.h)
        if mc.any():
            mask[ci[mc]] = full[mc]
        if mf.any():
            mask[fi[mf]] = full[mf]

        # 合成：网格线只占少部分像素，因此**只处理非零点**。
        # 全屏 144k 像素里通常只有 10~20k 个亮点，这一招能省掉 80% 的浮点运算。
        ys, xs = np.nonzero(mask)
        if ys.size:
            vals = mask[ys, xs]
            contrib = vals[:, None] * self.grid_color_rows[ys]
            self.frame[ys, xs] += contrib

    # ==================================================================
    # 3) 墙面
    # ==================================================================
    def _draw_walls(self, pos_x: float, pos_y: float,
                    dir_x: float, dir_y: float,
                    plane_x: float, plane_y: float,
                    pitch_px: float, ppu: float, level) -> None:
        """向量化 DDA：所有屏幕列的光线同步步进。"""
        W, H = self.w, self.h
        rdx = (dir_x + plane_x * self.cam_x).astype(np.float32)
        rdy = (dir_y + plane_y * self.cam_x).astype(np.float32)

        map_x = np.floor(pos_x).astype(np.int32) * np.ones(W, np.int32)
        map_y = np.floor(pos_y).astype(np.int32) * np.ones(W, np.int32)

        with np.errstate(divide="ignore", invalid="ignore"):
            delta_x = np.abs(1.0 / rdx).astype(np.float32)
            delta_y = np.abs(1.0 / rdy).astype(np.float32)
        delta_x = np.nan_to_num(delta_x, nan=1e30, posinf=1e30)
        delta_y = np.nan_to_num(delta_y, nan=1e30, posinf=1e30)

        step_x = np.where(rdx < 0, -1, 1).astype(np.int32)
        step_y = np.where(rdy < 0, -1, 1).astype(np.int32)

        side_x = np.where(rdx < 0, (pos_x - map_x) * delta_x,
                          (map_x + 1.0 - pos_x) * delta_x).astype(np.float32)
        side_y = np.where(rdy < 0, (pos_y - map_y) * delta_y,
                          (map_y + 1.0 - pos_y) * delta_y).astype(np.float32)

        hit = np.zeros(W, dtype=bool)
        oob_hit = np.zeros(W, dtype=bool)
        side = np.zeros(W, dtype=np.int8)
        tex_id = np.zeros(W, dtype=np.int32)
        grid_np = level.grid_np
        gw, gh = level.w, level.h
        gxm, gym = gw - 1, gh - 1

        # ------------------------------------------------------------------
        # 性能要点：这个循环是整条管线最热的地方。
        #
        # 两个关键优化：
        # 1. **不做边界检查**。地图生成保证最外一圈永远是实心墙，
        #    因此从内部出发的光线必然在越界之前撞墙。仍然保留 clip 兜底，
        #    但省掉了每步 4 次比较 + 2 次布尔合并。
        # 2. 用 `np.add(..., out=, where=)` 代替 `arr[mask] += x`。
        #    布尔花式索引每次都要构造索引数组并做 gather/scatter（3 次内存操作），
        #    ufunc 的 where 参数只在原数组上做一次掩码运算，快 2 倍以上。
        # ------------------------------------------------------------------
        max_steps = 96
        for _ in range(max_steps):
            active = ~hit
            if not active.any():
                break
            take_x = active & (side_x <= side_y)
            take_y = active & ~take_x

            np.copyto(side, 0, where=take_x)
            np.add(map_x, step_x, out=map_x, where=take_x)
            np.add(side_x, delta_x, out=side_x, where=take_x)

            np.copyto(side, 1, where=take_y)
            np.add(map_y, step_y, out=map_y, where=take_y)
            np.add(side_y, delta_y, out=side_y, where=take_y)

            cells = grid_np[np.clip(map_y, 0, gym), np.clip(map_x, 0, gxm)]
            solid = active & (cells != 0)
            if solid.any():
                hit |= solid
                np.copyto(tex_id, cells, where=solid)

        # 垂距（perpWallDist）：沿光线方向的距离，避免鱼眼畸变
        perp = np.where(side == 0, side_x - delta_x, side_y - delta_y).astype(np.float32)
        perp = np.maximum(perp, 0.02)
        perp[oob_hit] = 1.0e4
        self.zbuf[:] = perp

        line_h = ppu / perp
        horizon = H * 0.5 + pitch_px
        draw_start = horizon - line_h * 0.5
        draw_end = horizon + line_h * 0.5

        # 纹理横向坐标
        wall_x = np.where(side == 0,
                          pos_y + perp * rdy,
                          pos_x + perp * rdx).astype(np.float32)
        wall_x = wall_x - np.floor(wall_x)
        tex_x = (wall_x * self.S).astype(np.int32)
        flip = ((side == 0) & (rdx > 0)) | ((side == 1) & (rdy < 0))
        tex_x = np.where(flip, self.S - 1 - tex_x, tex_x)
        np.clip(tex_x, 0, self.S - 1, out=tex_x)

        # 只处理有墙覆盖的行区间，走廊尽头能省掉大量像素
        y0 = max(0, int(np.floor(np.min(draw_start))))
        y1 = min(H, int(np.ceil(np.max(draw_end))) + 1)
        if y1 <= y0:
            return

        ys = np.arange(y0, y1, dtype=np.float32)[:, None]
        inside = (ys >= draw_start[None, :]) & (ys < draw_end[None, :])

        ty = ((ys - draw_start[None, :]) * (self.S / np.maximum(line_h, 1e-4))[None, :])
        ty = np.clip(ty, 0.0, self.S - 0.001).astype(np.int32)

        flat = (tex_id[None, :] * (self.S * self.S) + ty * self.S + tex_x[None, :])
        colors = self.wall_flat[flat]                      # (ny, W, 3) uint8

        # 光照：距离雾 + 朝向明暗；自发光墙受雾影响更小
        fog_pow = np.ones(W, np.float32)
        for wid in C.EMISSIVE_WALLS:
            fog_pow[tex_id == wid] = C.EMISSIVE_FOG_POW
        t_fog = np.clip((perp - C.FOG_START) / max(0.001, C.MAX_VIEW_DIST - C.FOG_START), 0.0, 1.0)
        fog = np.power(1.0 - t_fog, fog_pow).astype(np.float32)
        side_mul = np.where(side == 0, 1.0, 0.74).astype(np.float32)
        light = (fog * side_mul)[None, :, None]

        block = np.multiply(colors, light, dtype=np.float32)
        np.copyto(self.frame[y0:y1], block, where=inside[:, :, None])

        # 在墙脚加一层"接地光晕"，让墙与地板自然衔接
        floor_line = np.rint(np.minimum(draw_end, H - 1)).astype(np.int32)
        valid = (perp < C.MAX_VIEW_DIST * 0.55) & (floor_line >= 0) & (floor_line < H)
        if valid.any():
            idx = floor_line[valid]
            cols = np.nonzero(valid)[0]
            glow_add = (np.array([26, 40, 70], np.float32)[None, :]
                        * (1.0 - np.clip(perp[valid] / (C.MAX_VIEW_DIST * 0.55), 0, 1))[:, None])
            tmp = self.frame[idx, cols] + glow_add
            self.frame[idx, cols] = tmp

    # ==================================================================
    # 4) 精灵
    # ==================================================================
    def _sprite_screen(self, wx: float, wy: float,
                       pos_x: float, pos_y: float,
                       dir_x: float, dir_y: float,
                       plane_x: float, plane_y: float) -> Optional[Tuple[float, float]]:
        """世界坐标 -> 相机空间 (transform_x, transform_y)。"""
        sx = wx - pos_x
        sy = wy - pos_y
        det = plane_x * dir_y - dir_x * plane_y
        if abs(det) < 1e-9:
            return None
        inv = 1.0 / det
        tx = inv * (dir_y * sx - dir_x * sy)
        ty = inv * (-plane_y * sx + plane_x * sy)
        return tx, ty

    def _blit_billboard(self, tex: np.ndarray, sx: float, ty: float, z: float,
                        h_world: float, ppu: float, horizon: float,
                        y_off: float, tint: Tuple[float, float, float],
                        alpha_scale: float) -> Tuple[float, float, float, float]:
        """
        把一张 RGBA 精灵按公告板方式贴进帧缓冲。

        返回屏幕包围盒 (x0, y0, x1, y1)（用于伤害飘字的定位）。
        """
        th, tw = tex.shape[0], tex.shape[1]
        if ty <= 0.14:
            return (0, 0, 0, 0)

        unit = ppu / ty                                   # 该距离下 1 世界单位 = 多少像素
        screen_x = (self.w * 0.5) * (1.0 + sx / ty)
        sprite_h = h_world * unit
        sprite_w = sprite_h * (tw / th)
        if sprite_h < 1.0 or sprite_w < 0.6:
            return (0, 0, 0, 0)

        # 眼睛高度 = 0.5 格：z 为精灵"脚底"的世界高度
        y_bottom = horizon - (z - 0.5) * unit + y_off
        y_top = y_bottom - sprite_h

        x0f = screen_x - sprite_w * 0.5
        x0 = int(math.floor(x0f))
        x1 = int(math.ceil(x0f + sprite_w))
        y0 = int(math.floor(y_top))
        y1 = int(math.ceil(y_bottom))

        cx0, cx1 = max(0, x0), min(self.w, x1)
        cy0, cy1 = max(0, y0), min(self.h, y1)
        if cx1 <= cx0 or cy1 <= cy0:
            return (0, 0, 0, 0)

        xs = np.arange(cx0, cx1, dtype=np.float32)[None, :]
        ys = np.arange(cy0, cy1, dtype=np.float32)[:, None]

        tx_i = ((xs - x0f) * (tw / max(1e-4, sprite_w))).astype(np.int32)
        np.clip(tx_i, 0, tw - 1, out=tx_i)
        ty_i = ((ys - y_top) * (th / max(1e-4, sprite_h))).astype(np.int32)
        np.clip(ty_i, 0, th - 1, out=ty_i)

        rgba = tex[ty_i, tx_i]                             # (ny, nx, 4)
        a = rgba[:, :, 3].astype(np.float32) * (alpha_scale / 255.0)
        ztest = (ty < self.zbuf[cx0:cx1][None, :])
        mask = (a > 0.012) & ztest

        if not mask.any():
            return (x0f, y_top, x0f + sprite_w, y_bottom)

        sub = self.frame[cy0:cy1, cx0:cx1]
        col = rgba[:, :, :3].astype(np.float32)
        col *= np.array(tint, np.float32)[None, None, :]
        sub[mask] = sub[mask] * (1.0 - a[mask, None]) + col[mask] * a[mask, None]
        return (x0f, y_top, x0f + sprite_w, y_bottom)

    def _blit_glow_point(self, px: float, py: float, pz: float,
                         size: float, color: Sequence[int], alpha: float,
                         pos_x: float, pos_y: float, dir_x: float, dir_y: float,
                         plane_x: float, plane_y: float,
                         ppu: float, horizon: float, y_off: float) -> None:
        """粒子：小尺寸发光方块，用距离平方衰减亮度。"""
        res = self._sprite_screen(px, py, pos_x, pos_y, dir_x, dir_y, plane_x, plane_y)
        if res is None:
            return
        sx, ty = res
        if ty <= 0.2:
            return
        unit = ppu / ty
        screen_x = (self.w * 0.5) * (1.0 + sx / ty)
        screen_y = horizon - (pz - 0.5) * unit + y_off
        r = max(0.7, size * unit)
        x0 = int(screen_x - r)
        x1 = int(screen_x + r) + 1
        y0 = int(screen_y - r)
        y1 = int(screen_y + r) + 1
        cx0, cx1 = max(0, x0), min(self.w, x1)
        cy0, cy1 = max(0, y0), min(self.h, y1)
        if cx1 <= cx0 or cy1 <= cy0:
            return
        if ty > C.MAX_VIEW_DIST:
            return

        # 亮度：距离衰减 + 中心高光
        fade = U.clamp(1.0 - ty / C.MAX_VIEW_DIST, 0.0, 1.0) * alpha
        if fade <= 0.02:
            return
        xs = np.arange(cx0, cx1, dtype=np.float32)[None, :]
        ys = np.arange(cy0, cy1, dtype=np.float32)[:, None]
        dist2 = ((xs - screen_x) ** 2 + (ys - screen_y) ** 2) / max(0.4, r * r)
        fall = np.clip(1.0 - dist2 * 0.55, 0.0, 1.0)
        add = fall * fade
        c = np.array(color, np.float32)[None, None, :]
        sub = self.frame[cy0:cy1, cx0:cx1]
        sub += add[:, :, None] * c * 1.6

    def _draw_sprites(self, world, dir_x: float, dir_y: float,
                      plane_x: float, plane_y: float,
                      pitch_px: float, ppu: float, shx: float, shy: float) -> None:
        """收集所有可见对象，按距离从远到近排序后依次绘制（画家算法）。"""
        p = world.player
        pos_x, pos_y = p.x, p.y
        horizon = self.h * 0.5 + pitch_px
        self.float_texts.clear()

        items: List[Tuple[float, int, object]] = []

        # 掉落物
        for pk in world.pickups:
            d = U.dist_sq(pos_x, pos_y, pk.x, pk.y)
            if d < C.MAX_VIEW_DIST ** 2:
                items.append((d, 0, pk))

        # 敌人
        for en in world.enemies:
            if not en.alive:
                continue
            d = U.dist_sq(pos_x, pos_y, en.x, en.y)
            if d < C.MAX_VIEW_DIST ** 2:
                items.append((d, 1, en))

        # 子弹
        for b in world.bullets:
            d = U.dist_sq(pos_x, pos_y, b.x, b.y)
            if d < C.MAX_VIEW_DIST ** 2:
                items.append((d, 2, b))

        items.sort(key=lambda it: -it[0])

        for d2, kind, obj in items:
            if kind == 0:                                   # ---- 掉落物 ----
                tx, ty = self._cam(obj.x, obj.y, pos_x, pos_y, dir_x, dir_y, plane_x, plane_y)
                if ty <= 0.15:
                    continue
                bob = math.sin(world.time * 3.0 + obj.phase) * 0.06
                tex = self.pickup_sprites[obj.kind]
                tint = (1.0, 1.0, 1.0)
                if obj.life < 4.0:                          # 即将消失时闪烁提醒
                    blink = 0.45 + 0.55 * (math.sin(world.time * 14.0) > 0)
                    tint = (blink, blink, blink)
                box = self._blit_billboard(tex, tx, ty, 0.28 + bob, 0.34, ppu,
                                           horizon, shy, tint, 1.0)
                _ = box

            elif kind == 1:                                 # ---- 敌人 ----
                en: "Enemy" = obj
                tx, ty = self._cam(en.x, en.y, pos_x, pos_y, dir_x, dir_y, plane_x, plane_y)
                if ty <= 0.15:
                    continue
                entry = self.enemy_sprites[en.kind]
                tex = entry["frames"][en.anim_frame % len(entry["frames"])]
                tint = (1.0, 1.0, 1.0)
                glowk = 1.0
                zbase = 0.0
                alpha = 1.0
                if en.hurt_flash > 0.01:                    # 受击白闪
                    f = en.hurt_flash
                    tint = (1.0 + f * 3.4, 1.0 + f * 1.6, 1.0 + f * 1.9)
                    glowk = 1.25
                if en.spawn_anim > 0.0:                     # 出生：从地板下升起 + 淡入
                    k = U.clamp(en.spawn_anim / 0.55, 0.0, 1.0)
                    zbase = -0.55 * k
                    alpha *= (1.0 - k * 0.85)
                if en.kind == "phantom":                    # 幻影半透明
                    alpha *= 0.62 + 0.22 * math.sin(world.time * 6.0 + en.id)
                box = self._blit_billboard(tex, tx, ty, zbase, entry["h_world"], ppu,
                                           horizon, shy, tint, alpha * glowk)
                # 血条：近距离或刚受击时显示，方便玩家判断优先目标
                if en.hp_ratio() < 0.999 and (en.hurt_flash > 0.02 or ty < 9.5):
                    self._queue_hp_bar(en, box, ty)

            else:                                           # ---- 子弹 ----
                b: "Bullet" = obj
                tx, ty = self._cam(b.x, b.y, pos_x, pos_y, dir_x, dir_y, plane_x, plane_y)
                if ty <= 0.2:
                    continue
                key = "rail" if b.owner == "player" and b.damage > 40 else (
                    "player" if b.owner == "player" else "enemy")
                tex = self.bullet_sprites[key]
                self._blit_billboard(tex, tx, ty, 0.5, b.size * 2.2, ppu, horizon,
                                     shy, (1.0, 1.0, 1.0), 1.0)

        # ---- 粒子（在实体之后画，始终可见但受距离衰减）----
        for pt in world.particles:
            life_k = pt.life / max(1e-4, pt.max_life)
            self._blit_glow_point(pt.x, pt.y, pt.z, pt.size, pt.color,
                                  life_k * (1.0 if pt.fade else 1.0),
                                  pos_x, pos_y, dir_x, dir_y, plane_x, plane_y,
                                  ppu, horizon, shy)

        # ---- 伤害飘字（投影成屏幕坐标，交给 pygame 画字）----
        for dn in world.damage_numbers:
            tx, ty = self._cam(dn.x, dn.y, pos_x, pos_y, dir_x, dir_y, plane_x, plane_y)
            if ty <= 0.3:
                continue
            unit = ppu / ty
            sx = (self.w * 0.5) * (1.0 + tx / ty)
            sy = horizon - (dn.z - 0.5) * unit + shy
            if 0 <= sx < self.w and 0 <= sy < self.h + 20:
                k = U.clamp(dn.life / dn.max_life, 0.0, 1.0)
                color = (255, 236, 120) if dn.crit else (235, 250, 255)
                self.float_texts.append((sx, sy, f"{int(dn.value)}", color, k))

    def _cam(self, wx, wy, pos_x, pos_y, dir_x, dir_y, plane_x, plane_y):
        res = self._sprite_screen(wx, wy, pos_x, pos_y, dir_x, dir_y, plane_x, plane_y)
        return res if res is not None else (0.0, -1.0)

    def _queue_hp_bar(self, en, box, ty) -> None:
        """把敌人血条记为屏幕空间浮字（用特殊前缀交给 HUD 层解析）。"""
        x0, y0, x1, y1 = box
        if x1 - x0 < 3.0:
            return
        self.float_texts.append((x0, y0, f"#HP:{en.hp_ratio():.3f}", (255, 90, 110), 1.0))

    # ==================================================================
    # 5) 后处理
    # ==================================================================
    def _post_process(self, world, dt: float, shx: float, shy: float) -> None:
        p = world.player

        # 枪口补光：开火瞬间照亮整个画面
        if p.flash > 0.02:
            self.frame += self.muzzle_glow * (p.flash * p.flash) * 1.4

        # 动态色偏：低血量时画面整体偏红（危险感提示）
        hp_k = 1.0 - (p.hp / max(1.0, p.max_hp))
        if hp_k > 0.35:
            pulse = 0.5 + 0.5 * math.sin(world.time * 5.0)
            k = (hp_k - 0.35) * 0.5 * (0.55 + 0.45 * pulse)
            self.frame[:, :, 0] *= (1.0 + k * 0.55)
            self.frame[:, :, 2] *= (1.0 - k * 0.30)

        # 受击红边
        if p.hurt_flash > 0.01:
            self.frame += self.red_edge * U.clamp(p.hurt_flash, 0.0, 1.0) * 0.85

        # 暗角 + 扫描线：两者都是"逐像素乘一个系数"，
        # 事先乘成一张表，后处理里就只剩**一次**全屏乘法（而不是两次）。
        self.frame *= self.post_mul[:, :, None]

        # 信号撕裂：低血量 / 受击时概率提升，黑客入侵的视觉母题
        glitch_p = C.GLITCH_CHANCE + p.hurt_flash * 0.22 + hp_k * 0.02
        if world.rng.random() < glitch_p:
            slices = world.rng.randint(1, 3)
            for _ in range(slices):
                y0 = world.rng.randrange(0, self.h - 2)
                hh = world.rng.randint(1, 5)
                shift = world.rng.randint(-14, 14)
                self.frame[y0:y0 + hh] = np.roll(self.frame[y0:y0 + hh], shift, axis=1)
                if world.rng.random() < 0.5:
                    self.frame[y0:y0 + hh, :, 2] *= 0.55

        # 死亡：画面整体抖动 + 去色
        if not p.alive:
            self.frame[:, :, 1] *= 0.7
            self.frame[:, :, 2] *= 0.7

    # ==================================================================
    @staticmethod
    def camera_vectors(facing: float) -> Tuple[float, float, float, float]:
        """供 HUD / 其他模块复用的相机基向量。"""
        dir_x = math.cos(facing)
        dir_y = math.sin(facing)
        P = math.tan(math.radians(C.FOV_DEG) * 0.5)
        return dir_x, dir_y, -dir_y * P, dir_x * P
