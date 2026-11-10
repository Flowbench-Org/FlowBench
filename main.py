"""FlowBench — 合法授权网络压力测试与性能监控工具（Python/Fluent 版）。"""
import os
import sys
import tempfile

# 崩溃诊断：C 层闪退（如 paho-mqtt 线程崩溃、Qt 访问违例）时把所有线程堆栈写入 crash.log
import faulthandler
_crash_log_dir = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "FlowBench", "logs")
try:
    os.makedirs(_crash_log_dir, exist_ok=True)
    _crash_log_file = open(os.path.join(_crash_log_dir, "crash.log"), "a", encoding="utf-8")
except OSError:
    # A locked or read-only log must never prevent the GUI from starting.
    try:
        _crash_log_file = open(
            os.path.join(tempfile.gettempdir(), "FlowBench-crash.log"),
            "a", encoding="utf-8")
    except OSError:
        _crash_log_file = open(os.devnull, "w", encoding="utf-8")
try:
    faulthandler.enable(_crash_log_file)
except (RuntimeError, OSError):
    pass

# Frozen PySide6 packages expose the PYZ namespace module before Qt binaries.
# Register the bundled DLL directories and preload Shiboken so QtCore.pyd can
# resolve pyside6.abi3.dll and libshiboken during the very first Qt import.
if getattr(sys, "frozen", False):
    _pyside_dir = os.path.join(sys._MEIPASS, "PySide6")
    _shiboken_dir = os.path.join(sys._MEIPASS, "shiboken6")
    for _dll_dir in (_pyside_dir, _shiboken_dir, sys._MEIPASS):
        os.add_dll_directory(_dll_dir)
    os.environ["PATH"] = _pyside_dir + os.pathsep + _shiboken_dir + os.pathsep + os.environ.get("PATH", "")
    import ctypes
    import shiboken6.Shiboken  # noqa: F401
    # Python's frozen importer can still miss the PySide6 extension-specific
    # loader context. Preload QtCore.pyd itself so every dependent DLL is in
    # the process before importlib touches it.
    ctypes.windll.kernel32.LoadLibraryExW(
        os.path.join(_pyside_dir, "QtCore.pyd"), 0, 8)

# 仅导入最核心、最轻量的模块，确保启动画面能第一时间显示
from PySide6.QtCore import QLocale, QTimer, QSize, QLockFile
from PySide6.QtGui import QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication
from app.ui.splash import create_splash

SINGLE_INSTANCE_KEY = "FlowBench_SingleInstance_Key"
APP_USER_MODEL_ID = "FlowBench.App"


def instance_lock_path() -> str:
    """Return a stable per-user lock path for the GUI process."""
    root = os.environ.get("APPDATA", os.path.expanduser("~"))
    directory = os.path.join(root, "FlowBench")
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError:
        directory = os.path.join(tempfile.gettempdir(), "FlowBench")
        os.makedirs(directory, exist_ok=True)
    return os.path.join(directory, "FlowBench.instance.lock")


def _set_app_user_model_id(app_id: str = APP_USER_MODEL_ID) -> None:
    """绑定稳定的 AUMID，否则 Win10/11 会静默丢掉托盘气泡/Toast。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass


def resource_path(rel: str) -> str:
    """兼容源码运行与 PyInstaller 打包。"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


def _allow_foreground_handoff() -> None:
    """Let the running FlowBench bring its window to the front.

    Windows only lets the foreground process raise its windows. That is this
    new launch, which the user just started; without passing the right on,
    the running copy could only flash its taskbar button.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.user32.AllowSetForegroundWindow(-1)    # ASFW_ANY
    except Exception:
        pass


def is_already_running(attempts: int = 10) -> bool:
    """Notify the running instance and return whether the request was sent.

    The first process creates the local server before importing the heavy UI.
    A short retry window covers the tiny interval between lock acquisition and
    the server becoming visible to the second process.
    """
    import time

    for _ in range(max(1, attempts)):
        socket = QLocalSocket()
        socket.connectToServer(SINGLE_INSTANCE_KEY)
        if socket.waitForConnected(100):
            _allow_foreground_handoff()
            socket.write(b"show")
            socket.flush()
            socket.waitForBytesWritten(200)
            socket.disconnectFromServer()
            return True
        socket.abort()
        time.sleep(0.05)
    return False


class SingleInstanceServer(QLocalServer):
    """Receive launch requests while the splash screen is still visible.

    ``main_window`` is deliberately optional: the server is started before
    the expensive UI construction, which closes the startup race where two
    rapid double-clicks could both pass the old socket check.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.main_window = None
        self._pending_show = False
        self._ready = False
        self.newConnection.connect(self._on_new_connection)

    def set_main_window(self, main_window):
        self.main_window = main_window

    def set_ready(self):
        """Allow queued launch requests to restore the fully built window."""
        self._ready = True
        if self._pending_show:
            self._pending_show = False
            QTimer.singleShot(0, self._show_window)

    def _on_new_connection(self):
        while self.hasPendingConnections():
            socket = self.nextPendingConnection()
            if socket is None:
                continue
            # The endpoint is dedicated to launch requests, so the payload is
            # intentionally irrelevant. A successful connection is the
            # request; handle it immediately so launch-to-show does not depend
            # on another event-loop turn or on the client flushing its bytes.
            self._handle_launch_socket(socket)

    def _handle_launch_socket(self, socket):
        if socket.property("flowbenchLaunchHandled"):
            return
        socket.setProperty("flowbenchLaunchHandled", True)
        socket.readAll()
        self._show_window()
        # This is a one-way notification socket. Abort it immediately so a
        # second launch can be accepted even if the first client remains alive
        # for a moment after sending its payload.
        socket.abort()
        socket.deleteLater()

    def _show_window(self):
        if self.main_window is None or not self._ready:
            self._pending_show = True
            return
        self.main_window._show_from_tray()


