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
| 单元测试（含 Tk 画布冒烟测试） | `python -m unittest discover -s tests -t .` | Ran 70 tests — **OK** |
| 需求验收清单 | `python tools/acceptance.py` | 13 项全部 `[OK ]`，结论：全部通过 |
| 音频后端 | `python tools/check_audio.py` | 9 项全部 `[OK ]`（pygame.mixer 真实播放，延迟补偿正确） |
| 示例谱面生成 | `python run_editor.py --make-demo` | 生成 `charts/demo/demo.ivchart`（6K 32 / 区域 3 / 滑键 6 / 4 个速度组） |
| 界面截图 | `python tools/screenshot_editor.py docs/images` | 3 张窗口截图（PrintWindow 抓取） |

截图与测试均在真实 Tk 画布上执行，因此“渲染 → 交互 → 数据”整条链路都有证据。
