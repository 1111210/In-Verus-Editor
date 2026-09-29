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
python run_editor.py --make-demo        # 生成 charts/demo/demo.json（普通 JSON）
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
python run_editor.py charts/demo/demo.json
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
| 单元测试（含 Tk 画布冒烟测试、应用级测试与游玩器测试） | `python -m unittest discover -s tests -t .` | Ran 307 tests — **OK** |
| 需求验收清单 | `python tools/acceptance.py` | 18 项全部 `[OK ]`，结论：全部通过（含游玩器 4 项） |
| 音频后端 | `python tools/check_audio.py` | 9 项全部 `[OK ]`（pygame.mixer 真实播放，延迟补偿正确） |
| 示例谱面生成 | `python run_editor.py --make-demo` | 生成 `charts/demo/demo.json`（6K 32 / 区域 3 / 滑键 6 / 点击按键 4 / 4 个速度组） |
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
| `tests/test_editor_smoke.py::test_divider_drag_resizes_both_panes` | 拖动分界线实时改变两栏宽度、总宽守恒、极端拖动被最小宽度夹住、画布分界线跟着走 |
| `tests/test_editor_smoke.py::test_divider_drag_does_not_place_notes` | 抓分界线拖动不会误放物件；叠加（演示）模式下没有可拖的分界线 |
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
| `tests/test_app_flow.py::test_menu_toggles_closed_on_second_click` | 回归：菜单按钮再次点击是收起，切换分组时直接替换 |
| `tests/test_app_flow.py::test_outside_click_and_escape_close_menu` | 点画布 / `Esc` 收起菜单，点自身按钮保持不变 |
| `tests/test_editor_smoke.py::test_slide_box_uses_actual_length_but_arrow_keeps_min_length` | 方框按实际长度、箭头仍有最小长度 |
| `tests/test_editor_smoke.py::test_slide_pending_start_is_single_yellow_line` | 选起点预览：只有一根黄色虚线 |
| `tests/test_editor_smoke.py::test_slide_pending_end_colors_follow_direction` | 选终点预览：向左黄色、向右绿色 |
| `tests/test_app_flow.py::test_blocking_popup_resets_state_after_close` | 阻塞式原生菜单：弹出返回后状态复位 |
| `tests/test_app_flow.py::test_grid_settings_live_in_view_menu_only` | 网格设置只在视图菜单（含自定义项），设置面板已无网格字段 |
| `tests/test_app_flow.py::test_horizontal_grid_has_no_separate_switch` | 横向辅助网格无独立开关，设置了等分就必然显示 |
| `tests/test_app_flow.py::test_toolbar_buttons_share_same_height_style` | 直执按钮与菜单按钮样式一致（高度相同） |
| `tests/test_app_flow.py::test_toolbar_groups_without_menubar` | 顶栏顺序：文件/编辑（合并）→ 谱面设置 → 放置物件 → 编辑物件 → 视图 → 速度组 → 音效 → 编辑器设置 |
| `tests/test_app_flow.py::test_panel_groups_split` | 编辑器设置菜单含「编辑器设置…/操作指南…/快捷键…」；播放按钮固定在右端 |
| `tests/test_app_flow.py::test_sound_effects_follow_the_music_clock_not_the_ui` | 延迟 ≠ 0 时：音效按软件位置触发（与音乐同步），判定线仍按听到的位置显示 |
| `tests/test_audio_rate.py::test_latency_scales_with_playback_rate` | 延迟按真实时间缩放：1.0x / 0.5x / 0.1x 下 200ms 分别只减 200 / 100 / 20ms |
| `tests/test_editor_smoke.py::test_overlay_demo_is_one_third_and_centered` | 演示模式占窗口宽度 1/3 且居中；轨道、网格、判定线都在窄带内 |
| `tests/test_editor_smoke.py::test_drag_selected_note_moves_time_and_lane` | 编辑器拖拽移动物件：时间 + 轨道，撤销 / 重做都能还原 |
| `tests/test_editor_smoke.py::test_drag_snaps_mouse_anchor_to_grid` | 拖动鼠标物件按**自身锚点**吸附：滑键起点 / 点击中心 / 空域起点中点，长度宽度不变 |
| `tests/test_global_settings.py`（4 条） | **全局设置**：读写往返、损坏文件回退默认值、谱面文件不再包含 `editor` 段、老谱面的 `editor` 段只迁移一次 |
| `tests/test_game_judge.py`（26 条） | 游玩器判定：EXACT/Exact/Near/Break 窗口、空敲无惩罚、一次按键只判一个、**输入类型必须匹配（6K 键只判 6K、鼠标点击只判鼠标点击按键）**、滑键吸附最好等级、空域半拍区间、长按“抬起事件 + 末 50ms 宽限（跨区间）”、多轨长按“所有行同时抬起才算抬起”、准度与分数公式、分数 × 倍速^1.5、连击与最大连击 |
| `tests/test_game_scene.py`（8 条） | 游玩器 3D 布局：1 单位 = 鼠标合法区宽 = 6K 中间四轨；鼠标平面高 0.4；两条判定线位置；近大远小；起点在屏幕上方 |
| `tests/test_game_settings.py`（8 条） | 游玩器设置：默认键位、范围夹取、键位补齐、全局读写与损坏回退、最近打开列表去重与上限、渲染后端设置（含非法值回退） |
| `tests/test_game_app.py`（18 条） | 游玩器 GUI 冒烟（headless）：主界面 → 加载 → 游玩 → 结算、鼠标映射、设置全局保存、轨道高亮与判定飘字、音量与暂停、谱面列表元数据与最近打开、渲染后端切换与回退、倍速、charts/ “每个含 json 的文件夹算一张谱面”、没有谱面时也能进设置、打开其他路径的 JSON、判定时刻过去的物件不再绘制、首拍 1.5 秒引导且音乐同步、文本输入走 TEXTINPUT / 键位按键捕获、鼠标可操作主界面 / 设置 / 结算、轨道键优先于界面快捷键（空格 = 第 6 轨）、**GL 呈现异常时自动回退软件渲染（坏配置不写回）**、**`--software` 命令行强制后端** |