def create_single_instance_server():
    """Listen on the local launch endpoint, recovering only stale endpoints."""
    server = SingleInstanceServer()
    if server.listen(SINGLE_INSTANCE_KEY):
        return server

    # Keep compatibility with installations started by the previous build,
    # which had a local server but no QLockFile yet.  Do not remove a live
    # endpoint belonging to that process.
    if is_already_running():
        server.close()
        return None

    # QLocalServer can leave an endpoint behind after a hard crash.  The
    # process lock has already established ownership, so it is safe to remove
    # that stale endpoint and retry once.  Never remove it before the lock is
    # acquired: doing so could disconnect a healthy running instance.
    QLocalServer.removeServer(SINGLE_INSTANCE_KEY)
    if not server.listen(SINGLE_INSTANCE_KEY):
        server.close()
        return None
    return server


def claim_instance():
    """Decide, before anything is drawn, whether this launch opens FlowBench.

    Returns ``(None, lock, server)`` when it does. Otherwise returns
    ``(exit_code, None, None)``: FlowBench is already running and has been
    asked to bring back its window, on the page it was showing, or the lock
    could not be taken. Such a launch exits without flashing the splash.
    """
    # The lock comes first and the launch endpoint right after it, so repeated
    # double-clicks during startup still leave only one process.
    instance_lock = QLockFile(instance_lock_path())
    instance_lock.setStaleLockTime(60000)
    if not instance_lock.tryLock(0):
        # A live instance owns the lock: notify it and exit this launch.
        if is_already_running():
            return 0, None, None

        # If no server answered, the lock is from a previous crash or forced
        # termination. Remove only that stale lock, then acquire it again.
        try:
            # The server endpoint is created immediately after the lock. If
            # no endpoint answered, this is a crash residue rather than a
            # live owner; use a short stale window so a recent crash does not
            # block the next launch for the full 60 seconds.
            instance_lock.setStaleLockTime(1000)
            instance_lock.removeStaleLockFile()
        except (AttributeError, RuntimeError, OSError):
            pass
        if not instance_lock.tryLock(1000):
            return 1, None, None

    local_server = create_single_instance_server()
    if local_server is None:
        instance_lock.unlock()
        return 0, None, None
    return None, instance_lock, local_server


