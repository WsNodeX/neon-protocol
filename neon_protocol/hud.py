# -*- coding: utf-8 -*-
"""
NEON PROTOCOL - HUD 与界面层
============================

所有 UI 都画在"输出窗口分辨率"上（而不是低分辨率渲染缓冲），
这样可以同时拥有：低分辨率 3D 画面的复古颗粒感 + 锐利的现代 UI。

包含
----
- 动态准心（随后坐力/移动扩散 + 命中标记）
- 分段式状态条（结构完整度 / 护盾 / 能量）
- 武器面板与武器槽
- 本地雷达（以玩家为中心的局部小地图）+ 全屏地图（Tab）
- 伤害方向指示器
- 标题 / 升级选卡 / 暂停 / 失败 / 通关 五种界面
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

import pygame

from . import config as C
from . import utils as U

# 常用字体候选（等宽优先，符合终端/黑客调性）
MONO_FONTS = ("consolas", "dejavusansmono", "couriernew", "monospace")
CN_FONTS = ("microsoftyaheiui", "microsoftyahei", "simhei", "dengxian", "simsun")
CN_FONT_FILES = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
)


class HUD:
    def __init__(self, size: Tuple[int, int]):
        self.w, self.h = size
        self._font_cache: Dict[Tuple[str, int, bool], pygame.font.Font] = {}
        self._cn_cache: Dict[int, pygame.font.Font] = {}
        self.cn_font: Optional[pygame.font.Font] = None
        self.cn_font_path: Optional[str] = None
        self._minimap_cache: Dict[int, pygame.Surface] = {}
        self._terrain_cache: Dict[int, pygame.Surface] = {}
        self._flash_cache: Dict[int, pygame.Surface] = {}
        self._gun_cache: Dict[int, Tuple[int, int, pygame.Surface]] = {}
        self._text_cache: Dict[tuple, tuple] = {}
        self._panel_cache: Dict[tuple, pygame.Surface] = {}
        self._build_fonts()

        # 动态元素
        self.spread = 0.0
        self.hit_marker = 0.0
        self.kill_marker = 0.0
        self.radar_cells = 15

    # ==================================================================
    # 字体
    # ==================================================================
    def _build_fonts(self) -> None:
        for path in CN_FONT_FILES:
            try:
                self.cn_font = pygame.font.Font(path, 22)
                self.cn_font_path = path
                break
            except Exception:
                continue
        if self.cn_font is None:
            try:
                f = pygame.font.SysFont(",".join(CN_FONTS), 22)
                if f is not None:
                    self.cn_font = f
            except Exception:
                self.cn_font = None

    def cn(self, size: int) -> Optional[pygame.font.Font]:
        """按尺寸取中文字体（带缓存）。找不到中文字体时返回 None。"""
        if self.cn_font is None:
            return None
        key = int(size)
        f = self._cn_cache.get(key)
        if f is None:
            if self.cn_font_path:
                try:
                    f = pygame.font.Font(self.cn_font_path, key)
                except Exception:
                    f = self.cn_font
            else:
                f = self.cn_font
            self._cn_cache[key] = f
        return f

    def font(self, size: int, bold: bool = False) -> pygame.font.Font:
        key = ("mono", size, bold)
        f = self._font_cache.get(key)
        if f is None:
            f = None
            for name in MONO_FONTS:
                try:
                    f = pygame.font.SysFont(name, size, bold=bold)
                    if f:
                        break
                except Exception:
                    continue
            if f is None:
                f = pygame.font.Font(None, size)
            self._font_cache[key] = f
        return f

    # ==================================================================
    # 绘制原语
    # ==================================================================
    def text(self, surf: pygame.Surface, s: str, pos: Tuple[float, float],
             color: Sequence[int], size: int = 16, bold: bool = False,
             align: str = "left", glow: Optional[Sequence[int]] = None,
             alpha: int = 255) -> Tuple[int, int]:
        """
        带辉光的等宽文本。align: left / center / right。

        性能要点：font.render + 4 次辉光偏移 blit 是相当贵的操作，
        而 HUD 每帧要画 30+ 段文字。因此这里做**已渲染图缓存**：
        键 = (文本, 颜色, 字号, 粗体, 是否有辉光)，alpha<255 的淡入淡出
        文字不缓存（set_alpha 会污染缓存对象）。
        """
        f = self.font(size, bold)
        key = (s, tuple(int(c) for c in color), size, bold,
               None if glow is None else tuple(int(c) for c in glow), alpha)
        cached = self._text_cache.get(key)
        if cached is not None:
            layer, tw, th, al = cached
            # 锚点换算：缓存里只存尺寸与对齐方式，位置每次按 pos 现算
            if al == "center":
                bx = int(pos[0]) - tw // 2
            elif al == "right":
                bx = int(pos[0]) - tw
            else:
                bx = int(pos[0])
            surf.blit(layer, (bx - 1, int(pos[1]) - 1))
            return (tw, th)

        img = f.render(s, True, tuple(int(c) for c in color))
        if alpha < 255:
            img.set_alpha(alpha)
        rect = img.get_rect()
        if align == "center":
            rect.midtop = (int(pos[0]), int(pos[1]))
        elif align == "right":
            rect.topright = (int(pos[0]), int(pos[1]))
        else:
            rect.topleft = (int(pos[0]), int(pos[1]))

        if glow is not None or alpha == 255:
            layer = pygame.Surface((rect.w + 3, rect.h + 3), pygame.SRCALPHA)
            ox, oy = 1, 1
            if glow is not None:
                gi = f.render(s, True, tuple(int(c) for c in glow))
                if alpha < 255:
                    gi.set_alpha(alpha // 2)
                for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    layer.blit(gi, (ox + dx, oy + dy))
            layer.blit(img, (ox, oy))
            if alpha == 255:
                if len(self._text_cache) > 900:
                    self._text_cache.clear()
                self._text_cache[key] = (layer, rect.w, rect.h, align)
            surf.blit(layer, (rect.x - ox, rect.y - oy))
        else:
            surf.blit(img, rect)
        return rect.size

    def text_cn(self, surf: pygame.Surface, s: str, pos: Tuple[float, float],
                color: Sequence[int], size: int = 20, align: str = "left",
                alpha: int = 255) -> None:
        """中文文本（找不到中文字体时自动跳过，避免出现方块）。"""
        f = self.cn(size)
        if f is None:
            return
        img = f.render(s, True, tuple(int(c) for c in color))
        if alpha < 255:
            img.set_alpha(alpha)
        rect = img.get_rect()
        if align == "center":
            rect.midtop = (int(pos[0]), int(pos[1]))
        elif align == "right":
            rect.topright = (int(pos[0]), int(pos[1]))
        else:
            rect.topleft = (int(pos[0]), int(pos[1]))
        surf.blit(img, rect)

    def panel(self, surf: pygame.Surface, rect: pygame.Rect,
              color: Sequence[int] = C.NEON["cyan"], alpha: int = 190,
              fill_alpha: int = 118, corner: int = 10, thickness: int = 2) -> None:
        """科技感面板：半透明底 + 四角短线（切断的边框）。

        面板的位置与尺寸在整局内是固定的，因此把"半透明底"这张 SRCALPHA 图层
        缓存起来复用，省掉每帧 5 次 Surface 分配。
        """
        key = (rect.x, rect.y, rect.w, rect.h, fill_alpha)
        layer = self._panel_cache.get(key)
        if layer is None:
            layer = pygame.Surface((rect.w, rect.h), pygame.SRCALPHA)
            layer.fill((10, 12, 24, fill_alpha))
            if len(self._panel_cache) > 64:
                self._panel_cache.clear()
            self._panel_cache[key] = layer
        surf.blit(layer, rect.topleft)
        c = tuple(int(v) for v in color)
        ln = max(4, corner)
        pygame.draw.line(surf, c, (rect.x, rect.y), (rect.x + ln, rect.y), thickness)
        pygame.draw.line(surf, c, (rect.x, rect.y), (rect.x, rect.y + ln), thickness)
        pygame.draw.line(surf, c, (rect.right - ln, rect.y), (rect.right, rect.y), thickness)
        pygame.draw.line(surf, c, (rect.right, rect.y), (rect.right, rect.y + ln), thickness)
        pygame.draw.line(surf, c, (rect.x, rect.bottom), (rect.x + ln, rect.bottom), thickness)
        pygame.draw.line(surf, c, (rect.x, rect.bottom - ln), (rect.x, rect.bottom), thickness)
        pygame.draw.line(surf, c, (rect.right - ln, rect.bottom), (rect.right, rect.bottom), thickness)
        pygame.draw.line(surf, c, (rect.right, rect.bottom - ln), (rect.right, rect.bottom), thickness)

    def seg_bar(self, surf: pygame.Surface, rect: pygame.Rect, ratio: float,
                color: Sequence[int], segments: int = 24, bg=(24, 28, 44),
                dim: float = 0.26) -> None:
        """分段式能量条：比实心条更有"仪表盘"的技术感。"""
        ratio = U.clamp(ratio, 0.0, 1.0)
        pygame.draw.rect(surf, bg, rect)
        sw = rect.w / segments
        gap = max(1.0, sw * 0.18)
        filled = ratio * segments
        for i in range(segments):
            x = rect.x + i * sw
            amount = U.clamp(filled - i, 0.0, 1.0)
            w = max(1, int(sw - gap))
            col = tuple(int(c * (0.22 + 0.78 * amount)) for c in color)
            pygame.draw.rect(surf, col, (int(x), rect.y + 1, w, rect.h - 2))
        pygame.draw.rect(surf, (70, 84, 118), rect, 1)

    # ==================================================================
    # 游戏内 HUD
    # ==================================================================
    def draw_gameplay(self, surf: pygame.Surface, world, fps: float) -> None:
        p = world.player
        sx = self.w / C.RES_W       # 低分辨率 -> 窗口 的换算系数

        self._draw_crosshair(surf, world)
        self._draw_status(surf, world, p)
        self._draw_weapon_panel(surf, world, p)
        self._draw_top_bar(surf, world, fps)
        self._draw_radar(surf, world)
        self._draw_notifications(surf, world)
        self._draw_damage_dirs(surf, world)
        self._draw_float_texts(surf, world, sx)

    # ------------------------------------------------------------------
    def _draw_crosshair(self, surf: pygame.Surface, world) -> None:
        p = world.player
        cx, cy = self.w * 0.5, self.h * 0.5
        # 移动/后坐力导致的扩散
        moving = min(1.0, math.hypot(p.vx, p.vy) / max(0.01, p.speed))
        spread = 6.0 + self.spread * 26.0 + moving * 7.0 + p.recoil * 12.0
        col = C.NEON["cyan"]
        if p.cooldown > 0.0:
            col = U.mix(col, (255, 255, 255), 0.4)
        d = spread
        ln = 8
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            x0 = cx + dx * d
            y0 = cy + dy * d
            pygame.draw.line(surf, col, (x0, y0), (x0 + dx * ln, y0 + dy * ln), 2)
        pygame.draw.circle(surf, col, (int(cx), int(cy)), 1)
        # 命中标记
        if self.hit_marker > 0.01:
            k = self.hit_marker
            mc = (255, 255, 255) if self.kill_marker <= 0.01 else (255, 96, 96)
            off = 5 + (1.0 - k) * 5
            for dx, dy in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
                pygame.draw.line(surf, mc, (cx + dx * off, cy + dy * off),
                                 (cx + dx * (off + 7), cy + dy * (off + 7)), 2)

    # ------------------------------------------------------------------
    def _draw_status(self, surf: pygame.Surface, world, p) -> None:
        x = 26
        y = self.h - 118
        rect = pygame.Rect(x - 12, y - 12, 404, 106)
        self.panel(surf, rect, C.NEON["cyan"], fill_alpha=104)

        # 结构完整度
        self.text(surf, "INTEGRITY", (x, y - 4), C.UI_DIM, 13)
        self.seg_bar(surf, pygame.Rect(x, y + 11, 300, 12),
                     p.hp / max(1.0, p.max_hp), C.NEON["red"], 30)
        self.text(surf, f"{int(max(0, p.hp))}/{int(p.max_hp)}", (x + 308, y + 10),
                  C.UI_FG, 13, align="left")
        if p.hp / max(1.0, p.max_hp) < 0.3:
            pulse = 0.5 + 0.5 * math.sin(world.time * 9.0)
            self.text(surf, "! CRITICAL !", (x + 308, y + 24),
                      U.mix((60, 20, 26), C.NEON["red"], pulse), 11)

        # 护盾
        self.text(surf, "AEGIS", (x, y + 28), C.UI_DIM, 13)
        self.seg_bar(surf, pygame.Rect(x, y + 43, 300, 10),
                     p.armor / max(1.0, p.max_armor), C.NEON["amber"], 30)
        self.text(surf, f"{int(p.armor)}", (x + 308, y + 40), C.UI_FG, 12)

        # 能量
        self.text(surf, "ENERGY", (x, y + 60), C.UI_DIM, 13)
        self.seg_bar(surf, pygame.Rect(x, y + 75, 300, 10),
                     p.energy / max(1.0, p.max_energy), C.NEON["cyan"], 30)
        self.text(surf, f"{int(p.energy)}", (x + 308, y + 72), C.UI_FG, 12)

        # 冲刺冷却指示
        dx = x + 352
        ready = p.dash_cd <= 0.0
        col = C.NEON["lime"] if ready else (90, 104, 140)
        pygame.draw.circle(surf, col, (dx + 12, y + 20), 10, 2)
        if not ready:
            k = 1.0 - p.dash_cd / max(0.001, C.DASH_COOLDOWN * p.dash_cd_mul)
            pygame.draw.arc(surf, C.NEON["cyan"], (dx + 2, y + 10, 20, 20),
                            -math.pi / 2, -math.pi / 2 + k * math.tau, 3)
        self.text(surf, "SHIFT", (dx + 12, y + 34), col, 10, align="center")

    # ------------------------------------------------------------------
    def _draw_weapon_panel(self, surf: pygame.Surface, world, p) -> None:
        w = p.weapon_data()
        pw, ph = 292, 106
        x = self.w - pw - 26
        y = self.h - ph - 26
        rect = pygame.Rect(x, y, pw, ph)
        self.panel(surf, rect, w["color"], fill_alpha=104)

        self.text(surf, w["name"], (x + 14, y + 8), w["color"], 16, bold=True,
                  glow=U.shade(w["color"], 0.4))
        self.text(surf, w["cn"], (x + 14, y + 28), C.UI_DIM, 12)

        cost = w["cost"] * p.cost_mul
        shots = int(p.energy / max(1e-6, cost)) if cost > 0 else 0
        self.text(surf, f"DMG {int(w['damage'] * p.damage_mul)}", (x + 150, y + 30), C.UI_FG, 13)
        self.text(surf, f"COST {cost:.1f}", (x + 150, y + 48), C.UI_FG, 13)
        self.text(surf, f"AMMO {shots}", (x + 150, y + 8), 
                  C.NEON["red"] if shots < 6 else C.NEON["cyan"], 15, bold=True)

        # 武器槽
        for i, wd in enumerate(C.WEAPONS):
            bx = x + 14 + i * 46
            by = y + 62
            active = (i == p.weapon)
            col = wd["color"] if active else (86, 96, 128)
            r = pygame.Rect(bx, by, 40, 26)
            pygame.draw.rect(surf, (18, 22, 38), r)
            pygame.draw.rect(surf, col, r, 2 if active else 1)
            self.text(surf, str(i + 1), (bx + 20, by + 6), col, 13, align="center")
            if active:
                for k in range(int(wd["pierce"] + p.pierce_bonus)):
                    pygame.draw.circle(surf, wd["color"], (bx + 8 + k * 6, by + 42), 2)
        # 暴击率 / 穿透
        self.text(surf, f"CRIT {int(p.crit_chance * 100)}%", (x + 176, y + 68), C.UI_DIM, 12)

    # ------------------------------------------------------------------
    def _draw_top_bar(self, surf: pygame.Surface, world, fps: float) -> None:
        # 波次
        rect = pygame.Rect(self.w * 0.5 - 190, 18, 380, 54)
        self.panel(surf, rect, C.NEON["magenta"], fill_alpha=96)
        self.text(surf, f"WAVE {world.wave}/{world.total_waves}",
                  (self.w * 0.5 - 172, 24), C.NEON["magenta"], 20, bold=True,
                  glow=(90, 30, 70))
        self.text(surf, f"HOSTILES {world.enemies_alive}", (self.w * 0.5 - 172, 47),
                  C.UI_FG, 13)
        self.text(surf, f"SCORE {int(world.player.score)}", (self.w * 0.5 + 176, 24),
                  C.NEON["amber"], 16, bold=True, align="right")
        self.text(surf, f"KILLS {world.player.kills}", (self.w * 0.5 + 176, 47),
                  C.UI_DIM, 13, align="right")

        # 左侧：性能 / 命中率
        pl = world.player
        acc = pl.hits_landed / max(1, pl.shots)
        self.text(surf, f"FPS {int(fps)}", (22, 18), C.UI_DIM, 12)
        self.text(surf, f"ACC {int(acc * 100)}%", (22, 34), C.UI_DIM, 12)
        self.text(surf, f"SHARDS {pl.shards}", (22, 50), C.NEON["violet"], 12)
        self.text(surf, f"AGGRO {'HIGH' if world.player_alerted else 'LOW'}",
                  (22, 66), C.NEON["red"] if world.player_alerted else C.UI_DIM, 12)

        # 波次提示横幅
        if world.wave_banner > 0.0:
            k = U.clamp(world.wave_banner / 2.2, 0.0, 1.0)
            a = int(255 * min(1.0, k * 2.0))
            self.text(surf, f"// INCOMING: WAVE {world.wave} //",
                      (self.w * 0.5, self.h * 0.30), C.NEON["magenta"], 34, bold=True,
                      align="center", glow=(120, 30, 90), alpha=a)
            self.text_cn(surf, "敌方防御程序正在部署", (self.w * 0.5, self.h * 0.30 + 44),
                         C.UI_DIM, 18, align="center", alpha=a)

        # 敌人清空提示
        if world.wave_cleared > 0.0:
            k = U.clamp(world.wave_cleared / 1.6, 0.0, 1.0)
            a = int(255 * min(1.0, k * 2.0))
            self.text(surf, "// SECTOR CLEARED //", (self.w * 0.5, self.h * 0.34),
                      C.NEON["lime"], 30, bold=True, align="center",
                      glow=(40, 110, 30), alpha=a)

    # ------------------------------------------------------------------
    def _terrain(self, lvl, cell: int) -> pygame.Surface:
        """
        整张地图的静态底图，雷达与全屏地图共用。

        缓存 key 必须带上 cell：雷达用 cell=8、全屏地图用 cell=6，
        只按 id(lvl) 缓存会让后请求的一方拿到错误像素密度的底图。
        """
        key = (id(lvl), cell)
        base = self._terrain_cache.get(key)
        if base is None:
            buf, mw, mh = lvl.render_minimap(scale=cell)
            base = pygame.image.frombuffer(buf, (mw, mh), "RGB")
            try:
                base = base.convert()
            except Exception:
                pass
            if len(self._terrain_cache) > 6:
                self._terrain_cache.clear()
            self._terrain_cache[key] = base
        return base

    def _draw_radar(self, surf: pygame.Surface, world) -> None:
        """
        局部雷达：以玩家为中心的方格地图。

        实现要点：不要逐格 draw.rect（15x15 = 225 次调用 ≈ 1ms+），
        而是预渲染一张整图底图，然后**做一次区域 blit**。

        坐标约定：世界坐标的单位是"格"，与底图 1 格 = cell 像素严格对应；
        底图、掉落物、敌人、玩家三角全部走同一个 to_screen()，
        避免各自用 (x - px + half) 这类近似偏移而互相错位。
        """
        p = world.player
        cell = 8
        n = self.radar_cells
        size = n * cell
        rx = self.w - size - 26
        ry = 92
        rect = pygame.Rect(rx, ry, size, size)
        self.panel(surf, rect, C.NEON["cyan"], fill_alpha=150, corner=6)

        base = self._terrain(world.level, cell)
        bw, bh = base.get_size()

        # 让玩家当前所在位置恰好落在面板可视区正中心
        inner = size - 2
        sx0 = int(p.x * cell - inner * 0.5)
        sy0 = int(p.y * cell - inner * 0.5)

        # 贴到地图边缘时裁剪源区域，并同步平移目标位置，否则整图会错位
        dx0, dy0 = rx + 1, ry + 1
        sw, sh = inner, inner
        if sx0 < 0:
            dx0 -= sx0
            sw += sx0
            sx0 = 0
        if sy0 < 0:
            dy0 -= sy0
            sh += sy0
            sy0 = 0
        sw = min(sw, bw - sx0)
        sh = min(sh, bh - sy0)
        if sw > 0 and sh > 0:
            surf.blit(base, (dx0, dy0), pygame.Rect(sx0, sy0, sw, sh))

        # 世界坐标（格）-> 雷达面板像素；与上面 blit 的映射完全一致
        ox = rx + 1 - sx0
        oy = ry + 1 - sy0

        def to_screen(wx: float, wy: float):
            return (ox + wx * cell, oy + wy * cell)

        px, py = p.x, p.y

        # 掉落物
        for pk in world.pickups:
            sx, sy = to_screen(pk.x, pk.y)
            if rx <= sx < rx + size and ry <= sy < ry + size:
                col = C.PICKUP_TYPES[pk.kind]["color"]
                pygame.draw.rect(surf, col, (int(sx) - 1, int(sy) - 1, 3, 3))

        # 敌人（雷达只显示范围内目标，制造"红点逼近"的紧张感）
        for en in world.enemies:
            if not en.alive:
                continue
            sx, sy = to_screen(en.x, en.y)
            if rx <= sx < rx + size and ry <= sy < ry + size:
                col = en.data["color"]
                cx, cy = int(sx), int(sy)
                pygame.draw.circle(surf, col, (cx, cy), 3)
                if en.state in ("chase", "attack"):
                    pygame.draw.circle(surf, (255, 255, 255), (cx, cy), 4, 1)

        # 玩家：三角形朝向（与底图上自己的位置像素级重合）
        cx, cy = to_screen(px, py)
        a = p.facing
        pts = [(cx + math.cos(a) * 7, cy + math.sin(a) * 7),
               (cx + math.cos(a + 2.5) * 5, cy + math.sin(a + 2.5) * 5),
               (cx + math.cos(a - 2.5) * 5, cy + math.sin(a - 2.5) * 5)]
        pygame.draw.polygon(surf, C.NEON["cyan"], pts)
        self.text(surf, "RADAR", (rx + 4, ry - 18), C.UI_DIM, 11)

    # ------------------------------------------------------------------
    def draw_full_map(self, surf: pygame.Surface, world) -> None:
        """Tab 全屏地图：缓存的静态底图 + 动态标记。"""
        lvl = world.level
        cell = 6
        base = self._terrain(lvl, cell)

        scale = min(self.w * 0.82 / base.get_width(), self.h * 0.78 / base.get_height())
        tw = int(base.get_width() * scale)
        th = int(base.get_height() * scale)
        img = pygame.transform.scale(base, (tw, th))
        x = (self.w - tw) // 2
        y = (self.h - th) // 2

        overlay = pygame.Surface((self.w, self.h), pygame.SRCALPHA)
        overlay.fill((4, 6, 14, 210))
        surf.blit(overlay, (0, 0))
        surf.blit(img, (x, y))
        pygame.draw.rect(surf, C.NEON["cyan"], (x, y, tw, th), 2)
        # 地图页的阅读锚点：标题、目标状态与图例形成一条稳定的信息层级。
        self.text(surf, "// NETWORK TOPOLOGY DUMP //", (self.w * 0.5, 28),
                  C.NEON["cyan"], 22, bold=True, align="center", glow=(20, 70, 90))
        self.text_cn(surf, "全网拓扑扫描 · 侦察模式", (self.w * 0.5, 56),
                     C.UI_DIM, 16, align="center")
        self.text(surf, f"HOSTILES {world.enemies_alive:02d}", (x, y + th + 16),
                  C.NEON["red"] if world.enemies_alive else C.NEON["lime"], 13, bold=True)
        self.text(surf, "CYAN  YOU   RED  HOSTILE   AMBER  CACHE", (x + tw, y + th + 16),
                  C.UI_DIM, 11, align="right")

        # 世界坐标单位是"格"，底图 1 格 = cell 像素，缩放后即 tw/lvl.w。
        # 用 tw/lvl.w 而非 scale*cell，可避免 int() 取整后的亚像素偏差。
        cw = tw / lvl.w
        ch = th / lvl.h

        def to_screen(wx, wy):
            return (x + wx * cw, y + wy * ch)

        for pk in world.pickups:
            sx, sy = to_screen(pk.x, pk.y)
            pygame.draw.circle(surf, C.PICKUP_TYPES[pk.kind]["color"], (int(sx), int(sy)), 4)
        for en in world.enemies:
            if not en.alive:
                continue
            sx, sy = to_screen(en.x, en.y)
            col = en.data["color"]
            pygame.draw.circle(surf, col, (int(sx), int(sy)), 6)
            pygame.draw.circle(surf, (255, 255, 255), (int(sx), int(sy)), 8, 1)
        px, py = to_screen(world.player.x, world.player.y)
        a = world.player.facing
        pygame.draw.polygon(surf, C.NEON["cyan"], [
            (px + math.cos(a) * 11, py + math.sin(a) * 11),
            (px + math.cos(a + 2.5) * 8, py + math.sin(a + 2.5) * 8),
            (px + math.cos(a - 2.5) * 8, py + math.sin(a - 2.5) * 8)])
        self.text(surf, "// NETWORK TOPOLOGY DUMP //", (self.w * 0.5, y - 34),
                  C.NEON["cyan"], 20, bold=True, align="center", glow=(20, 70, 90))
        self.text(surf, "RELEASE [TAB] TO CLOSE", (self.w * 0.5, y + th + 12),
                  C.UI_DIM, 13, align="center")

    # ------------------------------------------------------------------
    def _draw_notifications(self, surf: pygame.Surface, world) -> None:
        y = self.h * 0.44
        for (msg, color, life) in world.notifications[-5:]:
            k = U.clamp(life / 1.6, 0.0, 1.0)
            self.text(surf, msg, (self.w * 0.5 + 120, y), color, 15, align="center",
                      alpha=int(240 * k))
            y += 22

    def _draw_damage_dirs(self, surf: pygame.Surface, world) -> None:
        """受击方向指示：在准心周围画弧，提示伤害来向。"""
        p = world.player
        cx, cy = self.w * 0.5, self.h * 0.5
        for (ang, life) in world.damage_dirs:
            k = U.clamp(life / 1.1, 0.0, 1.0)
            rel = U.signed_angle_diff(p.facing, ang)
            # 屏幕极角：0 在正上方，顺时针
            scr = rel + math.pi / 2
            R = 96
            a0 = scr - 0.22
            a1 = scr + 0.22
            # pygame 的角度是逆时针、y 向下，故取负
            pygame.draw.arc(surf, U.mix((80, 20, 30), C.NEON["red"], k),
                            (cx - R, cy - R, R * 2, R * 2), -(a1) - math.pi / 2,
                            -(a0) - math.pi / 2, 4)

    def _draw_float_texts(self, surf: pygame.Surface, world, sx: float) -> None:
        """消费渲染器投影出的屏幕空间文本（伤害数字 + 敌人血条）。"""
        for (x, y, s, color, k) in world.renderer.float_texts:
            px = x * sx
            py = y * sx
            if s.startswith("#HP:"):
                ratio = float(s[4:])
                bw = 46
                if x <= 1.0 or px < -bw or px > self.w + bw:
                    continue
                bx = px - bw * 0.5
                by = py - 10
                pygame.draw.rect(surf, (10, 12, 20), (bx - 1, by - 1, bw + 2, 7))
                col = C.NEON["red"] if ratio < 0.34 else (C.NEON["amber"] if ratio < 0.7 else C.NEON["lime"])
                pygame.draw.rect(surf, col, (bx, by, bw * ratio, 5))
                continue
            if px < 0 or px > self.w or py < 0 or py > self.h:
                continue
            self.text(surf, s, (px, py), color, 15 if color[0] > 250 else 14,
                      bold=True, align="center", alpha=int(255 * k))

    # ==================================================================
    # 武器视图模型
    # ==================================================================
    def _scaled_gun(self, idx: int, gun: pygame.Surface):
        """持枪图的缩放缓存。"""
        cached = self._gun_cache.get(idx)
        if cached is None:
            sw, sh = gun.get_size()
            scale = (self.h / C.RES_H) * 1.15
            tw, th = max(1, int(sw * scale)), max(1, int(sh * scale))
            cached = (tw, th, pygame.transform.scale(gun, (tw, th)))
            self._gun_cache[idx] = cached
        return cached

    def draw_weapon(self, surf: pygame.Surface, world, sprites: List[Dict]) -> None:
        p = world.player
        if not p.alive:
            return
        w = sprites[p.weapon]
        gun = w["gun"]
        bob = math.sin(p.bob_phase) * 10.0 * p.bob_amount
        bob2 = abs(math.cos(p.bob_phase * 1.0)) * 6.0 * p.bob_amount
        kick = p.recoil * 22.0
        # 缩放后的持枪图按武器缓存：每帧只改变位置，不重复做 resize
        tw, th, img = self._scaled_gun(p.weapon, gun)

        base_x = self.w - tw - int(self.w * 0.02) + int(bob * 0.6)
        base_y = self.h - th + int(self.h * 0.055) + int(bob2 + kick)
        surf.blit(img, (base_x, base_y))

        if p.flash > 0.02:
            flash_surf = self._flash_cache.get(p.weapon)
            if flash_surf is None:
                flash_surf = rgba_to_surface(w["flash"])
                self._flash_cache[p.weapon] = flash_surf
            fw, fh = flash_surf.get_size()
            fscale = (self.h / C.RES_H) * 0.72 * (0.7 + p.flash * 0.5)
            ftw, fth = max(1, int(fw * fscale)), max(1, int(fh * fscale))
            fimg = pygame.transform.scale(flash_surf, (ftw, fth))
            fimg.set_alpha(int(235 * p.flash))
            fx = base_x + int(tw * 0.62) - ftw // 2
            fy = base_y + int(th * 0.10) - fth // 2
            surf.blit(fimg, (fx, fy), special_flags=pygame.BLEND_ADD)

    # ==================================================================
    # 界面：标题
    # ==================================================================
    def draw_title(self, surf: pygame.Surface, world, selected: int, blink: float) -> None:
        surf.fill(C.UI_BG)
        self._title_backdrop(surf, world)
        # 让背景成为有层次的“数据空间”：微弱的扫描块与两条主色轨道。
        pulse = 0.5 + 0.5 * math.sin(world.time * 1.7)
        pygame.draw.rect(surf, (8, 18, 32), (0, int(self.h * 0.61), self.w, 2))
        pygame.draw.rect(surf, C.NEON["cyan"],
                         (0, int(self.h * 0.61), int(self.w * (0.22 + pulse * 0.06)), 1))
        pygame.draw.rect(surf, C.NEON["magenta"],
                         (int(self.w * 0.74), int(self.h * 0.61), int(self.w * 0.18), 1))

        cx = self.w * 0.5
        # 细微的偏移副本让标题像 CRT 上的 RGB 分离，而不是廉价的重影。
        title_y = self.h * 0.20
        self.text(surf, C.TITLE, (cx - 3, title_y + 1), C.NEON["magenta"], 76,
                  bold=True, align="center", alpha=105)
        self.text(surf, C.TITLE, (cx + 3, title_y - 1), C.NEON["cyan"], 76,
                  bold=True, align="center", alpha=115)
        self.text(surf, C.TITLE, (cx, title_y), C.NEON["white"], 76, bold=True,
                  align="center", glow=(10, 80, 110))
        pygame.draw.line(surf, C.NEON["cyan"], (cx - 252, title_y + 82),
                         (cx - 82, title_y + 82), 2)
        pygame.draw.line(surf, C.NEON["magenta"], (cx + 82, title_y + 82),
                         (cx + 252, title_y + 82), 2)
        self.text_cn(surf, C.SUBTITLE, (cx, title_y + 84), C.NEON["magenta"], 26,
                     align="center")
        self.text(surf, "A RAYCASTING CYBER-INTRUSION FPS", (cx, title_y + 122),
                  C.UI_DIM, 15, align="center")

        items = [
            ("[ ENTER ]  BREACH THE FORTRESS", "开始入侵"),
            ("[ C ]      CONTROLS", "操作说明"),
            ("[ ESC ]    DISCONNECT", "退出"),
        ]
        y = self.h * 0.52
        for i, (label, cn) in enumerate(items):
            active = (i == selected)
            col = C.NEON["lime"] if active else C.UI_DIM
            pre = ">" if active else " "
            # Active row gets a restrained glass plate and a left-edge accent.
            row = pygame.Rect(int(cx - 250), int(y - 8), 500, 54)
            if active:
                self.panel(surf, row, C.NEON["lime"], fill_alpha=52, corner=10, thickness=2)
                pygame.draw.rect(surf, C.NEON["lime"], (row.left, row.top, 4, row.height))
            self.text(surf, f"{pre} {label}", (cx, y), col, 22, bold=active, align="center")
            self.text_cn(surf, cn, (cx, y + 26), col if active else (70, 82, 110), 16,
                         align="center")
            y += 62

        if blink > 0.5:
            self.text(surf, "SEED SYSTEM READY // PRESS ENTER", (cx, self.h - 62),
                      C.NEON["cyan"], 14, align="center", alpha=int(150 + blink * 90))

    def draw_controls(self, surf: pygame.Surface) -> None:
        overlay = pygame.Surface((self.w, self.h), pygame.SRCALPHA)
        overlay.fill((4, 6, 14, 232))
        surf.blit(overlay, (0, 0))
        cx = self.w * 0.5
        self.text(surf, "// OPERATION MANUAL //", (cx, 54), C.NEON["cyan"], 26, bold=True,
                  align="center", glow=(20, 70, 90))
        groups = [
            ("NAVIGATION / 导航", [("W A S D", "移动 / STRAFE", "Move"),
                                  ("MOUSE", "转向（左右 + 上下视角）", "Aim")]),
            ("COMBAT / 战斗", [("LEFT CLICK / SPACE", "开火", "Fire"),
                               ("SHIFT", "冲刺（含无敌帧）", "Dash"),
                               ("1 / 2 / 3", "切换武器", "Swap weapon"),
                               ("MOUSE WHEEL", "快速换枪", "Cycle")]),
            ("SYSTEM / 系统", [("TAB", "全网拓扑图", "Full map"),
                              ("ESC", "暂停 / 返回", "Pause"),
                              ("R", "重开一局", "Restart")]),
        ]
        y = 112
        for gi, (heading, rows) in enumerate(groups):
            self.text(surf, heading, (cx - 250, y), C.NEON["magenta"] if gi == 1 else C.NEON["cyan"],
                      12, bold=True)
            pygame.draw.line(surf, (40, 58, 86), (cx - 82, y + 8), (cx + 250, y + 8), 1)
            y += 22
            for key, cn, en in rows:
                # 键位胶囊：让扫描视线先落在可操作区域，再读说明。
                kw = max(92, len(key) * 10 + 20)
                pill = pygame.Rect(int(cx - 250), int(y - 3), kw, 25)
                pygame.draw.rect(surf, (14, 22, 38), pill)
                pygame.draw.rect(surf, C.NEON["lime"], pill, 1)
                self.text(surf, key, (pill.centerx, y + 2), C.NEON["lime"], 12,
                          bold=True, align="center")
                self.text_cn(surf, cn, (cx - 120, y + 1), C.UI_DIM, 14, align="left")
                self.text(surf, en, (cx + 250, y + 2), C.UI_FG, 14, align="right")
                y += 28
            y += 9
        self.text(surf, "PRESS [ESC] TO RETURN", (cx, self.h - 56), C.UI_DIM, 14,
                  align="center")

    def _title_backdrop(self, surf: pygame.Surface, world) -> None:
        """标题背景：透视网格 + 扫描线，用极少的绘制成本营造赛博空间感。"""
        t = world.time
        horizon = int(self.h * 0.62)
        for i in range(1, 26):
            k = (i / 26.0) ** 2.2
            y = horizon + int(k * (self.h - horizon))
            a = int(70 * (1.0 - k))
            pygame.draw.line(surf, (20, 60, 80, a), (0, y), (self.w, y), 1)
            _ = a
        ox = (t * 60.0) % 120
        for i in range(-8, 16):
            x = int(self.w * 0.5 + (i * 120 - ox) * 0.6)
            pygame.draw.line(surf, (16, 42, 62), (int(self.w * 0.5), horizon),
                             (x, self.h), 1)
        for i in range(0, self.h, 4):
            pygame.draw.line(surf, (0, 0, 0), (0, i), (self.w, i), 1)

    # ==================================================================
    # 界面：升级选卡
    # ==================================================================
    def draw_upgrade(self, surf: pygame.Surface, world, cards: List[Dict],
                     selected: int) -> None:
        overlay = pygame.Surface((self.w, self.h), pygame.SRCALPHA)
        overlay.fill((4, 6, 14, 226))
        surf.blit(overlay, (0, 0))

        cx = self.w * 0.5
        self.text(surf, "// PROTOCOL PATCH AVAILABLE //", (cx, 62), C.NEON["magenta"],
                  30, bold=True, align="center", glow=(90, 20, 70))
        self.text_cn(surf, "波次清空，选择一条协议补丁写入自身", (cx, 100),
                     C.UI_DIM, 18, align="center")

        n = len(cards)
        cw, ch = 320, 300
        gap = 34
        total = n * cw + (n - 1) * gap
        x0 = cx - total * 0.5
        y0 = self.h * 0.5 - ch * 0.42

        for i, card in enumerate(cards):
            x = x0 + i * (cw + gap)
            active = (i == selected)
            col = C.NEON["cyan"] if active else (72, 86, 120)
            rect = pygame.Rect(int(x), int(y0), cw, ch)
            if active:
                glow = pygame.Surface((cw + 18, ch + 18), pygame.SRCALPHA)
                glow.fill((0, 0, 0, 0))
                pygame.draw.rect(glow, (*C.NEON["cyan"], 26), glow.get_rect(), border_radius=6)
                surf.blit(glow, (x - 9, y0 - 9))
            self.panel(surf, rect, col, fill_alpha=170 if active else 120, corner=16,
                       thickness=3 if active else 2)

            self.text(surf, f"[{i + 1}]", (x + 18, y0 + 16), col, 20, bold=True)
            self.text(surf, card["name"], (x + cw * 0.5, y0 + 54), col if active else C.UI_FG,
                      21, bold=True, align="center")
            self.text_cn(surf, card["cn"], (x + cw * 0.5, y0 + 84),
                         C.UI_DIM if not active else C.NEON["lime"], 18, align="center")

            # 图标区
            ic = pygame.Rect(int(x + cw * 0.5 - 42), int(y0 + 122), 84, 84)
            pygame.draw.rect(surf, (12, 16, 28), ic)
            pygame.draw.rect(surf, col, ic, 2)
            self._draw_card_icon(surf, ic, card["id"], col, world.time)

            # 描述（按宽度手动折行）
            desc_cn = card["desc"]
            self.text_cn(surf, desc_cn, (x + cw * 0.5, y0 + 218), C.UI_FG, 16,
                         align="center")
            self.text(surf, card["effect"], (x + cw * 0.5, y0 + 258), col, 15,
                      bold=True, align="center")

        self.text(surf, "SELECT PATCH: [1/2/3] OR CLICK     CONFIRM: [ENTER]",
                  (cx, self.h - 74), C.UI_DIM, 15, align="center")

    def _draw_card_icon(self, surf: pygame.Surface, rect: pygame.Rect, cid: str,
                        col, t: float) -> None:
        cx, cy = rect.center
        pulse = 0.5 + 0.5 * math.sin(t * 3.0)
        if cid in ("hp_up", "armor_up", "energy_up"):
            pygame.draw.polygon(surf, col, [(cx, cy - 24), (cx + 22, cy), (cx, cy + 24), (cx - 22, cy)], 2)
            pygame.draw.polygon(surf, U.mix((10, 14, 26), col, 0.25 + pulse * 0.3),
                                [(cx, cy - 16), (cx + 15, cy), (cx, cy + 16), (cx - 15, cy)])
        elif cid in ("dmg_up", "crit_up", "pierce_up"):
            for k, ln in enumerate((10, 18, 26)):
                pygame.draw.line(surf, col, (cx - 26 + k * 8, cy + 20 - ln),
                                 (cx - 26 + k * 8, cy + 20), 3)
            pygame.draw.circle(surf, U.mix((10, 14, 26), col, pulse), (cx + 18, cy - 16), 8, 2)
        elif cid in ("speed_up", "dash_up"):
            for k in range(3):
                off = (t * 40 + k * 22) % 60
                pygame.draw.line(surf, U.mix((10, 14, 26), col, 0.35 + k * 0.3),
                                 (cx - 28 + off, cy - 14 + k * 14),
                                 (cx - 10 + off, cy - 14 + k * 14), 4)
        else:
            pygame.draw.circle(surf, col, (cx, cy), 26, 2)
            pygame.draw.circle(surf, col, (cx, cy), 14, 2)
            pygame.draw.circle(surf, U.mix((10, 14, 26), col, pulse), (cx, cy), 6)

    # ==================================================================
    # 界面：暂停 / 结算
    # ==================================================================
    def draw_pause(self, surf: pygame.Surface, world) -> None:
        overlay = pygame.Surface((self.w, self.h), pygame.SRCALPHA)
        overlay.fill((4, 6, 14, 178))
        surf.blit(overlay, (0, 0))
        cx = self.w * 0.5
        self.text(surf, "// PROCESS SUSPENDED //", (cx, self.h * 0.36), C.NEON["amber"],
                  34, bold=True, align="center", glow=(90, 60, 10))
        self.text_cn(surf, "进程已挂起", (cx, self.h * 0.36 + 46), C.UI_DIM, 20,
                     align="center")
        self.text(surf, "ESC / ENTER  RESUME          R  RESTART          Q  ABORT",
                  (cx, self.h * 0.52), C.UI_FG, 16, align="center")

    def draw_end(self, surf: pygame.Surface, world, win: bool) -> None:
        overlay = pygame.Surface((self.w, self.h), pygame.SRCALPHA)
        overlay.fill((4, 6, 14, 216))
        surf.blit(overlay, (0, 0))
        cx = self.w * 0.5
        p = world.player
        if win:
            title, sub, col = "// FORTRESS BREACHED //", "数据要塞已沦陷 —— 入侵成功", C.NEON["lime"]
        else:
            title, sub, col = "// PROCESS TERMINATED //", "进程被强制终止 —— 入侵失败", C.NEON["red"]
        self.text(surf, title, (cx, self.h * 0.16), col, 46, bold=True, align="center",
                  glow=U.shade(col, 0.35))
        self.text_cn(surf, sub, (cx, self.h * 0.16 + 62), C.UI_DIM, 22, align="center")

        stats = [
            ("SCORE", f"{int(p.score)}"),
            ("KILLS", f"{p.kills}"),
            ("WAVE REACHED", f"{world.wave}/{world.total_waves}"),
            ("ACCURACY", f"{int(100 * p.hits_landed / max(1, p.shots))}%"),
            ("DAMAGE DEALT", f"{int(p.damage_dealt)}"),
            ("SHARDS", f"{p.shards}"),
        ]
        y = self.h * 0.40
        bx = cx - 250
        for i, (k, v) in enumerate(stats):
            col_i = cx - 250 if i % 2 == 0 else cx + 30
            yy = y + (i // 2) * 44
            self.text(surf, k, (col_i, yy), C.UI_DIM, 16)
            self.text(surf, v, (col_i + 230, yy), C.UI_FG, 20, bold=True, align="right")
        _ = bx

        self.text(surf, "[R] REBOOT SEQUENCE       [ESC] RETURN TO TITLE",
                  (cx, self.h - 84), col, 17, bold=True, align="center")

    # ==================================================================
    @staticmethod
    def to_surface(rgba: "np.ndarray") -> pygame.Surface:
        return rgba_to_surface(rgba)


def rgba_to_surface(rgba) -> pygame.Surface:
    """
    (h, w, 4) uint8 RGBA 数组 -> 带 alpha 的 pygame.Surface。

    用 frombuffer 而不是 surfarray，避免锁面 / 深度不匹配的坑。
    """
    import numpy as np
    arr = np.ascontiguousarray(np.transpose(rgba, (1, 0, 2)))
    s = pygame.image.frombuffer(arr.tobytes(), (arr.shape[0], arr.shape[1]), "RGBA")
    return s.convert_alpha() if pygame.display.get_surface() is not None else s
