"""游玩器（3D 视角）。当前进度：

* :mod:`judge`   —— 判定核心（EXACT / Exact / Near / Break、滑键吸附、空域区间、分数）✅
* :mod:`scene`   —— 3D 布局与透视投影（判定平面、两条判定线、鼠标平面在上、近大远小）✅
* :mod:`settings`—— 玩家设置（键位、灵敏度、物件厚度、音量延迟、无界鼠标…）✅
* ``app``        —— 主界面 / 设置 / 加载 / 游玩 / 结算的 pygame 外壳（待接入）

设计要点见 ``docs/PLAYER.md``。
"""

from __future__ import annotations

__all__ = ["judge", "scene", "settings"]
