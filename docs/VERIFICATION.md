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
[OK ] 鼠标点击按键（左右红色竖线 + 中间红色横线 + 红色实心菱形，最上层） -- 4 个
[OK ] BPM 列表（第 0 拍项存在，含每小节拍数）
[OK ] 速度组（默认组 + 特效组，含 0/负数流速）
[OK ] 全局设置（缩放 / 网格 / 横向辅助 / 音频延迟；不写进谱面）
[OK ] 谱面可保存并重新读取（字段完全一致）
[OK ] 撤销 / 重做
[OK ] 复制 / 粘贴：整组时间平移，横向位置与相对间隔不变
[OK ] 沿游玩区中轴线镜像
[OK ] 内置自检（时间轴 / 速度组 / 编辑命令 / 存档往返）
[OK ] 游玩器（主界面 / 设置 / 加载 / 3D 游玩 / 结算）
[OK ] 游玩器判定窗口（±25/50/100ms，提前 100~150ms 为 Break、更早空敲）
[OK ] 游玩器 3D 布局（鼠标合法区 = 6K 中间四轨 = 1 单位）
[OK ] 游玩器设置（键位 / 厚度 / 延迟 / 流速 / 灵敏度 / 无界鼠标 / 玩家名 / 倍速）
结论：全部通过
```

## 3. 单元测试

```bash
python -m unittest discover -s tests -t .
```

本机（Windows + Python 3.14.7 + Tk 9.0 + pygame-ce 2.5.8 + Panda3D 1.10）：
**355 项全部通过**（约 16 秒），其中包含真实 Tk 画布与 headless pygame 的界面测试。

| 测试文件 | 覆盖内容 |
| --- | --- |
| `test_timing.py` | BPM 列表、偏移、拍 ↔ 秒换算、网格吸附、小节线 |
| `test_speed.py` | 速度组积分、0 / 负流速、非单调位置 |
| `test_notes.py` | 单轨 / 多轨 / 长按、空域边界与缓动、滑键方向、镜像、序列化 |
| `test_mouse_click.py` | 鼠标点击按键的模型、创建流程、吸附规则、渲染与命中 |
| `test_validate.py` | 自动检查：区间冲突、同轨重叠、长按 / 多轨相交、空域相交 |
| `test_chart_document.py` | 物件增删、撤销 / 重做、复制粘贴、镜像、速度组归属、ID 唯一性 |
| `test_undo_records.py` | 差量撤销：各类命令的撤销 / 重做等价性、记录量、栈上限 |
| `test_sorted_cache.py` | 排序缓存的失效规则 |
| `test_perf_hit.py` | 命中缓存：只含可见物件、视图变化回退全量、框选复用 |
| `test_chart_io.py` | 谱面 JSON 往返、音频复制与查找 |
| `test_global_settings.py` | 全局设置读写、损坏回退、旧 `editor` 段迁移 |
| `test_demo_selftest.py` | 示例谱面结构、内置自检、自动检查 |
| `test_dialogs.py` | BPM 第 0 拍项、默认速度组、物件编辑与缓动写入 |
| `test_editor_smoke.py` | 真实 Tk 画布：全部放置工具、图层、空域、滚动 / 缩放、拖拽 |
| `test_app_flow.py` | 应用级：菜单、播放 / 暂停、演示模式、设置与保存、窗口模式 |
| `test_audio_rate.py` | 倍速档位（含 0.01 ~ 0.1 细档）与重采样、延迟补偿、分窗口调度、连续播放 |
| `test_metronome.py` | 节拍音与打击音效、音量、慢放不改变音效长度 |
| `test_launcher.py` | 启动脚本、启动日志、音频后端诊断 |
| `test_game_judge.py` | 游玩器判定窗口、长按 / 空域区间、滑键、准度与分数 |
| `test_game_scene.py` | 游玩器 3D 布局、投影、取景 |
| `test_game_textures.py` | 玩家端贴图规格化与占位 |
| `test_game_render3d.py` | Panda3D 卡片变换与贴图夹边 |
| `test_game_settings.py` | 游玩器设置读写、迁移与夹取 |
| `test_game_app.py` | 游玩器 GUI：主界面 → 加载 → 游玩 → 结算、输入、渲染回退 |

## 4. 人工验收（需要图形界面）

```bash
python run_editor.py charts/demo/demo.json
```

1. 滚轮滚动 —— 两个区域同步移动，判定线始终落在网格线上。
2. `Ctrl+滚轮` —— 缩放改变可视秒数；反向滚动恢复。
3. 「视图 → 横向辅助网格」设为 8 → 鼠标区域出现 8 等分竖直虚线；
   用「左右滑键」点击时横坐标吸附到网格线。
4. 「视图 → 时间网格细分」改为 3 → 出现三连音网格。
5. 「长按」：点起点后滚动、缩放，再点结束时刻（可点在鼠标区域上）→ 生成长按。
6. 「空域」：两次点击定时间，再依次点击左右边界首尾，在对话框里选择线型。
7. 「选择」框选若干物件 → `Ctrl+C`、`Ctrl+V` 后点击新时间 → 轨道与横向位置不变。
8. `M` 镜像 → 6K 物件跨中轴线翻转，滑键方向反转。
9. 「速度组」新建组 → 添加 `从 x 拍起，倍率 -1` → 组内物件反向运动；删除组后回到默认组。
10. 空格播放 → 画面与音乐同步；再按空格暂停 → 判定线吸附到网格。
11. `Tab` 进入演示模式 → 只能滚动 / 缩放 / 播放，不能编辑；`Tab` 返回。
12. `python run_player.py charts/demo/demo.json` → 主界面 → 加载 → 游玩 → 结算；
    `F2` 切换渲染后端，`F11` 切换窗口 / 全屏。

## 5. 截图

```bash
python tools/screenshot_editor.py docs/images    # 编辑器 5 张
python tools/screenshot_player.py docs/images    # 游玩器 4 张
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
| 249 | 149 | 0.03 ms | 1.27 ms | 2.44 ms | 0.015 ms | 0.04 ms | 0.071 ms |
| 1000 | 149 | 0.10 ms | 2.28 ms | 9.74 ms | 0.010 ms | 0.09 ms | 0.136 ms |
| 4000 | 149 | 0.43 ms | 5.71 ms | 38.49 ms | 0.010 ms | 0.32 ms | 0.268 ms |

