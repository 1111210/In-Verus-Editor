# 截图目录

```bash
python tools/screenshot_editor.py docs/images
```

生成 `editor_overview.png`、`placement.png`、`demo_mode.png`
（脚本用 GDI `PrintWindow` 抓取窗口内容，失败时退回屏幕区域拷贝；需要可用的桌面会话）。
