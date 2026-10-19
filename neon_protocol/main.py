# -*- coding: utf-8 -*-
"""
NEON PROTOCOL - 启动入口
========================

用法
----
正常游玩：
    python -m neon_protocol.main

常用参数：
    --res 640x400        调整内部渲染分辨率（越大越清晰、越吃 CPU）
    --scale 2            窗口放大倍率
    --seed 12345         固定随机种子（复现同一张地图）
    --fullscreen         全屏
    --no-audio           静音
    --headless --frames 3000   无头自动压测（跑机器人 AI，输出性能统计）
    --bench             渲染基准测试（逐层耗时）

设计说明
--------
无头模式通过 SDL 的 dummy 驱动实现：不弹窗口、不出声，但走的是**完全一致**
的渲染与逻辑代码路径，因此可以用它做回归测试与性能回归。
"""

from __future__ import annotations

import argparse
import os
import sys
import time

# 允许直接 `python neon_protocol/main.py` 运行（把包父目录加入 sys.path）
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "neon_protocol"


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="neon_protocol",
        description="NEON PROTOCOL - 光线投射赛博朋克 FPS（纯 Python + NumPy）",
    )
    p.add_argument("--res", type=str, default=None, help="内部渲染分辨率，如 640x400")
    p.add_argument("--scale", type=int, default=None, help="窗口放大倍数")
    p.add_argument("--seed", type=int, default=None, help="随机种子")
    p.add_argument("--fullscreen", action="store_true", help="全屏运行")
    p.add_argument("--no-audio", action="store_true", help="禁用音频")
    p.add_argument("--headless", action="store_true", help="无头模式（不显示窗口）")
    p.add_argument("--frames", type=int, default=0, help="无头模式下的最大帧数")
    p.add_argument("--no-autoplay", action="store_true", help="无头模式下关闭机器人 AI")
    p.add_argument("--bench", action="store_true", help="渲染基准测试")
    p.add_argument("--start-wave", type=int, default=1, help="从第几波开始（调试用）")
    p.add_argument("--shots", type=str, default=None, help="截图输出目录")
    p.add_argument("--shot-frames", type=str, default="120,420,900,1500",
                   help="在第几帧截图，逗号分隔")
    p.add_argument("--shot-count", type=int, default=0,
                   help="覆盖 shot-frames：每隔 N 帧截一张，共 6 张")
    return p.parse_args(argv)


def apply_config(args) -> None:
    from . import config as C

    if args.res:
        try:
            w, h = args.res.lower().split("x")
            C.RES_W, C.RES_H = int(w), int(h)
        except Exception:
            print(f"[warn] 无法解析分辨率 '{args.res}'，忽略")
    if args.scale:
        C.SCALE = max(1, int(args.scale))
    C.WINDOW_W = C.RES_W * C.SCALE
    C.WINDOW_H = C.RES_H * C.SCALE
    if args.seed is not None:
        C.SEED = args.seed
    if args.no_audio:
        C.AUDIO_ENABLED = False


def run_bench(args) -> int:
    """渲染基准：分别测关卡生成、纹理生成、逐帧渲染的耗时。"""
    from . import config as C
    from .game import Game
    import pygame

    print("=" * 62)
    print(" NEON PROTOCOL // RENDER BENCHMARK")
    print("=" * 62)
    print(f" 内部渲染分辨率 : {C.RES_W} x {C.RES_H}")
    print(f" 窗口分辨率     : {C.WINDOW_W} x {C.WINDOW_H}    目标帧率: {C.TARGET_FPS}")
    print("-" * 62)

    t0 = time.perf_counter()
    g = Game(headless=True, seed=args.seed, autoplay=True)
    t_init = time.perf_counter() - t0
    print(f" 初始化（纹理/精灵/音效合成）: {t_init * 1000:8.1f} ms")

    from .level import Level
    import random

    t0 = time.perf_counter()
    for i in range(20):
        Level(random.Random(i), wave_index=i % 8)
    t_level = (time.perf_counter() - t0) / 20 * 1000
    print(f" 关卡生成（单张地图均值）    : {t_level:8.2f} ms")

    # 预热
    for _ in range(30):
        g.step(1 / 60.0, [])
    g.frame_times.clear()

    N = 200
    t0 = time.perf_counter()
    for _ in range(N):
        g.step(1 / 60.0, [])
    total = time.perf_counter() - t0
    per = total / N * 1000

    # 分阶段计时：单独测墙面 / 精灵 / 后处理
    r = g.renderer
    w = g.world
    p = w.player
    import math

    dir_x, dir_y = math.cos(p.facing), math.sin(p.facing)
    P = math.tan(math.radians(C.FOV_DEG) * 0.5)
    plane_x, plane_y = -dir_y * P, dir_x * P
    ppu = r.w / (2 * P)

    def timeit(fn, n=200):
        t = time.perf_counter()
        for _ in range(n):
            fn()
        return (time.perf_counter() - t) / n * 1000

    t_grid = timeit(lambda: r._draw_perspective_grid(p.x, p.y, dir_x, dir_y, plane_x, plane_y, 0.0))
    t_wall = timeit(lambda: r._draw_walls(p.x, p.y, dir_x, dir_y, plane_x, plane_y, 0.0, ppu, w.level))
    t_spr = timeit(lambda: r._draw_sprites(w, dir_x, dir_y, plane_x, plane_y, 0.0, ppu, 0.0, 0.0))
    t_post = timeit(lambda: r._post_process(w, 1 / 60.0, 0.0, 0.0))

    print("-" * 62)
    print(f" 完整一帧（逻辑+渲染）       : {per:8.2f} ms   -> {1000 / per:6.1f} FPS 理论值")
    print(f"   ├─ 透视网格地面           : {t_grid:8.2f} ms")
    print(f"   ├─ 墙面光线投射           : {t_wall:8.2f} ms")
    print(f"   ├─ 精灵/粒子              : {t_spr:8.2f} ms")
    print(f"   └─ 后处理                 : {t_post:8.2f} ms")
    print("-" * 62)
    print(f" 实体规模：敌人 {w.enemies_alive} / 子弹 {len(w.bullets)} / "
          f"粒子 {len(w.particles)} / 掉落 {len(w.pickups)}")
    print("=" * 62)
    g.shutdown()
    return 0


