"""演示视图：编辑器「演示模式」用的软件 3D 预览（只读、可暂停 / 继续）。

* scene     —— 3D 布局与透视投影（判定平面、两条判定线、鼠标平面在上）
* frame     —— 世界坐标下的物件几何与可见性（与编辑器共用同一套速度组语义）
* textures  —— 预设贴图的加载与规格化
* softview  —— 离屏软件渲染（编辑器把画出的帧贴到画布上）
* settings  —— 画面参数（物件厚度 / 流速 / 侧轨高亮）

这里没有任何判定、计分、输入或窗口逻辑：演示模式只是把谱面在“游玩视角”下画出来。
"""

from __future__ import annotations

__all__ = ["frame", "scene", "settings", "softview", "textures"]
