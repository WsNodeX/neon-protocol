# -*- coding: utf-8 -*-
"""
NEON PROTOCOL - 实体、AI 与战斗逻辑
===================================

本模块承载"游戏之所以是游戏"的部分：

实体
----
Player       玩家（幽灵进程）：移动、冲刺、开火、受伤、升级属性
Enemy        四类 ICE 防御程序，带有限状态机 AI
Bullet       子弹（玩家 / 敌人共用，用 owner 区分）
Pickup       掉落物
Particle     世界空间粒子（爆炸、火花、数据碎片）
DamageNumber 伤害飘字

AI 设计
-------
状态机：IDLE -> ALERT -> CHASE -> ATTACK -> (HURT) -> DEAD

- **感知**：视线（网格 DDA 射线）+ 距离 + 听觉（玩家开火产生仇恨）
- **战斗位移**：保持理想距离（ranged 风筝 / melee 贴身），并加入垂直方向
  的侧移（strafe），让敌人不是"直线冲脸"，需要玩家预判。
- **避障**：触须探测（三向射线）+ 沿墙滑动，避免卡在拐角。
- **差异化**：四种行为模板（ranged / melee / heavy / blink），
  配合数值差异，形成"远压制 + 近冲锋 + 重装 + 闪现"的复合压力。
"""

from __future__ import annotations

import math
import random
from typing import Dict, List, Optional, Tuple

from . import config as C
from . import utils as U


# ===========================================================================
# 碰撞
# ===========================================================================
def circle_blocked(level, x: float, y: float, r: float) -> bool:
    """圆与网格碰撞检测：取圆包围盒内的格子逐一做"最近点"测试。"""
    x0 = int(math.floor(x - r))
    x1 = int(math.floor(x + r))
    y0 = int(math.floor(y - r))
    y1 = int(math.floor(y + r))
    r2 = r * r
    for cy in range(y0, y1 + 1):
        for cx in range(x0, x1 + 1):
            if not level.is_wall(cx, cy):
                continue
            nx = U.clamp(x, cx, cx + 1.0)
            ny = U.clamp(y, cy, cy + 1.0)
            if (nx - x) ** 2 + (ny - y) ** 2 < r2:
                return True
    return False


def move_with_slide(level, x: float, y: float, dx: float, dy: float,
                    r: float) -> Tuple[float, float, bool, bool]:
    """
    分轴推进 + 贴墙滑动。

    返回 (新x, 新y, 是否撞X, 是否撞Y)。撞墙信息给 AI 用来决定转向方向。
    """
    hit_x = hit_y = False
    nx, ny = x, y

    if dx != 0.0:
        cand = x + dx
        if not circle_blocked(level, cand, ny, r):
            nx = cand
        else:
            # 撞墙前贴到极限位置，避免抖动
            step = dx
            for _ in range(4):
                step *= 0.5
                if not circle_blocked(level, nx + step, ny, r):
                    nx += step
                    break
            hit_x = True

    if dy != 0.0:
        cand = ny + dy
        if not circle_blocked(level, nx, cand, r):
            ny = cand
        else:
            step = dy
            for _ in range(4):
                step *= 0.5
                if not circle_blocked(level, nx, ny + step, r):
                    ny += step
                    break
            hit_y = True

    return nx, ny, hit_x, hit_y


# ===========================================================================
# 粒子
# ===========================================================================
class Particle:
    """世界空间粒子：地面坐标 + 高度 z，受重力与阻力影响。"""

    __slots__ = ("x", "y", "z", "vx", "vy", "vz", "life", "max_life",
                 "color", "size", "gravity", "drag", "fade")

    def __init__(self, x, y, z, vx, vy, vz, life, color, size,
                 gravity: float = C.PARTICLE_GRAVITY, drag: float = C.PARTICLE_DRAG, fade: bool = True):
        self.x, self.y, self.z = x, y, z
        self.vx, self.vy, self.vz = vx, vy, vz
        self.life = self.max_life = life
        self.color = color
        self.size = size
        self.gravity = gravity
        self.drag = drag
        self.fade = fade

    def update(self, dt: float, level) -> bool:
        """返回 False 表示该粒子应当被回收。"""
        self.life -= dt
        if self.life <= 0.0:
            return False
        k = math.exp(-self.drag * dt)
        self.vx *= k
        self.vy *= k
        self.vz -= self.gravity * dt
        nx = self.x + self.vx * dt
        ny = self.y + self.vy * dt
        nz = self.z + self.vz * dt
        if level.is_wall_f(nx, self.y):
            self.vx *= -0.35
        else:
            self.x = nx
        if level.is_wall_f(self.x, ny):
            self.vy *= -0.35
        else:
            self.y = ny
        if nz <= 0.02:
            nz = 0.02
            self.vz = abs(self.vz) * 0.32
        self.z = nz
        return True


