# 示例谱面

仓库内附带一份可直接打开的示例谱面 `demo.json`（普通 JSON 文件），也可以随时重新生成：

```bash
python run_editor.py --make-demo                   # 生成 charts/demo/demo.json
python run_editor.py charts/demo/demo.json         # 打开示例谱面
python run_editor.py charts/demo/demo.json --demo  # 直接进入演示模式
```

内容：

* 6K 单轨按键 / 多轨按键 / 长按（含跨轨道长按）共 32 个；
* 空域 3 个（线性 / 缓入 / 缓出三种边界）；
* 左右滑键 6 个、鼠标点击按键 4 个；
* 三段 BPM（140 / 168 / 120，最后一段每小节 3 拍）与 120 ms 偏移；
* 4 个速度组：默认组、加速特效、反向特效、往返特效（正负流速叠加，
  可把远处的物件推回判定线附近）。

生成逻辑见 `src/rhythm_editor/demo.py::build_demo_chart()`；
谱面结构见 [`docs/CHART_FORMAT.md`](../../docs/CHART_FORMAT.md)。
