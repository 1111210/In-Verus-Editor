# 验证清单

本文档记录**如何证明**需求已经实现，以及每一步的预期结果。

## 1. 环境准备

```powershell
# 虚拟环境
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
# 可选：真实音频后端
.\.venv\Scripts\python.exe -m pip install pygame-ce
```

> 不安装 pygame 时，编辑器使用静音时钟后端，状态栏会提示；
> 滚动 / 缩放 / 播放进度 / 全部编辑功能照常可用。

## 2. 一键自检（不启动界面）

```bash
python run_editor.py --make-demo        # 生成 charts/demo/demo.ivchart
python tools/acceptance.py              # 需求到证据的清单
```

预期：

```
[OK ] 项目结构（src/ docs/ tests/ tools/ charts/）
[OK ] 6K 物件：单轨 / 多轨 / 长按
[OK ] 鼠标停留区域（半透明蓝 + 不透明边界 + 缓动）
[OK ] 鼠标滑键（方向由首尾位置决定）
[OK ] BPM 列表（第 0 拍项存在，含每小节拍数）
[OK ] 速度组（默认组 + 特效组，含 0/负数流速）
[OK ] 全局设置（缩放 / 网格 / 横向辅助 / 音频延迟）
[OK ] 谱面可保存并重新读取（字段完全一致）
[OK ] 撤销 / 重做
[OK ] 复制 / 粘贴：时间平移，轨道与横向位置不变
[OK ] 沿游玩区中轴线镜像
[OK ] 内置自检（时间轴 / 速度组 / 编辑命令 / 存档往返）
结论：全部通过
```

## 3. 单元测试（含真实 Tk 画布冒烟测试）

```bash
python -m unittest discover -s tests -t . -v
```

覆盖内容：

| 测试文件 | 覆盖的需求 |
| --- | --- |
| `tests/test_timing.py` | BPM 列表、偏移、拍↔秒换算、网格吸附、小节线 |
| `tests/test_speed.py` | 速度组积分、0/负流速、判定时刻位于判定线 |
| `tests/test_notes.py` | 单轨/多轨/长按、区域边界与缓动、滑键方向、镜像、序列化 |
| `tests/test_chart_document.py` | 物件增删、撤销/重做、复制粘贴、镜像、速度组归属、ID 唯一性 |
| `tests/test_chart_io.py` | 谱面 JSON 往返、音频复制与查找 |
| `tests/test_demo_selftest.py` | 示例谱面结构、内置自检 |
| `tests/test_editor_smoke.py` | 真实 Tk 画布渲染、全部放置工具、框选/Ctrl 对称差、粘贴流程、滚动吸附、缩放、轨道渲染 |
| `tests/test_dialogs.py` | BPM 第 0 拍项不可删除/只可修改、默认速度组不可删除、物件编辑与缓动写入 |
| `tests/test_app_flow.py` | 应用级：播放/暂停后判定线吸附、延迟补偿、演示模式只读、设置与保存 |

## 4. 人工验收（需要图形界面）

```bash
python run_editor.py charts/demo/demo.ivchart
```

1. 滚轮滚动 —— 两个区域同步移动，判定线始终落在网格线上。
2. `Ctrl+滚轮` —— 网格大小改变（缩放）；`Ctrl+滚轮` 反向恢复。
3. 工具栏「横向辅助」设为 8 → 鼠标区域出现 8 等分竖直虚线；
   用「左右滑键」点击时横坐标吸附到网格线。
4. 「编辑器设置」把「时间网格」改为 3 → 出现三连音网格。
5. 「长按」：点起点后滚动、缩放，再点结束时刻（可点在鼠标区域上）→ 生成粉色长按。
6. 「停留区域」：两次点击后在对话框里设置两条边界（含缓入 / 缓出）→ 生成半透明蓝色区域。
7. 「选择」框选若干部件 → `Ctrl+C`、`Ctrl+V` 后点击新时间 → 轨道与横向位置不变。
8. `M` 镜像 → 6K 物件跨中轴线翻转，滑键方向反转。
9. 「速度组」新建组 → 添加 `从 x 拍起，倍率 -1` → 组内物件反向运动；删除组后回到默认组。
10. 空格播放 → 画面与音乐同步；再按空格暂停 → 判定线吸附到网格。
11. `Tab` 进入演示模式 → 只能滚动 / 缩放 / 播放，不能编辑；`Tab` 返回。