class DamageNumber:
    """伤害飘字：世界坐标 + 上浮。"""

    __slots__ = ("x", "y", "z", "value", "life", "max_life", "crit")

    def __init__(self, x, y, z, value: float, crit: bool = False):
        self.x, self.y, self.z = x, y, z
        self.value = value
        self.crit = crit
        self.life = self.max_life = 0.80 if not crit else 0.95

    def update(self, dt: float) -> bool:
        self.life -= dt
        self.z += dt * 0.85
        return self.life > 0.0


# ===========================================================================
# 子弹
# ===========================================================================
class Bullet:
    """
    通用子弹。

    用子步进（substep）保证高速弹不穿墙：每步位移不超过 0.22 格。
    """

    __slots__ = ("x", "y", "vx", "vy", "damage", "owner", "life", "radius",
                 "pierce", "color", "size", "hits", "trail")

    def __init__(self, x, y, vx, vy, damage, owner, life, radius, pierce,
                 color, size):
        self.x, self.y = x, y
        self.vx, self.vy = vx, vy
        self.damage = damage
        self.owner = owner              # "player" / "enemy"
        self.life = life
        self.radius = radius
        self.pierce = pierce
        self.color = color
        self.size = size
        self.hits: List[int] = []       # 已命中的敌人 id，避免穿透重复结算
        self.trail: List[Tuple[float, float]] = []

    def step(self, dt: float, level):
        """
        推进子弹。返回 (是否存活, 命中点或 None)。

        命中点用于生成火花粒子 —— 让每一次开枪都有物理反馈。
        """
        self.life -= dt
        if self.life <= 0.0:
            return False, None

        speed = math.hypot(self.vx, self.vy)
        travel = speed * dt
        steps = max(1, int(travel / 0.22) + 1)
        sdt = dt / steps

        self.trail.append((self.x, self.y))
        if len(self.trail) > 6:
            self.trail.pop(0)

        for _ in range(steps):
            nx = self.x + self.vx * sdt
            ny = self.y + self.vy * sdt
            if level.is_wall_f(nx, ny):
                return False, (self.x, self.y)
            self.x, self.y = nx, ny
        return True, None


# ===========================================================================
# 掉落物
# ===========================================================================
class Pickup:
    __slots__ = ("kind", "x", "y", "amount", "phase", "life", "magnet")

    def __init__(self, kind: str, x: float, y: float, phase: float = 0.0):
        self.kind = kind
        self.x, self.y = x, y
        self.amount = float(C.PICKUP_TYPES[kind]["amount"])
        self.phase = phase
        self.life = 26.0                # 26 秒后自动消失，避免掉落物无限堆积
        self.magnet = 0.0

    def update(self, dt: float, level, px: float, py: float) -> bool:
        """吸附逻辑：靠近时加速飞向玩家。返回 False 表示已过期。"""
        self.life -= dt
        if self.life <= 0.0:
            return False
        d = U.dist(self.x, self.y, px, py)
        if d < 2.2:
            k = 1.0 - d / 2.2
            self.magnet = min(1.0, self.magnet + dt * 3.0)
            pull = 3.2 * k * self.magnet
            ang = U.angle_to(self.x, self.y, px, py)
            nx = self.x + math.cos(ang) * pull * dt
            ny = self.y + math.sin(ang) * pull * dt
            if not level.is_wall_f(nx, ny):
                self.x, self.y = nx, ny
        return True


