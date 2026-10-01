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
        self.assertEqual(
            app.judge.score,
            round(app.judge.accuracy_score * (0.9 ** 0.6)) + app.judge.exact_bonus,
        )
        app._adjust_rate(-1)
        self.assertAlmostEqual(app.settings.rate, 0.8, places=6)
        self.assertTrue(app.paused, "改倍速会自动暂停")
        self.assertIn("0.8", app.status_hint)
        app._draw_play()          # 暂停遮罩 + 提示都要能画

    def test_renderer_switch_cycles_panda_opengl_software(self) -> None:
        """渲染后端三选一：Panda3D → OpenGL → 软件（不可用时自动回退）。"""
        from rhythm_editor.game import glview

        app = self.app
        original_available, original_make = glview.available, app._make_display
        glview.available = lambda: True                    # 假装 GL 可用，避免 dummy 回退干扰
        app._make_display = lambda width, height, **kwargs: app.screen
        try:
            app.settings.renderer = "panda3d"
            seen = []
            for _ in range(3):
                app._switch_renderer()
                seen.append(app.settings.renderer)
        finally:
            glview.available, app._make_display = original_available, original_make
        self.assertEqual(seen, ["opengl", "software", "panda3d"], "轮换顺序 Panda → OpenGL → 软件")
        app.settings.renderer = "software"
        app._draw_menu()
        app._present()          # 三种后端都要能呈现一帧

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
        """首拍永远在 1.5 秒后到判定平面（1x），音乐与物件同步。"""
        from rhythm_editor.game.app import LEAD_MS

        app = self.app
        app._enter_loading()
        app._start_play()
        offset = app.chart.timing.beat_to_song_ms(0.0)
        self.assertAlmostEqual(app.audio_start_ms, max(0.0, offset - LEAD_MS), places=6)
        self.assertAlmostEqual(
            app.lead_wait_real_ms, max(0.0, (LEAD_MS - offset) / app.settings.rate), places=6
        )
        # 起步时“第 0 拍”离判定平面还有整整 1.5 秒（判定时间 = offset）
        self.assertAlmostEqual(app._now_ms(), offset - LEAD_MS, delta=60.0)
        app._toggle_pause()                    # 暂停时引导时间也不能走
        frozen = app._now_ms()
        time.sleep(0.05)
        self.assertAlmostEqual(app._now_ms(), frozen, delta=1.0)
        app._toggle_pause()
        self.assertLess(app._now_ms(), frozen + 5.0)

    def test_lead_in_is_always_real_1500ms_regardless_of_rate(self) -> None:
        """引导时间是**真实** 1.5 秒：倍速再高也不会缩短，也不会提前跳过谱面开头。"""
        from rhythm_editor.game.app import LEAD_MS
        from rhythm_editor.game import app as app_module

        app = self.app
        app._enter_loading()
        chart = app.chart
        offset = chart.timing.beat_to_song_ms(0.0)
        for rate in (0.5, 1.0, 1.5):
            app.settings.rate = rate
            clock = [1000.0]
            original_perf = app_module.time.perf_counter
            app_module.time.perf_counter = lambda: clock[0]
            try:
                app._start_play()
                start = app._now_ms()
                self.assertAlmostEqual(
                    app.lead_start_ms, offset - LEAD_MS * rate, places=6
                )
                # 起步时第 0 拍还差“倍速 × 1.5 秒”的歌曲时间；换成真实时间就是 1.5 秒
                self.assertAlmostEqual(offset - start, LEAD_MS * rate, delta=60.0)
                self.assertAlmostEqual(
                    (offset - start) / rate, LEAD_MS, delta=60.0 / rate, msg="真实引导时间恒为 1.5s"
                )
                # 任何物件都不会在开局就“已经过线”
                for note in chart.all_notes():
                    self.assertGreaterEqual(
                        app._head_ms(note), start - 1e-6,
                        msg=f"rate={rate}：开局就有物件到达判定线",
                    )
                # 等满引导时间：歌曲时钟正好走到 0，且没有跳过任何歌曲时间
                clock[0] += app.lead_wait_real_ms / 1000.0
                self.assertAlmostEqual(app._now_ms(), 0.0, delta=60.0)
            finally:
                app_module.time.perf_counter = original_perf
        app.settings.rate = 1.0

    def test_autoplay_judges_exact_and_scores_negative(self) -> None:
        """自动播放：玩家名显示 Autoplay、输入不判定（只高亮）、过线全 EXACT、分数倍率 -1x。"""
        from rhythm_editor.game.app import key_code
        from rhythm_editor.model.notes import LaneNote, MouseClick

        app = self.app
        app.settings_row = app._row_index("autoplay")
        app._adjust_setting(1)
        self.assertTrue(app.settings.autoplay, "设置里可以开启自动播放")
        app._enter_loading()
        chart = app.chart
        app._start_play()
        self.assertTrue(app.judge.autoplay)
        self.assertEqual(app._player_label(), "Autoplay")
        # 键盘输入不再判定，但轨道高亮照常
        tap = next(n for n in chart.all_notes() if isinstance(n, LaneNote) and not n.is_hold)
        hit_ms = chart.timing.beat_to_song_ms(tap.head_beat)
        app._now_ms = lambda: hit_ms - 500.0          # 提前按，依然不该判
        pygame = self.pygame
        app._event_play(
            pygame.event.Event(pygame.KEYDOWN, key=key_code(app.settings.keys[tap.lane_start]), unicode="")
        )
        self.assertIn(tap.lane_start, app.pressed_lanes, "轨道高亮仍然要有")
        self.assertEqual(app.judge.judged_notes, 0, "自动播放时键盘不参与判定")
        app._event_play(
            pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=(400, 300))
        )
        self.assertEqual(app.judge.judged_notes, 0, "自动播放时鼠标点击不参与判定")
        # 时间推到曲末：所有物件 / 区间自动 EXACT
        last_ms = max(
            chart.timing.beat_to_song_ms(getattr(n, "tail_beat", n.head_beat))
            for n in chart.all_notes()
        )
        app._now_ms = lambda: last_ms + 5000.0
        app._update(1 / 120)
        self.assertEqual(app.judge.counts["Break"], 0)
        self.assertEqual(app.judge.counts["EXACT"], app.judge.total_notes)
        # 分数倍率 = -倍速^0.6
        expected_multiplier = -(app.settings.rate ** 0.6)
        self.assertAlmostEqual(app.judge.score_multiplier, expected_multiplier)
        self.assertEqual(
            app.judge.score,
            round(app.judge.accuracy_score * expected_multiplier) - app.judge.exact_bonus,
            "负倍率时附加分变扣分",
        )
        self.assertEqual(app.judge.exact_adjustment, -app.judge.exact_bonus)
        self.assertRegex(app._result_score_line(), r"^分数：-\d+ - \d+$", "负倍率写成额外扣分")
        app.settings.rate = 1.5
        app.judge.rate = 1.5
        self.assertAlmostEqual(app.judge.score_multiplier, -(1.5 ** 0.6), places=6)
        # 结算页：倍速行注明 -1x，判定数换成准度
        app._finish()
        self.assertIn("Autoplay", app._result_rate_line())
        self.assertIn(f"{app.judge.score_multiplier:.2f}", app._result_rate_line())
        self.assertIn("准度", app._result_accuracy_line())
        self.assertRegex(app._result_accuracy_line(), r"准度：\d+\.\d{3}%", "准度保留三位小数")
        self.assertEqual(app._player_label(), "Autoplay")
        # 结算右栏顺序：分数 -> 准度 -> 最大连击 -> 倍速
        labels = [line.split("：")[0] for line, _is_score in app._result_lines()]
        self.assertEqual(labels[0], "分数")
        self.assertEqual(labels[1], "准度")
        self.assertEqual(labels[2], "最大连击")
        self.assertEqual(labels[3], "倍速")
        app._draw()
        app.settings.autoplay = False
        app.settings.rate = 1.0

    def test_result_score_splits_bonus_but_hud_does_not(self) -> None:
        """结算页分数 = 「准度部分 + 附加分」（附加分不吃倍率）；游戏内 HUD 仍是总分。"""
        from rhythm_editor.game.app import WHITE

        app = self.app
        app.settings.rate = 1.5
        app._enter_loading()
        app._start_play()
        app.judge._judge_result("X", "EXACT", 0.0)
        app.judge._judge_result("Y", "Near", 1.0)
        self.assertEqual(app.judge.exact_bonus, 1)
        self.assertEqual(
            app.judge.scaled_score,
            round(app.judge.accuracy_score * (1.5 ** 0.6)),
            "结算展示的分数只把准度部分乘倍率",
        )
        self.assertEqual(app.judge.score, app.judge.scaled_score + 1, "附加分不吃倍率")
        line = app._result_score_line()
        self.assertRegex(line, r"^分数：-?\d+ \+ \d+$")
        # 游戏内 HUD 不受影响：仍然显示总分，不做拆分
        hud_score = [line for line in app._hud_right_lines() if line.startswith("分数")][0]
        self.assertIn(str(app.judge.score), hud_score)
        self.assertNotIn(" + ", hud_score)
        app._draw_hud(0.0)          # 画得出来
        app._finish()
        app._draw()
        app.settings.rate = 1.0

    def test_custom_input_ignores_the_step_grid(self) -> None:
        """手输的数值不受档位影响（只做范围夹取）；步长只决定 − / + 每次动多少。"""
        app = self.app
        pygame = self.pygame
        original = pygame.event.get

        def type_number(text: str) -> None:
            queue = [
                [pygame.event.Event(pygame.TEXTINPUT, text=text)],
                [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN)],
            ]
            pygame.event.get = lambda *a, **k: queue.pop(0) if queue else []
            app._prompt_number(app._row_key())

        try:
            # 倍速：1.37 不在 0.1 的档位上，也要原样保留
            app.settings_row = app._row_index("rate")
            app.settings.rate = 1.0
            type_number("1.37")
            self.assertAlmostEqual(app.settings.rate, 1.37, places=6)
            # 超出范围仍然夹到上限 1.5
            type_number("9.9")
            self.assertAlmostEqual(app.settings.rate, 1.5, places=6)
            # 其它数字项同理：单键厚度 0.37 不该被吸到 0.35 / 0.40
            app.settings_row = app._row_index("note_thickness")
            type_number("0.37")
            self.assertAlmostEqual(app.settings.note_thickness, 0.37, places=6)
            # 而 − / + 仍然按步长走
            app._adjust_setting(1)
            self.assertAlmostEqual(app.settings.note_thickness, 0.42, places=6)
        finally:
            pygame.event.get = original

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

    def test_rate_can_go_up_to_1_5(self) -> None:
        """倍速上限 1.5x（+/ - 步长 0.1），分数 = 准度部分 × 倍速^0.6 + 附加分。"""
        app = self.app
        app.settings_row = app._row_index("rate")
        for _ in range(60):
            app._adjust_setting(1)
        self.assertAlmostEqual(app.settings.rate, 1.5)
        app._enter_loading()
        app._start_play()
        self.assertAlmostEqual(app.judge.rate, 1.5)
        app.judge._judge_result("X", "EXACT", 0.0)
        self.assertEqual(
            app.judge.score,
            round(app.judge.accuracy_score * (1.5 ** 0.6)) + app.judge.exact_bonus,
        )
        app._adjust_rate(-1)
        self.assertAlmostEqual(app.settings.rate, 1.4)
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

    def test_sliced_texture_pieces_tile_without_gaps(self) -> None:
        """分片贴图（单键 / 长按开头）的相邻分片必须共用整数边界。"""
        app = self.app
        zs = [0.0, 0.25, 0.5, 0.75, 1.0, 1.25]
        pieces = app._lane_tex_pieces(-0.25, 0.0, zs, 96, 96)
        self.assertEqual(len(pieces), len(zs) - 1)
        for (za, zb, _sa, da), (_zb, _zc, _sb, db) in zip(pieces, pieces[1:]):
            self.assertLess(za, zb)
            # 列表按 z 从近到远：屏幕 y 越来越小，所以是上一片的**上**边界接下一片的**下**边界
            self.assertEqual(da.top, db.bottom, "相邻分片必须严丝合缝（不能留缝 / 重叠）")
        # 远处的分片更窄（透视收敛）
        self.assertGreaterEqual(pieces[0][3].width, pieces[-1][3].width)
        # 贴图 v：近端用图像的下边，远端用图像的上边
        self.assertEqual(pieces[0][2].bottom, 96)
        self.assertEqual(pieces[-1][2].top, 0)

    def test_hold_center_line_uses_texture_fraction(self) -> None:
        """软渲染的长按主体白线：占键宽与贴图一致（= 开头的白线），并按透视收窄。"""
        from rhythm_editor.game import textures as game_textures
        from rhythm_editor.game.app import WHITE

        app = self.app
        captured = []
        original = app._quad
        app._quad = lambda points, color: captured.append((list(points), color))
        try:
            app._blit_hold_center_line(0.0, 0.6, 0.0, 2.0)
        finally:
            app._quad = original
        self.assertEqual(len(captured), 1)
        points, color = captured[0]
        self.assertEqual(color, WHITE)
        half = 0.6 * game_textures.HOLD_LINE_FRACTION / 2.0     # 白线半宽（世界单位）
        want = [
            app._proj(0.3 - half, 0.0, 0.0),
            app._proj(0.3 + half, 0.0, 0.0),
            app._proj(0.3 + half, 0.0, 2.0),
            app._proj(0.3 - half, 0.0, 2.0),
        ]
        for got, expected in zip(points, want):
            for axis in (0, 1):
                self.assertAlmostEqual(got[axis], expected[axis], places=6)
        # 近端比远端宽（透视）
        self.assertGreater(abs(points[1][0] - points[0][0]), abs(points[2][0] - points[3][0]))

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

    def test_culling_matches_the_visible_span_at_any_rate(self) -> None:
        """剔除阈值必须和 z 映射用同一个跨度：低倍速不堆积、高倍速从轨道尽头出现。"""
        from rhythm_editor.game.scene import START_Z

        app = self.app
        app._enter_loading()
        notes = app.chart.all_notes()
        now = 1000.0
        for rate in (0.5, 1.0, 2.0, 3.0):
            app.settings.rate = rate
            span = app._visible_span_ms()
            self.assertAlmostEqual(span, app.settings.scroll_ms * rate, places=6)
            for note in notes:
                head = app._head_ms(note)
                delta = head - now
                z = app._note_z(head, now)
                if delta > span + 1e-6:                     # 远于可视范围
                    self.assertFalse(
                        app._note_ahead(note, now),
                        f"rate={rate}：远于可视范围的物件必须剔除（否则会贴在起点堆积）",
                    )
                else:                                       # 可视范围内
                    self.assertTrue(app._note_ahead(note, now))
                    self.assertLessEqual(z, START_Z + 1e-9, "不能画到轨道尽头之外")
                    if delta >= 0.0:
                        self.assertAlmostEqual(
                            z, START_Z * (delta / span), places=6,
                            msg="可视范围内必须按实际进度摆放（高倍速也要从尽头开始）",
                        )
                    else:
                        self.assertAlmostEqual(z, 0.0, places=6, msg="过去的物件贴在判定线上")
        app.settings.rate = 1.0

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
        """滑键 / 鼠标点击按键改用预设贴图：主体、箭头、范围、菱形、边界各就各位。"""
        from rhythm_editor.game.app import MOUSE_PLANE_HEIGHT, MouseClick, MouseSlide

        app = self.app
        app._enter_loading()
        app._start_play()
        self.assertTrue(app.textures, "加载阶段要读入预设贴图")
        chart = app.chart
        slide = next(n for n in chart.all_notes() if isinstance(n, MouseSlide))
        used = []
        original_blit = app._blit_tex
        original_quad_blit = app._blit_tex_quad
        app._blit_tex = lambda name, rect, flip_x=False, alpha=255: (
            used.append((name, flip_x)),
            original_blit(name, rect, flip_x, alpha),
        )[1]
        app._blit_tex_quad = lambda name, corners, flip_x=False: (
            used.append((name, flip_x)),
            original_quad_blit(name, corners, flip_x),
        )[1]
        try:
            app._draw_pattern(slide, app._head_ms(slide))
        finally:
            app._blit_tex = original_blit
            app._blit_tex_quad = original_quad_blit
        names = [item[0] for item in used]
        expected_body = "slide_body_right" if slide.direction >= 0 else "slide_body_left"
        self.assertIn(expected_body, names, "滑键主体用贴图")
        other = "slide_body_left" if slide.direction >= 0 else "slide_body_right"
        self.assertNotIn(other, names, "方向不同用不同的主体贴图")

        click = next(n for n in chart.all_notes() if isinstance(n, MouseClick))
        used = []
        app._blit_tex = lambda name, rect, flip_x=False, alpha=255: (
            used.append((name, flip_x)),
            original_blit(name, rect, flip_x, alpha),
        )[1]
        app._blit_tex_quad = lambda name, corners, flip_x=False: (
            used.append((name, flip_x)),
            original_quad_blit(name, corners, flip_x),
        )[1]
        try:
            app._draw_pattern(click, app._head_ms(click))
        finally:
            app._blit_tex = original_blit
            app._blit_tex_quad = original_quad_blit
        names = [item[0] for item in used]
        self.assertIn("click_range", names, "范围标识用贴图")
        self.assertIn("click_diamond", names, "位置标识用贴图")
        self.assertEqual(names.count("click_edge"), 2, "左右边界各一张（右侧镜像）")
        self.assertTrue(any(flip for name, flip in used if name == "click_edge"), "右边界用镜像")

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
        """结算：最大连击写成 x / 总判定数，倍速放在它后面。"""
        app = self.app
        app.settings.rate = 1.5
        app._enter_loading()
        app._start_play()
        app.judge._judge_result("X", "EXACT", 0.0)
        app._finish()
        combo_line = app._result_combo_line()
        self.assertIn("/", combo_line)
        self.assertIn(str(app.judge.total_notes), combo_line)
        rate_line = app._result_rate_line()
        self.assertIn("倍速", rate_line)
        self.assertIn("1.5x", rate_line)
        self.assertNotIn("×", rate_line)
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

    def test_flow_speed_is_independent_of_rate(self) -> None:
        """流速（可视毫秒）不受倍速影响：物件的**视觉移动速度**始终一致。"""
        from rhythm_editor.game.scene import START_Z

        app = self.app
        scroll = app.settings.scroll_ms          # 流速 1.0 -> 1500ms
        for rate in (1.0, 0.5, 0.1, 3.0):
            app.settings.rate = rate
            self.assertAlmostEqual(
                app._visible_span_ms(), scroll * rate, places=6, msg="可视跨度按倍速缩放"
            )
            # 走完整个可视跨度需要同样的**真实时间**：跳 z=START_Z 时正好是 span 之前
            self.assertAlmostEqual(app._note_z(scroll * rate, 0.0), START_Z, places=6)
            self.assertAlmostEqual(app._note_z(scroll * rate / 2, 0.0), START_Z / 2, places=6)
            self.assertAlmostEqual(app._note_z(0.0, 0.0), 0.0, places=6)
        app.settings.rate = 1.0
        self.assertAlmostEqual(app._visible_span_ms(), scroll, places=6)

    def test_mouse_click_accepts_left_and_right_only(self) -> None:
        """鼠标点击按键左右键都能判，滚轮（4/5）不算。"""
        from rhythm_editor.game.judge import Judge
        from rhythm_editor.model.notes import MouseClick

        app = self.app
        app._enter_loading()
        chart = app.chart
        app._start_play()
        click = next(n for n in chart.all_notes() if isinstance(n, MouseClick))
        pygame = self.pygame
        for button in (1, 3):
            app.judge = Judge(chart)                     # 每个键位用干净状态各测一次
            app._now_ms = lambda: chart.timing.beat_to_song_ms(click.head_beat)
            app.mouse_norm = click.center
            app._event_play(
                pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=button, pos=(400, 300))
            )
            self.assertEqual(app.judge.judged_notes, 1, f"button {button} 应该能判")
        app.judge = Judge(chart)
        app._event_play(
            pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=4, pos=(400, 300))
        )
        self.assertEqual(app.judge.judged_notes, 0, "滚轮不该判定")

    def test_software_region_is_flat_and_clipped_to_visible_span(self) -> None:
        """软件渲染的空域：只画可见段（不会堆在平面远端），而且是同一种颜色（无横条纹）。"""
        from rhythm_editor.game.app import MOUSE_PLANE_HEIGHT
        from rhythm_editor.game.scene import START_Z
        from rhythm_editor.model.notes import BoundaryCurve, MouseRegion

        app = self.app
        app._enter_loading()
        app._start_play()
        app.settings.rate = 0.4
        app.settings.speed = 2.6
        region = MouseRegion("R1", 0.0, 8.0, BoundaryCurve(0.1, 0.4),
                             BoundaryCurve(0.9, 0.6))
        app.chart.mouse_notes = [region]
        app.chart.invalidate_order()
        fills = []
        original = app._alpha_quad
        app._alpha_quad = lambda points, color, alpha: fills.append(
            (list(points), color, alpha))
        try:
            app._draw_region(region, app.chart.timing.beat_to_song_ms(4.0))
        finally:
            app._alpha_quad = original
        area = [entry for entry in fills if tuple(entry[1]) == (90, 150, 235)]
        self.assertEqual(len(area), 1, "空域底只铺一次：不再画一条条横向条纹")
        points, _color, _alpha = area[0]
        ys = [point[1] for point in points]
        near_y = app._proj(0.0, MOUSE_PLANE_HEIGHT, 0.0)[1]
        far_y = app._proj(0.0, MOUSE_PLANE_HEIGHT, START_Z)[1]
        self.assertAlmostEqual(max(ys), near_y, delta=6.0, msg="最近的边贴在判定线一侧")
        self.assertLessEqual(min(ys), far_y + 8.0,
                             msg="最远的边就在轨道尽头，不会堆到平面外")

    def test_software_slide_left_keeps_wide_side_on_screen_left(self) -> None:
        """软件路径：贴图左边界绑屏幕左边 —— 左滑键的宽端必须在屏幕左侧。"""
        import numpy as np

        from rhythm_editor.model.notes import MouseSlide

        pygame = self.pygame
        app = self.app
        app._enter_loading()
        app._start_play()
        app.scene3d = None                       # 强制走软件绘制
        app.settings.rate, app.settings.speed = 1.0, 5.0
        app.settings.slide_thickness = 0.3

        def shot():
            app.screen.fill((10, 10, 14))
            app._draw_planes(0.0)
            app._draw_notes(0.0)
            return np.transpose(pygame.surfarray.array3d(app.screen), (1, 0, 2)).astype(int)

        blank = shot()
        for note, side in ((MouseSlide("MR", 0.0, 0.2, 0.8), "right"),
                           (MouseSlide("ML", 0.0, 0.8, 0.2), "left")):
            app.chart.mouse_notes = [note]
            app.chart.invalidate_order()
            frame = shot()
            diff = np.abs(frame - blank).sum(axis=2)
            cols = np.flatnonzero(diff.any(axis=0))
            heights = diff[:, cols].sum(axis=0)
            thickest = int(cols[int(np.argmax(heights))])
            middle = (int(cols.min()) + int(cols.max())) // 2
            if side == "right":
                self.assertGreater(thickest, middle, "右滑键的宽端应在屏幕右侧")
            else:
                self.assertLess(thickest, middle, "左滑键的宽端应在屏幕左侧")

    def test_mouse_plane_has_blue_gradient_and_no_side_lines(self) -> None:
        """鼠标平面：不画边线；靠近判定线略蓝，远端保留很淡的一层（不彻底消失）。"""
        from rhythm_editor.game.app import MOUSE_PLANE_HEIGHT
        from rhythm_editor.game.scene import START_Z

        app = self.app
        app._enter_loading()
        app._start_play()
        app.settings.rate = 1.0
        quads = []
        lines = []
        fills = []
        original_quad, original_line = app._quad, app._line
        original_alpha = app._alpha_quad
        app._quad = lambda points, color: quads.append(points)
        app._line = lambda a, b, color, width=2: lines.append((a, b, color))
        app._alpha_quad = lambda points, color, alpha: fills.append((list(points), alpha, color))
        try:
            app._draw_planes(0.0)
        finally:
            app._quad, app._line = original_quad, original_line
            app._alpha_quad = original_alpha
        mouse_lines = [entry for entry in lines if entry[2] == (90, 220, 255)]
        # 原来的 6K 平面边线也算同色，这里只要求鼠标平面“从起点到判定线”的那两条消失
        # 蓝底渐变现在是「一维渐变 + 梯形掩码」烘好的一整张，直接量它的 alpha 坡
        overlay = app._mouse_plane_overlay
        self.assertIsNotNone(overlay, "鼠标平面要有一层渐变蓝底")
        surface, rect = overlay
        middle_x = rect.width // 2
        near = surface.get_at((middle_x, rect.height - 3))
        middle = surface.get_at((middle_x, rect.height // 2))
        far = surface.get_at((middle_x, 2))
        self.assertEqual(tuple(near)[:3], (58, 108, 200))
        self.assertGreater(near[3], middle[3], "靠近判定线更实、远处更透")
        self.assertGreater(middle[3], far[3], "越远越透")
        self.assertLessEqual(near[3], 100, "只是『略微不透明』")
        # 远端保留一点底透明度（26/255），否则在黑底上渐隐到看不见，
        # 看上去像背景只画到 6K 轨道远端那一行
        self.assertLess(far[3], 40, "轨道尽头要有很淡的一层，而不是完全消失")
        self.assertGreater(far[3], 4, "轨道尽头仍应看得到一点点蓝")
        # 贴着判定线的那一端就在判定线上，另一端一路铺到轨道尽头
        mouse_line_y = app._proj(0.0, MOUSE_PLANE_HEIGHT, 0.0)[1]
        self.assertAlmostEqual(rect.bottom, mouse_line_y, delta=4.0, msg="渐变贴着判定线")
        self.assertLess(rect.top, mouse_line_y - 100, "渐变一路铺到远处")
        self.assertGreaterEqual(START_Z, 12.0, "轨道比原来更长")
        # 6K 平面底色仍然只有一个四边形
        # 贴图版轨道不再画底色四边形（缺贴图时才会退回程序绘制）
        self.assertLessEqual(len(quads), 1)
        self.assertFalse(
            any(
                abs(a[1] - app._proj(0.0, MOUSE_PLANE_HEIGHT, START_Z)[1]) < 30
                for a, _b, _c in mouse_lines
            ),
            "鼠标平面不再画边线",
        )

    def test_side_lane_highlight_is_under_the_mouse_plane(self) -> None:
        """图层顺序：侧轨高亮属于 6K 平面，必须在鼠标平面（蓝底渐变）**下面**。"""
        from rhythm_editor.game.app import LANE_PRESS_TINT, SIDE_TINT

        app = self.app
        app._enter_loading()
        app._start_play()
        app.settings.side_lane_highlight = True
        app.pressed_lanes = {2}
        order = []
        original_quad, original_alpha = app._quad, app._alpha_quad
        original_gradient = app._draw_mouse_plane_gradient
        app._quad = lambda points, color: order.append(("quad", color))
        app._alpha_quad = lambda points, color, alpha: order.append(("gradient", color))
        app._draw_mouse_plane_gradient = lambda: order.append(("mouse_plane", None))
        try:
            app._draw_planes(0.0)
        finally:
            app._quad, app._alpha_quad = original_quad, original_alpha
            app._draw_mouse_plane_gradient = original_gradient
        # 只认鼠标平面那层蓝底渐变（轨道贴图填充也是 _alpha_quad，颜色不同）
        first_gradient = next(
            index
            for index, entry in enumerate(order)
            if entry[0] == "mouse_plane"
        )
        highlight_indexes = [
            index
            for index, entry in enumerate(order)
            if entry == ("quad", SIDE_TINT)
        ]
        press_colors = {
            app.texture_avg.get(f"lane_{lane}_pressed", ())[:3] for lane in range(1, 7)
        } | {LANE_PRESS_TINT}
        press_indexes = [
            index
            for index, entry in enumerate(order)
            if entry[1] in press_colors
        ]
        self.assertEqual(len(highlight_indexes), 2, "第 1 / 第 6 轨各一块高亮")
        for index in highlight_indexes + press_indexes:
            self.assertLess(index, first_gradient, "侧轨 / 按下高亮要在鼠标平面下方")

    def test_slide_judgement_shows_text_and_sound(self) -> None:
        """滑键判定要出判定文字（回归：以前 on_move 的返回值被丢掉，滑键没有飘字）。"""
        from rhythm_editor.model.notes import MouseSlide

        app = self.app
        app._enter_loading()
        chart = app.chart
        app._start_play()
        slide = next(n for n in chart.all_notes() if isinstance(n, MouseSlide))
        hit_ms = chart.timing.beat_to_song_ms(slide.beat)
        app._now_ms = lambda: hit_ms
        lo, hi = slide.x_range()
        outside = max(0.0, min(lo, hi) - 0.06)
        inside = (min(lo, hi) + max(lo, hi)) / 2.0
        app._mouse_norm = lambda: outside
        plays = []
        original = app.hits.play_hit
        app.hits.play_hit = lambda: plays.append(1)
        try:
            app._update(1 / 120)             # 先让 judge 采一次鼠标位置
            app.popups.clear()               # 前面那些“漏掉”的 Break 飘字不算
            plays.clear()
            # 鼠标移进滑键范围 -> 触发滑键判定
            app._mouse_norm = lambda: inside
            app._update(1 / 120)
        finally:
            app.hits.play_hit = original
        self.assertTrue(app.popups, "滑键判定要有飘字")
        grade, text, _left, x, y = app.popups[-1]
        self.assertEqual(grade, text)
        self.assertIn(grade, ("EXACT", "Exact", "Near"))
        self.assertGreater(y, 0.0)
        self.assertEqual(len(plays), 1, "滑键命中也应该响打击音")


    def test_target_fps_choices_follow_window_monitor(self) -> None:
        """期望帧率：档位跟随**窗口所在屏幕**的刷新率（多屏刷新率不同也要对）。"""
        app = self.app
        app._display_refresh = lambda: 144          # 假装窗口在一块 144Hz 屏上
        self.assertEqual(app._fps_choices(), (144, 120, 60, 30))
        app.settings.target_fps = 0                 # 未设置 = 跟随屏幕刷新率
        self.assertEqual(app._target_fps(), 144)
        self.assertIn("144", app._setting_value_text("target_fps"))
        app.settings_row = app._row_index("target_fps")
        app._adjust_setting(1)                      # 144 -> 120
        self.assertEqual(app.settings.target_fps, 120)
        self.assertEqual(app._target_fps(), 120)
        app.settings.target_fps = 200               # 换屏后旧档位不合法：取不超过它的最大档
        self.assertEqual(app._target_fps(), 144)
        app.settings.target_fps = 20
        self.assertEqual(app._target_fps(), 30, "低于最低档时取最低档")
        app._display_refresh = lambda: 60           # 换到 60Hz 屏：240/120 档位消失
        self.assertEqual(app._fps_choices(), (60, 30))
        app.settings.target_fps = 120
        self.assertEqual(app._target_fps(), 60)

    def test_play_hud_shows_live_fps(self) -> None:
        app = self.app
        app._enter_loading()
        app._start_play()
        app.fps_smooth = 123.0
        seen = []
        original = app.text
        app.text = lambda content, pos, font=None, color=None: seen.append(content)
        try:
            app._draw_hud(0.0)
        finally:
            app.text = original
        self.assertTrue(any("FPS" in text for text in seen), "游玩界面要显示实时帧率")

    def test_texture_averages_ready_after_loading(self) -> None:
        """加载页就要算好贴图平均色。

        软件路径的长按主体 / 轨道 / 边界都用平均色填多边形；以前只在"渲染回退"时才算，
        正常进游戏时是空的，于是长按走了"切片重复贴图"的兜底分支，看起来是锯齿状。
        """
        app = self.app
        app._load_textures()
        for key in ("region_area", "hold_body_1", "hold_body_6",
                    "lane_1_default", "lane_6_pressed"):
            self.assertIsNotNone(app.texture_avg.get(key), f"{key} 的平均色缺失")

    def test_mouse_plane_far_edge_is_above_k6(self) -> None:
        """鼠标平面远端应在 6K 轨道远端**之上**（它更靠近相机，同一个 z 投影更高）。"""
        from rhythm_editor.game.app import MOUSE_PLANE_HEIGHT
        from rhythm_editor.game.scene import START_Z

        app = self.app
        app._enter_loading()
        app._start_play()
        row_k6 = app._proj(0.0, 0.0, START_Z)[1]
        row_mouse = app._proj(0.0, MOUSE_PLANE_HEIGHT, START_Z)[1]
        self.assertLess(row_mouse, row_k6, "鼠标平面远端应在 6K 轨道远端之上")
        self.assertLess(row_k6 - row_mouse, 400.0, "两个平面不应差得太远")


if __name__ == "__main__":
    unittest.main()
