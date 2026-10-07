# 验证清单

本文档说明如何验证当前实现，并记录本机的实测结果。

## 1. 环境准备

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

编辑器界面使用标准库 `tkinter`，没有第三方包也能完整编辑；音频在缺少 pygame 时
自动使用静音时钟后端，状态栏会明确提示。

## 2. 一键自检（不启动界面）

```bash
python run_editor.py --make-demo        # 生成 charts/demo/demo.json
python tools/acceptance.py              # 需求 → 证据清单
```

`acceptance.py` 的输出（本机实测，节选）：

```
[OK ] 项目结构（src/ docs/ tests/ tools/ charts/）
[OK ] 6K 物件：单轨 / 多轨 / 长按 -- 32 个
[OK ] 空域（半透明蓝 + 不透明左右边界 + 缓动） -- 3 个
[OK ] 鼠标滑键（方向由首尾位置决定） -- 6 个
[OK ] 禁区（与空域同一套几何与放置 / 编辑逻辑，绘制为红色） -- 示例谱面 3 个空域、0 个禁区
[OK ] BPM 列表（第 0 拍项存在，含每小节拍数）
[OK ] 速度组（默认组 + 特效组，含 0/负数流速）
[OK ] 全局设置（缩放 / 网格 / 横向辅助 / 音频延迟；不写进谱面）
[OK ] 谱面可保存并重新读取（字段完全一致）
[OK ] 撤销 / 重做
[OK ] 复制 / 粘贴：整组时间平移，横向位置与相对间隔不变
[OK ] 沿游玩区中轴线镜像
[OK ] 内置自检（时间轴 / 速度组 / 编辑命令 / 存档往返）
结论：全部通过
```

## 3. 单元测试

```bash
python -m unittest discover -s tests -t .
```

本机（Windows + Python 3.14.7 + Tk 9.0 + pygame-ce 2.5.8）：
**105 项全部通过**（约 3 秒），其中包含真实 Tk 画布与 headless pygame 的界面测试。

| 测试文件 | 覆盖内容 |
| --- | --- |
| `test_model_timing.py` | BPM 列表、偏移、拍 ↔ 秒换算、网格吸附、小节线、速度组积分与非单调位置 |
| `test_model_notes.py` | 单轨 / 多轨 / 长按、空域 / 禁区 / 滑键、镜像与序列化、自动检查规则 |
| `test_model_chart.py` | 谱面分组与 ID、撤销 / 重做、复制粘贴、排序缓存、谱面 JSON 与音频复制 |
| `test_settings.py` | 编辑器全局设置读写、旧存档迁移、损坏回退、演示视图参数夹取 |
| `test_demo.py` | 示例谱面结构、内置自检、自动检查 |
| `test_audio.py` | 倍速细档与重采样、延迟补偿、分窗口调度、音效时刻表与混音 |
| `test_editor_render.py` | 真实 Tk 画布：分栏、图层、裁剪、放置工具、滚动 / 缩放、命中缓存 |
| `test_editor_app.py` | 编辑器应用层：菜单、播放 / 暂停、演示模式、设置与保存、启动脚本 |
| `test_dialogs.py` | BPM 第 0 拍项、默认速度组、物件编辑与缓动写入、演示视图设置、弹窗只有确认按钮 |
| `test_preview_render.py` | 演示视图：3D 布局与取景、投影下界、贴图规格化、离屏软渲染、速度组与剔除 |

## 4. 人工验收（需要图形界面）

```bash
python run_editor.py charts/demo/demo.json
```

1. 滚轮滚动 —— 两个区域同步移动，判定线始终落在网格线上。
2. `Ctrl+滚轮` —— 缩放改变可视秒数；反向滚动恢复。
3. 「视图 → 横向辅助网格」设为 8 → 鼠标区域出现 8 等分竖直虚线；
   用「左右滑键」点击时横坐标吸附到网格线。
4. 「视图 → 时间网格细分」选择 3 → 出现三连音网格。
5. 「长按」：点起点后滚动、缩放，再点结束时刻（可点在鼠标区域上）→ 生成长按。
6. 「空域」：两次点击定时间，再依次点击左右边界首尾，在对话框里选择线型；
   换「禁区」工具重复一次 → 同样的流程，生成红色半透明区域；与空域重叠时显示两层合成色。
7. 「选择」框选若干物件 → `Ctrl+C`、`Ctrl+V` 后点击新时间 → 轨道与横向位置不变。
8. `M` 镜像 → 6K 物件跨中轴线翻转，滑键方向反转。
9. 「速度组」新建组 → 添加 `从 x 拍起，倍率 -1` → 组内物件反向运动；删除组后回到默认组。
10. 空格播放 → 画面与音乐同步；再按空格暂停 → 判定线吸附到网格。
11. `Tab` 进入演示模式 → 只能滚动 / 缩放 / 播放，不能编辑；`Tab` 返回。
    「编辑器设置 → 演示视图设置…」改厚度 / 流速 / 侧轨高亮 → 演示画面立刻变化，
    且不出现鼠标指针；弹窗底部只有确认按钮（`Esc` 关闭）。

## 5. 截图

```bash
python tools/screenshot_editor.py docs/images    # 编辑器 5 张
```

脚本用 GDI `PrintWindow` 抓取窗口客户区，需要可用的桌面会话。

## 6. 音频后端校验

```bash
python tools/check_audio.py
```

本机输出：9 项全部 `[OK ]` —— 真实播放、位置随时间前进、
`耳朵听到的位置 = 后端位置 − 系统延迟`、暂停、seek、续播与停止归零。