# ===========================================================================
# 玩家
# ===========================================================================
class Player:
    """玩家（代号 GHOST）：一个被上传进敌方内网的黑客进程。"""

    def __init__(self, x: float, y: float, facing: float = 0.0):
        self.x, self.y = x, y
        self.facing = facing                    # 朝向（弧度）
        self.vx = self.vy = 0.0                 # 速度（惯性用）
        self.radius = C.PLAYER_RADIUS

        # 资源
        self.max_hp = C.PLAYER_MAX_HP
        self.hp = self.max_hp
        self.max_armor = C.PLAYER_MAX_ARMOR
        self.armor = 0.0
        self.max_energy = C.PLAYER_MAX_ENERGY
        self.energy = self.max_energy
        self.energy_regen = C.PLAYER_ENERGY_REGEN

        # 局内升级累积
        self.damage_mul = 1.0
        self.rate_mul = 1.0
        self.pierce_bonus = 0
        self.crit_chance = 0.05
        self.leech = 0.0
        self.speed_mul = 1.0
        self.dash_cd_mul = 1.0
        self.iframe_mul = 1.0
        self.cost_mul = 1.0

        # 武器
        self.weapon = C.START_WEAPON
        self.cooldown = 0.0
        self.firing = False

        # 冲刺
        self.dash_timer = 0.0
        self.dash_cd = 0.0
        self.dash_dx = 1.0
        self.dash_dy = 0.0
        self.iframe = 0.0

        # 视觉
        self.bob_phase = 0.0
        self.bob_amount = 0.0
        self.pitch = 0.0
        self.recoil = 0.0                   # 枪械后坐（屏幕空间）
        self.flash = 0.0                    # 枪口闪光计时
        self.hurt_flash = 0.0
        self.alive = True

        # 统计
        self.kills = 0
        self.shots = 0
        self.hits_landed = 0
        self.damage_dealt = 0.0
        self.score = 0
        self.shards = 0

    # ------------------------------------------------------------------
    @property
    def speed(self) -> float:
        return C.MOVE_SPEED * self.speed_mul

    def weapon_data(self) -> Dict:
        return C.WEAPONS[self.weapon]

    # ------------------------------------------------------------------
    def take_damage(self, amount: float) -> float:
        """
        结算伤害：先扣护盾，再扣结构完整度。

        返回实际扣掉的血量（0 表示被无敌帧免疫），用于触发红屏反馈。
        """
        if not self.alive or self.iframe > 0.0:
            return 0.0
        left = amount
        if self.armor > 0.0:
            absorbed = min(self.armor, left * 0.68)
            self.armor -= absorbed
            left -= absorbed
        self.hp -= left
        self.hurt_flash = min(1.0, self.hurt_flash + 0.55 + amount * 0.012)
        if self.hp <= 0.0:
            self.hp = 0.0
            self.alive = False
        return amount

    def heal(self, amount: float) -> None:
        self.hp = min(self.max_hp, self.hp + amount)

    def add_armor(self, amount: float) -> None:
        self.armor = min(self.max_armor, self.armor + amount)

    def add_energy(self, amount: float) -> None:
        self.energy = min(self.max_energy, self.energy + amount)

    # ------------------------------------------------------------------
    def start_dash(self, dx: float, dy: float) -> bool:
        """朝输入方向冲刺，带短暂无敌帧（这是唯一的"主动防御"手段）。"""
        if self.dash_cd > 0.0 or self.dash_timer > 0.0:
            return False
        dx, dy = U.norm(dx, dy)
        if dx == 0.0 and dy == 0.0:
            dx, dy = math.cos(self.facing), math.sin(self.facing)
        self.dash_dx, self.dash_dy = dx, dy
        self.dash_timer = C.DASH_TIME
        self.dash_cd = C.DASH_COOLDOWN * self.dash_cd_mul
        self.iframe = max(self.iframe, C.DASH_IFRAMES * self.iframe_mul)
        return True

    # ------------------------------------------------------------------
    def update(self, dt: float, level, move_x: float, move_y: float, sprint: bool) -> None:
        """每帧推进玩家物理与资源回复。"""
        if not self.alive:
            return

        # 自救：玩家万一被挤进墙里（出生点异常 / 极端碰撞），先把身体推回格心
        if circle_blocked(level, self.x, self.y, self.radius):
            cx, cy = int(self.x) + 0.5, int(self.y) + 0.5
            k = min(1.0, dt * 5.0)
            self.x += (cx - self.x) * k
            self.y += (cy - self.y) * k

        self.cooldown = max(0.0, self.cooldown - dt)
        self.dash_cd = max(0.0, self.dash_cd - dt)
        self.iframe = max(0.0, self.iframe - dt)
        self.flash = max(0.0, self.flash - dt * 7.0)
        self.recoil = U.damp(self.recoil, 0.0, 12.0, dt)
        self.hurt_flash = U.damp(self.hurt_flash, 0.0, C.HIT_FLASH_DECAY, dt)

        if self.dash_timer > 0.0:
            self.dash_timer -= dt
            spd = C.DASH_SPEED
            dx = self.dash_dx * spd * dt
            dy = self.dash_dy * spd * dt
            nx, ny, hx, hy = move_with_slide(level, self.x, self.y, dx, dy, self.radius)
            self.x, self.y = nx, ny
            if hx and hy:
                self.dash_timer = 0.0
            self.bob_amount = U.damp(self.bob_amount, 2.0, 9.0, dt)
        else:
            mx, my = U.norm(move_x, move_y)
            target_vx = mx * self.speed
            target_vy = my * self.speed
            accel = C.MOVE_ACCEL * dt
            self.vx = U.approach(self.vx, target_vx, accel)
            self.vy = U.approach(self.vy, target_vy, accel)
            if mx == 0.0 and my == 0.0:
                self.vx = U.damp(self.vx, 0.0, 14.0, dt)
                self.vy = U.damp(self.vy, 0.0, 14.0, dt)
            nx, ny, _, _ = move_with_slide(level, self.x, self.y, self.vx * dt, self.vy * dt, self.radius)
            self.x, self.y = nx, ny

            spd = math.hypot(self.vx, self.vy)
            self.bob_phase += dt * C.BOB_SPEED * (spd / max(0.01, self.speed))
            self.bob_amount = U.damp(self.bob_amount, min(1.0, spd / max(0.01, self.speed)), 7.0, dt)

        # 能量自动回充（防卡死：永远不会因为没弹药而无法输出）
        self.energy = min(self.max_energy, self.energy + self.energy_regen * dt)

    # ------------------------------------------------------------------
    def can_fire(self) -> bool:
        w = self.weapon_data()
        cost = w["cost"] * self.cost_mul
        return self.alive and self.cooldown <= 0.0 and self.energy >= cost

    def fire(self, world) -> bool:
        """
        开火。由 game 层调用，返回是否真的射出。

        霰弹类武器一次生成多个弹丸（pellets），并各自独立散布，
        这样近距离伤害期望高、远距离衰减明显（自然的射程惩罚）。
        """
        if not self.can_fire():
            return False
        w = self.weapon_data()
        self.energy -= w["cost"] * self.cost_mul
        self.cooldown = w["cooldown"] / self.rate_mul
        self.shots += 1
        self.flash = 1.0
        self.recoil = min(1.0, self.recoil + 0.55 + w["cost"] * 0.012)

        muzzle_x = self.x + math.cos(self.facing) * 0.30
        muzzle_y = self.y + math.sin(self.facing) * 0.30
        # 轻微屏幕震动：射击手感的 30% 来自镜头反馈
        world.shake(0.55 + w["cost"] * 0.035)

        for _ in range(w["pellets"]):
            ang = self.facing + U.jitter(world.rng, w["spread"])
            vx = math.cos(ang) * w["speed"]
            vy = math.sin(ang) * w["speed"]
            dmg = w["damage"] * self.damage_mul
            world.bullets.append(Bullet(
                muzzle_x, muzzle_y, vx, vy, dmg, "player",
                life=1.5, radius=0.10,
                pierce=w["pierce"] + self.pierce_bonus,
                color=w["color"],
                size=0.16 if w["key"] != "rail" else 0.26,
            ))

        # 枪口粒子
        for _ in range(3 if w["pellets"] == 1 else 6):
            a = self.facing + U.jitter(world.rng, 0.5)
            world.spawn_particle(
                muzzle_x, muzzle_y, 0.55,
                math.cos(a) * world.rng.uniform(1.5, 4.5),
                math.sin(a) * world.rng.uniform(1.5, 4.5),
                world.rng.uniform(0.3, 1.3),
                0.16, w["color"], 0.05,
            )
        world.alert_nearby(self.x, self.y, 11.0)
        world.audio.play(w["key"], 0.85)
        return True

    # ------------------------------------------------------------------
    def apply_upgrade(self, up: Dict) -> str:
        """应用一张协议补丁，返回给玩家看的效果描述。"""
        kind = up["apply"]
        val = up["value"]
        if kind == "max_hp":
            self.max_hp += val
            self.heal(val)
            return f"MAX HP +{int(val)}"
        if kind == "damage_mul":
            self.damage_mul += val
            return f"DAMAGE +{int(val * 100)}%"
        if kind == "rate_mul":
            self.rate_mul += val
            self.cost_mul = max(0.45, self.cost_mul - 0.08)
            return f"FIRE RATE +{int(val * 100)}%"
        if kind == "speed_mul":
            self.speed_mul += val
            self.dash_cd_mul = max(0.35, self.dash_cd_mul - 0.18)
            return f"MOVE +{int(val * 100)}%"
        if kind == "max_armor":
            self.max_armor += val
            self.armor = self.max_armor
            return f"MAX ARMOR +{int(val)}"
        if kind == "pierce":
            self.pierce_bonus += int(val)
            return f"PIERCE +{int(val)}"
        if kind == "energy":
            self.max_energy += val
            self.energy = self.max_energy
            self.energy_regen *= 1.45
            return f"ENERGY +{int(val)}"
        if kind == "dash":
            self.dash_cd_mul = max(0.25, self.dash_cd_mul - val)
            self.iframe_mul += 0.6
            return "DASH COOLDOWN -25%"
        if kind == "crit":
            self.crit_chance += val
            return f"CRIT +{int(val * 100)}%"
        if kind == "leech":
            self.leech += val
            return f"LEECH +{int(val)}/KILL"
        return "UNKNOWN PATCH"


