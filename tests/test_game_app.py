"""游玩器 GUI 冒烟测试（headless：SDL dummy 驱动）。"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from rhythm_editor.game import settings as psettings  # noqa: E402


class PlayerAppSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            import pygame  # noqa: F401
        except Exception as exc:  # pragma: no cover
            raise unittest.SkipTest(f"没有 pygame：{exc}")
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["VERUS_PLAYER_SETTINGS"] = os.path.join(cls.tmp.name, "player.json")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()
        os.environ.pop("VERUS_PLAYER_SETTINGS", None)

    def setUp(self) -> None:
        import pygame

        from rhythm_editor.game.app import PlayerApp

        chart = os.path.join(ROOT, "charts", "demo", "demo.json")
        if not os.path.isfile(chart):
            self.skipTest("缺少示例谱面")
        self.pygame = pygame
        psettings.save_settings(psettings.PlayerSettings())    # 每个用例都从默认设置开始
        self.app = PlayerApp([chart])
        self.addCleanup(self._close)

    def _close(self) -> None:
        try:
            self.app.audio.unload()
        finally:
            self.pygame.quit()

    def test_menu_loading_play_result_flow(self) -> None:
        app = self.app
        self.assertEqual(app.state, "menu")
        app._draw_menu()
        app._enter_loading()
        self.assertEqual(app.state, "loading")
        self.assertEqual(app.chart.metadata.title[:4], "Demo")
        app._start_play()
        self.assertEqual(app.state, "play")
        self.assertGreater(app.judge.total_notes, 0)
        for _ in range(10):          # 跑几帧：渲染 + 判定推进都不应抛异常
            app._update(1 / 120)
            app._draw_play()
        app._finish()
        self.assertEqual(app.state, "result")
        app._draw_result()

    def test_judge_windows_and_mouse_mapping(self) -> None:
        from rhythm_editor.game.judge import EXACT, Judge, grade_for_offset

        self.assertEqual(grade_for_offset(10.0), EXACT)
        app = self.app
        app._enter_loading()
        app._start_play()
        norm = app._mouse_norm()
        self.assertGreaterEqual(norm, 0.0)
        self.assertLessEqual(norm, 1.0, "默认（有界）鼠标应夹在 0..1")
        self.assertIsInstance(app.judge, Judge)

    def test_settings_are_saved_globally(self) -> None:
        app = self.app
        app.settings.player_name = "测试玩家"
        psettings.save_settings(app.settings)
        self.assertEqual(psettings.load_settings().player_name, "测试玩家")

    def test_lane_highlight_and_popups(self) -> None:
        """按下轨道时该轨高亮；判定结果会生成飘字。"""
        from rhythm_editor.game.judge import Judged

        app = self.app
        app._enter_loading()
        app._start_play()
        app.pressed_lanes.add(2)
        app._register(Judged("L1", "EXACT", 0.0))
        self.assertEqual(len(app.popups), 1)
        app._draw_play()          # 高亮 + 飘字都要能画出来（不抛异常）
        self.assertIn(2, app.pressed_lanes)

    def test_volumes_and_pause(self) -> None:
        """设置里能调音乐/打击音量；空格暂停并能在暂停后继续。"""
        app = self.app
        app.settings_row = app._row_index("music_volume")
        before = app.settings.music_volume
        app._adjust_setting(-1)
        self.assertLess(app.settings.music_volume, before)
        app.settings_row = app._row_index("hit_volume")
        hit_before = app.settings.hit_volume
        app._adjust_setting(-1)
        self.assertLess(app.settings.hit_volume, hit_before)
        app._enter_loading()
        app._start_play()
        app._toggle_pause()
        self.assertTrue(app.paused)
        app._draw_play()          # 暂停遮罩也要能画
        app._toggle_pause()
        self.assertFalse(app.paused)

    def test_rate_control_and_score_multiplier(self) -> None:
        """游玩器倍速：设置里 0.1 一档、0.1~1.0x；游玩中按 [-] 改倍速并自动暂停。"""
        app = self.app
        app.settings_row = app._row_index("rate")
        app._adjust_setting(-1)
        self.assertAlmostEqual(app.settings.rate, 0.9, places=6)
        app._enter_loading()
        app._start_play()
        self.assertAlmostEqual(app.judge.rate, 0.9, places=6)
        app.judge._judge_result("X", "EXACT", 0.0)
        self.assertEqual(app.judge.score, round(app.judge.base_score * (0.9 ** 1.5)))
        app._adjust_rate(-1)
        self.assertAlmostEqual(app.settings.rate, 0.8, places=6)
        self.assertTrue(app.paused, "改倍速会自动暂停")
        self.assertIn("0.8", app.status_hint)
        app._draw_play()          # 暂停遮罩 + 提示都要能画

    def test_renderer_switch_falls_back_without_gl(self) -> None:
        """切到 OpenGL：能建上下文**且自检通过**才用 GL，否则回退软件并记住该配置。"""
        app = self.app
        self.assertEqual(app.renderer_name, "software")
        app.settings_row = app._row_index("renderer")
        app._adjust_setting(1)
        if app.renderer_name == "opengl":
            self.assertEqual(app.settings.renderer, "opengl")
        else:
            # GL 不可用（或画面全黑）时不要把坏配置留着，否则下次启动还是黑屏
            self.assertEqual(app.settings.renderer, "software")
        app._draw_menu()
        app._present()          # 两种后端都要能呈现一帧
        app._adjust_setting(1)  # 再切一次
        self.assertEqual(app.settings.renderer, "software")
        self.assertEqual(app.renderer_name, "software")

    def test_chart_list_shows_metadata_and_recent(self) -> None:
        """主界面列出元数据；打开过的谱面进入「最近」并排在前面。"""
        app = self.app
        path = app.paths[0]
        title, artist, difficulty, bpm = app._chart_info(path)
        self.assertTrue(title)
        self.assertGreater(bpm, 0.0)
        app._enter_loading()
        self.assertEqual(app.settings.recent[0], path)
        app._draw_menu()          # 列表 + 元数据渲染不抛异常
        self.assertEqual(app.paths[0], path, "最近打开应排在前面")

    def test_charts_folder_lists_one_entry_per_folder(self) -> None:
        """charts/ 下每个含 json 的文件夹算一张谱面；多个 json 取主谱面。"""
        from rhythm_editor.game.app import chart_files

        with tempfile.TemporaryDirectory() as tmp:
            alpha = os.path.join(tmp, "charts", "alpha")
            beta = os.path.join(tmp, "charts", "beta", "audio")
            empty = os.path.join(tmp, "charts", "empty")
            os.makedirs(alpha)
            os.makedirs(beta)
            os.makedirs(empty)
            for folder in (alpha, beta):
                for name in ("chart.json", "b_alt.json", "_autosave.json"):
                    with open(os.path.join(folder, name), "w", encoding="utf-8") as handle:
                        handle.write("{}")
            entries = chart_files(tmp)
            self.assertEqual(
                [os.path.basename(p) for p in entries],
                ["chart.json", "chart.json"],
                "一个文件夹只出一条（多个 json 时取主谱面）",
            )
            self.assertEqual([os.path.dirname(p) for p in entries], [alpha, beta])

            # 只有下划线开头的临时 json 的文件夹、以及没有 json 的文件夹都不算谱面
            solo = os.path.join(tmp, "charts", "solo")
            os.makedirs(solo)
            with open(os.path.join(solo, "_tmp.json"), "w", encoding="utf-8") as handle:
                handle.write("{}")
            self.assertEqual(len(chart_files(tmp)), 2)

    def test_menu_actions_are_reachable_without_any_chart(self) -> None:
        """没有谱面时主界面仍有「打开其他路径」与「设置」两行，Enter 能进设置。"""
        app = self.app
        app.paths = []
        app._rebuild_entries()
        self.assertEqual(app._menu_rows(), 2)
        app._draw_menu()                       # 空列表也要能画（不抛异常）
        app.selected = 1                       # 固定行：0 = 打开其他路径，1 = 设置
        app._activate_selected()
        self.assertEqual(app.state, "settings")
        app.state = "menu"
        app.selected = 99                      # 越界也要夹回设置那一行
        app._rebuild_entries()
        self.assertEqual(app.selected, 1)
        # ↑↓ / Home / End 只在两行之间循环
        app._event_menu(self.pygame.event.Event(self.pygame.KEYDOWN, key=self.pygame.K_DOWN))
        self.assertEqual(app.selected, 0)
        app._event_menu(self.pygame.event.Event(self.pygame.KEYDOWN, key=self.pygame.K_UP))
        self.assertEqual(app.selected, 1)

    def test_open_external_chart_by_path(self) -> None:
        """「打开其他路径」：直接给路径时能读入并加入列表（不弹对话框）。"""
        app = self.app
        source = os.path.join(ROOT, "charts", "demo", "demo.json")
        with tempfile.TemporaryDirectory() as tmp:
            outside = os.path.join(tmp, "somewhere", "my_chart.json")
            os.makedirs(os.path.dirname(outside))
            with open(source, "r", encoding="utf-8") as src, open(
                outside, "w", encoding="utf-8"
            ) as dst:
                dst.write(src.read())
            app.paths = []
            app.selected = 0
            self.assertTrue(app.open_external_chart(f'"{outside}"'), "带引号的路径也要认")
            self.assertEqual(app.state, "loading")
            self.assertIn(outside, app.paths, "外部谱面要留在列表里")
            self.assertEqual(app.selected, 0, "外部谱面打开后应被选中（最近打开排最前）")
            self.assertTrue(os.path.isabs(app._display_path(outside)), "仓库外显示绝对路径")
            app.state = "menu"
            self.assertFalse(app.open_external_chart(os.path.join(tmp, "none.json")))
            self.assertIn("找不到文件", app.message)
            self.assertIsNone(app._native_open_dialog(), "无显示 / 测试环境不弹系统对话框")
            app._draw_menu()

    def test_notes_vanish_after_their_judge_time(self) -> None:
        """判定时刻过去的物件不再显示（长按 / 空域画到末尾过去为止）。"""
        from rhythm_editor.game.app import START_Z
        from rhythm_editor.model.notes import LaneNote, MouseRegion

        app = self.app
        app._enter_loading()
        chart = app.chart
        notes = chart.all_notes()
        last_ms = max(chart.timing.beat_to_song_ms(n.head_beat) for n in notes)

        def visible(now_ms: float):
            return [
                n
                for n in notes
                if app._note_z(app._head_ms(n), now_ms) <= START_Z
                and app._note_visible(n, now_ms)
            ]

        self.assertEqual(visible(last_ms + 5000.0), [], "曲末不该还留着已经过去的物件")
        taps = [n for n in notes if isinstance(n, LaneNote) and not n.is_hold]
        if taps:
            note = taps[0]
            head = app._head_ms(note)
            self.assertIn(note, visible(head - 1.0))
            self.assertNotIn(note, visible(head + 1.0))
        holds = [n for n in notes if isinstance(n, LaneNote) and n.is_hold]
        for note in holds[:2]:
            tail = chart.timing.beat_to_song_ms(note.tail_beat)
            self.assertIn(note, visible(tail - 1.0), "长按期间要一直画着")
            self.assertNotIn(note, visible(tail + 1.0))
        regions = [n for n in notes if isinstance(n, MouseRegion)]
        for note in regions[:2]:
            end = chart.timing.beat_to_song_ms(note.beat_end)
            self.assertIn(note, visible(end - 1.0))
            self.assertNotIn(note, visible(end + 1.0))

    def test_first_beat_lead_keeps_music_in_sync(self) -> None:
        """首拍仍在 1.5 秒后到判定平面，但音乐与物件同步（音频提前 1.5 秒起播）。"""
        from rhythm_editor.game.app import LEAD_MS

        app = self.app
        app._enter_loading()
        app._start_play()
        offset = app.chart.timing.beat_to_song_ms(0.0)
        self.assertAlmostEqual(app.audio_start_ms, max(0.0, offset - LEAD_MS), places=6)
        self.assertAlmostEqual(app.lead_wait_ms, max(0.0, LEAD_MS - offset), places=6)
        # 起步时“第 0 拍”离判定平面还有整整 1.5 秒（判定时间 = offset）
        self.assertAlmostEqual(app._now_ms(), offset - LEAD_MS, delta=60.0)
        app._toggle_pause()                    # 暂停时引导时间也不能走
        frozen = app._now_ms()
        time.sleep(0.05)
        self.assertAlmostEqual(app._now_ms(), frozen, delta=1.0)
        app._toggle_pause()
        self.assertLess(app._now_ms(), frozen + 5.0)

    def test_prompt_accepts_text_input_and_key_capture(self) -> None:
        """文字输入走 TEXTINPUT（中文可输入）；键位改为“按下想设的键”。"""
        app = self.app
        pygame = self.pygame
        queue = [
            [pygame.event.Event(pygame.TEXTINPUT, text="测")],
            [pygame.event.Event(pygame.TEXTINPUT, text="试")],
            [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_BACKSPACE)],
            [pygame.event.Event(pygame.TEXTINPUT, text="A")],
            [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN)],
        ]
        original = pygame.event.get
        pygame.event.get = lambda *a, **k: queue.pop(0) if queue else []
        try:
            self.assertEqual(app._prompt("玩家名称", ""), "测A")
            app.settings_row = app._row_index("keys")
            queue.extend(
                [
                    [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_LSHIFT)],
                    [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE)],
                ]
            )
            self.assertEqual(app._prompt_key("第 1 轨"), "left shift")
            self.assertIsNone(app._prompt_key("第 2 轨"), "Esc 取消返回 None")
        finally:
            pygame.event.get = original

    def test_mouse_can_operate_menu_settings_and_result(self) -> None:
        """鼠标：点谱面行选中 / 再点开始，点设置行与 ◀ ▶，点结算页按钮。"""
        app = self.app
        pygame = self.pygame
        app._draw()                                   # 画出主界面并登记命中区域
        row = app.ui["chart:0"]
        app._event_menu(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=row.center))
        app._event_menu(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=row.center))
        self.assertEqual(app.state, "loading")
        app.state = "settings"
        app._draw()
        latency_row = app._row_index("audio_latency_ms")
        app.settings_row = app._row_index("player_name")
        plus = app.ui[f"set_plus:{latency_row}"]
        before = app.settings.audio_latency_ms
        app._event_settings(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=plus.center))
        self.assertEqual(app.settings_row, latency_row)
        self.assertGreater(app.settings.audio_latency_ms, before)
        toggle = app.ui[f"set:{app._row_index('side_lane_highlight')}"]
        app._event_settings(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=toggle.center))
        app._event_settings(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=toggle.center))
        self.assertTrue(app.settings.side_lane_highlight, "再点一次该行 = 切换")
        app._start_play()
        app._finish()
        app._draw()
        app._event_result(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=app.ui["retry"].center))
        self.assertEqual(app.state, "play")
        app._finish()
        app._draw()
        app._event_result(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=app.ui["back"].center))
        self.assertEqual(app.state, "menu")

    def test_lane_keys_win_over_ui_shortcuts(self) -> None:
        """轨道键优先于界面快捷键：默认第 6 轨是空格，按它必须判物件而不是暂停。"""
        from rhythm_editor.game.app import key_code
        from rhythm_editor.model.notes import LaneNote

        app = self.app
        app._enter_loading()
        chart = app.chart
        app._start_play()
        self.assertEqual(app.settings.keys[-1], "space")
        note = next(n for n in chart.all_notes() if isinstance(n, LaneNote) and n.lane_start == 5)
        hit_ms = chart.timing.beat_to_song_ms(note.head_beat)
        app._now_ms = lambda: hit_ms
        pygame = self.pygame
        app._event_play(pygame.event.Event(pygame.KEYDOWN, key=key_code("space"), unicode=" "))
        app._event_play(pygame.event.Event(pygame.KEYUP, key=key_code("space")))
        self.assertFalse(app.paused, "空格绑成轨道键时不该暂停")
        self.assertEqual(app.judge.counts["Break"], 0)
        self.assertGreaterEqual(app.judge.judged_notes, 1)
        # 没被绑成轨道键的空格（临时解绑）仍然可以暂停
        app.settings.keys[-1] = "u"
        app._event_play(pygame.event.Event(pygame.KEYDOWN, key=key_code("space"), unicode=" "))
        self.assertTrue(app.paused)
        app._event_play(pygame.event.Event(pygame.KEYDOWN, key=key_code("space"), unicode=" "))
        self.assertFalse(app.paused, "暂停时空格继续")

    def test_broken_gl_presenter_falls_back_to_software(self) -> None:
        """GL 呈现抛异常 / 自检不过时自动切回软件渲染，并且不把坏配置留在 player.json。"""

        class Boom:
            def present(self, surface, verify=False):
                raise RuntimeError("boom")

        class Black:
            def present(self, surface, verify=False):
                return False

        for presenter in (Boom(), Black()):
            app = self.app
            app.settings.renderer = "opengl"
            app.renderer_name = "opengl"
            app.gl_presenter = presenter
            app._draw()
            app._present()                      # 不能抛异常
            self.assertEqual(app.renderer_name, "software")
            self.assertEqual(app.settings.renderer, "software")
            self.assertIn("软件渲染", app.message)
        self.assertEqual(psettings.load_settings().renderer, "software")

    def test_renderer_can_be_forced_from_cli(self) -> None:
        """--software / --opengl 强制后端（写进 player.json，不用改设置就能救回黑屏）。"""
        from rhythm_editor.game.app import PlayerApp

        forced = PlayerApp([], renderer="software")
        self.addCleanup(forced.audio.unload)
        self.assertEqual(forced.renderer_name, "software")
        self.assertEqual(psettings.load_settings().renderer, "software")
        forced.running = False

    def test_settings_speed_row_with_dot_input(self) -> None:
        """第 4 行是流速（1~10，0.1 一档，可视毫秒 = 1500/流速），● 可以自定义输入。"""
        app = self.app
        pygame = self.pygame
        app.settings_row = app._row_index("speed")
        self.assertAlmostEqual(app.settings.speed, 1.0)
        app._adjust_setting(1)
        self.assertAlmostEqual(app.settings.speed, 1.1)
        self.assertAlmostEqual(app.settings.scroll_ms, 1500.0 / 1.1)
        for _ in range(200):                       # 上限 10
            app._adjust_setting(1)
        self.assertAlmostEqual(app.settings.speed, 10.0)
        self.assertAlmostEqual(app.settings.scroll_ms, 150.0)
        for _ in range(500):                       # 下限 1
            app._adjust_setting(-1)
        self.assertAlmostEqual(app.settings.speed, 1.0)

        # 点 ● -> 输入 4.5 回车
        app.state = "settings"
        app._draw()
        dot = app.ui[f"set_dot:{app._row_index('speed')}"]
        queue = [
            [pygame.event.Event(pygame.TEXTINPUT, text="4.5")],
            [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN)],
        ]
        original = pygame.event.get
        pygame.event.get = lambda *a, **k: queue.pop(0) if queue else []
        try:
            app._event_settings(
                pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=dot.center)
            )
        finally:
            pygame.event.get = original
        self.assertAlmostEqual(app.settings.speed, 4.5)
        self.assertAlmostEqual(app.settings.scroll_ms, 1500.0 / 4.5)

    def test_settings_buttons_only_on_numeric_rows(self) -> None:
        """文本 / 开关类选项不再出现 − + 按钮；数字类才有 − ● +。"""
        app = self.app
        pygame = self.pygame
        app.state = "settings"
        app._draw()
        for key in ("player_name", "keys", "side_lane_highlight", "unbounded_mouse", "renderer"):
            row = app._row_index(key)
            self.assertNotIn(f"set_minus:{row}", app.ui)
            self.assertNotIn(f"set_plus:{row}", app.ui)
            self.assertNotIn(f"set_dot:{row}", app.ui)
            self.assertIn(f"set:{row}", app.ui)
        for key in sorted(app.NUMERIC_ROWS):       # 数字类：− ● +
            row = app._row_index(key)
            self.assertIn(f"set_minus:{row}", app.ui)
            self.assertIn(f"set_dot:{row}", app.ui)
            self.assertIn(f"set_plus:{row}", app.ui)
        # 非数字行仍然可以点行切换 / 输入
        side_row = app._row_index("side_lane_highlight")
        app.settings_row = side_row
        self.assertFalse(app.settings.side_lane_highlight)
        row = app.ui[f"set:{side_row}"]
        app._event_settings(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=row.center))
        self.assertTrue(app.settings.side_lane_highlight, "点已选中的行 = 切换开关")

    def test_rate_can_go_up_to_three(self) -> None:
        """倍速上限扩大到 3x（0.1 一档），分数 = 基础分 × 倍速^1.5。"""
        app = self.app
        app.settings_row = app._row_index("rate")
        for _ in range(60):
            app._adjust_setting(1)
        self.assertAlmostEqual(app.settings.rate, 3.0)
        app._enter_loading()
        app._start_play()
        self.assertAlmostEqual(app.judge.rate, 3.0)
        app.judge._judge_result("X", "EXACT", 0.0)
        self.assertEqual(app.judge.score, round(app.judge.base_score * (3.0 ** 1.5)))
        app._adjust_rate(-1)
        self.assertAlmostEqual(app.settings.rate, 2.9)
        for _ in range(60):
            app._adjust_setting(-1)
        self.assertAlmostEqual(app.settings.rate, 0.1)

    def test_thickness_settings_are_independent(self) -> None:
        """单键 / 多轨单键 / 滑键 / 鼠标点击各有自己的厚度；长按与空域没有厚度。"""
        app = self.app
        keys = ("note_thickness", "multi_thickness", "slide_thickness", "click_thickness")
        before = {key: getattr(app.settings, key) for key in keys}
        app.settings_row = app._row_index("slide_thickness")
        app._adjust_setting(1)
        self.assertGreater(app.settings.slide_thickness, before["slide_thickness"])
        for key in ("note_thickness", "multi_thickness", "click_thickness"):
            self.assertAlmostEqual(
                getattr(app.settings, key), before[key], places=6,
                msg="不同物件的厚度互不影响",
            )
        app.settings_row = app._row_index("multi_thickness")
        app._adjust_setting(1)
        self.assertGreater(app.settings.multi_thickness, before["multi_thickness"])
        self.assertGreater(app.settings.note_thickness, 0.0, "单键厚度仍独立存在")
        labels = [label for _key, label in app.SETTING_ROWS]
        self.assertFalse(any("长按" in text or "空域" in text for text in labels))
        app._enter_loading()
        app._start_play()
        app._draw_play()                    # 各厚度都要能画出来

    def test_result_layout_thirds_and_no_rate(self) -> None:
        """结算页：左 1/3、右 2/3；分数行不显示倍速与倍率。"""
        app = self.app
        app.settings.rate = 2.0
        app._enter_loading()
        app._start_play()
        app._finish()
        self.assertEqual(app._result_split(), app.w // 3)
        line = app._result_score_line()
        self.assertNotIn("倍速", line)
        self.assertNotIn("×", line)
        app._draw()                          # 结算页能画出来

    def test_window_resize_and_fullscreen_do_not_rebuild_display(self) -> None:
        """最大化只跟随尺寸（不再 set_mode，否则会被打回）；F10 / F11 不抛异常。"""
        app = self.app
        pygame = self.pygame
        before = app.screen
        pygame.event.post(
            pygame.event.Event(pygame.VIDEORESIZE, w=1000, h=700, size=(1000, 700))
        )
        app._events()
        self.assertIs(app.screen, before, "VIDEORESIZE 不该重建显示表面")

        class FakePresenter:               # 模拟 GL 模式：离屏表面要跟着窗口尺寸变
            def present(self, surface, verify=False):
                return True

        app.gl_presenter = FakePresenter()
        app._sync_display_size(1200, 800)
        self.assertEqual((app.w, app.h), (1200, 800))
        app.gl_presenter = None
        app._sync_display_size()
        self.assertIs(app.screen, pygame.display.get_surface())
        app._maximize_window()               # 不应抛异常
        app._toggle_fullscreen()             # dummy 驱动下失败也要有提示、不抛
        self.assertTrue(app.message)
        app._draw()                          # 尺寸变化后还能正常画

    def test_player_feeds_the_stretch_audio_backend_every_frame(self) -> None:
        """每帧都要 poll 音频后端：不喂的话低倍速只播第一段，谱面时钟跟着卡住。"""
        app = self.app
        app._enter_loading()
        app._start_play()
        calls = []
        original = app.audio.poll
        app.audio.poll = lambda: (calls.append(1), original())[1]
        try:
            for _ in range(10):
                app._update(1 / 120)
        finally:
            app.audio.poll = original
        self.assertGreaterEqual(len(calls), 10, "每帧都要喂音频后端")

    def test_audio_gap_does_not_end_the_game_immediately(self) -> None:
        """换窗口时音频可能短暂停下：要等 0.5 秒以上才算播完，且结束后还要等 3 秒才结算。"""
        from rhythm_editor.game.app import END_DELAY_S

        app = self.app
        app._enter_loading()
        app._start_play()
        app._now_ms = lambda: 1000.0
        original = type(app.audio).is_playing
        type(app.audio).is_playing = property(lambda self: False)
        try:
            for _ in range(30):                 # 0.25 秒
                app._update(1 / 120)
            self.assertEqual(app.state, "play", "短暂无声不该立刻结束")
            for _ in range(60):                 # 再 0.5 秒
                app._update(1 / 120)
            self.assertTrue(app.ending, "静音够久后进入“等待结算”")
            self.assertEqual(app.state, "play")
            for _ in range(int(END_DELAY_S * 120) + 5):     # 再等满 3 秒
                app._update(1 / 120)
            self.assertEqual(app.state, "result")
        finally:
            type(app.audio).is_playing = original

    def test_notes_beyond_the_horizon_are_not_drawn(self) -> None:
        """比可视范围更远的滑键 / 点击按键不画（否则会全堆在屏幕最上方）。"""
        from rhythm_editor.model.notes import MouseClick, MouseSlide

        app = self.app
        app._enter_loading()
        chart = app.chart
        markers = [n for n in chart.all_notes() if isinstance(n, (MouseSlide, MouseClick))]
        self.assertTrue(markers, "示例谱面应有滑键 / 点击按键")
        scroll = app.settings.scroll_ms
        for now in (0.0, 1000.0):
            drawn = [n for n in markers if app._note_ahead(n, now) and app._note_visible(n, now)]
            for note in drawn:
                self.assertLessEqual(
                    app._head_ms(note) - now, scroll + 1e-6, "远于可视范围的物件不该被画"
                )
        first = min(markers, key=lambda n: app._head_ms(n))
        head = app._head_ms(first)
        just_entered = head - scroll + 1.0
        self.assertTrue(app._note_ahead(first, just_entered))
        self.assertFalse(app._note_ahead(first, just_entered - 2.0))

    def test_white_line_is_thinner_when_far(self) -> None:
        """白线粗细随透视：远处的线更细（近大远小），不会远看反而更粗。"""
        from rhythm_editor.game.app import WHITE
        from rhythm_editor.game.scene import START_Z

        app = self.app
        widths = []
        original = app._line
        app._line = lambda a, b, color, width=2: widths.append(int(width))
        try:
            near_a, near_b = app._proj(0.0, 0.0, 0.0), app._proj(1.0, 0.0, 0.0)
            far_a, far_b = app._proj(0.0, 0.0, START_Z), app._proj(1.0, 0.0, START_Z)
            app._line_scaled(near_a, near_b, WHITE, 3.0, near_a[2])
            app._line_scaled(far_a, far_b, WHITE, 3.0, far_a[2])
        finally:
            app._line = original
        self.assertGreater(widths[0], widths[1], "远处的白线应更细")
        self.assertGreaterEqual(widths[1], 1)

    def test_judgement_text_appears_at_the_note(self) -> None:
        """瞬时物件（单键 / 鼠标点击）也要在被判定的位置冒出判定字符。"""
        from rhythm_editor.game.judge import Judged
        from rhythm_editor.model.notes import LaneNote, MouseClick

        app = self.app
        app._enter_loading()
        chart = app.chart
        tap = next(n for n in chart.all_notes() if isinstance(n, LaneNote) and not n.is_hold)
        app._start_play()
        app.popups.clear()
        app._register(Judged(tap.id, "EXACT", tap.head_beat))
        self.assertEqual(len(app.popups), 1)
        grade, text, left, x, y = app.popups[0]
        self.assertEqual((grade, text), ("EXACT", "EXACT"))
        expected_x = app._proj(app.scene.lane_center(tap.lane_start), 0.0, 0.0)[0]
        self.assertAlmostEqual(x, expected_x, delta=2.0, msg="飘字应出现在被判定的那条轨道上")
        self.assertGreater(y, 0.0)
        click = next(n for n in chart.all_notes() if isinstance(n, MouseClick))
        app._register(Judged(click.id, "Near", click.head_beat))
        self.assertEqual(app.popups[-1][0], "Near")
        app._draw_popups()                     # 飘字要能画出来

    def test_slide_direction_and_click_colors(self) -> None:
        """滑键：朝向侧更厚更实、没有白线；鼠标点击按键：所有线都是红色。"""
        from rhythm_editor.game.app import MOUSE_PLANE_HEIGHT, WHITE, MouseClick, MouseSlide

        app = self.app
        app._enter_loading()
        app._start_play()
        chart = app.chart
        slide = next(n for n in chart.all_notes() if isinstance(n, MouseSlide))
        alphas = []
        lines = []
        original_alpha, original_line = app._alpha_quad, app._line
        app._alpha_quad = lambda points, color, alpha: alphas.append(alpha)
        app._line = lambda a, b, color, width=2: lines.append(color)
        try:
            app._draw_pattern(slide, app._head_ms(slide))
        finally:
            app._alpha_quad, app._line = original_alpha, original_line
        self.assertEqual(len(alphas), 2, "滑键画两层：底 + 朝向侧")
        self.assertGreater(alphas[1], alphas[0], "朝向的一侧不透明度更高")
        self.assertNotIn(WHITE, lines, "滑键不再画中间的实线")

        click = next(n for n in chart.all_notes() if isinstance(n, MouseClick))
        lines = []
        app._line = lambda a, b, color, width=2: lines.append(color)
        try:
            app._draw_pattern(click, app._head_ms(click))
        finally:
            app._line = original_line
        self.assertTrue(lines)
        for color in lines:
            self.assertGreater(color[0], 200, "鼠标点击按键的线应该是红色")
            self.assertLess(color[1], 130)

        # 鼠标指针（三角形）躺在物件运动的水平面内
        points = []
        original_proj = app._proj

        def spy(x, y, z):
            points.append((x, y, z))
            return original_proj(x, y, z)

        app._proj = spy
        try:
            app._draw_mouse_marker()
        finally:
            app._proj = original_proj
        self.assertEqual(len(points), 3)
        for _x, y, _z in points:
            self.assertAlmostEqual(y, MOUSE_PLANE_HEIGHT, places=6, msg="三角形要在鼠标平面内")
        # 矮胖：屏幕上看应该更宽而不是更长
        screen = [original_proj(*point) for point in points]
        width = max(p[0] for p in screen) - min(p[0] for p in screen)
        height = max(p[1] for p in screen) - min(p[1] for p in screen)
        self.assertGreater(width, height, "鼠标指针要矮胖")

    def test_result_page_shows_combo_ratio_and_multiplier(self) -> None:
        """结算：最大连击写成 x / 总判定数，倍率放在它后面。"""
        app = self.app
        app.settings.rate = 2.0
        app._enter_loading()
        app._start_play()
        app.judge._judge_result("X", "EXACT", 0.0)
        app._finish()
        combo_line = app._result_combo_line()
        self.assertIn("/", combo_line)
        self.assertIn(str(app.judge.total_notes), combo_line)
        self.assertIn("×", app._result_multiplier_line())
        self.assertAlmostEqual(app.settings.rate ** 1.5, 2.0 ** 1.5, places=6)
        app._draw()

    def test_display_mode_cycles_window_borderless_fullscreen(self) -> None:
        """F11 依次切换 窗口 → 无边框全屏 → 独占全屏 → 窗口。"""
        app = self.app
        self.assertEqual(app.display_mode, "windowed")
        seen = []
        for _ in range(4):
            app._toggle_fullscreen()
            seen.append(app.display_mode)
            self.assertTrue(app.message)
            app._draw()                       # 换模式后还能画
        self.assertEqual(seen, ["borderless", "fullscreen", "windowed", "borderless"])

    def test_mouse_marker_fill_rules(self) -> None:
        """无界：永远实心；关闭：界内实心、出界时判定位置实心 + 实际位置只画边线。"""
        from rhythm_editor.game.app import MOUSE_LEGAL_WIDTH, MOUSE_LEFT

        app = self.app
        app._enter_loading()
        app._start_play()
        fills = []
        xs = []
        original_alpha, original_proj = app._alpha_quad, app._proj
        app._alpha_quad = lambda points, color, alpha: fills.append(alpha)

        def spy(x, y, z):
            xs.append(x)
            return original_proj(x, y, z)

        app._proj = spy

        def norm_of(world_x: float) -> float:
            return (world_x - MOUSE_LEFT) / MOUSE_LEGAL_WIDTH

        try:
            def draw_and_reset():
                fills.clear()
                xs.clear()
                app._draw_mouse_marker()
                return [norm_of(x) for x in xs]

            # 1) 开启无界鼠标：出界的实际位置也画实心
            app.settings.unbounded_mouse = True
            app.mouse_norm, app.mouse_raw = 1.35, 1.35
            drawn = draw_and_reset()
            self.assertEqual(len(fills), 1, "无界鼠标永远实心")
            self.assertAlmostEqual(drawn[0], 1.35, delta=1e-6, msg="指针尖在鼠标实际位置")

            # 2) 关闭无界 + 界内：只有判定位置（=实际位置）画实心
            app.settings.unbounded_mouse = False
            app.mouse_norm, app.mouse_raw = 0.4, 0.4
            drawn = draw_and_reset()
            self.assertEqual(len(fills), 1)
            self.assertAlmostEqual(drawn[0], 0.4, delta=1e-6)

            # 3) 关闭无界 + 出界：判定位置（夹到 1.0）实心，实际位置（1.3）只画边线
            app.mouse_norm, app.mouse_raw = 1.0, 1.3
            drawn = draw_and_reset()
            self.assertEqual(len(fills), 1, "只有判定位置是实心")
            self.assertEqual(len(drawn), 6, "判定位置 + 实际位置各一个三角形")
            self.assertAlmostEqual(drawn[0], 1.0, delta=1e-6, msg="判定位置夹在边界上")
            self.assertAlmostEqual(drawn[3], 1.3, delta=1e-6, msg="实际位置也画出来")
        finally:
            app._alpha_quad, app._proj = original_alpha, original_proj


if __name__ == "__main__":
    unittest.main()
