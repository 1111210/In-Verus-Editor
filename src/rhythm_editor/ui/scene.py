"""带差分的 Tk 画布场景：按稳定 key 复用画布对象，避免每帧重建。

用法::

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
        self._frames = 0
        self._unsupported: set[str] = set()
        # 图层：先声明的层在下面。切换布局时画布对象可能被重新创建，
        # 若不显式排序，新对象会跑到最上层（例如区域底色盖住物件）。
        self._current_layer = 0
        self._layers: set[int] = set()
        self._z_dirty = False

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
    def begin(self) -> None:
        self._used.clear()

    def end(self) -> None:
        stale = [key for key in self._items if key not in self._used]
        for key in stale:
            item_id, _, _ = self._items.pop(key)
            self.canvas.delete(item_id)
        if self._z_dirty:
            self._fix_z_order()
        self._frames += 1

    def clear(self) -> None:
        for item_id, _, _ in self._items.values():
            self.canvas.delete(item_id)
        self._items.clear()
        self._used.clear()

    @property
    def item_count(self) -> int:
        return len(self._items)

    @property
    def frames(self) -> int:
        return self._frames

    # ------------------------------------------------------------------ 基础图元
    def _put(self, key: str, kind: str, coords: Sequence[float], options: Dict[str, Any]) -> None:
        self._used.add(key)
        tags = tuple(options.get("tags") or ()) + (self._layer_tag(),)
        options["tags"] = tags
        spec = (tuple(round(float(c), 2) for c in coords), tuple(sorted(options.items())))
        entry = self._items.get(key)
        if entry is None or entry[1] != kind:
            if entry is not None:
                self.canvas.delete(entry[0])
            item_id = self._create(kind, coords, options)
            self._z_dirty = True
            self._items[key] = (item_id, kind, spec)
            return
        item_id, _, old_spec = entry
        if old_spec == spec:
            return
        self.canvas.coords(item_id, *[round(float(c), 2) for c in coords])
        try:
            self.canvas.itemconfigure(item_id, **self._safe(options))
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

    # ------------------------------------------------------------------ 命中
    def item_at(self, x: float, y: float, tags: Optional[Tuple[str, ...]] = None) -> Optional[str]:
        """返回该点最上层图形对应的 key（仅限本场景登记过的对象）。"""
        owned = {item: key for key, (item, _, _) in self._items.items()}
        for item_id in reversed(self.canvas.find_overlapping(x - 1, y - 1, x + 1, y + 1)):
            key = owned.get(item_id)
            if key is None:
                continue
            if tags and not set(tags).issubset(set(self.canvas.gettags(item_id))):
                continue
            return key
        return None

    def key_of_item(self, item_id: int) -> Optional[str]:
        for key, (item, _, _) in self._items.items():
            if item == item_id:
                return key
        return None
