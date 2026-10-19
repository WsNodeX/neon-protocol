# -*- coding: utf-8 -*-
"""
NEON PROTOCOL - 程序化音频合成
==============================

不加载任何 wav/ogg，全部音效在启动时用 numpy 合成波形并交给 pygame.mixer 播放。

合成链
------
oscillator(sine/saw/square/noise) -> 包络(ADSR) -> 滤波(卷积低通) -> 立体声展宽 -> int16

为什么这样做
------------
1. 零素材依赖，仓库里没有一个二进制文件；
2. 参数化：音高/时长/滤波都跟武器数据绑定，改武器手感时音效自动跟着变；
3. 体量极小（几百 KB 内存），且能保证 8 位/16 位复古电子音（chiptune）的统一听感。

全部失败可降级：若无声卡（CI 环境），Audio.ok=False，游戏静默运行不崩溃。
"""

from __future__ import annotations

import math
from typing import Dict, Optional

import numpy as np

try:
    import pygame
    _HAS_PYGAME = True
except Exception:                                     # pragma: no cover
    _HAS_PYGAME = False

from . import config as C

RATE = C.AUDIO_RATE


# ===========================================================================
# 基础波形
# ===========================================================================
def _t(n: int) -> np.ndarray:
    return np.arange(n, dtype=np.float32) / RATE


def sine(freq: float, n: int, phase: float = 0.0) -> np.ndarray:
    return np.sin(2.0 * np.pi * freq * _t(n) + phase).astype(np.float32)


def sweep(f0: float, f1: float, n: int, curve: float = 1.0) -> np.ndarray:
    """频率从 f0 线性/曲线扫到 f1 的振荡（相位连续，避免爆音）。"""
    tt = _t(n)
    k = (tt / max(1e-6, tt[-1] if n > 1 else 1.0)) ** curve
    f = f0 + (f1 - f0) * k
    phase = 2.0 * np.pi * np.cumsum(f) / RATE
    return np.sin(phase).astype(np.float32)


def saw(freq: float, n: int) -> np.ndarray:
    ph = (_t(n) * freq) % 1.0
    return (ph * 2.0 - 1.0).astype(np.float32)


def square(freq: float, n: int, duty: float = 0.5) -> np.ndarray:
    ph = (_t(n) * freq) % 1.0
    return np.where(ph < duty, 1.0, -1.0).astype(np.float32)


