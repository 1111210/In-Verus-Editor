# 截图目录

```bash
python tools/screenshot_editor.py docs/images
python tools/screenshot_player.py docs/images
```

编辑器截图：`editor_overview.png`、`placement.png`、`demo_mode.png`、
`maximized_editor.png`、`fullscreen_editor.png`。

游玩器截图：`player_menu.png`、`player_settings.png`、`player_play.png`、`player_result.png`。

脚本用 GDI `PrintWindow` 抓取窗口内容（失败时退回屏幕区域拷贝），需要可用的桌面会话。