## 5. 截图

```bash
python tools/screenshot_editor.py docs/images
```

会生成 `docs/images/editor_overview.png`、`placement.png`、`demo_mode.png`
（需要可用的桌面会话；脚本用 GDI `PrintWindow` 抓取窗口客户区）。

## 6. 音频后端校验

```bash
python tools/check_audio.py
```

预期（安装 pygame-ce 后为真实播放）：

```
[OK ] 音频后端：pygame.mixer -- 真实播放
[OK ] 载入 WAV
[OK ] 播放位置随时间前进
[OK ] 耳朵听到的位置 = 后端位置 − 系统延迟
[OK ] 暂停后不再前进
[OK ] 暂停状态标记正确
[OK ] seek 到指定位置
[OK ] 从任意位置继续播放
[OK ] 停止后位置归零
结论：全部通过
```

## 7. 实测结果（本机：Windows + Python 3.14.7 + Tk 9.0 + pygame-ce 2.5.8）

| 项目 | 命令 | 结果 |
| --- | --- | --- |
| 单元测试（含 Tk 画布冒烟测试与应用级测试） | `python -m unittest discover -s tests -t .` | Ran 138 tests — **OK** |
| 需求验收清单 | `python tools/acceptance.py` | 13 项全部 `[OK ]`，结论：全部通过 |
| 音频后端 | `python tools/check_audio.py` | 9 项全部 `[OK ]`（pygame.mixer 真实播放，延迟补偿正确） |
| 示例谱面生成 | `python run_editor.py --make-demo` | 生成 `charts/demo/demo.ivchart`（6K 32 / 区域 3 / 滑键 6 / 4 个速度组） |
| 界面截图 | `python tools/screenshot_editor.py docs/images` | 5 张窗口截图（分栏 / 放置 / 叠加演示 / 最大化 / 全屏） |
| 依赖安装 | `python -m pip install -r requirements.txt` | 已安装 `pygame-ce 2.5.8` |
| 菜单频闪 | `python tools/check_menu_flicker.py` | 4 项全部 `[OK ]`（展开期间帧数冻结、菜单项零改写、关闭后恢复） |

截图与测试均在真实 Tk 画布上执行，因此“渲染 → 交互 → 数据”整条链路都有证据。

### 7.0 渲染性能基准（`tools/bench_render.py`）

移除底部总览条前后的实测对比（同一台机器，静态帧、无输入）：

| 物件数 | 修复前 整帧渲染 | 修复后 整帧渲染 | 画布图元（前 → 后） |
| --- | --- | --- | --- |
| 1000 | 92 ms | 3.3 ms | 1152 → 147 |
| 3000 | 738 ms | 8.7 ms | 3152 → 149 |
| 6000 | 4146 ms | 27 ms 级 | 6152 → 149 |

根因：总览条每帧给**每个物件**画一根刻度，且每根刻度都重新调用一次
`chart.beat_bounds()`（本身 O(n)），整帧因此是 O(n²)。现在时间导航由右侧 ttk 竖向
滚动条负责，画布只保留可见物件。

修复后各环节的实测开销（20000 物件）：排序 2.3 ms、整帧渲染 53 ms、命中测试 149 ms、
`beat_bounds` 2.5 ms、撤销快照 65 ms —— 后续优化方向见 `docs/ROADMAP.md`。

### 7.1 关于“流速可正可负”的专项验证

速度组允许 `v` 取正数、0、负数并在各段叠加，因此物件位置**不是**判定时刻的单调函数：
远处的物件可能正好被推回判定线上。相应的回归测试：

