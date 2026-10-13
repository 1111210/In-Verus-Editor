"""带差分的 Tk 画布场景：按稳定 key 复用画布对象，避免每帧重建。

用法：

    scene.begin()
    scene.rect("lane0", x0, y0, x1, y1, fill="#123")
    scene.line(...)
    scene.end()      # 自动删除本帧未出现的对象
"""

from __future__ import annotations

import tkinter as tk
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


class Scene:
    def __init__(self, canvas: tk.Canvas) -> None:
        self.canvas = canvas
        self._items: Dict[str, Tuple[int, str, tuple]] = {}
        self._used: set[str] = set()
        # 分组：同一画布上「放置层 / 预览层」等各自独立增删，
        # 这样预览可以每帧重画，而放置层只在内容变化时更新。
        self._active_group = "main"
        self._used_by_group: Dict[str, set[str]] = {"main": self._used}
        self._group_of: Dict[str, str] = {}
        self._frames = 0
        self._unsupported: set[str] = set()
        # 图层：先声明的层在下面。切换布局时画布对象可能被重新创建，
        # 若不显式排序，新对象会跑到最上层（例如区域底色盖住物件）。
        self._current_layer = 0
        self._layers: set[int] = set()
        self._z_dirty = False
        # 统计信息（性能诊断用）：本帧/累计的建对象、移动、改样式、删对象次数
        self.stats: Dict[str, int] = {"created": 0, "moved": 0, "configured": 0, "deleted": 0}
        self.frame_stats: Dict[str, int] = {"created": 0, "moved": 0, "configured": 0, "deleted": 0}

    def set_layer(self, layer: int) -> None:
        self._current_layer = int(layer)
        self._layers.add(self._current_layer)

    def _layer_tag(self) -> str:
        return f"__layer{self._current_layer}"

    def _fix_z_order(self) -> None:
        for layer in sorted(self._layers):
            try:
                self.canvas.tag_raise(f"__layer{layer}")
            except tk.TclError:  # pragma: no cover
                pass
        self._z_dirty = False

    # ------------------------------------------------------------------ 兼容
    _OPTIONAL_OPTIONS = ("dash", "stipple", "arrowshape", "smooth", "outlineoffset")

    def _safe(self, options: Dict[str, Any]) -> Dict[str, Any]:
        if not self._unsupported:
            return options
        return {k: v for k, v in options.items() if k not in self._unsupported}

    def _drop_optional(self, options: Dict[str, Any], exc: tk.TclError) -> Dict[str, Any]:
        """某些平台/Tk 版本不支持个别画布选项时，记录并降级，保证渲染不中断。"""
        reduced = dict(options)
        for key in self._OPTIONAL_OPTIONS:
            if key in reduced:
                self._unsupported.add(key)
                reduced.pop(key, None)
                return reduced
        raise exc

    # ------------------------------------------------------------------ 帧控制
    def begin(self, group: str = "main") -> None:
        self._active_group = str(group)
        self._used = self._used_by_group.setdefault(self._active_group, set())
        self._used.clear()
        self.frame_stats = {"created": 0, "moved": 0, "configured": 0, "deleted": 0}

    def end(self, group: Optional[str] = None) -> None:
        name = self._active_group if group is None else str(group)
        used = self._used_by_group.setdefault(name, set())
        stale = [
            key for key, owner in self._group_of.items() if owner == name and key not in used
        ]
        for key in stale:
            item_id, _, _ = self._items.pop(key)
            self._group_of.pop(key, None)
            self.canvas.delete(item_id)
            self.stats["deleted"] += 1
            self.frame_stats["deleted"] += 1
        if self._z_dirty:
            self._fix_z_order()
        self._frames += 1

    def clear(self) -> None:
        for item_id, _, _ in self._items.values():
            self.canvas.delete(item_id)
        self._items.clear()
        self._used_by_group.clear()
        self._group_of.clear()
        self._active_group = "main"
        self._used = set()
        self._used_by_group["main"] = self._used

    @property
    def item_count(self) -> int:
        return len(self._items)

    @property
    def frames(self) -> int:
        return self._frames

    # ------------------------------------------------------------------ 基础图元
    def _put(self, key: str, kind: str, coords: Sequence[float], options: Dict[str, Any]) -> None:
        self._used.add(key)
        self._group_of[key] = self._active_group
        tags = tuple(options.get("tags") or ()) + (self._layer_tag(),)
        options["tags"] = tags
        new_coords = tuple(round(float(c), 2) for c in coords)
        new_options = tuple(options.items())
        spec = (new_coords, new_options)
        entry = self._items.get(key)
        if entry is None or entry[1] != kind:
            if entry is not None:
                self.canvas.delete(entry[0])
            item_id = self._create(kind, new_coords, options)
            self._z_dirty = True
            self.stats["created"] += 1
            self.frame_stats["created"] += 1
            self._items[key] = (item_id, kind, spec)
            return
        item_id, _, old_spec = entry
        if old_spec == spec:
            return
        # 坐标与样式分开比较：滚动 / 播放时只有坐标在变，可省掉 itemconfigure 的 Tcl 往返
        if old_spec[0] != new_coords:
            self.canvas.coords(item_id, *new_coords)
            self.stats["moved"] += 1
            self.frame_stats["moved"] += 1
        if old_spec[1] != new_options:
            try:
                self.canvas.itemconfigure(item_id, **self._safe(options))
                self.stats["configured"] += 1
                self.frame_stats["configured"] += 1
            except tk.TclError as exc:  # pragma: no cover - 依赖平台
                try:
                    self.canvas.itemconfigure(item_id, **self._drop_optional(options, exc))
                except tk.TclError:
                    pass
        self._items[key] = (item_id, kind, spec)

    def _create(self, kind: str, coords: Sequence[float], options: Dict[str, Any]) -> int:
        canvas = self.canvas
        creators = {
            "rect": canvas.create_rectangle,
            "line": canvas.create_line,
            "polygon": canvas.create_polygon,
            "oval": canvas.create_oval,
            "text": canvas.create_text,
        }
        if kind not in creators:
            raise ValueError(kind)
        create = creators[kind]
        try:
            return create(*coords, **self._safe(options))
        except tk.TclError as exc:  # pragma: no cover - 依赖平台
            return create(*coords, **self._drop_optional(options, exc))

    # ------------------------------------------------------------------ 快捷方法
    def rect(self, key: str, x0: float, y0: float, x1: float, y1: float, **options: Any) -> None:
        options.setdefault("tags", ())
        if x0 > x1:
            x0, x1 = x1, x0
        if y0 > y1:
            y0, y1 = y1, y0
        self._put(key, "rect", (x0, y0, x1, y1), options)

    def line(self, key: str, x0: float, y0: float, x1: float, y1: float, **options: Any) -> None:
        options.setdefault("tags", ())
        self._put(key, "line", (x0, y0, x1, y1), options)

    def polyline(self, key: str, points: Iterable[Tuple[float, float]], **options: Any) -> None:
        flat: List[float] = []
        for x, y in points:
            flat.extend((x, y))
        if len(flat) < 4:
            return
        options.setdefault("tags", ())
        self._put(key, "line", flat, options)

    def polygon(self, key: str, points: Iterable[Tuple[float, float]], **options: Any) -> None:
        flat: List[float] = []
        for x, y in points:
            flat.extend((x, y))
        if len(flat) < 6:
            return
        options.setdefault("tags", ())
        self._put(key, "polygon", flat, options)

    def oval(self, key: str, x0: float, y0: float, x1: float, y1: float, **options: Any) -> None:
        options.setdefault("tags", ())
        self._put(key, "oval", (x0, y0, x1, y1), options)

    def text(self, key: str, x: float, y: float, content: str, **options: Any) -> None:
        options["text"] = content
        options.setdefault("tags", ())
        options.setdefault("anchor", "nw")
        self._put(key, "text", (x, y), options)
