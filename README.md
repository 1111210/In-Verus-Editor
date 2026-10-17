# In Verus Editor

## 1. 项目简介

In Verus Editor 是游戏 **《In Falsus》**（[Steam 商店页](https://store.steampowered.com/app/3971950)）的非官方社区谱面编辑器，用于编辑双平面谱面：左边 **6K 下落式**，右边 **鼠标平面**（空域 / 禁区 / 滑键），两边共享同一条判定线与同一套时间网格。

编辑器产出的谱面推荐搭配播放器 **[In Verus Player](https://github.com/1111210/In-Verus-Player)** 游玩与练习。

以 MIT 许可发布，详见 [LICENSE](LICENSE)。

## 2. 主要特点

1. **工具尽可能不限制创造力。**
   * 流速与 BPM 完全分离：BPM 只决定「拍 ↔ 秒」的换算，物件运动速度由速度组的流速积分决定，可以加速、暂停、反向与往返，两者互不干扰。
   * 超界与重叠物件照常渲染：横向位置允许超出 0 .. 1 的合法区域；重叠的半透明空域 / 禁区按发光叠加绘制，即使谱面写得再"出格"也能看清每一层。
2. **操作逻辑尽可能符合直觉，降低上手难度。**
   * 快捷键按常见办公软件的习惯设置（`Ctrl+N / O / S / Z / Y / X / C / V / A`、`Delete`、`Esc` 等），并且每一步都可撤销。
   * 提供大量便捷操作：直接拖动物件改时间 / 轨道 / 横向位置，滚轮滚动、`Ctrl+滚轮` 缩放，拖分界线改左右宽度，框选配合 `Ctrl` 做对称差选择，放置全程带幽灵预览。

## 3. 快速启动

### 3.1 运行发布版

在项目的 Release 页面下载 `InVerusEditor-windows-x64.zip`，解压后双击
`InVerusEditor.exe` 即可，无需安装 Python 与任何依赖。请保留整个解压目录，不要
只单独移动 exe；程序依赖同目录下的 `_internal/`。

发布包采用 PyInstaller `onedir` 模式，避免单文件程序每次启动时都把完整运行库解压到
临时目录，通常可以明显缩短启动时间。介意打包程序的话也可以按 3.2 从源码启动。

### 3.2 从源码启动

需要 Python 3.10 或更高版本（见 `pyproject.toml` 的 `requires-python`），然后：

```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1      # Windows PowerShell（其它系统用 source .venv/bin/activate）
pip install -r requirements.txt
python run_editor.py
```

`pygame-ce` 与 `numpy` 只在音频播放与演示模式下需要：没有它们时编辑器仍可完整编辑与播放（静音时钟），只是没有声音。视图 / 音效 / 快捷键 / 上次谱面路径等偏好保存在程序旁边的 `settings.json`（源码运行时就是仓库根目录），整个文件夹可以连设置一起搬迁。

常用命令行参数：

```bash
python run_editor.py                         # 空白谱面（默认最大化，F10 还原 / F11 全屏）
python run_editor.py charts/demo/demo.json   # 打开指定谱面
python run_editor.py --make-demo             # 生成示例谱面后退出
```

### 3.3 重新打包 Windows 发布版

安装构建依赖并执行构建脚本：

```bash
pip install -e .[all,build]
python packaging/build_release.py
```

脚本要求构建环境的 Tcl/Tk 为 9.x，并通过运行时路径钩子直接使用 Tcl/Tk DLL
内置的脚本库，避免再携带数百个重复数据文件。构建使用 `onedir` 模式，并在
`release/` 下生成 `InVerusEditor/` 与 `InVerusEditor-windows-x64.zip`。构建后的
exe 需要与 `_internal/` 一起分发。

## 4. 项目结构

```
.
├─ run_editor.py                # 免安装启动脚本：把 src 加入搜索路径后启动编辑器
├─ packaging/
│  ├─ InVerusEditor.spec        # PyInstaller onedir 发布配置
│  ├─ rthook_tcl9.py            # 为 Tcl/Tk 9 设置内置 zipfs 运行库路径
│  └─ build_release.py          # 校验依赖、打包与 ZIP 生成
├─ requirements.txt             # 安装 pyproject.toml 中定义的完整可选依赖
├─ pyproject.toml               # 包元数据与入口点
├─ LICENSE                      # MIT 许可
├─ assets/                       # 演示视图贴图（缺失时自动生成占位图）
└─ src/
   ├─ __main__.py               # `python src` 入口
   ├─ app.py                    # 主程序：窗口、工具栏、画布、播放循环与全部命令
   ├─ config.py                 # 全局常量、默认值与枚举字符串
   ├─ chart_io.py               # 谱面 JSON 读写与音频复制
   ├─ audio.py                  # 音频后端：流式播放 / 变速重采样 / 静音时钟，含延迟补偿
   ├─ metronome.py              # 节拍音与打击音：预计算时刻表 + 混音合成
   ├─ demo.py                   # 示例谱面构造与无界面自检
   ├─ editor_settings.py        # 全局编辑设置模型（不写入谱面）
   ├─ global_settings.py        # 全局设置的读写（存程序旁边的 settings.json，不写进谱面）
   ├─ settings_store.py         # 设置文件的路径解析与容错读写
   ├─ shortcuts.py              # 可配置快捷键的定义、规范化与事件转换
   ├─ model/                    # 数据层（纯数据，可独立测试）
   │  ├─ timing.py              # BPM 列表与偏移：拍 ↔ 秒换算
   │  ├─ speed.py               # 速度组：位置由流速积分反推
   │  ├─ notes.py               # 物件模型：6K 按键 / 空域 / 禁区 / 滑键
   │  ├─ chart.py               # 谱面聚合、序列化与派生索引
   │  ├─ document.py            # 撤销 / 重做（差量记录式）
   │  ├─ segments.py            # 分段常量序列的公共操作
   │  └─ validate.py            # 自动检查：轨道重叠、滑键与空域、禁区覆盖
   ├─ ui/                       # 编辑器界面
   │  ├─ theme.py               # 配色、字体与 ttk 样式表
   │  ├─ scene.py               # 带差分的 Tk 画布封装
   │  ├─ view.py                # 布局几何与屏幕 ↔ 时间轴换算
   │  ├─ geometry.py            # 物件几何、裁剪与命中测试
   │  ├─ renderer.py            # 画布渲染（放置层 + 预览层）
   │  ├─ interactions.py        # 工具状态机：放置 / 选择 / 粘贴 / 滚动 / 缩放
   │  ├─ toolbar.py             # 顶部工具栏与二级菜单
   │  └─ dialogs.py             # 全部对话框与内置帮助文本
   └─ preview/                  # 演示视图（软件渲染的游玩视角预览，已弃用）
      ├─ scene.py               # 3D 布局与透视投影
      ├─ frame.py               # 物件的世界坐标描述
      ├─ textures.py            # 贴图加载与规格化
      ├─ settings.py            # 演示视图的渲染参数
      └─ softview.py            # pygame 软件渲染
```
