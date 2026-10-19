<div align="center">

# NEON PROTOCOL

### 霓虹协议 · 数据要塞入侵

*纯 Python + NumPy 实现的赛博朋克光线投射 FPS，无游戏引擎、无外部资源文件。*

[![Python](https://img.shields.io/badge/python-3.13-blue.svg)](https://www.python.org)
[![Engine](https://img.shields.io/badge/engine-pygame%20%2B%20numpy-9cf.svg)](https://www.pygame.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

</div>

---

## 玩法

你是一个被上传进敌方内网的黑客进程（代号 **GHOST**）。要塞里部署了四类 ICE 防御程序，
它们会沿掩体追猎你。在 8 波不断增强的攻势中存活下来，并用途中拾取的「协议补丁」强化自己。

- **WASD** — 移动（相对视角方向）
- **鼠标** — 转向瞄准
- **左键** — 开火
- **Shift** — 冲刺（短暂无敌帧，唯一的主动防御手段）
- **1 / 2 / 3** — 切换武器
- **Tab** — 全屏地图
- **ESC** — 暂停

## 特性

- **自研光线投射引擎**：墙体/地板/精灵/粒子全部用 NumPy 数组手写，无贴图依赖，程序化生成霓虹纹理。
- **流场寻路（flow field）**：敌人沿 BFS 距离场绕墙推进，不会顶着墙抖动；失去视线时自动绕路。
- **有限状态机 AI**：idle → alert → chase → attack → dead，配感知（视线 + 距离 + 视野锥 + 听觉）与避障。
- **惯性移动 + 贴墙滑动**：手感平滑，撞墙不死锁。
- **固定步长物理**：低帧率机器上手感一致。
- **波次 / 升级系统**：每波抽 3 张协议补丁，构成局内 build。

## 运行

```bash
# 1. 创建虚拟环境并安装依赖
cd neon_protocol
python -m venv venv
venv\Scripts\activate        # Windows
pip install pygame numpy

# 2. 启动（或直接双击根目录的 启动游戏.bat）
python -m neon_protocol.main
```

无头 / 调试参数：

```bash
python -m neon_protocol.main --headless --frames 3000   # 机器人自动跑，输出性能报告
python -m neon_protocol.main --bench                    # 渲染分层基准测试
python -m neon_protocol.main --res 640x400 --scale 2    # 调分辨率 / 放大倍率
```

## 项目结构

```
neon_protocol/
  main.py       启动入口、参数、无头压测 / 截图 / 基准
  game.py       主循环、状态机、输入、固定步长物理、渲染编排、世界状态（World）
  entities.py   玩家 / 敌人 / 子弹 / 掉落物 / 粒子
  level.py      随机要塞生成（房间 + 走廊）
  renderer.py   光线投射渲染与后处理（扫描线 / 暗角 / 撕裂）
  hud.py        界面叠层（武器 / 血量 / 小地图 / 升级卡）
  textures.py   程序化纹理与武器精灵
  audio.py      实时合成音效（无需音频文件）
  config.py     全局数值（所有「魔法数字」集中管理）
  utils.py      数学 / 几何工具
```

## 许可

MIT —— 自由使用、修改、再分发。
