# 《In Falsus》社区谱面编辑器

游戏 **《In Falsus》** 的非官方社区谱面编辑器，用于编辑双平面谱面：左边
**6K 下落式**，右边 **鼠标平面**（空域 / 禁区 / 滑键），两边共享同一条判定线
与同一套时间网格。基于 Python + Tkinter，核心功能只依赖标准库。

## 功能

* **放置**：单轨、多轨、长按（可跨轨）、空域、禁区、左右滑键，全部点击完成，
  带幽灵预览，放置过程中可滚动、缩放。
* **编辑**：点击 / 框选 / `Ctrl` 对称差选择，拖动调整时间与位置，剪切、复制、
  粘贴、镜像、删除；单选即可编辑参数，所有改动一步撤销 / 重做。
* **时间轴**：BPM 列表与偏移、按拍吸附的网格与小节线；速度组支持加速、暂停、
  反向与往返，物件位置由判定时刻反推。
* **播放**：节拍音、打击音效与三路独立音量，0.01x ~ 1.0x 慢放（变速变调）。
* **演示模式**：`Tab` 切换到软件渲染的游玩视角，只读、可暂停。
* **自动检查**：一键找出重叠、越界、禁区覆盖等问题并选中出问题的物件。

## 快速开始

```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

python run_editor.py --make-demo                       # 生成示例谱面
python run_editor.py charts/demo/demo.json             # 打开示例谱面（默认最大化）
python run_editor.py charts/demo/demo.json --windowed  # 普通窗口
python run_editor.py charts/demo/demo.json --demo      # 直接进入演示模式
```

Windows 下也可以直接双击 `run_editor.bat`（会优先使用项目里的 `.venv`）。
没有安装 `pygame-ce` 时编辑器仍可完整编辑与播放（静音时钟），只是没有声音与
演示模式。

## 常用快捷键

| 快捷键 | 作用 |
| --- | --- |
| `1` ~ `7` | 切换工具（选择 / 单轨 / 多轨 / 长按 / 空域 / 禁区 / 滑键） |
| 空格 | 播放 / 暂停（演示模式下为暂停 / 继续） |
| `Tab` | 进入 / 退出演示模式 |
| 滚轮 / `Ctrl+滚轮` | 滚动时间轴 / 缩放 |
| `Ctrl+X` | 剪切选中物件（复制后删除，`Ctrl+Z` 一步恢复） |
| `Ctrl+C` / `Ctrl+V` | 复制 / 粘贴（粘贴时点击目标时间） |
| `M` | 沿中轴线镜像选中物件 |
| `Ctrl+Z` / `Ctrl+Y` | 撤销 / 重做 |
| `Delete` | 删除选中物件 |
| `F10` / `F11` | 最大化 / 全屏 |

## 文件与设置

* 谱面保存为 JSON，音频复制到谱面所在目录的 `audio/` 子目录并用相对路径引用，
  整个目录可以直接搬迁。
* 视图 / 音效 / 演示视图等偏好与具体谱面无关，保存在用户配置目录
  （Windows 为 `%APPDATA%/InFalsusEditor/settings.json`）。
* `charts/` 属于用户内容、不入库，示例谱面可用
  `python run_editor.py --make-demo` 随时重新生成。

以 MIT 许可发布，详见 [LICENSE](LICENSE)。
