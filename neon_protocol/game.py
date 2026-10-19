# -*- coding: utf-8 -*-
"""
NEON PROTOCOL - 世界状态与主循环
================================

World  —— 纯逻辑层：实体容器、波次调度、伤害结算、事件广播
Game   —— 表现层：输入、状态机、固定步长物理、渲染编排

为什么把两者分开
----------------
World 不依赖任何 pygame 显示上下文（只用到 surface 数组），
因此可以在无头（headless / SDL dummy）环境下跑压力测试：
把 AI、碰撞、波次推进全部自动模拟上千帧，验证不崩、不卡、不掉帧。

游戏状态机
----------
TITLE -> PLAY -> (UPGRADE -> PLAY) * n -> WIN / DEAD
         ^  `- PAUSE -'          `- CONTROLS

固定步长
--------
物理以 1/120 秒为步长，渲染跟随显示器刷新。
这样在低帧率机器上，玩家的移动速度、敌人 AI 的反应时间都保持一致，
不会出现"帧率越低打得越慢"的经典问题。
"""

from __future__ import annotations

import math
import random
import time as _time
from typing import Dict, List, Optional, Tuple

import numpy as np
import pygame

from . import config as C
from . import entities as E
from . import hud as HUD_MOD
from . import renderer as R
from . import textures as T
from . import utils as U
from .audio import Audio

# 状态
ST_TITLE = "title"
ST_CONTROLS = "controls"
ST_PLAY = "play"
ST_UPGRADE = "upgrade"
ST_PAUSE = "pause"
ST_DEAD = "dead"
ST_WIN = "win"

FIXED_DT = 1.0 / 120.0


