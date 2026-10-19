# -*- coding: utf-8 -*-
"""
NEON PROTOCOL
=============

一个用纯 Python + NumPy 实现的光线投射（Raycasting）第一人称射击游戏。

- 自研伪 3D 渲染管线（DDA 光线投射 + 透视网格地面 + 精灵 z-buffer + 后处理）
- 程序化生成关卡、程序化生成全部美术资源、程序化合成全部音效
- 波次生存 + 局内升级（roguelite）玩法

运行
----
    python -m neon_protocol.main

更多见 main.py 的 --help。
"""

__version__ = "1.0.0"
__all__ = ["main"]