* 画布图元数只与屏幕内的物件有关（差分场景 + 显式图层）；
* 排序走缓存，编辑后的第一帧重建；
* 命中缓存命中时不必遍历整张谱面，未命中（视图 / 谱面已变）时回退全量扫描。

### 7.2 关键不变量

| 不变量 | 验证 |
| --- | --- |
| 流速可正可负，物件位置不是判定时刻的单调函数 | `test_speed.py::test_position_is_not_monotonic_in_beat`、`test_editor_smoke.py::test_far_note_pushed_back_to_judgment_line_is_drawn_and_hittable` |
| 可见性剔除只按算出的像素位置，不用拍差 | `test_editor_smoke.py::test_note_flung_far_below_line_is_culled_by_pixels_only` |
| 命中测试与渲染共用几何 | `test_perf_hit.py`、`ui/geometry.py::HitCache` |
| 游玩器两条渲染路径共用世界几何 | `game/frame.py`；`test_game_app.py` 的两条路径像素断言 |
| 编辑器与游玩器共用图层顺序 | `model/notes.py::layer_rank()`；`test_editor_smoke.py::test_hit_priority_follows_layer_order` |
| 音效与音乐同步、判定线跟随听到的位置 | `test_app_flow.py::test_sound_effects_follow_the_music_clock_not_the_ui` |
| 判定窗口不随倍速改变 | `test_game_judge.py::test_windows_are_real_time_not_song_time` |

### 7.3 游玩器实测

| 项目 | 结果 |
| --- | --- |
| 三种后端 | Panda3D（默认）与 OpenGL 呈现路径在真实窗口可用；两者不可用时自动回退软件渲染 |
| 窗口 | 启动时不超过桌面并居中；`F10` 最大化 / 还原、`F11` 窗口 / 无边框 / 独占全屏循环 |
| 帧率 | HUD 左下角显示实时帧率与上限；上限按窗口所在屏幕的刷新率自适应 |
| 长谱面 | 判定时刻已过与可视范围之外的物件都不绘制；低倍速不堆积、高倍速从轨道尽头出现 |

更完整的玩法与实现说明见 [`GAMEPLAY.md`](GAMEPLAY.md)、[`PLAYER.md`](PLAYER.md)；
设计细节见 [`DESIGN.md`](DESIGN.md)；逐条规格见 [`SPEC.md`](SPEC.md)。