def run_shots(args) -> int:
    """无头截图：用于验证画面（渲染路径与实机完全一致）。"""
    import pygame
    from . import config as C
    from .game import Game

    outdir = args.shots
    os.makedirs(outdir, exist_ok=True)

    if args.shot_count > 0:
        marks = [args.shot_count * (i + 1) for i in range(6)]
    else:
        marks = []
        for tok in args.shot_frames.split(","):
            tok = tok.strip()
            if tok.isdigit():
                marks.append(int(tok))
    marks = sorted(set(m for m in marks if m > 0))

    saved = []

    # 1) 标题界面
    g0 = Game(headless=True, seed=args.seed, autoplay=False)
    for _ in range(3):
        g0.fps_ring.push(1 / 60.0)
        g0.step(1 / 60.0, [])
    p = os.path.join(outdir, "00_title.png")
    pygame.image.save(g0.screen, p)
    saved.append(p)

    # 2) 操作说明界面
    from .game import ST_CONTROLS, ST_PLAY, ST_UPGRADE
    g0.state = ST_CONTROLS
    g0.step(1 / 60.0, [])
    p = os.path.join(outdir, "01_controls.png")
    pygame.image.save(g0.screen, p)
    saved.append(p)
    g0.shutdown()

    # 3) 战斗画面
    g = Game(headless=True, seed=args.seed, autoplay=True)
    last = max(marks)
    got_upgrade = False
    for i in range(1, last + 1):
        g.fps_ring.push(1 / 60.0)
        g.step(1 / 60.0, [])
        if i in marks:
            tag = "play" if g.state == ST_PLAY else g.state
            p = os.path.join(outdir, f"{len(saved):02d}_{tag}_f{i:05d}.png")
            pygame.image.save(g.screen, p)
            saved.append(p)
        # 抓一张升级选卡界面
        if g.state == ST_UPGRADE and not got_upgrade:
            got_upgrade = True
            p = os.path.join(outdir, f"{len(saved):02d}_upgrade.png")
            pygame.image.save(g.screen, p)
            saved.append(p)

    # 4) 全屏地图（Tab）
    g.show_map = True
    g.step(1 / 60.0, [])
    p = os.path.join(outdir, f"{len(saved):02d}_fullmap.png")
    pygame.image.save(g.screen, p)
    saved.append(p)

    print("=" * 62)
    print(" NEON PROTOCOL // SCREENSHOT DUMP")
    print("=" * 62)
    print(f" 分辨率  : {C.WINDOW_W} x {C.WINDOW_H}")
    print(f" 输出目录: {os.path.abspath(outdir)}")
    for s in saved:
        print(f"   - {os.path.basename(s)}")
    print("=" * 62)
    g.shutdown()
    return 0


def main(argv=None) -> int:
    args = parse_args(argv)

    if args.headless:
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

    apply_config(args)

    from . import config as C

    if args.fullscreen and not args.headless:
        os.environ["SDL_VIDEO_WINDOW_POS"] = "centered"

    if args.bench:
        return run_bench(args)

    if not args.headless:
        print(f"[NEON PROTOCOL] 窗口 {C.WINDOW_W}x{C.WINDOW_H} / "
              f"内部渲染 {C.RES_W}x{C.RES_H} / 目标 {C.TARGET_FPS} FPS")
        print("                W A S D 移动 | 鼠标转向 | 左键开火 | Shift 冲刺 | "
              "1/2/3 换枪 | Tab 地图 | ESC 暂停")

    from .game import Game

    game = Game(headless=args.headless, seed=args.seed,
                autoplay=args.headless and not args.no_autoplay)

    if args.start_wave > 1:
        game.world.new_run(args.start_wave)

    if args.shots:
        return run_shots(args)

    if args.headless:
        frames = args.frames if args.frames > 0 else 1800
        t0 = time.perf_counter()
        stats = game.run(max_frames=frames)
        wall = time.perf_counter() - t0
        print("=" * 62)
        print(" NEON PROTOCOL // HEADLESS SELFTEST REPORT")
        print("=" * 62)
        print(f" 模拟帧数        : {int(stats.get('frames', 0))}")
        print(f" 模拟游戏时长    : {frames / 60.0:.1f} s   （真实耗时 {wall:.1f} s）")
        print(f" 到达波次        : {int(stats.get('wave', 0))} / {C.TOTAL_WAVES}")
        print(f" 击杀数          : {int(stats.get('kills', 0))}")
        print(f" 玩家存活        : {'YES' if stats.get('alive', 0) > 0.5 else 'NO (被击杀)'}"
              f"  剩余结构完整度 {stats.get('hp', 0):.0f}")
        print(f" 渲染耗时 均值   : {stats.get('render_ms_avg', 0):.2f} ms")
        print(f" 渲染耗时 P95    : {stats.get('render_ms_p95', 0):.2f} ms")
        print(f" 渲染耗时 峰值   : {stats.get('render_ms_max', 0):.2f} ms")
        print(f" 理论帧率        : {1000.0 / max(0.01, stats.get('render_ms_avg', 1)):.1f} FPS")
        print("=" * 62)
        return 0

    try:
        game.run()
    except KeyboardInterrupt:
        print("\n[NEON PROTOCOL] 中断退出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