# ===========================================================================
# 世界
# ===========================================================================
class World:
    """一局游戏的全部可模拟状态。"""

    def __init__(self, audio: Audio, seed: Optional[int] = None):
        self.audio = audio
        self.rng = random.Random(seed)
        self.seed = seed

        self.time = 0.0
        self.level = None
        self.player: Optional[E.Player] = None

        self.enemies: List[E.Enemy] = []
        self.bullets: List[E.Bullet] = []
        self.pickups: List[E.Pickup] = []
        self.particles: List[E.Particle] = []
        self.damage_numbers: List[E.DamageNumber] = []

        self.renderer: Optional[R.Renderer] = None

        # 波次
        self.wave = 1
        self.total_waves = C.TOTAL_WAVES
        self.spawn_queue: List[Tuple[str, float, float]] = []
        self.spawn_timer = 0.0
        self.wave_banner = 0.0
        self.wave_cleared = 0.0
        self.wave_settle = 0.0
        self.between_waves = False

        # 反馈
        self.notifications: List[Tuple[str, Tuple[int, int, int], float]] = []
        self.damage_dirs: List[Tuple[float, float]] = []
        self.shake_amount = 0.0
        self.player_alerted = False
        self.alert_timer = 0.0
        self.difficulty_attack_rate = 1.0

        # 导航流场：以玩家为源的 BFS 距离场，让敌人会绕墙而不是顶着墙走
        self.nav: Optional[List[List[int]]] = None
        self.nav_timer = 0.0

        self.upgrade_cards: List[Dict] = []
        self.pending_upgrades: List[Dict] = []

    # ------------------------------------------------------------------
    # 初始化一局
    # ------------------------------------------------------------------
    def new_run(self, wave: int = 1) -> None:
        from .level import Level
        self.rng = random.Random(self.seed if self.seed is not None else random.randrange(1 << 30))
        self.level = Level(self.rng, wave_index=wave)
        sx, sy = self.level.spawn
        self.player = E.Player(sx, sy, self.rng.uniform(0, math.tau))

        self.enemies.clear()
        self.bullets.clear()
        self.pickups.clear()
        self.particles.clear()
        self.damage_numbers.clear()
        self.notifications.clear()
        self.damage_dirs.clear()
        self.wave = wave
        self.spawn_queue.clear()
        self.spawn_timer = 0.0
        self.wave_banner = 2.2
        self.wave_cleared = 0.0
        self.wave_settle = 0.0
        self.between_waves = False
        self.shake_amount = 0.0
        self.player_alerted = False
        self.time = 0.0
        self.difficulty_attack_rate = 1.0

        # 开局给几张升级卡池（每波抽 3 张）
        self.pending_upgrades = list(C.UPGRADE_POOL)
        self.rng.shuffle(self.pending_upgrades)

        self.nav = None
        self.nav_timer = 0.0
        self.build_nav()
        self.start_wave(wave)

    # ------------------------------------------------------------------
    # 导航流场
    # ------------------------------------------------------------------
    def build_nav(self) -> None:
        """
        以玩家所在格为源做一次 BFS，得到全图"到玩家还有多少步"的距离场。

        敌人沿着这个场的梯度下降方向走，就等价于 A* 寻路，
        但整个地图只需要算一次（而不是每个敌人算一次）——
        这就是经典的 flow field / navigation field 技巧。

        成本：48x40 的地图约 1900 个格子，每 0.25 秒重算一次，
        单次耗时 1ms 级，完全可以承受。
        """
        from collections import deque
        lvl = self.level
        if lvl is None or self.player is None:
            return
        w, h = lvl.w, lvl.h
        grid = lvl.grid
        px = int(self.player.x)
        py = int(self.player.y)

        # 玩家万一卡进墙里（被击退等极端情况），就近找一个可通行格当源
        if not (0 <= px < w and 0 <= py < h) or grid[py][px] != 0:
            found = False
            for r in range(1, 4):
                for dy in range(-r, r + 1):
                    for dx in range(-r, r + 1):
                        nx, ny = px + dx, py + dy
                        if 0 <= nx < w and 0 <= ny < h and grid[ny][nx] == 0:
                            px, py = nx, ny
                            found = True
                            break
                    if found:
                        break
                if found:
                    break
            if not found:
                return

        dist = [[-1] * w for _ in range(h)]
        dist[py][px] = 0
        q = deque([(px, py)])
        while q:
            x, y = q.popleft()
            d = dist[y][x] + 1
            if x + 1 < w and grid[y][x + 1] == 0 and dist[y][x + 1] < 0:
                dist[y][x + 1] = d
                q.append((x + 1, y))
            if x - 1 >= 0 and grid[y][x - 1] == 0 and dist[y][x - 1] < 0:
                dist[y][x - 1] = d
                q.append((x - 1, y))
            if y + 1 < h and grid[y + 1][x] == 0 and dist[y + 1][x] < 0:
                dist[y + 1][x] = d
                q.append((x, y + 1))
            if y - 1 >= 0 and grid[y - 1][x] == 0 and dist[y - 1][x] < 0:
                dist[y - 1][x] = d
                q.append((x, y - 1))
        self.nav = dist

    def flow_direction(self, x: float, y: float) -> Optional[Tuple[float, float]]:
        """查询流场：返回从 (x,y) 朝玩家前进的单位方向，无路可走时返回 None。"""
        nav = self.nav
        if nav is None:
            return None
        lvl = self.level
        cx, cy = int(x), int(y)
        if not (0 <= cx < lvl.w and 0 <= cy < lvl.h):
            return None
        cur = nav[cy][cx]
        best_v = cur if cur >= 0 else 1 << 30
        best = None
        # 八邻域：允许斜向，走位更自然
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                nx, ny = cx + dx, cy + dy
                if not (0 <= nx < lvl.w and 0 <= ny < lvl.h):
                    continue
                if lvl.grid[ny][nx] != 0:
                    continue
                v = nav[ny][nx]
                if v < 0:
                    continue
                if v < best_v:
                    best_v = v
                    best = (nx + 0.5, ny + 0.5)
        if best is None:
            return None
        d = U.norm(best[0] - x, best[1] - y)
        if d == (0.0, 0.0):
            return None
        return d

    # ------------------------------------------------------------------
    # 波次
    # ------------------------------------------------------------------
    def _enemy_weights(self, wave: int) -> Dict[str, float]:
        """按波次解锁表取当前可用的敌人构成。"""
        best = None
        for w, table in sorted(C.ENEMY_UNLOCK.items()):
            if wave >= w:
                best = table
        return best or {"sentry": 1.0}

    def start_wave(self, wave: int) -> None:
        """生成出生队列：数量 / 血量 / 伤害随波次成长。"""
        self.wave = wave
        self.wave_banner = 2.2
        self.wave_cleared = 0.0
        self.between_waves = False

        count = int(C.WAVE_BASE_COUNT + (wave - 1) * C.WAVE_COUNT_GROWTH)
        hp_scale = 1.0 + (wave - 1) * C.WAVE_HP_SCALE
        dmg_scale = 1.0 + (wave - 1) * C.WAVE_DMG_SCALE
        self.difficulty_attack_rate = 1.0 + (wave - 1) * 0.022

        weights = self._enemy_weights(wave)
        points = self.level.spawn_points(self.player.x, self.player.y, 6.0, count)
        queue: List[Tuple[str, float, float]] = []
        for i in range(count):
            kind = U.weighted_choice(self.rng, list(weights.items()))
            if points:
                px, py = points[i % len(points)]
                # 抖动必须小且要校验：偏移过大可能把敌人塞进墙里，导致它永远卡住
                jx = px + U.jitter(self.rng, 0.16)
                jy = py + U.jitter(self.rng, 0.16)
                if not E.circle_blocked(self.level, jx, jy, C.ENEMY_TYPES[kind]["radius"]):
                    px, py = jx, jy
            else:
                px, py = self.level.spawn
            queue.append((kind, px, py))
        self.spawn_queue = queue
        self.spawn_hp_scale = hp_scale
        self.spawn_dmg_scale = dmg_scale
        self.spawn_timer = 0.5

        # 每波开始补给几件资源，保证循环不会被弹尽粮绝打断
        for _ in range(2 + wave // 2):
            self._spawn_wave_supply()
        self.audio.play("wave", 0.8)
        self.notify(f"WAVE {wave} INCOMING", C.NEON["magenta"])

    def _spawn_wave_supply(self) -> None:
        """在离玩家 3~8 格处投放补给。"""
        for _ in range(24):
            ang = self.rng.uniform(0, math.tau)
            d = self.rng.uniform(3.0, 8.0)
            x = self.player.x + math.cos(ang) * d
            y = self.player.y + math.sin(ang) * d
            if not self.level.is_wall_f(x, y):
                kind = U.weighted_choice(self.rng, [("energy", 0.5), ("health", 0.28), ("armor", 0.22)])
                self.pickups.append(E.Pickup(kind, x, y, self.rng.uniform(0, 6.28)))
                return

    @property
    def enemies_alive(self) -> int:
        return sum(1 for e in self.enemies if e.alive)

    # ------------------------------------------------------------------
    # 事件 / 反馈 API（供实体模块调用）
    # ------------------------------------------------------------------
    def shake(self, amount: float) -> None:
        self.shake_amount = min(C.SHAKE_MAX, self.shake_amount + amount)

    def shake_offset(self) -> Tuple[float, float]:
        if self.shake_amount <= 0.01:
            return 0.0, 0.0
        return (U.jitter(self.rng, self.shake_amount),
                U.jitter(self.rng, self.shake_amount * 0.7))

    def spawn_particle(self, x, y, z, vx, vy, vz, life, color, size,
                       gravity: float = C.PARTICLE_GRAVITY) -> None:
        if len(self.particles) >= C.MAX_PARTICLES:
            return
        self.particles.append(E.Particle(x, y, z, vx, vy, vz, life, color, size, gravity))

    def spawn_burst(self, x: float, y: float, z: float, color, count: int = 12,
                    power: float = 3.4) -> None:
        """径向爆发粒子。"""
        for _ in range(count):
            ang = self.rng.uniform(0, math.tau)
            spd = self.rng.uniform(power * 0.35, power)
            self.spawn_particle(
                x, y, z,
                math.cos(ang) * spd, math.sin(ang) * spd, self.rng.uniform(-0.5, 2.6),
                self.rng.uniform(0.28, 0.72), color, self.rng.uniform(0.035, 0.085),
            )

    def spawn_sparks(self, x: float, y: float, z: float, color, count: int = 6,
                     ang: float = 0.0) -> None:
        """定向火花（打在墙上/敌人身上）。"""
        for _ in range(count):
            a = ang + U.jitter(self.rng, 1.5)
            spd = self.rng.uniform(1.4, 4.6)
            self.spawn_particle(
                x, y, z,
                math.cos(a) * spd, math.sin(a) * spd, self.rng.uniform(0.4, 2.4),
                self.rng.uniform(0.14, 0.4), color, self.rng.uniform(0.025, 0.06),
            )

    def notify(self, msg: str, color=(230, 240, 255)) -> None:
        self.notifications.append((msg, tuple(color), 1.6))
        if len(self.notifications) > 12:
            self.notifications.pop(0)

    def alert_nearby(self, x: float, y: float, radius: float) -> None:
        """开枪产生的噪音：通知半径内的敌人，让它们进入警戒。"""
        r2 = radius * radius
        for en in self.enemies:
            if en.alive and U.dist_sq(x, y, en.x, en.y) < r2:
                en.last_seen = (self.player.x, self.player.y)
                if en.state in ("idle", "patrol"):
                    en.state = "alert"
                    en.state_time = 0.0
        self.player_alerted = True
        self.alert_timer = 3.2

    def on_player_hit(self) -> None:
        """玩家受击：方向指示由子弹命中处统一登记，这里只做接口占位。"""
        return

    def add_damage_dir(self, ang: float) -> None:
        self.damage_dirs.append((ang, 1.1))
        if len(self.damage_dirs) > 6:
            self.damage_dirs.pop(0)

    # ------------------------------------------------------------------
    # 每帧推进
    # ------------------------------------------------------------------
    def update(self, dt: float, move_x: float, move_y: float, dash: bool,
               firing: bool, allow_spawn: bool = True) -> None:
        self.time += dt
        p = self.player
        if p is None:
            return

        # 冲刺输入
        if dash:
            if p.start_dash(move_x, move_y):
                self.audio.play("dash", 0.6)
                self.spawn_burst(p.x, p.y, 0.4, C.NEON["cyan"], 10, 2.4)

        # 玩家
        p.update(dt, self.level, move_x, move_y, False)
        self.level.mark_explored(p.x, p.y, 5)

        # 导航流场刷新（不需要每帧算，0.25 秒足够跟上玩家走位）
        self.nav_timer -= dt
        if self.nav_timer <= 0.0:
            self.nav_timer = 0.25
            self.build_nav()

        # 开火
        if firing:
            if p.fire(self):
                self.shake(0.5)

        # 敌人
        for en in self.enemies:
            if en.alive:
                en.update(dt, self, p)

        # 子弹
        self._update_bullets(dt)

        # 掉落物
        self._update_pickups(dt)

        # 粒子 / 飘字
        self.particles = [pt for pt in self.particles if pt.update(dt, self.level)]
        self.damage_numbers = [dn for dn in self.damage_numbers if dn.update(dt)]

        # 敌人尸体清理（保留一小段溶解时间）
        self.enemies = [en for en in self.enemies if en.alive or getattr(en, "_dead_t", 0.0) > 0.0]

        # 波次调度
        if allow_spawn:
            self._update_waves(dt)
        # 反馈衰减
        self.shake_amount = U.damp(self.shake_amount, 0.0, C.SHAKE_DECAY, dt)
        self.wave_banner = max(0.0, self.wave_banner - dt)
        self.wave_cleared = max(0.0, self.wave_cleared - dt)
        self.alert_timer = max(0.0, self.alert_timer - dt)
        if self.alert_timer <= 0.0:
            self.player_alerted = False
        self.notifications = [(m, c, l - dt) for (m, c, l) in self.notifications if l - dt > 0]
        self.damage_dirs = [(a, l - dt) for (a, l) in self.damage_dirs if l - dt > 0]

        # 紧张度：驱动环境音（敌人越多越压迫）
        n = self.enemies_alive
        tension = U.clamp(n / 14.0, 0.0, 1.0) * 0.7 + (1.0 - p.hp / max(1.0, p.max_hp)) * 0.3
        self.audio.set_tension(tension)

    # ------------------------------------------------------------------
    def _update_bullets(self, dt: float) -> None:
        p = self.player
        alive: List[E.Bullet] = []
        for b in self.bullets:
            ok, hit_point = b.step(dt, self.level)
            if not ok:
                if hit_point is not None:
                    # 打墙：火花 + 墙面亮斑
                    self.spawn_sparks(hit_point[0], hit_point[1], 0.5, b.color, 6,
                                      math.atan2(-b.vy, -b.vx))
                continue

            if b.owner == "player":
                consumed = False
                for en in self.enemies:
                    if not en.alive or en.id in b.hits:
                        continue
                    if U.dist_sq(b.x, b.y, en.x, en.y) <= (en.radius + b.radius) ** 2:
                        crit = self.rng.random() < p.crit_chance
                        dmg = b.damage * (C.CRIT_MULT if crit else 1.0)
                        died = en.hurt(dmg)
                        p.damage_dealt += dmg
                        p.hits_landed += 1
                        b.hits.append(en.id)
                        self.spawn_sparks(b.x, b.y, 0.55, en.data["accent"],
                                          8 if crit else 5, math.atan2(-b.vy, -b.vx))
                        self.damage_numbers.append(
                            E.DamageNumber(en.x, en.y, 0.95 + self.rng.uniform(-0.1, 0.1), dmg, crit))
                        self.audio.play("crit" if crit else "hit", 0.75)
                        if died:
                            self._kill_enemy(en)
                        if b.pierce > 0:
                            b.pierce -= 1
                        else:
                            consumed = True
                            break
                if consumed:
                    continue
            else:
                if p.alive and U.dist_sq(b.x, b.y, p.x, p.y) <= (p.radius + b.radius) ** 2:
                    before = p.hp + p.armor
                    dealt = p.take_damage(b.damage)
                    if dealt > 0:
                        self.add_damage_dir(U.angle_to(p.x, p.y, b.x, b.y))
                        self.audio.play("player_hit", 0.8)
                        self.shake(2.6)
                        self.spawn_sparks(b.x, b.y, 0.55, b.color, 8,
                                          math.atan2(-b.vy, -b.vx))
                    continue

            alive.append(b)
        self.bullets = alive

    # ------------------------------------------------------------------
    def _kill_enemy(self, en: E.Enemy) -> None:
        p = self.player
        p.kills += 1
        p.score += en.data["score"]
        self.audio.play("kill", 0.85)
        self.shake(1.2)
        self.spawn_burst(en.x, en.y, 0.6, en.data["color"], 22, 4.2)
        self.spawn_burst(en.x, en.y, 0.45, en.data["accent"], 10, 2.6)
        if p.leech > 0.0:
            p.heal(p.leech)
        # 掉落
        kind = U.weighted_choice(self.rng, C.DROP_TABLE)
        if kind is None:
            kind = "shard"
        self.pickups.append(E.Pickup(kind, en.x, en.y, self.rng.uniform(0, 6.28)))

    # ------------------------------------------------------------------
    def _update_pickups(self, dt: float) -> None:
        p = self.player
        keep: List[E.Pickup] = []
        for pk in self.pickups:
            if not pk.update(dt, self.level, p.x, p.y):
                continue
            if U.dist_sq(pk.x, pk.y, p.x, p.y) < C.PICKUP_RADIUS ** 2:
                info = C.PICKUP_TYPES[pk.kind]
                if pk.kind == "energy":
                    p.add_energy(info["amount"])
                elif pk.kind == "armor":
                    p.add_armor(info["amount"])
                elif pk.kind == "health":
                    p.heal(info["amount"])
                else:
                    p.shards += 1
                    p.score += 25
                self.audio.play("pickup", 0.7)
                self.notify(f"+{info['label']}", info["color"])
                self.spawn_burst(pk.x, pk.y, 0.35, info["color"], 8, 2.2)
                continue
            keep.append(pk)
        self.pickups = keep

    # ------------------------------------------------------------------
    def _update_waves(self, dt: float) -> None:
        # 出生
        if self.spawn_queue:
            self.spawn_timer -= dt
            if self.spawn_timer <= 0.0 and self.enemies_alive < C.MAX_ALIVE_ENEMIES:
                self.spawn_timer = C.WAVE_SPAWN_INTERVAL
                kind, x, y = self.spawn_queue.pop()
                en = E.Enemy(kind, x, y, getattr(self, "spawn_hp_scale", 1.0),
                             getattr(self, "spawn_dmg_scale", 1.0))
                self.enemies.append(en)
                self.spawn_burst(x, y, 0.4, C.ENEMY_TYPES[kind]["color"], 14, 3.0)
                self.audio.play("hit", 0.22)

        # 清波判定
        if not self.spawn_queue and self.enemies_alive == 0 and not self.between_waves:
            self.wave_settle += dt
            if self.wave_settle > C.WAVE_BREAK_TIME:
                self.wave_cleared = 1.8        # 结算横幅展示时长，之后才弹升级界面
                self.between_waves = True
                self.wave_settle = 0.0
                self.player.heal(6.0)
        elif self.spawn_queue or self.enemies_alive > 0:
            self.wave_settle = 0.0

    # ------------------------------------------------------------------
    def roll_upgrade_cards(self) -> List[Dict]:
        """抽 3 张互不相同的协议补丁。"""
        pool = self.pending_upgrades if self.pending_upgrades else list(C.UPGRADE_POOL)
        if len(pool) < 3:
            pool = list(C.UPGRADE_POOL)
        cards = self.rng.sample(pool, 3)
        for c in cards:
            c = dict(c)
            c["effect"] = self._effect_text(c)
        self.upgrade_cards = [dict(c, effect=self._effect_text(c)) for c in cards]
        return self.upgrade_cards

    @staticmethod
    def _effect_text(card: Dict) -> str:
        kind, val = card["apply"], card["value"]
        if kind in ("damage_mul", "rate_mul", "speed_mul", "crit"):
            return f"+{int(val * 100)}%"
        if kind in ("max_hp", "max_armor", "energy", "leech"):
            return f"+{int(val)}"
        if kind == "pierce":
            return f"+{int(val)} PIERCE"
        if kind == "dash":
            return "-25% CD"
        return ""


# 前向引用占位（避免类型注解运行期解析）
E_level = "Level"


# ===========================================================================
# 游戏
# ===========================================================================
class Game:
    """主循环与状态机。"""

    def __init__(self, headless: bool = False, seed: Optional[int] = None,
                 autoplay: bool = False):
        self.headless = headless
        self.autoplay = autoplay

        pygame.init()
        pygame.font.init()

        flags = 0
        if not headless:
            self.screen = pygame.display.set_mode((C.WINDOW_W, C.WINDOW_H), flags)
            pygame.display.set_caption(f"{C.TITLE} - {C.SUBTITLE}")
        else:
            # 无头模式：仍然创建 Surface，走完全一致的绘制路径
            self.screen = pygame.Surface((C.WINDOW_W, C.WINDOW_H))

        self.clock = pygame.time.Clock()
        self.audio = Audio()
        self.audio.init()
        if not headless:
            self.audio.start_ambient()

        self.world = World(self.audio, seed)
        self.renderer = R.Renderer()
        self.world.renderer = self.renderer
        self.hud = HUD_MOD.HUD((C.WINDOW_W, C.WINDOW_H))
        self.weapon_sprites = T.build_weapon_sprites()
        self.scaled_surf = pygame.Surface((C.WINDOW_W, C.WINDOW_H), 0, 24)

        self.state = ST_TITLE
        self.running = True
        self.show_map = False
        self.menu_index = 0
        self.card_index = 0
        self.blink = 0.0
        self.fps_ring = U.Ring(45)
        self.frame_times: List[float] = []
        self.last_frame_ms = 0.0
        self.render_ms = 0.0
        self.total_frames = 0

        # 鼠标 / 键盘输入
        self.mouse_look = not headless
        self._keys_down = set()          # 自建按键集合，替代 pygame.key.get_pressed()
        if not headless:
            # 标题界面不调用 set_grab / 隐藏鼠标：
            # 过早 set_grab 会让 SDL 在窗口尚未拿到 OS 键盘焦点时（常见于从 IDE 启动）
            # 进入"键盘状态冻结"，表现为进游戏后 WASD 无响应、按一下别的键才恢复。
            # 鼠标捕获改为进入战斗后、等玩家实际移动鼠标（窗口已聚焦）时再启用，
            # 见 _mouse_look，彻底规避启动期冻结。
            pygame.mouse.get_rel()        # 清掉进入标题前的鼠标位移残留

        # 自动演示用的相位
        self._auto_angle = 0.0
        self.trigger = False           # 半自动武器的扳机边沿
        self.upgrade_delay = 0.0

        self.world.new_run()
        # 无头/自动演示：跳过标题界面，直接进入战斗（否则世界不会推进）
        if self.autoplay:
            self.state = ST_PLAY

    # ------------------------------------------------------------------
    # 输入
    # ------------------------------------------------------------------
    def _read_input(self) -> Tuple[float, float, bool, bool, bool]:
        """
        返回 (move_x, move_y, dash, fire_pressed, fire_held)。

        键盘转向与鼠标转向并行支持：没有鼠标的笔记本也能玩。
        """
        kd = self._keys_down
        mx = my = 0.0
        # 前进方向按屏幕朝向解算
        f = self.world.player.facing
        fx, fy = math.cos(f), math.sin(f)
        rx, ry = -fy, fx
        if pygame.K_w in kd:
            mx += fx
            my += fy
        if pygame.K_s in kd:
            mx -= fx
            my -= fy
        if pygame.K_a in kd:
            mx -= rx
            my -= ry
        if pygame.K_d in kd:
            mx += rx
            my += ry

        dash = (pygame.K_LSHIFT in kd) or (pygame.K_RSHIFT in kd)
        fire_held = (pygame.mouse.get_pressed()[0] if self.mouse_look else False) or (pygame.K_SPACE in kd)
        return mx, my, dash, fire_held, fire_held

    def _mouse_look(self) -> None:
        if not self.mouse_look or self.state != ST_PLAY:
            return
        dx, dy = pygame.mouse.get_rel()
        p = self.world.player
        p.facing = (p.facing + dx * C.MOUSE_SENS_X) % (2 * math.pi)
        p.pitch = U.clamp(p.pitch - dy * C.MOUSE_SENS_Y, -C.PITCH_LIMIT, C.PITCH_LIMIT)

    def _keyboard_look(self, dt: float) -> None:
        kd = self._keys_down
        p = self.world.player
        turn = 0.0
        if pygame.K_LEFT in kd or pygame.K_q in kd:
            turn -= 1.0
        if pygame.K_RIGHT in kd or pygame.K_e in kd:
            turn += 1.0
        if turn:
            p.facing = (p.facing + turn * 2.6 * dt) % (2 * math.pi)
        pitch = 0.0
        if pygame.K_UP in kd:
            pitch -= 1.0
        if pygame.K_DOWN in kd:
            pitch += 1.0
        if pitch:
            p.pitch = U.clamp(p.pitch + pitch * 0.7 * dt, -C.PITCH_LIMIT, C.PITCH_LIMIT)

    # ------------------------------------------------------------------
    def _handle_event(self, ev: pygame.event.Event) -> None:
        if ev.type == pygame.QUIT:
            self.running = False
            return

        if ev.type == pygame.MOUSEBUTTONDOWN:
            if ev.button == 4:                       # 滚轮上
                self._cycle_weapon(-1)
            elif ev.button == 5:
                self._cycle_weapon(1)
            elif ev.button == 1:
                self.trigger = True
                if self.state == ST_UPGRADE:
                    self._click_upgrade(ev.pos)
            elif ev.button == 3:
                self._cycle_weapon(1)
            return

        if ev.type != pygame.KEYDOWN:
            return
        k = ev.key
        self._keys_down.add(k)            # 自建按键集合（事件驱动输入）
        if k == pygame.K_SPACE:
            self.trigger = True

        if self.state == ST_TITLE:
            if k in (pygame.K_UP, pygame.K_w):
                self.menu_index = (self.menu_index - 1) % 3
                self.audio.play("ui_move")
            elif k in (pygame.K_DOWN, pygame.K_s):
                self.menu_index = (self.menu_index + 1) % 3
                self.audio.play("ui_move")
            elif k in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self.audio.play("ui_confirm")
                if self.menu_index == 0:
                    self._start_play()
                elif self.menu_index == 1:
                    self.state = ST_CONTROLS
                else:
                    self.running = False
            elif k == pygame.K_ESCAPE:
                self.running = False
            return

        if self.state == ST_CONTROLS:
            if k in (pygame.K_ESCAPE, pygame.K_RETURN):
                self.state = ST_TITLE
                self.audio.play("ui_confirm")
            return

        if self.state == ST_UPGRADE:
            if k in (pygame.K_1, pygame.K_KP1):
                self._install_upgrade(0)
            elif k in (pygame.K_2, pygame.K_KP2):
                self._install_upgrade(1)
            elif k in (pygame.K_3, pygame.K_KP3):
                self._install_upgrade(2)
            elif k == pygame.K_LEFT:
                self.card_index = (self.card_index - 1) % 3
                self.audio.play("ui_move")
            elif k == pygame.K_RIGHT:
                self.card_index = (self.card_index + 1) % 3
                self.audio.play("ui_move")
            elif k in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                self._install_upgrade(self.card_index)
            return

        if self.state == ST_PAUSE:
            if k in (pygame.K_ESCAPE, pygame.K_RETURN):
                self.state = ST_PLAY
                self.audio.play("ui_confirm")
            elif k == pygame.K_r:
                self._start_play()
            elif k == pygame.K_q:
                self.state = ST_TITLE
            return

        if self.state in (ST_DEAD, ST_WIN):
            if k == pygame.K_r:
                self._start_play()
            elif k == pygame.K_ESCAPE:
                self.state = ST_TITLE
            return

        # ---- 游戏内 ----
        if k == pygame.K_ESCAPE:
            self.state = ST_PAUSE
            if self.mouse_look:
                pygame.event.set_grab(False)
                pygame.mouse.set_visible(True)
        elif k == pygame.K_TAB:
            self.show_map = True
        elif k in (pygame.K_1, pygame.K_KP1):
            self._set_weapon(0)
        elif k in (pygame.K_2, pygame.K_KP2):
            self._set_weapon(1)
        elif k in (pygame.K_3, pygame.K_KP3):
            self._set_weapon(2)
        elif k == pygame.K_r:
            self._start_play()
        elif k == pygame.K_m:
            self.audio.ok = False if self.audio.ok else True

    def _handle_keyup(self, ev: pygame.event.Event) -> None:
        if ev.type == pygame.KEYUP:
            self._keys_down.discard(ev.key)   # 事件驱动输入：松开即移出集合
            if ev.key == pygame.K_TAB:
                self.show_map = False

    def _cycle_weapon(self, d: int) -> None:
        p = self.world.player
        self._set_weapon((p.weapon + d) % len(C.WEAPONS))

    def _set_weapon(self, idx: int) -> None:
        p = self.world.player
        if idx != p.weapon:
            p.weapon = idx
            p.cooldown = max(p.cooldown, 0.14)
            self.audio.play("ui_move", 0.6)

    def _click_upgrade(self, pos) -> None:
        cx, cy = C.WINDOW_W * 0.5, C.WINDOW_H * 0.5
        cw, ch = 320, 300
        gap = 34
        total = 3 * cw + 2 * gap
        x0 = cx - total * 0.5
        y0 = C.WINDOW_H * 0.5 - ch * 0.42
        for i in range(3):
            x = x0 + i * (cw + gap)
            if x <= pos[0] <= x + cw and y0 <= pos[1] <= y0 + ch:
                self._install_upgrade(i)
                return

    # ------------------------------------------------------------------
    # 状态切换
    # ------------------------------------------------------------------
    def _start_play(self) -> None:
        self.world.seed = self.world.seed if self.world.seed is not None else None
        self.world.new_run(1)
        self.state = ST_PLAY
        self.show_map = False
        if self.mouse_look:
            pygame.event.set_grab(True)
            pygame.mouse.set_visible(False)
            pygame.mouse.get_rel()
        self.hud._minimap_cache.clear()
        self.hud._terrain_cache.clear()

    def _install_upgrade(self, idx: int) -> None:
        if not self.world.upgrade_cards:
            self.state = ST_PLAY
            return
        idx = max(0, min(len(self.world.upgrade_cards) - 1, idx))
        card = self.world.upgrade_cards[idx]
        effect = self.world.player.apply_upgrade(card)
        self.audio.play("upgrade", 0.9)
        self.world.notify(f"{card['name']}  {effect}", C.NEON["lime"])
        self.world.upgrade_cards = []
        self.world.start_wave(self.world.wave + 1)
        self.state = ST_PLAY

    # ------------------------------------------------------------------
    # 自动演示（用于无头压力测试）
    # ------------------------------------------------------------------
    def _autopilot(self, dt: float) -> Tuple[float, float, bool, bool]:
        """一个简单的机器人玩家：瞄准最近敌人、持续走位与开火。"""
        w = self.world
        p = w.player
        target = None
        best = 1e18
        for en in w.enemies:
            if not en.alive:
                continue
            d = U.dist_sq(p.x, p.y, en.x, en.y)
            if d < best:
                best = d
                target = en

        if target is not None:
            want = U.angle_to(p.x, p.y, target.x, target.y)
            p.facing = U.rotate_towards(p.facing, want, dt * 6.0)
            # 距离过近就后退
            d = math.sqrt(best)
            back = -1.0 if d < 2.4 else (1.0 if d > 5.0 else 0.0)
            mx = math.cos(p.facing) * back
            my = math.sin(p.facing) * back
            # 侧向绕圈
            mx += math.cos(p.facing + math.pi / 2) * 0.7
            my += math.sin(p.facing + math.pi / 2) * 0.7
        else:
            self._auto_angle += dt * 1.1
            mx = math.cos(self._auto_angle)
            my = math.sin(self._auto_angle)
            p.facing = (p.facing + dt * 1.2) % (2 * math.pi)

        # 无敌人时自动捡东西
        if target is None and w.pickups:
            pk = min(w.pickups, key=lambda q: U.dist_sq(p.x, p.y, q.x, q.y))
            want = U.angle_to(p.x, p.y, pk.x, pk.y)
            p.facing = U.rotate_towards(p.facing, want, dt * 4.0)
            mx, my = math.cos(want), math.sin(want)

        fire = False
        if target is not None:
            # 只有真有视线才开枪（否则就是对着墙空放，压测数据会失真）
            fire = not U.grid_ray_hit(w.level.grid, p.x, p.y, target.x, target.y, 90)
        dash = (p.dash_cd <= 0.0) and (best < 10.0) and (self.total_frames % 97 == 0)
        # 能量见底时切回一号武器，避免卡在磁轨长矛上
        if p.energy < 20 and p.weapon != 0:
            self._set_weapon(0)
        return U.norm(mx, my)[0], U.norm(mx, my)[1], dash, fire

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------
    def step(self, dt: float, events: List[pygame.event.Event]) -> None:
        """推进一帧（渲染 + 逻辑）。dt 为真实帧间隔。"""
        self.total_frames += 1
        self.blink = 0.5 + 0.5 * math.sin(self.total_frames * 0.12)

        we_type = getattr(pygame, "WINDOWEVENT", 32768)
        focus_gained = getattr(pygame, "WINDOWEVENT_FOCUS_GAINED", 22)
        focus_lost = getattr(pygame, "WINDOWEVENT_FOCUS_LOST", 21)

        for ev in events:
            # 窗口焦点变化：失去焦点时释放鼠标捕获并清空按键集合（防止卡键）；
            # 重新获得焦点且在战斗中时，恢复鼠标捕获。
            if ev.type == we_type:
                sub = getattr(ev, "event", 0)
                if sub == focus_lost:
                    if not self.headless:
                        pygame.event.set_grab(False)
                    self._keys_down.clear()
                elif sub == focus_gained and self.state == ST_PLAY and self.mouse_look and not self.headless:
                    pygame.event.set_grab(True)
            self._handle_event(ev)
            self._handle_keyup(ev)
            if ev.type == pygame.MOUSEMOTION and self.state == ST_PLAY:
                pass

        w = self.world

        if self.state in (ST_PLAY, ST_UPGRADE):
            if self.state == ST_PLAY:
                self._mouse_look()
                self._keyboard_look(dt)

            # 固定步长物理
            if self.state == ST_PLAY and w.player.alive:
                if self.autoplay:
                    mx, my, dash, fire = self._autopilot(dt)
                else:
                    mx, my, dash, held, _ = self._read_input()
                    wd = w.player.weapon_data()
                    # 全自动武器按住即连发；半自动武器只在"扣扳机的那一帧"开火
                    if wd["auto"]:
                        fire = held
                    else:
                        fire = held and self.trigger
                    self.trigger = False
                steps = 0
                acc = min(dt, 0.10)
                while acc > 0.0 and steps < 6:
                    sub = min(FIXED_DT, acc)
                    w.update(sub, mx, my, dash, fire)
                    acc -= sub
                    steps += 1
            else:
                # 升级界面：世界冻结，只让计时/粒子继续（视觉上"暂停但不死寂"）
                w.time += dt * 0.4
                w.particles = [pt for pt in w.particles if pt.update(dt * 0.4, w.level)]
                w.damage_numbers = [dn for dn in w.damage_numbers if dn.update(dt * 0.4)]

            # 状态迁移：清波横幅播完 -> 升级选卡 / 通关
            if self.state == ST_PLAY and w.between_waves and w.wave_cleared <= 0.0:
                w.between_waves = False
                if w.wave >= w.total_waves:
                    self.state = ST_WIN
                    self.audio.play("win", 0.9)
                    self.audio.stop_ambient()
                else:
                    w.roll_upgrade_cards()
                    self.card_index = 0
                    self.state = ST_UPGRADE
                    self.audio.play("upgrade", 0.5)
            elif self.state == ST_PLAY and not w.player.alive:
                self.state = ST_DEAD
                self.audio.play("game_over", 0.9)
                self.audio.stop_ambient()

            # 机器人自动选卡：停留 0.5 秒后随机安装一张（模拟玩家决策时间）
            if self.autoplay and self.state == ST_UPGRADE:
                self.upgrade_delay += dt
                if self.upgrade_delay > 0.5:
                    self.upgrade_delay = 0.0
                    self._install_upgrade(w.rng.randrange(max(1, len(w.upgrade_cards))))

        # ---- 渲染 ----
        t0 = _time.perf_counter()
        if self.state in (ST_PLAY, ST_UPGRADE, ST_DEAD, ST_WIN, ST_PAUSE):
            low = self.renderer.render(w, dt)
            # 预分配目标面 + 指定 dest，省掉每帧 4MB 的临时 Surface 申请
            dest = self.scaled_surf
            try:
                pygame.transform.scale(low, (C.WINDOW_W, C.WINDOW_H), dest)
            except TypeError:
                dest = pygame.transform.scale(low, (C.WINDOW_W, C.WINDOW_H))
                self.scaled_surf = dest
            self.screen.blit(dest, (0, 0))
        else:
            self.screen.fill(C.UI_BG)

        self._draw_overlay()
        t1 = _time.perf_counter()
        self.render_ms = (t1 - t0) * 1000.0

    # ------------------------------------------------------------------
    def _draw_overlay(self) -> None:
        w = self.world
        fps = 1.0 / max(1e-4, self.fps_ring.mean()) if self.fps_ring.mean() > 0 else 0.0

        if self.state == ST_TITLE:
            self.hud.draw_title(self.screen, w, self.menu_index, self.blink)
            return
        if self.state == ST_CONTROLS:
            self.hud.draw_controls(self.screen)
            return

        # 3D 画面上的叠加层
        self.hud.hit_marker = max(0.0, self.hud.hit_marker - 0.05)
        self.hud.spread = U.damp(self.hud.spread, min(1.0, w.player.cooldown > 0) * 0.4, 8.0, 0.016)

        if w.player.alive:
            self.hud.draw_weapon(self.screen, w, self.weapon_sprites)
        self.hud.draw_gameplay(self.screen, w, fps)

        if self.show_map:
            self.hud.draw_full_map(self.screen, w)
        if self.state == ST_UPGRADE:
            self.hud.draw_upgrade(self.screen, w, w.upgrade_cards, self.card_index)
        elif self.state == ST_PAUSE:
            self.hud.draw_pause(self.screen, w)
        elif self.state in (ST_DEAD, ST_WIN):
            self.hud.draw_end(self.screen, w, self.state == ST_WIN)

        if self.headless and self.total_frames % 60 == 0:
            self.hud.text(self.screen, f"HEADLESS {self.total_frames}",
                          (C.WINDOW_W - 240, C.WINDOW_H - 40), (255, 0, 255), 16)

    # ------------------------------------------------------------------
    def run(self, max_frames: Optional[int] = None) -> Dict[str, float]:
        """
        主循环。max_frames 用于无头自动测试：跑完指定帧数后返回统计。
        """
        stats: Dict[str, float] = {}
        while self.running:
            if self.headless:
                # 无头压测：不受垂直同步限制，全速跑，dt 固定，保证结果可复现
                dt = 1.0 / 60.0
            else:
                dt = self.clock.tick(C.TARGET_FPS) / 1000.0
                dt = min(dt, 0.066)
                if dt <= 0.0:
                    dt = 1.0 / 60.0
            self.fps_ring.push(max(dt, 1e-4))
            self.frame_times.append(self.render_ms)

            events = pygame.event.get()
            self.step(dt, events)
            if not self.headless:
                pygame.display.flip()

            if max_frames is not None and self.total_frames >= max_frames:
                break

        if self.frame_times:
            arr = sorted(self.frame_times)
            stats = {
                "frames": float(self.total_frames),
                "render_ms_avg": sum(arr) / len(arr),
                "render_ms_p95": arr[int(len(arr) * 0.95) - 1] if len(arr) > 1 else arr[0],
                "render_ms_max": arr[-1],
                "wave": float(self.world.wave),
                "kills": float(self.world.player.kills),
                "hp": float(self.world.player.hp),
                "alive": 1.0 if self.world.player.alive else 0.0,
            }
        self.shutdown()
        return stats

    def shutdown(self) -> None:
        self.audio.shutdown()
        pygame.quit()
