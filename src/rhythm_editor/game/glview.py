"""OpenGL 呈现层（可选）：把软件绘制的画面作为纹理上传后用 GL 全屏绘制。

这样做的意义：

* 现在就能在两种后端之间切换、比较稳定性与延迟；
* 之后要做真正的 3D 特效（粒子、发光、后期处理）时，绘制代码已经在 GL 管线里，
  可以把对应物件改为直接发顶点 / 着色器绘制，而不必重写整个游玩器。

没有 PyOpenGL、或者创建不了 GL 上下文时，:func:`available` 返回 ``False``，
游玩器会自动回退到纯软件渲染。
"""

from __future__ import annotations

from typing import Tuple

import pygame

try:  # pragma: no cover - 依赖可选依赖
    from OpenGL import GL  # type: ignore
except Exception:  # pragma: no cover
    GL = None  # type: ignore[assignment]


def available() -> bool:
    """PyOpenGL 是否可用（不代表一定能创建上下文）。"""
    return GL is not None


class GLPresenter:
    """把 pygame 表面作为纹理贴到全屏四边形上。"""

    def __init__(self) -> None:
        if GL is None:  # pragma: no cover - 调用方会先检查
            raise RuntimeError("没有可用的 PyOpenGL")
        self._texture = GL.glGenTextures(1)
        self._size: Tuple[int, int] = (0, 0)
        self._allocated = False
        GL.glBindTexture(GL.GL_TEXTURE_2D, self._texture)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
        GL.glEnable(GL.GL_TEXTURE_2D)

    def present(self, surface: pygame.Surface, verify: bool = False) -> bool:
        """上传像素并绘制（调用方随后 ``pygame.display.flip()`` 交换缓冲）。

        ``verify=True`` 时回读一小块像素自检：驱动把画面画成全黑 / 报 GL 错误时返回
        ``False``，调用方据此回退软件渲染（某些机器上 GL 窗口“一直黑屏”就是这个原因）。
        """
        if GL is None:  # pragma: no cover
            return False
        size = surface.get_size()
        data = pygame.image.tostring(surface, "RGB", False)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self._texture)
        if not self._allocated or size != self._size:
            # 只在尺寸变化时重新分配纹理，之后用 glTexSubImage2D 覆盖（每帧快很多）
            self._size = size
            self._allocated = True
            GL.glTexImage2D(
                GL.GL_TEXTURE_2D, 0, GL.GL_RGB, size[0], size[1], 0,
                GL.GL_RGB, GL.GL_UNSIGNED_BYTE, data,
            )
        else:
            GL.glTexSubImage2D(
                GL.GL_TEXTURE_2D, 0, 0, 0, size[0], size[1],
                GL.GL_RGB, GL.GL_UNSIGNED_BYTE, data,
            )
        GL.glMatrixMode(GL.GL_PROJECTION)
        GL.glLoadIdentity()
        GL.glOrtho(0.0, 1.0, 0.0, 1.0, -1.0, 1.0)
        GL.glMatrixMode(GL.GL_MODELVIEW)
        GL.glLoadIdentity()
        GL.glClearColor(0.0, 0.0, 0.0, 1.0)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT)
        # 注意：图像数据是从上到下，纹理坐标要垂直翻转
        GL.glBegin(GL.GL_QUADS)
        GL.glTexCoord2f(0.0, 0.0)
        GL.glVertex2f(0.0, 1.0)
        GL.glTexCoord2f(1.0, 0.0)
        GL.glVertex2f(1.0, 1.0)
        GL.glTexCoord2f(1.0, 1.0)
        GL.glVertex2f(1.0, 0.0)
        GL.glTexCoord2f(0.0, 1.0)
        GL.glVertex2f(0.0, 0.0)
        GL.glEnd()
        if not verify:
            return True
        try:
            if GL.glGetError():
                return False
            width, height = size
            box = max(1, min(16, width, height))
            x = max(0, width // 2 - box // 2)
            y = max(0, height // 2 - box // 2)
            data = GL.glReadPixels(
                x, height - y - box, box, box, GL.GL_RGB, GL.GL_UNSIGNED_BYTE
            )
            raw = bytes(data)
            # 自检帧中央画了白块：回读“不是全黑”才算 GL 呈现可用
            return bool(raw) and max(raw) > 40
        except Exception:  # pragma: no cover - 依赖驱动
            return False
