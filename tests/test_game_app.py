"""游玩器 GUI 关键流程（headless：SDL dummy 驱动）。"""

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


class PlayerAppTests(unittest.TestCase):
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

    def test_settings_are_saved_globally(self) -> None:
        app = self.app
        app.settings.player_name = "测试玩家"
        psettings.save_settings(app.settings)
        self.assertEqual(psettings.load_settings().player_name, "测试玩家")

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

    def test_event_song_ms_uses_event_timestamp(self) -> None:
        """输入事件按自身时间戳换算歌曲时间，而不是处理它的那一帧。"""
        app = self.app
        app._enter_loading()
        app._start_play()
        pygame = self.pygame
        now = 1234.0
        app._now_ms = lambda: now

        stamp = pygame.time.get_ticks() - 100
        event = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_a, timestamp=stamp)
        self.assertAlmostEqual(
            app._event_song_ms(event), now - 100.0 * app.settings.rate, delta=15.0
        )
        # 没有时间戳（测试里手工造的事件 / 老版本 pygame）时退化为当前帧
        plain = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_a)
        self.assertAlmostEqual(app._event_song_ms(plain), now, places=6)
        # 暂停时歌曲时间冻结，不往前回推
        app.paused = True
        self.assertAlmostEqual(app._event_song_ms(event), now, places=6)

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

    def test_reverse_notes_draw_in_front_of_the_judgement_line(self) -> None:
        """反向流速：物件走到判定线之前（更靠近镜头）仍要画，超出相机可视下界才剔除。

        回归：以前 z 被夹在 0（反向物件贴在判定线上），或按固定余量判断可见性。
        """
        from rhythm_editor.game.scene import MOUSE_PLANE_HEIGHT
        from rhythm_editor.model.notes import LaneNote, MouseClick
        from rhythm_editor.model.speed import SpeedSegment
        from rhythm_editor.model.timing import BpmSegment, TimingMap

        app = self.app
        app._enter_loading()
        app._start_play()
        app.settings.speed, app.settings.rate = 1.0, 1.0
        app.chart.timing = TimingMap([BpmSegment(0.0, 120.0, 4)])
        rev = app.chart.add_group("rev", [SpeedSegment(0.0, -1.0)])
        app.chart.mouse_notes = [MouseClick("M", 6.0, 0.5, 0.2, group=rev.id)]
        app.chart.lane_notes = [LaneNote("K", 1, 1, 6.0, group=rev.id)]
        app.chart.invalidate_order()
        judge = app.chart.timing.beat_to_song_ms(6.0)
        z_near_k6 = app._plane_z_near(0.0)
        z_near_mouse = app._plane_z_near(MOUSE_PLANE_HEIGHT)
        self.assertLess(z_near_k6, 0.0, "6K 可视下界在判定平面之前")
        self.assertLess(z_near_mouse, z_near_k6, "鼠标平面能更靠近镜头")

        near = judge - 2.0                       # 判定线之前 2ms
        z = app._note_z(judge, near, group=rev)
        self.assertLess(z, 0.0, "反向物件应当走到判定线之前")
        self.assertGreater(z, z_near_k6, "还在可视范围内")
        self.assertAlmostEqual(
            app._note_z(judge, near, group=rev), z, places=9, msg="鼠标物件同速"
        )
        far = judge - 100.0                      # 掉出画面
        self.assertLess(app._note_z(judge, far, group=rev), z_near_k6)
        self.assertLess(app._note_z(judge, far, group=rev), z_near_mouse)

        # 3D 路径：反向的 6K 按键 / 鼠标图案卡片中心也是负 z（画在判定线之前）
        class StubScene:
            def __init__(self) -> None:
                self.quads: list = []

            def quad(self, name, x, z, sx, sz, y, *args, **kwargs) -> None:
                self.quads.append((name, float(z), float(sz)))

            def __getattr__(self, item):
                return lambda *args, **kwargs: None

        scene = StubScene()
        original = app.scene3d
        app.scene3d = scene  # type: ignore[assignment]
        try:
            app._render_scene3d(near)
        finally:
            app.scene3d = original
        tap = [(z_value, size) for name, z_value, size in scene.quads if name.startswith("tap_")]
        click = [z_value for name, z_value, _size in scene.quads if name == "click_range"]
        self.assertTrue(tap, "3D 路径应当画出反向按键")
        self.assertTrue(click, "3D 路径应当画出反向点击图案")
        # 单键卡片的近端边（判定边）= 中心 − 厚度/2，应当落在判定线之前
        self.assertLess(tap[0][0] - tap[0][1] / 2.0, 0.0, "反向按键的近端边在判定线之前")
        self.assertLess(click[0], 0.0, "反向点击图案画在判定线之前")

    def test_region_head_beyond_span_still_draws_camera_side_part(self) -> None:
        """反向空域：开头还在可视跨度之外，但后半段已经跑到判定线靠近摄像机的一侧。

        回归：以前只按物件**开头**的滚动距离剔除，整段区域被丢掉，
        摄像机一侧的那一段（确实应该可见）也一起消失了。
        """
        from rhythm_editor.game import frame as game_frame
        from rhythm_editor.game.scene import MOUSE_PLANE_HEIGHT
        from rhythm_editor.model.notes import BoundaryCurve, MouseRegion
        from rhythm_editor.model.speed import SpeedSegment
        from rhythm_editor.model.timing import BpmSegment, TimingMap

        app = self.app
        app._enter_loading()
        app._start_play()
        app.settings.speed, app.settings.rate = 1.0, 1.0
        app.chart.timing = TimingMap([BpmSegment(0.0, 120.0, 4)])       # 每拍 500ms
        group = app.chart.add_group("rev", [
            SpeedSegment(0.0, 1.0),            # 先正常下落
            SpeedSegment(6.0, -1.5),           # 区域开头处反向
            SpeedSegment(10.0, 1.0),
        ])
        region = MouseRegion(
            "R", 6.0, 10.0, BoundaryCurve(0.2, 0.2), BoundaryCurve(0.8, 0.8),
            group=group.id,
        )
        app.chart.lane_notes = []
        app.chart.mouse_notes = [region]
        app.chart.invalidate_order()
        now = app.chart.timing.beat_to_song_ms(2.0)      # 区域开头前 4 拍
        span = app._visible_span_ms()
        self.assertGreater(
            game_frame.note_scroll_ms(app.chart, region, now), span,
            "开头应当还在可视跨度之外",
        )
        visible = [n.id for n in game_frame.visible_notes(app.chart, now, span)]
        self.assertIn("R", visible, "整段区域不能因为开头太远就被剔除")
        runs = game_frame.region_samples(
            app.chart, region, now, span,
            near_ms=app._plane_near_distance_ms(MOUSE_PLANE_HEIGHT, span),
        )
        self.assertTrue(runs, "摄像机一侧的那一段应当被画出来")
        distances = [distance for run in runs for _beat, distance in run]
        self.assertLess(min(distances), 0.0, "包含判定线靠近摄像机一侧的部分")
        self.assertLessEqual(max(distances), span + 1e-6)

    def test_hold_and_region_hide_the_elapsed_part(self) -> None:
        """长按 / 空域：判定没结束也只画「当前时刻之后」的部分（已判过的不画）。"""
        from rhythm_editor.game import frame as game_frame
        from rhythm_editor.model.notes import BoundaryCurve, LaneNote, MouseRegion
        from rhythm_editor.model.timing import BpmSegment, TimingMap

        app = self.app
        app._enter_loading()
        app._start_play()
        app.settings.speed, app.settings.rate = 1.0, 1.0
        app.chart.timing = TimingMap([BpmSegment(0.0, 120.0, 4)])
        hold = LaneNote("H", 1, 1, 6.0, 8.0)
        region = MouseRegion("R", 6.0, 8.0, BoundaryCurve(0.2, 0.2), BoundaryCurve(0.8, 0.8))
        app.chart.lane_notes = [hold]
        app.chart.mouse_notes = [region]
        app.chart.invalidate_order()
        span = app._visible_span_ms()
        during = app.chart.timing.beat_to_song_ms(7.0)          # 判定过半
        hold_span = game_frame.note_z_span(app.chart, hold, during, span)
        self.assertIsNotNone(hold_span)
        self.assertAlmostEqual(hold_span[0], 0.0, places=6, msg="长按从判定线画起")
        self.assertGreater(hold_span[1], 0.0, "长按剩下的那截还在前面")
        beats = [b for run in game_frame.region_samples(app.chart, region, during, span)
                 for b, _d in run]
        self.assertTrue(beats)
        self.assertGreaterEqual(min(beats), 7.0 - 1e-6, "空域不采样已经过去的部分")
        # 判定线高亮只在空域真的压在线上时画
        before = app.chart.timing.beat_to_song_ms(5.0)
        self.assertFalse(game_frame.region_crosses_line(app.chart, region, before, span))
        self.assertTrue(game_frame.region_crosses_line(app.chart, region, during, span))
        after = app.chart.timing.beat_to_song_ms(9.0)
        self.assertFalse(game_frame.region_crosses_line(app.chart, region, after, span))

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

    def test_software_slide_arrow_direction_and_placement(self) -> None:
        """箭头渲染路径（临时可见贴图验证）：贴终点内侧、朝滑动方向、在平面上方。

        正式箭头美术尚未实装（贴图全透明），这里在内存里换上一张可见箭头，
        把渲染路径的几何与朝向锁住。
        """
        import numpy as np

        from rhythm_editor.game import frame as game_frame
        from rhythm_editor.game.scene import MOUSE_PLANE_HEIGHT
        from rhythm_editor.model.notes import MouseSlide

        pygame = self.pygame
        app = self.app
        app._enter_loading()
        app._start_play()
        app._load_textures()

        def arrow_surface(direction: int, size: int = 48):
            """两倍高、上半是洋红三角箭头（尖朝 direction）；正式美术的占位替身。"""
            xs = np.linspace(-1.0, 1.0, size, dtype=np.float32)[None, :]
            ys = np.linspace(-1.0, 1.0, size, dtype=np.float32)[:, None]
            if direction < 0:
                xs = -xs
            inside = np.abs(ys) <= (1.0 - xs) / 2.0
            rgba = np.zeros((size * 2, size, 4), dtype=np.uint8)
            rgba[:size, ..., 0] = 255
            rgba[:size, ..., 2] = 255
            rgba[:size, ..., 3] = np.where(inside, 255, 0)
            return pygame.image.frombuffer(rgba.tobytes(), (size, size * 2), "RGBA")

        app.textures["slide_arrow_right"] = arrow_surface(1).convert_alpha()
        app.textures["slide_arrow_left"] = arrow_surface(-1).convert_alpha()
        app.scene3d = None                       # 强制软件绘制
        app.settings.speed = 5.0
        app.settings.slide_thickness = 0.12
        now = app.chart.timing.beat_to_song_ms(0.0)
        plane_y = app._proj(0.0, MOUSE_PLANE_HEIGHT, 0.0)[1]
        for start, end, direction in ((0.2, 0.8, 1), (0.8, 0.2, -1)):
            app.chart.mouse_notes = [MouseSlide("S", 0.0, start, end)]
            app.chart.invalidate_order()
            app.screen.fill((10, 10, 14))
            app._draw_planes(now)
            app._draw_notes(now)
            arr = np.transpose(pygame.surfarray.array3d(app.screen), (1, 0, 2)).astype(int)
            arrow = (arr[:, :, 0] > 180) & (arr[:, :, 2] > 180) & (arr[:, :, 1] < 120)
            cols = np.flatnonzero(arrow.any(axis=0))
            rows = np.flatnonzero(arrow.any(axis=1))
            self.assertTrue(cols.size, f"应当画出箭头（direction={direction}）")
            widths = arrow.sum(axis=0)
            left_h = float(widths[cols.min():cols.min() + 3].mean())
            right_h = float(widths[cols.max() - 2:cols.max() + 1].mean())
            if direction > 0:
                self.assertGreater(left_h, right_h, "右滑键的箭头应当朝右")
            else:
                self.assertLess(left_h, right_h, "左滑键的箭头应当朝左")
            self.assertLess(int(rows.max()), plane_y + 1, "箭头应当整条在平面上方")
            end_x = app._proj(-0.5 + end, MOUSE_PLANE_HEIGHT, 0.0)[0]
            tip_x = cols.max() if direction > 0 else cols.min()
            self.assertLessEqual(
                abs(int(tip_x) - end_x), 8.0, "箭头尖端应当落在滑动终点处（内侧）"
            )


if __name__ == "__main__":
    unittest.main()