## 7. 实测结果

### 7.1 编辑器渲染性能（`tools/bench_render.py`）

静态帧、无输入，本机实测：

| 物件数 | 画布图元 | 排序（冷） | 整帧渲染 | 命中（兜底） | 命中（缓存） | beat_bounds | 编辑 + 撤销 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 249 | 149 | 0.04 ms | 0.95 ms | 0.48 ms | 0.010 ms | 0.04 ms | 0.053 ms |
| 1000 | 149 | 0.14 ms | 1.06 ms | 0.52 ms | 0.010 ms | 0.09 ms | 0.115 ms |
| 4000 | 149 | 0.57 ms | 1.07 ms | 0.49 ms | 0.011 ms | 0.34 ms | 0.446 ms |
| 10000 | 149 | 1.49 ms | 1.20 ms | 0.48 ms | 0.010 ms | 0.75 ms | 1.022 ms |

* 画布图元数只与屏幕内的物件有关（差分场景 + 显式图层）；
* 每帧先按**可视拍窗口**二分出候选物件，只对候选算几何；因此整帧渲染 / 命中
  基本不随谱面规模增长（默认流速下 O(可见 + log N)）；
* 排序走缓存（编辑后的第一帧重建），`beat_bounds` / `note_counts` / 物件 id 与
  速度组索引也都带缓存或 O(1)；空闲帧按画面指纹直接跳过重绘；
* 读档 20000 物件 1.19 s → **0.05 s**、粘贴 200 物件（12500 物件谱面）
  146 ms → **2.5 ms**；10000 物件 + 500 个 BPM 段的空闲帧 762 ms → **1.5 ms**。

### 7.2 关键不变量

| 不变量 | 验证 |
| --- | --- |
| 流速可正可负，物件位置不是判定时刻的单调函数 | `test_model_timing.py::SpeedGroupTests::test_position_is_not_monotonic_in_beat`、`test_editor_render.py::EditorRenderTests::test_far_note_pushed_back_to_judgment_line_is_drawn_and_hittable` |
| 可见性剔除只按算出的像素位置，不用拍差 | `test_editor_render.py::EditorRenderTests::test_far_note_pushed_back_to_judgment_line_is_drawn_and_hittable` |
| 命中测试与渲染共用几何 | `test_editor_render.py::HitCacheTests`、`ui/geometry.py::HitCache` |
| 演示视图与编辑器共用世界几何与图层顺序 | `preview/frame.py`、`model/notes.py::layer_rank()`；`test_preview_render.py::SoftwareViewTests::test_frame_matches_draw_sequence` |
| 音效时刻表按歌曲毫秒预计算、增量维护（编辑只重算打击音、音量 / 倍速不重算） | `test_audio.py::EffectScheduleTests` |
| 起播裁剪用新的起播位置、播放中新增音效立刻进缓冲（打击音不漏） | `test_audio.py::EffectMixTests::test_change_ahead_of_playhead_rebuilds_current_window` |
| 播放中改音量 / 音效增益立刻生效（重合成当前窗口，音乐不跳时间） | `test_audio.py::EffectMixTests::test_change_ahead_of_playhead_rebuilds_current_window` |
| 音效混进歌曲输出（不参与重采样、窗口边界不截断） | `test_audio.py::EffectMixTests::test_effect_mixed_at_scaled_position_without_stretching` |
| 判定线跟随听到的位置（延迟补偿按倍速缩放） | `test_audio.py::RateTests::test_latency_is_signed_and_scales_with_rate`、`test_editor_app.py::EditorAppTests::test_play_pause_snaps_judgment_line` |
| 演示视图跟随编辑器独立的演示视图设置（固定软件渲染） | `test_editor_app.py::EditorAppTests::test_demo_view_settings_are_independent_and_applied` |
| 弹出窗口只有一个确认按钮（没有「取消」） | `test_dialogs.py::DialogButtonTests` |
| 鼠标图案的贴图中心对齐鼠标运动平面（滑键贴图双倍高、下半透明，可见楔形仍在平面上方） | `test_preview_render.py::TextureTests::test_real_assets_have_expected_geometry`、`test_preview_render.py::SoftwareViewTests` |
| 空域 / 禁区：同一套几何与放置流程，禁区画成红色，两层半透明填充重叠处按合成色显示 | `test_model_notes.py::MouseNoteTests::test_forbidden_region_geometry_and_clone`、`test_editor_render.py::EditorRenderTests::test_forbidden_region_uses_red_fill`、`test_preview_render.py::SoftwareViewTests::test_forbidden_zone_is_red_and_blends_with_region` |
| 速度组在演示视图里生效（位置按 ∫v、剔除与排序也按滚动距离） | `test_preview_render.py::SoftwareViewTests::test_demo_view_applies_speed_groups`、`test_model_timing.py::SpeedGroupTests` |
| 反向流速的物件在判定线之前（更靠近摄像头）照常绘制，超出相机可视下界才剔除 | `test_preview_render.py::ProjectionTests::test_projection_and_visible_bounds`、`test_preview_render.py::SoftwareViewTests::test_reverse_notes_draw_in_front_of_the_judgement_line` |
| 宽窗口下游玩区水平拉伸铺满可用宽度（6K 平面接近窗口左右边缘） | `test_preview_render.py::ProjectionTests::test_field_fills_window_and_stretches_on_wide_screens` |

设计细节见 [`DESIGN.md`](DESIGN.md)；逐条规格见 [`SPEC.md`](SPEC.md)。
