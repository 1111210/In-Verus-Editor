# 示例谱面

仓库内已经附带一份可直接打开的示例谱面 `demo.ivchart`；
它也可以随时用 Python 代码重新生成（覆盖同名文件）：

```bash
python run_editor.py --make-demo          # 生成 charts/demo/demo.ivchart
python run_editor.py charts/demo/demo.ivchart        # 打开示例谱面
python run_editor.py charts/demo/demo.ivchart --demo # 直接进入演示模式
```

示例谱面包含：

* 6K 单轨按键 / 多轨按键 / 长按
* 鼠标停留区域（线性 / 缓入 / 缓出三种边界）
* 鼠标左右滑键（向左、向右）
* 三段 BPM（140 / 168 / 120，最后一段每小节 3 拍）与 120 ms 偏移
* 三个特效速度组：加速、反向、往返（正负流速叠加，可把远处的物件推回判定线附近）

生成逻辑见 [`src/rhythm_editor/demo.py`](../../src/rhythm_editor/demo.py) 的 `build_demo_chart()`：

* 6K 单轨按键 / 多轨按键 / 长按（含跨轨道长按）
* 三个鼠标停留区域（线性 / 缓入 / 缓出）与五个左右滑键
* 三段 BPM 与 120 ms 偏移
* 两个特效速度组（加速、反向运动）

结构说明见 [`docs/CHART_FORMAT.md`](../../docs/CHART_FORMAT.md)。