def noise(n: int, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.uniform(-1.0, 1.0, n).astype(np.float32)


# ===========================================================================
# 包络与滤波
# ===========================================================================
def adsr(n: int, a: float = 0.005, d: float = 0.05, s: float = 0.5,
         r: float = 0.06, sustain_level: float = 0.6) -> np.ndarray:
    """
    生成 ADSR 包络。a/d/r 为秒，s 为保持时长。
    最终长度可能短于 n，会自动补零到 n。
    """
    na = max(1, int(a * RATE))
    nd = max(1, int(d * RATE))
    ns = max(0, int(s * RATE))
    nr = max(1, int(r * RATE))
    env = np.concatenate([
        np.linspace(0.0, 1.0, na, dtype=np.float32),
        np.linspace(1.0, sustain_level, nd, dtype=np.float32),
        np.full(ns, sustain_level, np.float32),
        np.linspace(sustain_level, 0.0, nr, dtype=np.float32),
    ])
    if len(env) < n:
        env = np.concatenate([env, np.zeros(n - len(env), np.float32)])
    return env[:n]


def exp_decay(n: int, tau: float = 0.08) -> np.ndarray:
    """指数衰减包络，打击类音效的主力。"""
    return np.exp(-_t(n) / max(1e-4, tau)).astype(np.float32)


def lowpass(x: np.ndarray, cutoff_ratio: float = 0.15) -> np.ndarray:
    """用汉宁窗核做一次卷积低通（cutoff_ratio 越小越闷）。"""
    k = max(3, int(1.0 / max(0.01, cutoff_ratio)))
    if k % 2 == 0:
        k += 1
    kernel = np.hanning(k).astype(np.float32)
    kernel /= kernel.sum()
    return np.convolve(x, kernel, mode="same").astype(np.float32)


def highpass(x: np.ndarray, ratio: float = 0.05) -> np.ndarray:
    """原始信号减去低通分量 => 高通。让打击音更"脆"。"""
    return (x - lowpass(x, ratio)).astype(np.float32)


def distortion(x: np.ndarray, drive: float = 2.4) -> np.ndarray:
    """tanh 软削波，给电子音加厚度。"""
    return np.tanh(x * drive).astype(np.float32)


def normalize(x: np.ndarray, peak: float = 0.9) -> np.ndarray:
    m = float(np.max(np.abs(x))) if x.size else 0.0
    if m < 1e-6:
        return x
    return (x / m * peak).astype(np.float32)


def stereo(x: np.ndarray, width: float = 0.0) -> np.ndarray:
    """
    单声道 -> 立体声 int16。

    width>0 时给右声道加极小延迟，制造廉价的"空间感"。
    """
    if width > 0.0:
        d = max(1, int(width * RATE))
        left = np.concatenate([x, np.zeros(d, np.float32)])[:len(x)]
        right = np.concatenate([np.zeros(d, np.float32), x])[:len(x)]
        st = np.stack([left, right], axis=1)
    else:
        st = np.stack([x, x], axis=1)
    return (np.clip(st, -1.0, 1.0) * 32767.0).astype(np.int16)


# ===========================================================================
# 具体音效
# ===========================================================================
def _sfx_pulse() -> np.ndarray:
    """脉冲冲锋枪：短促方波 + 快速下滑，干脆利落。"""
    n = int(0.075 * RATE)
    body = square(880, n, 0.35) * 0.5 + sweep(1400, 320, n, 0.6) * 0.7
    x = distortion(body, 2.0) * exp_decay(n, 0.024)
    x = highpass(x, 0.06)
    return normalize(x, 0.78)


def _sfx_rail() -> np.ndarray:
    """磁轨长矛：0.25s 充能上扫 + 爆裂瞬态。"""
    charge_n = int(0.22 * RATE)
    hit_n = int(0.30 * RATE)
    charge = sweep(180, 2400, charge_n, 1.8) * adsr(charge_n, 0.01, 0.02, 0.12, 0.02, 0.85) * 0.5
    crack = highpass(noise(hit_n, 3), 0.02) * exp_decay(hit_n, 0.05) * 0.9
    tail = sine(72, hit_n) * exp_decay(hit_n, 0.12) * 0.8
    x = np.concatenate([charge, crack + tail])
    return normalize(distortion(x, 1.6), 0.92)


def _sfx_scatter() -> np.ndarray:
    """散射阵列：宽带噪声 + 低频冲击。"""
    n = int(0.26 * RATE)
    x = lowpass(noise(n, 11), 0.30) * exp_decay(n, 0.055) * 1.0
    x += sine(96, n) * exp_decay(n, 0.05) * 0.7
    x = distortion(x, 1.7)
    return normalize(x, 0.86)


def _sfx_hit() -> np.ndarray:
    """命中敌人：金属脆响。"""
    n = int(0.07 * RATE)
    x = highpass(noise(n, 5), 0.04) * exp_decay(n, 0.016)
    x += sine(1750, n) * exp_decay(n, 0.013) * 0.5
    return normalize(x, 0.55)


def _sfx_crit() -> np.ndarray:
    """暴击：更亮、带一点金属泛音。"""
    n = int(0.13 * RATE)
    x = highpass(noise(n, 9), 0.03) * exp_decay(n, 0.028)
    x += sine(2600, n) * exp_decay(n, 0.03) * 0.6
    x += sine(3900, n) * exp_decay(n, 0.02) * 0.35
    return normalize(x, 0.7)


def _sfx_kill() -> np.ndarray:
    """敌人解体：下滑噪声 + 低频轰鸣。"""
    n = int(0.42 * RATE)
    x = sweep(900, 90, n, 0.8) * exp_decay(n, 0.11) * 0.8
    x += lowpass(noise(n, 21), 0.22) * exp_decay(n, 0.09) * 0.7
    x += sine(58, n) * exp_decay(n, 0.16) * 0.5
    return normalize(x, 0.82)


def _sfx_player_hit() -> np.ndarray:
    """玩家受击：沉闷的重击 + 破碎噪声。"""
    n = int(0.34 * RATE)
    x = sine(120, n) * exp_decay(n, 0.10) * 1.0
    x += sweep(400, 60, n, 1.4) * exp_decay(n, 0.08) * 0.6
    x += lowpass(noise(n, 31), 0.4) * exp_decay(n, 0.05) * 0.5
    return normalize(distortion(x, 1.5), 0.85)


def _sfx_pickup() -> np.ndarray:
    """拾取：两个上行的纯音，反馈清晰。"""
    a = int(0.05 * RATE)
    b = int(0.09 * RATE)
    x = np.concatenate([
        sine(1050, a) * exp_decay(a, 0.02),
        sine(1580, b) * exp_decay(b, 0.03),
    ]) * 0.8
    return normalize(x, 0.6)


def _sfx_upgrade() -> np.ndarray:
    """升级：四音上行琶音，仪式感。"""
    notes = [523.25, 659.25, 783.99, 1046.5]
    parts = []
    for f in notes:
        n = int(0.11 * RATE)
        parts.append((sine(f, n) * 0.6 + sine(f * 2, n) * 0.22) * adsr(n, 0.004, 0.03, 0.03, 0.03, 0.5))
    x = np.concatenate(parts)
    return normalize(x, 0.55)


def _sfx_wave() -> np.ndarray:
    """新一波：低频警报上扫 + 双音提示。"""
    n = int(0.85 * RATE)
    alarm = sweep(120, 520, n, 1.5) * (0.55 + 0.45 * (np.sin(_t(n) * 2 * np.pi * 6.0) * 0.5 + 0.5))
    alarm *= adsr(n, 0.05, 0.1, 0.5, 0.2, 0.7)
    x = alarm * 0.7 + sweep(240, 1040, n, 1.2) * exp_decay(n, 0.25) * 0.35
    return normalize(x, 0.6)


def _sfx_dash() -> np.ndarray:
    """冲刺：带通噪声的"咻"。"""
    n = int(0.24 * RATE)
    x = highpass(lowpass(noise(n, 41), 0.5), 0.04) * adsr(n, 0.01, 0.05, 0.08, 0.08, 0.55)
    x += sweep(1600, 380, n, 2.2) * exp_decay(n, 0.07) * 0.35
    return normalize(x, 0.5)


def _sfx_ui_move() -> np.ndarray:
    n = int(0.045 * RATE)
    return normalize(square(1240, n, 0.25) * exp_decay(n, 0.012) * 0.4, 0.4)


def _sfx_ui_confirm() -> np.ndarray:
    n = int(0.16 * RATE)
    x = np.concatenate([
        sine(880, int(0.05 * RATE)) * exp_decay(int(0.05 * RATE), 0.02),
        sine(1320, int(0.10 * RATE)) * exp_decay(int(0.10 * RATE), 0.04),
    ])
    return normalize(x, 0.5)


def _sfx_game_over() -> np.ndarray:
    """失败：下行小调，沉重但不刺耳。"""
    parts = []
    for f, d in ((392.0, 0.22), (311.1, 0.22), (261.6, 0.5)):
        n = int(d * RATE)
        parts.append((sine(f, n) * 0.6 + saw(f * 0.5, n) * 0.18) * adsr(n, 0.01, 0.05, d * 0.5, 0.1, 0.5))
    return normalize(np.concatenate(parts), 0.6)


def _sfx_win() -> np.ndarray:
    """胜利：大三和弦上行 + 长尾。"""
    parts = []
    for f, d in ((523.25, 0.18), (659.25, 0.18), (783.99, 0.18), (1046.5, 0.65)):
        n = int(d * RATE)
        parts.append((sine(f, n) * 0.55 + sine(f * 2, n) * 0.2) * adsr(n, 0.008, 0.04, d * 0.5, 0.12, 0.55))
    return normalize(np.concatenate(parts), 0.62)


def _sfx_ambient() -> np.ndarray:
    """
    环境底噪（循环）。

    关键点：所有频率都取 0.5Hz 的整数倍（= 2 秒缓冲内的整数周期），
    这样首尾自然衔接，循环播放不会出现咔哒声。
    """
    dur = 2.0
    n = int(dur * RATE)
    x = np.zeros(n, np.float32)
    x += sine(55.0, n) * 0.34
    x += sine(82.5, n) * 0.20
    x += sine(110.0, n) * 0.12
    x += sine(164.5, n) * 0.07
    # 缓慢起伏的"机房风声"
    lfo = 0.5 + 0.5 * np.sin(2 * np.pi * 0.5 * np.cumsum(np.ones(n, np.float32)) / RATE)
    x *= (0.75 + 0.25 * lfo)
    x += lowpass(noise(n, 77), 0.02) * 0.06
    return normalize(x, 0.30)


def _sfx_alarm() -> np.ndarray:
    """低血量心跳：两声闷响。"""
    n = int(0.9 * RATE)
    x = np.zeros(n, np.float32)
    for start, amp in ((0.0, 1.0), (0.24, 0.7)):
        s = int(start * RATE)
        seg_n = int(0.16 * RATE)
        seg = sine(64, seg_n) * exp_decay(seg_n, 0.045)
        x[s:s + seg_n] += seg * amp
    return normalize(x, 0.5)


# ===========================================================================
# 音频管理器
# ===========================================================================
class Audio:
    """
    音频管理器。

    - init() 在 mixer 不可用时静默降级
    - play(name) 播放一次性音效，带同名节流，避免高速射击时声道爆炸
    - ambient 用独立 channel 循环播放，音量随紧张度动态抬升
    """

    def __init__(self) -> None:
        self.ok = False
        self.sounds: Dict[str, "pygame.mixer.Sound"] = {}
        self.volumes: Dict[str, float] = {}
        self._last_play: Dict[str, float] = {}
        self._throttle: Dict[str, float] = {
            "hit": 0.030, "crit": 0.040, "kill": 0.045,
            "player_hit": 0.09, "pickup": 0.05,
            "pulse": 0.035, "scatter": 0.05, "rail": 0.08,
        }
        self.ambient_channel: Optional["pygame.mixer.Channel"] = None
        self._tension = 0.0

    # ------------------------------------------------------------------
    def init(self) -> bool:
        if not _HAS_PYGAME or not C.AUDIO_ENABLED:
            return False
        try:
            pygame.mixer.pre_init(C.AUDIO_RATE, -16, 2, 512)
            pygame.mixer.init(C.AUDIO_RATE, -16, 2, 512)
            pygame.mixer.set_num_channels(C.MAX_SFX_CHANNELS)
        except Exception:
            self.ok = False
            return False

        builders = {
            "pulse": (_sfx_pulse, 0.85),
            "rail": (_sfx_rail, 0.75),
            "scatter": (_sfx_scatter, 0.80),
            "hit": (_sfx_hit, 0.50),
            "crit": (_sfx_crit, 0.60),
            "kill": (_sfx_kill, 0.65),
            "player_hit": (_sfx_player_hit, 0.80),
            "pickup": (_sfx_pickup, 0.55),
            "upgrade": (_sfx_upgrade, 0.70),
            "wave": (_sfx_wave, 0.65),
            "dash": (_sfx_dash, 0.55),
            "ui_move": (_sfx_ui_move, 0.35),
            "ui_confirm": (_sfx_ui_confirm, 0.55),
            "game_over": (_sfx_game_over, 0.75),
            "win": (_sfx_win, 0.75),
            "ambient": (_sfx_ambient, 0.32),
            "alarm": (_sfx_alarm, 0.60),
        }
        try:
            for name, (fn, vol) in builders.items():
                wave = fn()
                snd = pygame.sndarray.make_sound(stereo(wave, 0.0006 if name != "ambient" else 0.0))
                snd.set_volume(vol * C.SFX_VOLUME * 3.0)
                self.sounds[name] = snd
                self.volumes[name] = vol
        except Exception:
            self.ok = False
            return False

        self.ok = True
        return True

    # ------------------------------------------------------------------
    def play(self, name: str, volume: float = 1.0) -> None:
        """播放一次性音效。带同名节流，防止每帧上百个声道。"""
        if not self.ok:
            return
        snd = self.sounds.get(name)
        if snd is None:
            return
        now = pygame.time.get_ticks() / 1000.0
        th = self._throttle.get(name, 0.0)
        if th and now - self._last_play.get(name, -99.0) < th:
            return
        self._last_play[name] = now
        ch = pygame.mixer.find_channel(True)
        if ch is None:
            return
        ch.set_volume(max(0.0, min(1.0, volume)))
        ch.play(snd)

    # ------------------------------------------------------------------
    def start_ambient(self) -> None:
        if not self.ok:
            return
        snd = self.sounds.get("ambient")
        if snd is None:
            return
        try:
            self.ambient_channel = snd.play(loops=-1)
            if self.ambient_channel:
                self.ambient_channel.set_volume(0.5)
        except Exception:
            self.ambient_channel = None

    def stop_ambient(self) -> None:
        if self.ambient_channel:
            try:
                self.ambient_channel.stop()
            except Exception:
                pass
            self.ambient_channel = None

    def set_tension(self, t: float) -> None:
        """0~1 的紧张度：驱动环境音音量与超低频嗡鸣。"""
        self._tension = max(0.0, min(1.0, t))
        if self.ambient_channel:
            try:
                self.ambient_channel.set_volume(0.35 + 0.5 * self._tension)
            except Exception:
                pass

    def shutdown(self) -> None:
        self.stop_ambient()
        if self.ok:
            try:
                pygame.mixer.quit()
            except Exception:
                pass
        self.ok = False
