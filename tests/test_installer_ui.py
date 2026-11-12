"""The setup and uninstall windows' look: the logo drawing and its animations,
the language and theme buttons, and the steps of the new wizard."""
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("APPDATA", os.path.join(tempfile.gettempdir(), "FlowBench-installer-tests"))

from PySide6.QtCore import QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QImage, QPainter, QRegion
from PySide6.QtWidgets import QApplication, QWidget
from qfluentwidgets import Theme, isDarkTheme, qconfig, setTheme

from flowbench_installer import core, gui, visuals, winplat
from flowbench_installer.gui import SetupWindow
from flowbench_installer.session import SetupSession
from flowbench_installer.uninstall_gui import UninstallWindow
from tests.test_installer import FakePlatform, build_payload, wait_for

ROOT = Path(__file__).resolve().parent.parent
CJK = re.compile(r"[　-〿一-鿿＀-￯]")


def app():
    return QApplication.instance() or QApplication([])


def render(widget) -> QImage:
    """The widget alone, on a transparent background."""
    image = QImage(widget.size(), QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    widget.render(painter, QPoint(0, 0), QRegion(), QWidget.RenderFlag.DrawChildren)
    painter.end()
    return image


def alpha(image: QImage, x: float, y: float) -> int:
    return (image.pixel(int(x), int(y)) >> 24) & 0xFF


def interior_point(path, from_right=False) -> QPointF:
    """A point well inside a ribbon (in logo units); ``from_right`` picks
    one near its tip, the last part an intro sweep reaches."""
    box = path.boundingRect()
    xs = [box.left() + 10 + 10 * i for i in range(int(box.width() / 10) - 1)]
    for x in (reversed(xs) if from_right else xs):
        y = box.top() + 10
        while y < box.bottom():
            if all(path.contains(QPointF(x + dx, y + dy))
                   for dx, dy in ((0, 0), (30, 0), (-30, 0), (0, 30), (0, -30))):
                return QPointF(x, y)
            y += 10
    raise AssertionError("no interior point")


def to_widget(logo, point: QPointF) -> QPointF:
    rect = logo.logo_rect()
    scale = rect.height() / 1000.0
    return QPointF(rect.left() + point.x() * scale, rect.top() + point.y() * scale)


def window_texts(win):
    """Every text a user can read in the window, hidden pages included."""
    texts = [win.windowTitle()]
    for widget in win.findChildren(QWidget):
        if widget is win.titleBar.languageButton:
            continue                  # "语言 · Language" on purpose
        for getter in ("text", "toolTip", "accessibleName", "placeholderText"):
            value = getattr(widget, getter, None)
            if callable(value):
                try:
                    value = value()
                except TypeError:
                    continue
                if isinstance(value, str) and value:
                    texts.append(value)
    return texts


class Clock:
    """Stands in for visuals.now so a test can render a given moment."""

    def __init__(self, start=1000.0):
        self.t = start

    def __call__(self):
        return self.t


class LogoDrawingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app()

    def test_ribbons_are_closed_shapes_inside_the_logo(self):
        art = visuals.LogoArt()
        for name in visuals.RIBBONS:
            with self.subTest(ribbon=name):
                points = visuals._RIBBON_POINTS[name]
                self.assertEqual(len(points) % 2, 0)
                self.assertGreater(len(points), 40)
                box = art.paths[name].boundingRect()
                self.assertGreaterEqual(box.left(), -1)
                self.assertGreaterEqual(box.top(), -1)
                self.assertLessEqual(box.right(), visuals.LOGO_WIDTH + 1)
                self.assertLessEqual(box.bottom(), 1001)
                start, end = art.sweeps[name]
                self.assertLess(start.x(), end.x())      # stem (left) to tip (right)
                self.assertGreater(start.y(), end.y())   # bottom to top
        # The F: the top band is on top, the leaf at the bottom left.
        self.assertLess(art.paths["top"].boundingRect().top(), 5)
        self.assertGreater(art.paths["leaf"].boundingRect().bottom(), 990)
        self.assertLess(art.paths["leaf"].boundingRect().right(), 400)

    def test_png_crop_finds_the_logo(self):
        image = QImage(str(ROOT / "app_logo.png"))
        box = visuals._alpha_bounds(image)
        for got, expected in zip((box.left(), box.top(), box.width(), box.height()),
                                 (249, 168, 883, 998)):
            self.assertAlmostEqual(got, expected, delta=2)
        empty = QImage(8, 8, QImage.Format_ARGB32)
        empty.fill(Qt.transparent)
        self.assertIsNone(visuals._alpha_bounds(empty))

    def test_vector_logo_matches_the_png(self):
        """The traced shapes are the logo: >95 % overlap with the PNG at 200 px."""
        for dark, png in ((False, "app_logo.png"), (True, "app_logo_dark.png")):
            with self.subTest(png=png):
                from_png = visuals.LogoArt(ROOT / "app_logo.png", ROOT / "app_logo_dark.png")
                vector = visuals.LogoArt()
                a = from_png.image(200, 1.0, dark)
                b = vector.image(200, 1.0, dark)
                self.assertEqual(a.size(), b.size())
                both = either = 0
                for y in range(0, a.height(), 2):
                    for x in range(0, a.width(), 2):
                        in_a, in_b = alpha(a, x, y) > 127, alpha(b, x, y) > 127
                        both += in_a and in_b
                        either += in_a or in_b
                self.assertGreater(both / either, 0.95)

    def test_missing_png_falls_back_to_the_vector_logo(self):
        art = visuals.LogoArt(ROOT / "no-such-logo.png", None)
        self.assertFalse(art.uses_png)
        image = art.image(100)
        point = interior_point(art.paths["band"])
        self.assertGreater(alpha(image, point.x() / 10, point.y() / 10), 200)


class AnimatedLogoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app()

    def setUp(self):
        self.clock = Clock()
        patcher = mock.patch.object(visuals, "now", self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.art = visuals.LogoArt(ROOT / "app_logo.png", ROOT / "app_logo_dark.png")
        self.centres = {name: interior_point(path) for name, path in self.art.paths.items()}

    def logo(self, motion=True, canvas=(360, 150)):
        from PySide6.QtCore import QSize
        widget = visuals.AnimatedLogo(self.art, 100, QSize(*canvas), motion)
        self.addCleanup(widget.deleteLater)
        return widget

    def coverage(self, logo):
        image = render(logo)
        return {name: alpha(image, *_xy(to_widget(logo, point)))
                for name, point in self.centres.items()}

    def test_intro_forms_the_logo_from_the_bottom_up(self):
        logo = self.logo()
        logo.play_intro(0.0)
        start = self.clock.t
        self.clock.t = start + 0.05
        first = self.coverage(logo)
        self.assertTrue(all(value < 40 for value in first.values()), first)
        self.clock.t = start + 0.85             # the leaf is in, the top band's tip is not yet
        middle = self.coverage(logo)
        tip = interior_point(self.art.paths["top"], from_right=True)
        image = render(logo)
        self.assertGreater(middle["leaf"], 200, middle)
        self.assertLess(alpha(image, *_xy(to_widget(logo, tip))), 60)
        self.clock.t = start + visuals.INTRO_END + 0.1
        done = self.coverage(logo)
        self.assertTrue(logo.intro_done())
        self.assertTrue(all(value > 240 for value in done.values()), done)

    def test_without_motion_the_logo_is_whole_at_once(self):
        logo = self.logo(motion=False)
        logo.play_intro(0.0)
        self.assertTrue(logo.intro_done())
        self.assertFalse(logo.is_animating())
        self.assertTrue(all(value > 240 for value in self.coverage(logo).values()))

    def test_liquid_fill_follows_progress(self):
        logo = self.logo()
        logo.set_liquid("fill", 0.5, immediate=True)
        half = self.coverage(logo)
        self.assertGreater(half["leaf"], 200, half)     # below the surface
        self.assertLess(half["top"], 110, half)         # only the faint outline
        logo.set_liquid("fill", 1.0, immediate=True)
        self.assertTrue(all(value > 240 for value in self.coverage(logo).values()))
        logo.set_liquid("drain", 0.9, immediate=True)    # uninstall: 90 % gone
        self.assertAlmostEqual(logo.level(), 0.1)
        drained = self.coverage(logo)
        self.assertLess(drained["top"], 110, drained)
        self.assertLess(drained["band"], 110, drained)

    def test_liquid_rises_smoothly_to_a_new_value(self):
        logo = self.logo()
        logo.set_liquid("fill", 0.0, immediate=True)
        logo.set_liquid("fill", 0.8)
        render(logo)
        self.clock.t += 0.1
        render(logo)
        self.assertGreater(logo.level(), 0.0)
        self.assertLess(logo.level(), 0.8)
        for _ in range(30):
            self.clock.t += 0.1
            render(logo)
        self.assertAlmostEqual(logo.level(), 0.8, places=2)

    def test_nothing_is_drawn_outside_the_logo_while_filling(self):
        """Regression: the liquid mask once left a white edge right of and
        below the logo."""
        logo = self.logo()
        for dark in (False, True):
            with self.subTest(dark=dark), mock.patch.object(visuals, "isDarkTheme", lambda: dark):
                logo.set_liquid("fill", 0.5, immediate=True)
                image = render(logo)
                rect = logo.logo_rect()
                for y in range(int(rect.center().y()), int(rect.bottom()) + 3):
                    self.assertLess(alpha(image, rect.right() + 2, y), 90, y)
                for x in range(int(rect.center().x()), int(rect.right()) + 3):
                    self.assertLess(alpha(image, x, rect.bottom() + 2), 90, x)

    def test_glow_fades_out_inside_the_widget(self):
        logo = self.logo(canvas=(150, 150))
        image = render(logo)
        edges = [alpha(image, x, 0) for x in range(150)] + \
                [alpha(image, x, 149) for x in range(150)] + \
                [alpha(image, 0, y) for y in range(150)] + [alpha(image, 149, y) for y in range(150)]
        self.assertEqual(max(edges), 0)

    def test_done_badge(self):
        logo = self.logo()
        logo.show_badge()
        self.assertTrue(logo.has_badge())
        self.clock.t += 2
        image = render(logo)
        rect = logo.logo_rect()
        badge = QPointF(rect.left() + rect.width() * 0.80, rect.top() + rect.height() * 0.84)
        colour = image.pixelColor(int(badge.x() + rect.height() * 0.1), int(badge.y()))
        self.assertGreater(colour.green(), colour.blue())   # the green success badge
        logo.show_badge(ghost=True)            # uninstalled: a faint logo with the badge
        faint = self.coverage(logo)
        self.assertLess(faint["top"], 90, faint)


def _xy(point):
    return point.x(), point.y()


class WordmarkAndBackgroundTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app()

    def test_wordmark_writes_itself_in(self):
        clock = Clock()
        with mock.patch.object(visuals, "now", clock):
            mark = visuals.Wordmark(40)
            mark.play(0.0)
            self.assertTrue(mark.is_animating())
            clock.t += 0.01
            early = render(mark)
            clock.t += 2.0
            done = render(mark)
            self.assertFalse(mark.is_animating())
        def ink(image):
            return sum(alpha(image, x, y) > 128 for y in range(0, image.height(), 2)
                       for x in range(0, image.width(), 2))
        self.assertLess(ink(early), ink(done) * 0.1)
        self.assertGreater(ink(done), 100)
        self.assertEqual(mark.text(), "FlowBench")

    def test_background_only_moves_in_the_wave_band(self):
        background = visuals.FlowBackground()
        from PySide6.QtCore import QRectF
        rect = QRectF(0, 0, 720, 480)
        band = background.moving_rect(rect)
        self.assertGreater(band.top(), 240)
        self.assertEqual(band.bottom(), 480)

        def frame(t):
            image = QImage(720, 480, QImage.Format_ARGB32_Premultiplied)
            with mock.patch.object(visuals, "now", lambda: background.epoch + t):
                painter = QPainter(image)
                background.paint(painter, rect, False)
                painter.end()
            return image
        a, b = frame(1.0), frame(2.5)
        top = QRect(0, 0, 720, int(band.top()))
        self.assertEqual(a.copy(top), b.copy(top))              # still above the band
        self.assertNotEqual(a.copy(band.toRect()), b.copy(band.toRect()))
        for dark in (False, True):
            image = QImage(720, 480, QImage.Format_ARGB32_Premultiplied)
            painter = QPainter(image)
            background.paint(painter, rect, dark)
            painter.end()
            lightness = image.pixelColor(360, 120).lightness()
            self.assertTrue(lightness < 70 if dark else lightness > 200, lightness)


class WizardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.payload, self.info = build_payload(self.root)
        self.platform = FakePlatform(self.root)
        self.target = self.root / "Programs" / "FlowBench"
        theme = qconfig.themeMode.value
        self.addCleanup(lambda: setTheme(theme))

    def wizard(self, **kwargs):
        kwargs.setdefault("chinese", False)
        win = SetupWindow(self.payload, self.info, target=self.target, platform=self.platform,
                          resources=ROOT, **kwargs)
        win.show()
        self.addCleanup(win.close)
        return win

    def test_title_bar(self):
        win = self.wizard()
        bar = win.titleBar
        self.assertFalse(bar.maxBtn.isVisible())
        self.assertIsInstance(bar.closeBtn, gui.PlainTitleButton)
        self.assertIsInstance(bar.minBtn, gui.PlainTitleButton)
        self.assertEqual(bar.titleLabel.text(), "FlowBench Setup")
        self.assertTrue(bar.languageButton.isVisible())
        self.assertTrue(bar.themeButton.isVisible())
        bar.closeBtn.click()
        self.assertFalse(win.isVisible())

    def test_english_and_chinese_everywhere(self):
        win = self.wizard()
        leaked = [text for text in window_texts(win) if CJK.search(text)]
        self.assertEqual(leaked, [])
        win.set_language(True)
        self.assertEqual(win.startButton.text(), "开始安装")
        self.assertEqual(win.installButton.text(), "安装")
        self.assertEqual(win.titleBar.titleLabel.text(), "FlowBench 安装程序")
        self.assertIn("安装", win.windowTitle())
        self.assertEqual(win.backButton.text(), "上一步")
        self.assertEqual(win.openButton.text(), "打开 FlowBench")
        self.assertTrue(CJK.search(win.spaceLabel.text()))
        win.set_language(False)
        self.assertEqual([text for text in window_texts(win) if CJK.search(text)], [])

    def test_language_menu_switches_language(self):
        win = self.wizard(chinese=True)
        win.show_language_menu()
        menu = win._language_menu
        actions = menu.menuActions()
        self.assertEqual([a.text() for a in actions], ["简体中文", "English"])
        self.assertEqual([a.isChecked() for a in actions], [True, False])
        changed = []
        win.languageChanged.connect(changed.append)
        actions[1].trigger()
        menu.close()
        self.assertFalse(win.chinese)
        self.assertEqual(changed, [False])
        self.assertEqual(win.startButton.text(), "Get started")

    def test_language_switch_mid_install_keeps_the_state(self):
        win = self.wizard()
        win.startButton.click()
        win.phase = "installing"
        win.pages.setCurrentWidget(win.progressPage)
        win.busy = True
        win._on_progress(40, "_internal/base_library.zip")
        win.busy = False
        win.session = SetupSession(self.payload, self.info, self.target, platform=self.platform)
        win._show_error(core.InstallError("No permission to write to X.", "没有权限写入 X。"),
                        staged=False)
        self.assertEqual(win.progressTitle.text(), "Setup did not finish")
        self.assertEqual(win.progressError.text(), "No permission to write to X.")
        win.set_language(True)
        self.assertEqual(win.progressTitle.text(), "安装没有完成")
        self.assertEqual(win.progressError.text(), "没有权限写入 X。")
        self.assertEqual(win.stopButton.text(), "关闭")
        self.assertTrue(win.retryButton.isVisible())
        self.assertTrue(win.progressBar.isError())

    def test_theme_button_switches_light_and_dark(self):
        setTheme(Theme.LIGHT)
        win = self.wizard()
        self.assertEqual(win.titleBar.themeButton.toolTip(), "Switch to dark mode")
        win.toggle_theme()
        self.assertTrue(isDarkTheme())
        self.assertEqual(win.titleBar.themeButton.toolTip(), "Switch to light mode")
        self.assertEqual(win.titleBar.closeBtn.normalColor.name(), "#ffffff")
        win.toggle_theme()
        self.assertFalse(isDarkTheme())
        self.assertEqual(win.titleBar.closeBtn.normalColor.name(), "#000000")

    def test_switching_fades_the_old_look_out(self):
        win = self.wizard()
        win.toggle_theme()
        self.assertTrue(win._overlay.active())
        self.assertEqual(win._dirty_region().boundingRect(), win.rect())
        self.assertTrue(wait_for(lambda: not win._overlay.isVisible(), 3000))

    def test_location_page(self):
        win = self.wizard()
        win.startButton.click()
        self.assertIs(win.pages.currentWidget(), win.locationPage)
        self.assertFalse(win.defaultButton.isVisible())          # already the default
        win.pathEdit.setText(str(self.root / "Elsewhere" / "FlowBench"))
        self.assertTrue(win.defaultButton.isVisible())
        win.defaultButton.click()
        self.assertEqual(win.chosen_target(), self.platform.default_install_dir())
        with mock.patch.object(core, "free_space", return_value=10 * 1024 ** 3):
            win._refresh_location()
        self.assertIn("10.0 GB free", win.spaceLabel.text())
        self.assertIn("Needs about", win.spaceLabel.text())
        self.assertFalse(win.space_low)
        with mock.patch.object(core, "free_space", return_value=1024 ** 2):
            win._refresh_location()
        self.assertIn("Not enough space", win.spaceLabel.text())
        self.assertTrue(win.space_low)
        win.backButton.click()
        self.assertIs(win.pages.currentWidget(), win.welcomePage)

    def test_progress_fills_the_logo(self):
        win = self.wizard()
        win.startButton.click()
        win.phase = "installing"
        win.pages.setCurrentWidget(win.progressPage)
        win.busy = True
        win._on_progress(60, "FlowBench.exe")
        win.busy = False
        self.assertEqual(win.percentLabel.text(), "60%")
        self.assertAlmostEqual(win.progressLogo._target, 0.6)
        self.assertIn("Extracting: FlowBench.exe", win.progressDetail.text())

    def test_done_page_shows_the_badge(self):
        win = self.wizard()
        win.startButton.click()
        win.installButton.click()
        self.assertTrue(wait_for(lambda: win.pages.currentWidget() is win.donePage, 10000))
        self.assertTrue(win.doneLogo.has_badge())
        self.assertEqual(win.doneTitle.text(), "FlowBench is installed")
        self.assertIn(str(self.target), win.doneDetail.text())

    def test_welcome_intro_plays_once(self):
        win = self.wizard()
        self.assertFalse(win.heroLogo.intro_done())
        self.assertTrue(win.wordmark.is_animating())
        self.assertTrue(win.clock.running())
        win.startButton.click()                  # leaving finishes the intro at once
        self.assertTrue(win.heroLogo.intro_done())
        self.assertFalse(win.wordmark.is_animating())
        self.assertIsNone(win.startButton.graphicsEffect())
        region = win._dirty_region()
        band = win.background.moving_rect(win.rect()).toAlignedRect()
        self.assertTrue(region.contains(band))
        self.assertFalse(region.intersects(QRect(0, 0, win.width(), win.titleBar.height())))

    def test_animations_off(self):
        win = self.wizard(motion=False)
        self.assertFalse(win.clock.running())
        self.assertTrue(win.heroLogo.intro_done())
        self.assertFalse(win.wordmark.is_animating())
        self.assertIsNone(win.startButton.graphicsEffect())
        win.startButton.click()
        self.assertIsNone(win.locationPage.graphicsEffect())    # no slide-in

    def test_motion_follows_windows_setting(self):
        class Off(FakePlatform):
            def animations_enabled(self):
                return False

        class Broken(FakePlatform):
            def animations_enabled(self):
                raise OSError("no")
        self.assertFalse(gui.motion_enabled(Off(self.root)))
        self.assertTrue(gui.motion_enabled(Broken(self.root)))
        self.assertTrue(gui.motion_enabled(FakePlatform(self.root)))
        self.assertTrue(winplat.animations_enabled())           # not Windows: on

    def test_update_window_texts(self):
        with mock.patch.object(SetupWindow, "start_update", lambda self: None):
            win = SetupWindow(self.payload, self.info, update=True, target=self.target,
                              wait_pid=7, chinese=False, platform=self.platform, resources=ROOT)
        win.show()
        self.addCleanup(win.close)
        win.busy = True
        self.assertEqual(win._progress_text(), "Waiting for FlowBench to exit…")
        win._on_progress(0, "")
        self.assertEqual(win.updateDetail.text(), "Waiting for FlowBench to exit…")
        win._on_progress(50, "FlowBench.exe")
        self.assertAlmostEqual(win.updateLogo._target, 0.5)
        win.busy = False
        self.assertEqual(win.titleBar.titleLabel.text(), "FlowBench Update")
        self.assertEqual([text for text in window_texts(win) if CJK.search(text)], [])
        win.set_language(True)
        self.assertIn("正在更新到", win.updateTitle.text())


class UninstallLookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        payload, info = build_payload(self.root)
        self.platform = FakePlatform(self.root)
        self.target = self.root / "Programs" / "FlowBench"
        s = SetupSession(payload, info, self.target, platform=self.platform)
        s.stage()
        s.activate()
        s.finalize()
        self.data = self.root / "AppData" / "FlowBench"
        self.data.mkdir(parents=True)

    def window(self, chinese=False):
        win = UninstallWindow(self.target, data_dir=self.data, chinese=chinese,
                              platform=self.platform, resources=ROOT)
        win.show()
        self.addCleanup(win.close)
        return win

    def test_both_languages(self):
        win = self.window()
        self.assertEqual(win.titleBar.titleLabel.text(), "FlowBench Uninstaller")
        self.assertEqual([text for text in window_texts(win) if CJK.search(text)], [])
        win.removeDataCheck.setChecked(True)
        win.set_language(True)
        self.assertEqual(win.uninstallButton.text(), "卸载")
        self.assertIn("无法恢复", win.dataNote.text())
        self.assertEqual(win.titleBar.titleLabel.text(), "FlowBench 卸载程序")
        win.set_language(False)
        self.assertIn("cannot be undone", win.dataNote.text())

    def test_logo_drains_and_ends_faint_with_the_badge(self):
        win = self.window()
        win.uninstallButton.click()
        self.assertTrue(wait_for(lambda: win.pages.currentWidget() is win.donePage, 10000))
        self.assertTrue(win.doneLogo.has_badge())
        self.assertTrue(win.doneLogo._ghost)
        self.assertEqual(win.progressLogo._liquid, "drain")
        self.assertEqual(win.progressLogo._target, 0.0)      # 100 % removed: empty

    def test_progress_text_and_percent(self):
        win = self.window()
        win.phase = "running"
        win.pages.setCurrentWidget(win.progressPage)
        win._on_progress(40, "FlowBench.exe")
        self.assertEqual(win.percentLabel.text(), "40%")
        self.assertIn("Removing: FlowBench.exe", win.progressDetail.text())
        self.assertAlmostEqual(win.progressLogo._target, 0.6)


class FreeSpaceTests(unittest.TestCase):
    def test_free_space_walks_up_to_an_existing_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = core.free_space(Path(tmp))
            self.assertIsInstance(base, int)
            deep = core.free_space(Path(tmp) / "not" / "there" / "FlowBench")
            self.assertIsInstance(deep, int)
            self.assertAlmostEqual(deep, base, delta=64 * 1024 ** 2)
        self.assertIsNone(core.free_space(Path("relative/FlowBench")))

    def test_size_text(self):
        self.assertEqual(gui.size_text(412 * 1024 ** 2), "412 MB")
        self.assertEqual(gui.size_text(5 * 1024 ** 3 // 2), "2.5 GB")
        self.assertEqual(gui.size_text(10), "1 MB")


if __name__ == "__main__":
    unittest.main()