def main():
    # AUMID 必须在创建任何窗口之前设置，否则 Win10/11 会静默丢掉托盘气泡。
    _set_app_user_model_id()

    # ① 创建 QApplication（启动画面要等确认这次启动负责打开窗口后才显示）
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("FlowBench")
    app.setApplicationDisplayName("FlowBench")
    app.setOrganizationName("FlowBench")
    ico = resource_path("app.ico")
    if os.path.exists(ico):
        app.setWindowIcon(QIcon(ico))
    # The splash is the only top-level window during startup. Closing it
    # before the main window is visible can queue an application quit event.
    app.setQuitOnLastWindowClosed(False)

    # ② 先确认这次启动是不是要打开 FlowBench：已经在运行的话，只通知它
    #    把窗口调到前面（还是原来的页面），这次启动直接退出，不闪启动画面。
    exit_code, instance_lock, local_server = claim_instance()
    if exit_code is not None:
        return exit_code
    app._single_instance_lock = instance_lock
    app._single_instance_server = local_server

    splash = create_splash()  # 这是双击后用户看到的第一样东西

    # 便捷函数：设置进度 + 双语状态文字
    def step(percent, zh, en):
        from app.ui.i18n import L
        splash.set_progress(percent, L(zh, en))

    step(10, "正在启动...", "Starting...")

    # ③ 应用图标
    step(25, "加载资源...", "Loading resources...")

    # ④ 主题
    from qfluentwidgets import Theme, setTheme, setThemeColor
    from app.services.settings import settings
    from app.ui.i18n import L, current_lang
    step(45, "加载主题...", "Loading theme...")
    setTheme(Theme.DARK if settings.theme == "dark" else Theme.LIGHT)
    # Apply the accent after the theme: qfluentwidgets may rebuild its global
    # stylesheet during setTheme and otherwise reset the saved accent color.
    setThemeColor(settings.theme_color or "#0078D4")
    # qfluentwidgets 的 pip 版本使用 Qt Translator，而不是旧版的
    # setLanguage/Language API。安装对应翻译器后，导航按钮、菜单等
    # 组件自带文案才能真正跟随 FlowBench 的语言设置。
    from qfluentwidgets import FluentTranslator
    fluent_locale = QLocale("en_US" if current_lang() == "en-US" else "zh_CN")
    fluent_translator = FluentTranslator(fluent_locale, app)
    app.installTranslator(fluent_translator)
    # 显式保留引用，避免翻译器被垃圾回收。
    app._fluent_translator = fluent_translator

    # ⑤ 创建主窗口
    from app.ui.main_window import MainWindow
    step(65, "初始化界面...", "Initializing interface...")
    win = MainWindow()

    step(80, "配置服务...", "Configuring services...")
    local_server.set_main_window(win)
    if not local_server.isListening():
        from app.services.logger import log
        log.warning(L(f"单实例服务器启动失败: {local_server.errorString()}",
                      f"Single-instance server failed to start: {local_server.errorString()}"))

    # ⑦ 命令行指定起始页
    step(90, "即将就绪...", "Almost ready...")
    if "--page" in sys.argv:
        page = sys.argv[sys.argv.index("--page") + 1].lower() if len(sys.argv) > sys.argv.index("--page") + 1 else ""
        target = {"monitor": win.monitor, "stress": win.stress,
                  "collab": win.collab, "home": win.dashboard,
                  "agent": win.agentView, "server": win.agentView}.get(page)
        if target is not None:
            win.switchTo(target)

    # ⑧ 完成启动流程
    step(100, "准备就绪", "Ready")

    # 记录主窗口引用
    splash.finish_with_window(win)

    def complete_startup():
        """完成启动：关闭 splash，显示主窗口。"""
        # Show the main window before closing the only other top-level window.
        # Otherwise Qt may emit lastWindowClosed and exit between these calls.
        win.resize(win._default_size)
        win.show()
        # Do not raise/activate/process events synchronously here.  FluentWindow
        # is backed by qframelesswindow on Windows; forcing another native event
        # pass while its first show event is still being handled can crash the
        # process before the splash has finished.  The normal window manager
        # activation is sufficient once the splash closes.
        splash.finish_splash()
        app.setQuitOnLastWindowClosed(True)
        local_server.set_ready()
        # 多次延迟确保尺寸生效（应对 FluentWindow 初始化布局可能的 resize）
        QTimer.singleShot(250, post_startup)

    def post_startup():
        """主窗口显示后的处理。"""
        # 再次确保尺寸正确
        # 免责声明（首次启动）
        if not settings.disclaimer_accepted:
            from app.ui.disclaimer import DisclaimerDialog
            dlg = DisclaimerDialog(win)
            if not dlg.exec():
                from app.services.logger import log
                log.info(L("用户未同意免责声明，程序退出。", "Disclaimer not accepted; exiting."))
                app.quit()
                return
            settings.set("disclaimer_accepted", True)
            from app.services.logger import log
            log.info(L("用户已同意免责声明。", "Disclaimer accepted."))

    # 让进度条在 100% 停留一会儿再关闭 splash
    QTimer.singleShot(350, complete_startup)

    # ⑨ 启动后台服务 after the native window has been shown. Starting the
    # psutil worker while qframelesswindow is still handling the first paint
    # made native startup crashes much more likely.
    from app.services.monitor import monitor
    from app.services.logger import log

    def start_background_services():
        monitor.start()
        log.info(L("FlowBench 启动。", "FlowBench started."))

    # Let qframelesswindow finish its first native paint before starting the
    # psutil worker.  This also keeps a slow machine from competing for CPU
    # during the visible startup transition.
    QTimer.singleShot(1800, start_background_services)

    # 启动 3 秒后静默检查更新
    def _auto_update_check():
        try:
            from app.services.updater import check_for_updates
            check_for_updates(parent=win, manual=False)
        except Exception:
            pass

    QTimer.singleShot(5000, _auto_update_check)

    code = app.exec()
    monitor.stop()
    local_server.close()
    instance_lock.unlock()
    return code


if __name__ == "__main__":
    sys.exit(main())