# ===========================================================================
# 敌人
# ===========================================================================
class Enemy:
    """ICE 防御程序。行为由 ENEMY_TYPES[type]["behavior"] 模板驱动。"""

    _next_id = 1

    def __init__(self, kind: str, x: float, y: float, hp_scale: float = 1.0,
                 dmg_scale: float = 1.0):
        self.id = Enemy._next_id
        Enemy._next_id += 1
        data = C.ENEMY_TYPES[kind]
        self.kind = kind
        self.data = data
        self.x, self.y = x, y
        self.radius = data["radius"]
        self.max_hp = data["hp"] * hp_scale
        self.hp = self.max_hp
        self.damage = data["damage"] * dmg_scale
        self.speed = data["speed"]
        self.alive = True

        self.state = "idle"
        self.state_time = 0.0
        self.facing = 0.0
        self.attack_cd = 0.0
        self.anim_t = 0.0
        self.anim_frame = 0
        self.hurt_flash = 0.0
        self.last_seen: Optional[Tuple[float, float]] = None
        self.patrol_dir = 0.0
        self.wander_timer = 0.0
        self.strafe_dir = 1.0
        self.strafe_timer = 0.0
        self.blink_cd = 2.0
        self.spawn_anim = 0.55           # 出生动画期间不攻击，给玩家反应时间
        self.hit_slow = 0.0

    # ------------------------------------------------------------------
    @property
    def sprite_key(self) -> str:
        return self.kind

    def hurt(self, amount: float) -> bool:
        """扣血，返回是否死亡。"""
        if not self.alive:
            return False
        self.hp -= amount
        self.hurt_flash = 1.0
        self.hit_slow = 0.12
        if self.state in ("idle", "patrol"):
            self.state = "alert"
            self.state_time = 0.0
        if self.hp <= 0.0:
            self.hp = 0.0
            self.alive = False
            return True
        return False

    # ------------------------------------------------------------------
    def _has_los(self, level, px: float, py: float) -> bool:
        return not U.grid_ray_hit(level.grid, self.x, self.y, px, py, max_steps=90)

    def _sense(self, level, player, alerted: bool) -> bool:
        """
        感知判定：视线 + 距离 + 视野锥 + 听觉。

        视野锥设为 290°（近乎全向），因为敌人是部署在要塞里的常驻防御程序，
        本来就在"值班"。真正的限制来自 **视线遮挡** —— 墙才是玩家的掩护，
        这也让"利用掩体脱离战斗"成为一个有效的战术。
        """
        d = U.dist(self.x, self.y, player.x, player.y)
        if d > 20.0:
            return False
        if alerted:
            return True
        if not self._has_los(level, player.x, player.y):
            return False
        ang = U.angle_to(self.x, self.y, player.x, player.y)
        if abs(U.signed_angle_diff(self.facing, ang)) > math.radians(145.0):
            return False
        return d < 17.0

    # ------------------------------------------------------------------
    def update(self, dt: float, world, player) -> None:
        if not self.alive:
            return
        level = world.level
        self.state_time += dt
        self.hurt_flash = max(0.0, self.hurt_flash - dt * 3.4)
        self.hit_slow = max(0.0, self.hit_slow - dt)
        self.attack_cd = max(0.0, self.attack_cd - dt)

        # 自救：万一被挤进几何体内部（生成点抖动、被击退等），先把自己推出来。
        # 没有这段兜底，"敌人卡在墙里不动"会变成极难复现的偶发 bug。
        if circle_blocked(level, self.x, self.y, self.radius):
            self._unstick(dt, level)
            return

        if self.spawn_anim > 0.0:
            self.spawn_anim -= dt
            return

        px, py = player.x, player.y
        d = U.dist(self.x, self.y, px, py)
        sees = self._sense(level, player, world.player_alerted and d < 12.0)

        # ---------------- 状态迁移 ----------------
        if sees:
            self.last_seen = (px, py)
            if self.state in ("idle", "patrol", "alert"):
                self.state = "chase"
                self.state_time = 0.0
        elif self.state == "chase" and self.state_time > 2.6:
            # 丢失目标一段时间后回到巡逻（但记住最后位置）
            self.state = "patrol"
            self.state_time = 0.0

        target_atk_range = self.data["attack_range"]

        # ---------------- 行为执行 ----------------
        speed_scale = 1.0 - min(0.55, self.hit_slow * 3.0)
        spd = self.speed * speed_scale

        if self.state == "idle":
            self._idle(dt, world)
        elif self.state == "alert":
            # 刚被惊动：转向玩家方向，短暂僵直（给玩家先手）
            self.facing = U.rotate_towards(self.facing, U.angle_to(self.x, self.y, px, py), dt * 5.0)
            if self.state_time > 0.28:
                self.state = "chase"
        elif self.state == "patrol":
            self._patrol(dt, world, spd)
        elif self.state == "chase":
            self._chase(dt, world, player, d, spd, sees)
        elif self.state == "attack":
            self._attack(dt, world, player, d)
            if self.state_time > 0.22:
                self.state = "chase"

        # 朝向：追击时始终面向玩家（有转向速度上限，不是瞬间锁头）
        if self.state in ("chase", "attack") or sees:
            want = U.angle_to(self.x, self.y, px, py)
            turn = dt * (3.4 if self.data["behavior"] != "melee" else 5.5)
            self.facing = U.rotate_towards(self.facing, want, turn)

        # ---------------- 动画 ----------------
        moving = math.hypot(self._last_move[0], self._last_move[1]) if hasattr(self, "_last_move") else 0.0
        self.anim_t += dt * (3.5 + moving * 6.0)
        self.anim_frame = int(self.anim_t) % 4

    # ------------------------------------------------------------------
    _last_move = (0.0, 0.0)

    def _unstick(self, dt: float, level) -> None:
        """
        脱困：向最近的可通行格中心"硬拉"。

        这一步故意不做碰撞检测 —— 目的是把实体从非法位置里拽出来，
        而不是表现得像个正常行走。
        """
        bx, by = int(self.x), int(self.y)
        for r in range(1, 5):
            for dy in range(-r, r + 1):
                for dx in range(-r, r + 1):
                    nx, ny = bx + dx, by + dy
                    if not level.in_bounds(nx, ny) or level.grid[ny][nx] != 0:
                        continue
                    cx, cy = nx + 0.5, ny + 0.5
                    if circle_blocked(level, cx, cy, self.radius):
                        continue
                    ang = math.atan2(cy - self.y, cx - self.x)
                    self.x += math.cos(ang) * 3.0 * dt
                    self.y += math.sin(ang) * 3.0 * dt
                    return

    def _move(self, dt: float, world, dx: float, dy: float, spd: float) -> None:
        """移动 + 避障。_last_move 用于动画速度。"""
        nx, ny, hx, hy = move_with_slide(world.level, self.x, self.y, dx * spd * dt, dy * spd * dt, self.radius)
        moved = (nx - self.x, ny - self.y)
        self._last_move = moved
        self.x, self.y = nx, ny
        if hx or hy:
            # 卡墙：沿墙切向试探（触须）
            self._avoid(dt, world, spd)

    def _avoid(self, dt: float, world, spd: float) -> None:
        """触须避障：左右各探 60°，选更开阔的一侧绕行。"""
        best_ang = self.facing
        best_free = -1.0
        for off in (1.05, -1.05, 1.6, -1.6, 0.5, -0.5):
            ang = self.facing + off
            free = U.ray_cast_distance(world.level.grid, self.x, self.y, ang, 1.4, 0.16)
            if free > best_free:
                best_free = free
                best_ang = ang
        self.facing = U.rotate_towards(self.facing, best_ang, dt * 7.0)
        dx, dy = math.cos(self.facing), math.sin(self.facing)
        nx, ny, _, _ = move_with_slide(world.level, self.x, self.y, dx * spd * dt * 0.9, dy * spd * dt * 0.9, self.radius)
        self.x, self.y = nx, ny

    # ------------------------------------------------------------------
    def _idle(self, dt: float, world) -> None:
        if self.state_time > 1.4:
            self.state = "patrol"
            self.state_time = 0.0
            self.patrol_dir = world.rng.uniform(0.0, math.tau)

    def _patrol(self, dt: float, world, spd: float) -> None:
        """
        搜索推进。

        设计取舍：这是**波次生存**，不是潜行游戏。敌人默认沿流场朝玩家推进
        （它们本就部署在要塞里等着你），只在离玩家较远时才有小概率随机游走，
        制造一点"它去哪了"的不确定性。
        """
        d_to_player = U.dist(self.x, self.y, world.player.x, world.player.y)
        self.wander_timer -= dt
        if self.wander_timer <= 0.0:
            self.wander_timer = world.rng.uniform(1.2, 3.0)
            # 只有远离玩家时才可能分心游走；贴脸时必然直冲
            self.wander_mode = (d_to_player > 10.0) and (world.rng.random() < 0.30)

        if not self.wander_mode:
            flow = world.flow_direction(self.x, self.y)
            if flow is not None:
                want = math.atan2(flow[1], flow[0])
                self.facing = U.rotate_towards(self.facing, want, dt * 3.4)
                self._move(dt, world, flow[0], flow[1], spd * 0.88)
                return
            # 流场没有更优邻居（已到玩家所在格）时，直接朝玩家走
            dx, dy = U.norm(world.player.x - self.x, world.player.y - self.y)
            if dx != 0.0 or dy != 0.0:
                self._move(dt, world, dx, dy, spd * 0.88)
                return

        self.patrol_dir += U.jitter(world.rng, 0.9)
        if self.last_seen is not None:
            self.patrol_dir = U.angle_to(self.x, self.y, *self.last_seen)
            if U.dist(self.x, self.y, *self.last_seen) < 1.1:
                self.last_seen = None
        dx, dy = math.cos(self.patrol_dir), math.sin(self.patrol_dir)
        self.facing = U.rotate_towards(self.facing, self.patrol_dir, dt * 2.4)
        self._move(dt, world, dx, dy, spd * 0.62)

    def _chase(self, dt: float, world, player, d: float, spd: float, sees: bool) -> None:
        """
        追击。

        关键：**没有视线时改用流场寻路**。
        只用"朝玩家直线走"的话，敌人会顶在墙上原地抖动（AI 最经典的失败模式）。
        这里在没有 LOS 时沿 BFS 流场绕路，有视线后再切换成战术走位。
        """
        beh = self.data["behavior"]
        px, py = player.x, player.y
        ang = U.angle_to(self.x, self.y, px, py)
        has_los = self._has_los(world.level, px, py)

        if not has_los:
            flow = world.flow_direction(self.x, self.y)
            if flow is not None:
                self._move(dt, world, flow[0], flow[1], spd * 1.04)
                return
            # 流场不可用时退化为绕行探测
            self._avoid(dt, world, spd)
            return

        if beh == "melee":
            dx, dy = math.cos(ang), math.sin(ang)
            # 近战冲刺：偶尔加速突进
            boost = 1.35 if d < 5.0 else 1.0
            self._move(dt, world, dx, dy, spd * boost)
            if d < self.data["attack_range"] and self.attack_cd <= 0.0:
                self._enter_attack(world)
            return

        if beh == "blink":
            # 幻影：周期性短距闪现，走位不可预测
            self.blink_cd -= dt
            if self.blink_cd <= 0.0 and d > 2.4:
                self.blink_cd = world.rng.uniform(2.6, 4.4)
                dist = min(4.2, d - 1.6)
                tx = self.x + math.cos(ang) * dist
                ty = self.y + math.sin(ang) * dist
                if not circle_blocked(world.level, tx, ty, self.radius):
                    world.spawn_burst(self.x, self.y, 0.5, self.data["color"], 12)
                    self.x, self.y = tx, ty
                    world.spawn_burst(self.x, self.y, 0.5, self.data["accent"], 12)
            self._strafe(dt, world, ang, d, spd, self.data["keep_dist"])
            if sees and d < self.data["attack_range"] and self.attack_cd <= 0.0:
                self._enter_attack(world)
            return

        # ranged / heavy：保持距离 + 侧移
        self._strafe(dt, world, ang, d, spd, self.data["keep_dist"])
        if sees and d < self.data["attack_range"] and self.attack_cd <= 0.0:
            self._enter_attack(world)

    def _strafe(self, dt: float, world, ang: float, d: float, spd: float, keep: float) -> None:
        """
        以 ang 方向为基准，做径向（接近/后退）+ 切向（侧移）的合成移动。
        """
        radial = 0.0
        if d > keep + 1.1:
            radial = 1.0
        elif d < keep - 0.7:
            radial = -0.85

        self.strafe_timer -= dt
        if self.strafe_timer <= 0.0:
            self.strafe_timer = world.rng.uniform(0.7, 1.9)
            self.strafe_dir = world.rng.choice((1.0, -1.0))

        tang = self.strafe_dir * (0.85 if radial != 0.0 else 1.0)
        tx = math.cos(ang + math.pi / 2) * tang
        ty = math.sin(ang + math.pi / 2) * tang
        rx = math.cos(ang) * radial
        ry = math.sin(ang) * radial
        dx, dy = U.norm(tx + rx, ty + ry)
        if dx == 0.0 and dy == 0.0:
            dx, dy = math.cos(ang), math.sin(ang)
        self._move(dt, world, dx, dy, spd)

    # ------------------------------------------------------------------
    def _enter_attack(self, world) -> None:
        self.state = "attack"
        self.state_time = 0.0

    def _attack(self, dt: float, world, player, d: float) -> None:
        """攻击结算（瞬时生效，视觉上由弹体/特效承担反馈）。"""
        if self.attack_cd > 0.0:
            return
        self.attack_cd = self.data["attack_cd"] * world.difficulty_attack_rate
        beh = self.data["behavior"]

        if beh == "melee":
            if d < self.data["attack_range"] + 0.25:
                player.take_damage(self.damage)
                world.audio.play("player_hit", 0.7)
                world.shake(3.0)
                world.on_player_hit()
            return

        # 远程弹体：向玩家当前位置射击（不做预判，给玩家闪避空间）
        ang = U.angle_to(self.x, self.y, player.x, player.y)
        ang += U.jitter(world.rng, 0.045)
        speed = C.ENEMY_BULLET_SPEED * (1.35 if beh == "heavy" else 1.0)
        bx = self.x + math.cos(ang) * (self.radius + 0.14)
        by = self.y + math.sin(ang) * (self.radius + 0.14)
        world.bullets.append(Bullet(
            bx, by, math.cos(ang) * speed, math.sin(ang) * speed,
            self.damage, "enemy", life=2.6,
            radius=C.ENEMY_BULLET_RADIUS,
            pierce=0, color=self.data["accent"],
            size=0.18 if beh != "heavy" else 0.26,
        ))
        world.audio.play("hit", 0.16)

    # ------------------------------------------------------------------
    def hp_ratio(self) -> float:
        return self.hp / max(1e-6, self.max_hp)
