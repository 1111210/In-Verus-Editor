"""验证 Panda3D 离屏渲染 + 贴图 + 像素回读（游玩器换渲染层的前提）。

用法::

    python tools/check_panda3d.py

成功时打印 Panda3D 版本、离屏缓冲尺寸、以及一帧里的非背景像素数量。
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    try:
        from panda3d.core import (
            CardMaker,
            Filename,
            FrameBufferProperties,
            GraphicsPipe,
            GraphicsOutput,
            NodePath,
            Texture,
            WindowProperties,
            loadPrcFileData,
        )
        from direct.showbase.ShowBase import ShowBase
        import panda3d
    except Exception as exc:                      # pragma: no cover - 依赖缺失
        print(f"[FAIL] 无法导入 Panda3D：{exc}")
        return 1

    loadPrcFileData("", "window-type offscreen")
    loadPrcFileData("", "audio-library-name null")
    base = ShowBase()
    try:
        width, height = 640, 480
        texture_path = os.path.join(ROOT, "assets", "textures", "player", "tap_1.png")
        if not os.path.isfile(texture_path):
            print(f"[FAIL] 缺少测试贴图：{texture_path}")
            return 1
        texture = base.loader.loadTexture(Filename.fromOsSpecific(texture_path))
        texture.setFormat(Texture.FRgba)

        # 离屏渲染目标（把渲染结果拷回内存，便于转成 pygame 画面）
        props = FrameBufferProperties()
        props.setRgbaBits(8, 8, 8, 8)
        props.setDepthBits(24)
        win_props = WindowProperties.size(width, height)
        buffer = base.graphicsEngine.make_output(
            base.pipe,
            "panda-check",
            -1,
            props,
            win_props,
            GraphicsPipe.BFRefuseWindow,
            base.win.getGsg(),
            base.win,
        )
        if buffer is None:
            print("[FAIL] 无法创建离屏缓冲（make_output 返回 None）")
            return 1
        render_tex = Texture()
        render_tex.setFormat(Texture.FRgba8)
        buffer.add_render_texture(render_tex, GraphicsOutput.RTMCopyRam)
        camera = base.makeCamera(buffer)
        camera.setPos(0, -3.0, 0.6)
        camera.setP(-20)
        base.camLens.setFov(55)

        card = CardMaker("tap")
        card.setFrame(-0.4, 0.4, -0.2, 0.2)
        node = NodePath(card.generate())
        node.setTexture(texture)
        node.setP(90)                                     # 平铺在水平面里
        node.reparentTo(base.render)

        base.graphicsEngine.render_frame()
        data = render_tex.getRamImageAs("RGB")
        raw = bytes(data)
        if len(raw) < width * height * 3:
            print(f"[FAIL] 回读像素不足：{len(raw)} < {width * height * 3}")
            return 1
        step = 3 * 7
        bright = sum(1 for index in range(0, len(raw), step) if raw[index] > 40)
        total = len(range(0, len(raw), step))
        print(f"[OK ] Panda3D {panda3d.__version__} 离屏渲染 {width}x{height}，"
              f"非背景采样点 {bright}/{total}")
        return 0 if bright > 0 else 1
    finally:
        base.destroy()


if __name__ == "__main__":
    raise SystemExit(main())