| 测试 | 验证点 |
| --- | --- |
| `tests/test_speed.py::test_superposed_speeds_return_object_to_judgment_line` | `∫v = 0` 时判定时刻相差 20 拍的物件正好在判定线上；各段积分确定性校核 |
| `tests/test_speed.py::test_position_is_not_monotonic_in_beat` | 相差 200 拍的判定时刻同样可以回到判定线 |
| `tests/test_editor_smoke.py::test_far_note_pushed_back_to_judgment_line_is_drawn_and_hittable` | 该物件被**正常渲染**（下边缘压在判定线上）且**可点选** |
| `tests/test_editor_smoke.py::test_note_flung_far_below_line_is_culled_by_pixels_only` | 被甩出画面的物件按**像素位置**剔除（而不是拍差阈值） |

实现约束：`ui/renderer.py::_draw_notes` 先算位置再决定是否绘制；
`ui/geometry.py` 与渲染共用同一套几何（含速度段边界折点采样 `sample_beats`）。

### 7.2 界面重构（分栏 / 叠加 / 全屏 / 工具栏）的验证

| 测试 | 验证点 |
| --- | --- |
| `tests/test_editor_smoke.py::test_split_layout_geometry` | 编辑器分栏：两个区域不重叠、共用判定线 y、区域外点不到 6K 轨道 |
| `tests/test_editor_smoke.py::test_overlay_layout_puts_mouse_area_on_middle_lanes` | 叠加布局：鼠标区域正好等于中间四条轨道 |
| `tests/test_editor_smoke.py::test_layer_order_survives_layout_switch` | 切换布局后区域底色不会盖住物件（显式图层） |
| `tests/test_app_flow.py::test_split_layout_and_demo_overlay` | 应用级：默认分栏，`Tab` 进入演示后叠加，退出后恢复分栏 |
| `tests/test_app_flow.py::test_zoom_plus_button_is_always_available` | 缩放 `+` 按钮固定在右侧优先分配空间，不会被挤掉 |
| `tests/test_app_flow.py::test_toolbar_groups_without_menubar` | 顶栏只有分组入口 + 播放，且不再有系统菜单栏 / 演示按钮 / 缩放文字 |
| `tests/test_app_flow.py::test_panel_groups_split` | 「面板」拆成谱面设置（元数据、BPM 与偏移）+ 速度组 + 编辑器设置 |
| `tests/test_app_flow.py::test_help_menu_has_guide_and_shortcuts_only` | 帮助菜单只有操作指南与快捷键（无“关于”） |
| `tests/test_app_flow.py::test_zoom_buttons_direction` | `+` 放大、`−` 缩小，与滚轮手感一致 |
| `tests/test_editor_smoke.py::test_wheel_scrolls_exactly_one_grid_step` | 滚轮一格 = 一个网格格（细分改变后随之改变），且始终吸附网格 |
| `tests/test_editor_smoke.py::test_grid_labels_show_bar_and_beat_numbers` | 小节线标注带绝对拍号、拍线旁有拍号（方便填数据） |
| `tests/test_editor_smoke.py::test_region_draws_only_left_and_right_edges` | 蓝色停留区域只画左右边界线（无上下封口） |
| `tests/test_editor_smoke.py::test_region_pending_preview_has_only_side_edges` | 放置预览同样只有左右边界线 |
| `tests/test_editor_smoke.py::test_place_hold_sets_end_lane_and_time` | 长按（含多轨）：第二次点击的横坐标定结束轨道、纵坐标定结束时间；结束早于起点自动补一格 |
| `tests/test_editor_smoke.py::test_region_time_selection_preview_follows_cursor` | 回归：空域选结束时间时预览跟随光标（曾经高度为 0，看起来像没有预览） |
| `tests/test_app_flow.py::test_toolbar_groups_without_menubar` | 分组顺序为 文件/编辑/谱面设置/放置/视图/速度组/编辑器设置/帮助 |
| `tests/test_editor_smoke.py::test_place_region_uses_dialog_result` | 空域新流程：2 次定时间 + 4 次定左右边界首尾，弹窗收到四个位置并选择线型 |
| `tests/test_editor_smoke.py::test_region_preview_defaults_for_unplaced_points` | 未放置的边界点按左 0 / 右 1 预览，等待中的点跟随光标 |
| `tests/test_editor_smoke.py::test_hold_preview_extends_upward_from_start_line` | 回归：长按起始位置的预览画在时间网格线上方 |
| `tests/test_editor_smoke.py::test_slide_direction_follows_click_order_when_snapped_to_same_line` | 回归：两次点击吸附到同一网格线时，方向仍按点击的左右关系判断 |
| `tests/test_app_flow.py::test_edit_note_button_is_in_toolbar_not_menu` | 「编辑物件」在顶栏「编辑」右侧、不在二级菜单里，且仅单选时可用 |
| `tests/test_app_flow.py::test_toolbar_scrolls_horizontally_when_narrow` | 窄窗口自动出现横向滚动条，宽窗口自动隐藏 |
| `tests/test_app_flow.py::test_menu_protection_has_no_hold_time` | 菜单保护没有等待时间，交互后立即恢复重绘 |
| `tests/test_editor_smoke.py::test_no_ruler_items_on_canvas` | 总览条已移除（画布上不再有 ruler:* 图元） |
| `tests/test_editor_smoke.py::test_scrollbar_fractions_track_view` | 竖向滚动条滑块比例跟随可视窗口，拖动可跳转时间 |
| `tests/test_app_flow.py::test_vertical_scrollbar_drives_timeline` | 应用级：拖动 / 翻页 / 步进滚动条都会移动判定线且吸附网格 |
| `tests/test_validate.py`（14 条） | 自动检查：区间冲突规则、同轨道重叠、长按/多轨相交、端点紧贴放行、滑键与空域相交/紧贴/分离、报告文本 |
| `tests/test_editor_smoke.py::test_slide_arrow_points_to_swipe_direction` | 回归：向左滑键箭头必须指向左 |
| `tests/test_editor_smoke.py::test_left_slide_uses_yellow_color` | 向左滑键为半透明黄色，向右保持绿色 |
| `tests/test_app_flow.py::test_auto_check_is_in_edit_menu_and_selects_issues` | 自动检查在「编辑」菜单里，运行后选中问题物件并弹出报告 |
| `tests/test_demo_selftest.py::test_demo_chart_passes_auto_check` | 仓库自带示例谱面通过自动检查 |
| `tests/test_app_flow.py::test_fullscreen_toggle` | 全屏开关切换 |
| `tests/test_app_flow.py::test_maximized_by_default` | 默认最大化（`-fullscreen` 属性为 0），不是无边框全屏 |
| `tests/test_app_flow.py::test_toggle_maximize_and_fullscreen` | 最大化 / 还原 / 全屏互斥切换 |
| `tests/test_app_flow.py::test_menu_not_reconfigured_every_frame` | 菜单项只在状态变化时写入（频闪根因之一） |
| `tests/test_app_flow.py::test_tick_skips_redraw_while_menu_is_posted` | 菜单展开期间不重绘画布（频闪根因之二） |