窗口 / 后端实测（真实窗口，`python` 直接跑）：

| 项目 | 命令 | 结果 |
| --- | --- | --- |
| 启动首帧（不最大化） | 脚本化启动后立刻截图 | 菜单正常显示，`1280x744`（桌面 1536×864 内居中） |
| 强制 OpenGL + 自检 | `PlayerApp(..., renderer="opengl")` | 自检通过，`renderer_name == "opengl"`，3.0ms/帧、88fps，无回退 |
| 游玩中持续刷新 + 音乐 | 真机跑 9 秒并抓 3 帧 | 3 帧互不相同（刷新正常），`now_ms` 从 −473 → 8360 跟随音频，物件按判定时刻逐个结算 |
| `tests/test_editor_smoke.py::test_scrollbar_drag_round_trip_keeps_thumb_under_cursor` | 回归：滚动条滑块拖动不跳段（把滑块放到 f 后读回仍是 `(f, f+窗口占比)`） |
| `tests/test_audio_rate.py`（16 条） | 倍速（重采样，**变速变调**）：长度随倍率伸缩、0.5x 时频率减半（过零率）、静音时钟跟随倍速、倍速夹取、**负延迟**的 heard 位置、延迟范围、**立体声也能慢放**、**分窗口首尾相接**、**调度连续不断音**（回归：pygame 只播一个排队块）、**打开谱面后第一次播放用真实后端**（回归）、**播放中改倍速自动暂停**并保持位置 |
| `tests/test_app_flow.py::test_rate_buttons_step_and_clamp` | 倍速按钮 ±0.1x 与 0.1~1.0x 夹取，标签同步 |
| `tests/test_app_flow.py::test_rate_buttons_are_always_available` | 右侧倍速按钮固定在右侧优先分配空间 |
| `tests/test_app_flow.py::test_scrollbars_use_white_thumb_black_trough` | 滚动条白滑块 / 黑背景 |
| `tests/test_editor_smoke.py::test_offscreen_regions_skip_boundary_computation` | 回归：屏幕外空域不计算边界形状（30 个远处空域 0 次采样） |
| `tests/test_editor_smoke.py::test_region_samples_adapt_to_screen_height` | 空域采样点数随屏幕高度自适应 |
| `tests/test_perf_hit.py`（6 条） | 命中缓存：只含可见物件、命中测试不再触碰整张谱面、视图变化回退全量、框选复用缓存、签名随视图/谱面变化 |
| `tests/test_sorted_cache.py`（7 条） | 排序缓存：复用同一列表、增删 / 编辑自动失效、显式失效、分类列表、快照后缓存重建 |
| `tests/test_editor_smoke.py::test_note_layer_order_matches_readability_spec` | 七层绘制顺序（画布图元次序）符合规格 |
| `tests/test_editor_smoke.py::test_hit_priority_follows_layer_order` | 命中优先级与图层一致（点击按键 > 滑键 > 空域 > 单轨单键 > …） |
| `tests/test_undo_records.py`（10 条） | 差量撤销：增删/粘贴/镜像/编辑/改组/改元数据与 BPM 的撤销重做等价性、记录量只随改动量增长、栈上限、选择还原 |
| `tests/test_editor_smoke.py::test_tap_note_style` | 单键图案：实心蓝细矩形（厚度 9px，以判定时刻居中）+ 白边 + 白横线压在节拍线上 |
| `tests/test_editor_smoke.py::test_hold_note_style_and_constant_center_line` | 长按图案：实心蓝矩形 + 白竖线（粗细与轨道数无关）+ 开头套用单键样式（细矩形 + 压线白横线） |
| `tests/test_editor_smoke.py::test_multi_holds_stack_by_lane_span` | 多轨层内排序：跨得越多越靠下层 |
| `tests/test_chart_io.py::test_suffix` | 只支持 JSON：任何后缀都会被规范成 `.json` |
| `tests/test_app_flow.py::test_fullscreen_toggle` | 全屏开关切换 |
| `tests/test_app_flow.py::test_maximized_by_default` | 默认最大化（`-fullscreen` 属性为 0），不是无边框全屏 |
| `tests/test_app_flow.py::test_toggle_maximize_and_fullscreen` | 最大化 / 还原 / 全屏互斥切换 |
| `tests/test_app_flow.py::test_menu_not_reconfigured_every_frame` | 菜单项只在状态变化时写入（频闪根因之一） |
| `tests/test_app_flow.py::test_tick_skips_redraw_while_menu_is_posted` | 菜单展开期间不重绘画布（频闪根因之二） |
| `tests/test_mouse_click.py`（18 条） | 鼠标点击按键：模型 / 序列化 / 镜像 / 复制粘贴 / 编辑撤销、与滑键同规则 + 同拍唯一、两次点击创建（中心 → 大小）、**启用横向网格才吸附并夹进 0..1、未启用时出界也照常放置**（滑键同样）、渲染为左右红色竖线 + 中间红色横线（粗细同 6K 中心实线）+ 红色实心菱形且最上层、兜底命中 / 框选路径 |
| `tests/test_app_flow.py::test_app_boots_with_chart` | 示例谱面物件总数与分类计数（含 4 个鼠标点击按键） |
| `tests/test_editor_smoke.py::test_bpm_change_shows_up_on_the_axis_immediately` | 纵向按秒线性：BPM 变更处之后的格子**立刻**变成新 BPM 的间距（240 BPM 的格子恰好是 120 BPM 的一半高） |
| `tests/test_editor_smoke.py::test_crossing_bpm_change_does_not_rescale_the_view` | 判定线跨过 BPM 变更点时画面不整屏突变（远处物件只按滚动量平移） |
| `tests/test_editor_smoke.py::test_y_to_beat_roundtrip_across_bpm_segments` | 分段映射求逆：`y_to_beat(beat_to_y(b)) == b`（跨 BPM 段、负数拍） |
| `tests/test_editor_smoke.py::test_note_on_grid_stays_on_grid_when_bpm_changes` | 默认流速的物件在 BPM 分段时仍精确压在网格线上 |
| `tests/test_speed.py`（+5 条） | `scroll_seconds()`：默认流速 = 秒数差、跟随 BPM 段、倍率缩放（0 / 负数）、流速段与 BPM 段边界取并集积分、快速路径与通用路径一致 |
| `tests/test_timing.py::test_zero_beats_per_bar_disables_bar_lines` | 每小节 0 拍：段内不再划线，但 **BPM 变更处那条小节线一定在**（小节编号继续累计） |
| `tests/test_launcher.py`（8 条） | 启动脚本**不再检测 / 切换解释器**（`venv_guard` 已移除）；**启动日志**写明实际使用的 Python 路径与后端；音频诊断：缺依赖 vs **设备打不开**分别报对原因（不再一律写“未安装 pygame”）、提示含解释器路径、`retry_backend()` 可在设备恢复后重新启用；**播放停止判据**：音乐与谱面都播完 3 秒才停、只满足一边不停、没有音乐永不停 |
| `tests/test_metronome.py`（9 条） | 节拍音：默认关闭、每整拍一响、只在小节线响（含 BPM 变更处）、跳转 / 倒放不补发、设置项读写；**慢放不改变这一声的长度**；**打击音效**：单键 / 长按开头 / 空域开头 / 滑键 / 点击按键各响一次、中段不重复、白噪声特征（相邻样本不相关）、**音量设置**读写与夹取 |
| `python run_editor.py --make-demo`（系统解释器） | 实测：自动切换到 `.venv` 后启动（stderr 打印一行说明），音频后端起效 |

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