人工确认：`docs/images/editor_overview.png`（分栏 + 分组工具栏）、
`docs/images/maximized_editor.png`（默认启动状态）、`docs/images/fullscreen_editor.png`（全屏）、
`docs/images/demo_mode.png`（叠加演示）。

### 7.3 二级菜单频闪的定位与修复

诊断脚本先确认了平台行为：Tk 在 Windows 上使用**原生弹出菜单** ——
`menu.post()` 会进入嵌套消息循环，`winfo_ismapped()` 恒为 0，`<Map>/<Unmap>` 不触发，
`grab_current()` 也为空。因此“菜单是否展开”无法用窗口映射状态判断，频闪只能来自
**每帧触碰菜单/窗口**：

1. `Toolbar.sync()` 过去每帧对所有菜单项调用 `entryconfigure(state=...)` 并 `var.set(...)`；
   原生菜单因此被反复重建 → 频闪。现在只在值真的变化时写入（回归测试
   `test_menu_not_reconfigured_every_frame`）。
2. `_tick()` 过去每帧 `root.title(...)` 并重绘画布；Windows 上重设标题会重画非客户区，
   正好打断展开中的菜单。现在标题/状态栏文本只在变化时写入，
   并在菜单展开期间暂停画布重绘（`Toolbar.is_menu_posted()`，超时兜底 + 用户操作自动恢复；
   回归测试 `test_tick_skips_redraw_while_menu_is_posted` 与
   `tools/check_menu_flicker.py`）。
