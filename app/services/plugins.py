"""FlowBench 插件系统：发现、加载、卸载、启用/禁用第三方扩展。

插件目录：%APPDATA%/FlowBench/plugins/
插件格式：
  1) 单文件插件：xxx.py（定义 FlowBenchPlugin 子类）
  2) 文件夹插件：xxx/main.py（定义 FlowBenchPlugin 子类）
插件 ID = 文件名 / 文件夹名（唯一且稳定）。

安全提示：插件是第三方代码，运行于主程序进程内，拥有同等权限。
请仅安装可信来源的插件；插件行为同样受本工具免责声明约束。
"""
import importlib.util
import os
import shutil
import sys
import traceback

from PySide6.QtCore import QObject, Signal

from app.services.logger import log
from app.services.settings import settings

PLUGIN_API_VERSION = 1


def plugins_dir() -> str:
    """插件目录（自动创建）。"""
    base = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                        "FlowBench", "plugins")
    os.makedirs(base, exist_ok=True)
    return base


def plugin_icon_path(pid: str) -> str:
    """市场安装时保存的插件图标路径（{pid}.icon.png），不存在返回空字符串。"""
    p = os.path.join(plugins_dir(), f"{pid}.icon.png")
    return p if os.path.isfile(p) else ""


def resolve_plugin_icon(plugin, pid: str, rec_path: str = "", icon_val: str = ""):
    """解析插件导航图标，返回 QIcon 或 FluentIcon；失败返回 None。

    优先级：
    1. 市场图标 {plugins_dir}/{pid}.icon.png
    2. 插件类 icon 属性（图片路径 / FluentIcon 名）
    3. None（调用方使用默认图标）

    plugin 为 None 时，可通过 icon_val 直接传入图标字符串（用于禁用/卸载后）。
    """
    from PySide6.QtGui import QIcon
    # 1. 市场安装时保存的图标
    mp = plugin_icon_path(pid)
    if mp:
        return QIcon(mp)
    # 2. 插件自定义
    if plugin is not None:
        ic = getattr(plugin, "icon", "") or ""
    else:
        ic = icon_val or ""
    # 已经是 QIcon / FluentIcon 枚举对象，直接返回
    if not isinstance(ic, str):
        return ic if ic else None
    ic = ic.strip()
    if not ic:
        return None
    # 图片文件名（如 "test_env.png"）：优先按插件目录解析，
    # 否则不含斜杠的文件名会被误当成 FluentIcon 名
    if not os.path.isabs(ic) and rec_path:
        rel = os.path.join(os.path.dirname(rec_path), ic)
        if os.path.isfile(rel):
            return QIcon(rel)
    # FluentIcon 名（如 "SPEED_HIGH"）
    if not os.path.isabs(ic) and not os.path.exists(ic) and "/" not in ic and "\\" not in ic:
        try:
            from qfluentwidgets import FluentIcon as FIF
            return getattr(FIF, ic.upper(), None)
        except Exception:
            return None
    # 图片路径（绝对路径 / 相对当前目录）
    if os.path.isfile(ic):
        return QIcon(ic)
    return None


def _i18n_text(v):
    """元组 (中文, 英本) 按当前界面语言取值；普通字符串原样返回。"""
    from app.ui.i18n import current_lang
    if isinstance(v, (tuple, list)) and len(v) == 2:
        return str(v[0]) if current_lang() == "zh-CN" else str(v[1])
    return str(v)


class FlowBenchPlugin:
    """插件基类：插件继承此类实现扩展功能。

    插件文件中可直接使用 FlowBenchPlugin（宿主已注入模块命名空间），
    也可 from app.services.plugins import FlowBenchPlugin。
    """

    id = "unknown"                # 由宿主按文件/文件夹名覆盖
    name = "Unnamed Plugin"       # 显示名，可为 (中文, 英文)
    version = "1.0"
    author = ""
    description = ""              # 简介，可为 (中文, 英文)
    icon = ""                     # 导航图标：图片路径（相对插件文件）或 FluentIcon 名（如"SPEED_HIGH"）
    category = ""                 # 市场分类：tool/protocol/ui/other；缺省自动识别
    api_version = PLUGIN_API_VERSION

    def on_load(self, ctx: "PluginContext"):
        """加载时调用，可初始化资源、注册扩展（协议/导出器/目标源/指标订阅）。"""
        pass

    def on_unload(self):
        """卸载时调用，可释放资源。注册项由宿主自动清理。"""
        pass

    def create_widget(self, parent):
        """返回插件主页面控件（加入主窗口导航）；返回 None 则不添加页面。"""
        return None

    def page_title(self):
        """导航页标题。"""
        t = getattr(self, "title", None)
        return _i18n_text(t) if t else _i18n_text(self.name)

    # ---------- 压测生命周期（可选实现） ----------
    def on_test_start(self, configs):
        """每次压测开始时调用（主线程）。configs 为目标配置 dict 列表。"""
        pass

    def on_test_end(self, report):
        """每次压测结束/停止后调用（主线程）。report 为汇总报告 dict。"""
        pass


class PluginContext:
    """提供给插件的宿主上下文（受限的便捷接口）。"""

    def __init__(self, pid: str):
        self.plugin_id = pid
        from app.services.updater import APP_VERSION
        self.app_version = APP_VERSION
        self.logger = log

    # 双语助手
    @staticmethod
    def tr(zh: str, en: str) -> str:
        from app.ui.i18n import L
        return L(zh, en)

    # 插件私有配置（命名空间隔离，持久化在主配置的 plugin_data 下）
    def get(self, key, default=None):
        data = settings.plugin_data or {}
        return data.get(self.plugin_id, {}).get(key, default)

    def set(self, key, value):
        data = dict(settings.plugin_data or {})
        slot = dict(data.get(self.plugin_id, {}))
        slot[key] = value
        data[self.plugin_id] = slot
        settings.set("plugin_data", data)

    # ---------- 扩展注册（on_load 中调用） ----------
    def register_protocol(self, name: str, handler):
        """注册自定义测试协议，压测页协议下拉框会出现该项。

        handler(config: dict, timeout: float, state: dict) -> (ok, err, nbytes)
        - 在 worker 线程执行，必须线程安全、可重入
        - config: {"target", "port", "protocol", "packet_size", "headers", ...}
        - state: 每个 worker 线程一份的字典，可存放 socket 等长连接资源
        - 返回 (成功?, 错误码或None, 发送字节数)
        """
        plugin_manager._register_protocol(self.plugin_id, name, handler)

    def register_exporter(self, label, callback):
        """注册报告导出器，压测页"导出报告"菜单出现该项。

        callback(report: dict, path: str) —— 把汇总报告写出到 path（主线程调用）。
        label 可为 (中文, 英文) 元组。
        """
        plugin_manager._register_exporter(self.plugin_id, label, callback)

    def register_target_provider(self, label, callback):
        """注册目标集提供者，压测页出现"插件目标"按钮。

        callback() -> [str] —— 返回目标地址列表（主线程调用）。
        label 可为 (中文, 英文) 元组。
        """
        plugin_manager._register_target_provider(self.plugin_id, label, callback)

    def subscribe_metrics(self, callback):
        """订阅实时压测指标（QPS/延迟/成功失败计数等）。

        callback(snapshot: dict) 在主线程周期调用（压测运行期间约 500ms 一次）。
        """
        plugin_manager._subscribe_metrics(self.plugin_id, callback)


class _PluginRecord:
    """一个已发现的插件条目。"""

    def __init__(self, path: str):
        self.path = path              # main.py 完整路径
        self.plugin = None            # FlowBenchPlugin 实例（未加载为 None）
        self.error = None             # 加载错误信息
        self._meta = {}               # 加载成功后缓存的元数据（卸载后仍可显示）

    def _cache_meta(self):
        """从插件实例缓存元数据，以便卸载/禁用后仍能显示名称、图标等。"""
        p = self.plugin
        if p is None:
            return
        self._meta = {
            "name": getattr(p, "name", ""),
            "version": getattr(p, "version", ""),
            "author": getattr(p, "author", ""),
            "description": getattr(p, "description", ""),
            "icon": getattr(p, "icon", ""),
        }

    @property
    def pid(self) -> str:
        """插件 ID：main.py 所在目录名（文件夹插件）或文件名去扩展。"""
        d = os.path.dirname(self.path)
        root = plugins_dir()
        if os.path.dirname(d) == root:
            return os.path.basename(d)
        return os.path.splitext(os.path.basename(self.path))[0]

    @property
    def disabled(self) -> bool:
        return self.pid in set(settings.plugins_disabled or [])

    @property
    def state(self) -> str:
        """loaded / disabled / error / unloaded"""
        if self.disabled:
            return "disabled"
        if self.error:
            return "error"
        return "loaded" if self.plugin else "unloaded"

    # 以下属性优先用已加载实例，否则用缓存（禁用/卸载后仍可显示）
    @property
    def display_name(self):
        if self.plugin is not None:
            return self.plugin.name
        return self._meta.get("name", self.pid)

    @property
    def display_version(self):
        if self.plugin is not None:
            return self.plugin.version
        return self._meta.get("version", "")

    @property
    def display_author(self):
        if self.plugin is not None:
            return self.plugin.author
        return self._meta.get("author", "")

    @property
    def display_description(self):
        if self.plugin is not None:
            return self.plugin.description
        return self._meta.get("description", "")

    @property
    def display_icon(self):
        if self.plugin is not None:
            return self.plugin.icon
        return self._meta.get("icon", "")


class PluginManager(QObject):
    """插件管理器：扫描目录、安全加载、启停、导入、删除。"""

    loaded = Signal(object)   # FlowBenchPlugin 实例
    unloaded = Signal(str)    # 插件 ID
    changed = Signal()        # 列表/状态变化（设置页刷新）

    def __init__(self):
        super().__init__()
        self._records = []    # [_PluginRecord]
        # 扩展注册表（key -> {"pid": 插件ID, "label": 标签, "fn": 回调}）
        self._protocols = {}        # 协议名(大写) -> handler
        self._exporters = {}        # 导出器名 -> {"label", "fn"}
        self._target_providers = {} # 提供者名 -> {"label", "fn"}
        self._metric_subs = []      # [{"pid", "fn"}]
        self.discover()

    # ---------- 扩展注册表（由 PluginContext 调用） ----------
    def _register_protocol(self, pid, name, handler):
        key = str(name).strip().upper()
        if not key:
            return
        self._protocols[key] = {"pid": pid, "fn": handler}
        self.changed.emit()

    def _register_exporter(self, pid, label, callback):
        key = f"{pid}::{id(callback)}"
        self._exporters[key] = {"pid": pid, "label": label, "fn": callback}

    def _register_target_provider(self, pid, label, callback):
        key = f"{pid}::{id(callback)}"
        self._target_providers[key] = {"pid": pid, "label": label, "fn": callback}
        self.changed.emit()

    def _subscribe_metrics(self, pid, callback):
        self._metric_subs.append({"pid": pid, "fn": callback})

    def _cleanup_registrations(self, pid: str):
        """插件卸载时清理它的全部注册项。"""
        self._protocols = {k: v for k, v in self._protocols.items() if v["pid"] != pid}
        self._exporters = {k: v for k, v in self._exporters.items() if v["pid"] != pid}
        self._target_providers = {k: v for k, v in self._target_providers.items() if v["pid"] != pid}
        self._metric_subs = [s for s in self._metric_subs if s["pid"] != pid]

    # ---------- 供引擎/界面查询 ----------
    def protocol_names(self):
        """已注册的插件协议名列表（大写）。"""
        return sorted(self._protocols.keys())

    def protocol_handler(self, name: str):
        """取协议 handler；未注册返回 None。"""
        ent = self._protocols.get(str(name).strip().upper())
        return ent["fn"] if ent else None

    def exporters(self):
        """[(显示名, callback)] 导出器列表。"""
        return [(_i18n_text(v["label"]), v["fn"]) for v in self._exporters.values()]

    def target_providers(self):
        """[(显示名, callback)] 目标集提供者列表。"""
        return [(_i18n_text(v["label"]), v["fn"]) for v in self._target_providers.values()]

    # ---------- 压测事件分发（主线程调用） ----------
    def notify_test_start(self, configs):
        from app.ui.i18n import L
        for rec in self._records:
            if rec.plugin is not None:
                try:
                    rec.plugin.on_test_start(configs)
                except Exception as e:
                    log.error(L(f"插件 on_test_start 回调失败: {rec.pid}: {e}",
                                f"Plugin on_test_start failed: {rec.pid}: {e}"))

    def notify_test_end(self, report):
        from app.ui.i18n import L
        for rec in self._records:
            if rec.plugin is not None:
                try:
                    rec.plugin.on_test_end(report)
                except Exception as e:
                    log.error(L(f"插件 on_test_end 回调失败: {rec.pid}: {e}",
                                f"Plugin on_test_end failed: {rec.pid}: {e}"))

    def dispatch_metrics(self, snapshot: dict):
        """转发实时指标给订阅插件（主线程，异常不中断）。"""
        from app.ui.i18n import L
        for sub in list(self._metric_subs):
            try:
                sub["fn"](snapshot)
            except Exception as e:
                log.error(L(f"插件指标回调失败: {e}", f"Plugin metrics callback failed: {e}"))

    # ---------- 发现 ----------
    def discover(self):
        """扫描插件目录，合并式更新记录列表：同 ID 保留原 record（含已加载实例），
        避免设置页刷新时把已加载插件重置为未加载。"""
        old = {r.pid: r for r in self._records}
        self._records = []
        root = plugins_dir()
        try:
            entries = sorted(os.listdir(root))
        except Exception:
            entries = []
        for entry in entries:
            path = os.path.join(root, entry)
            main_py = None
            if entry.lower().endswith(".py") and entry.lower() != "__init__.py":
                main_py = path
            elif os.path.isdir(path) and os.path.isfile(os.path.join(path, "main.py")):
                main_py = os.path.join(path, "main.py")
            if main_py:
                fresh = _PluginRecord(main_py)
                prev = old.get(fresh.pid)
                if prev is not None:
                    # A plugin can be replaced from a single file with a
                    # folder plugin (or vice versa) while keeping its ID.
                    # Retaining the old path would reload deleted code.
                    prev.path = fresh.path
                    self._records.append(prev)
                else:
                    self._records.append(fresh)
        return self._records

    def records(self):
        return list(self._records)

    def record(self, pid: str):
        for r in self._records:
            if r.pid == pid:
                return r
        return None

    # ---------- 加载 / 卸载 ----------
    def load_all(self) -> int:
        """加载所有未禁用的插件，返回成功数量。"""
        n = 0
        for rec in self.records():
            if self.load(rec):
                n += 1
        return n

    def load(self, rec: _PluginRecord) -> bool:
        """安全加载单个插件（异常捕获，不拖垮主程序）。"""
        if rec.plugin is not None or rec.disabled:
            return rec.plugin is not None
        pid = rec.pid
        rec.error = None
        try:
            mod_name = f"flowbench_plugin_{pid}"
            spec = importlib.util.spec_from_file_location(mod_name, rec.path)
            mod = importlib.util.module_from_spec(spec)
            # 注入基类，插件文件无需 import 即可继承；sys.modules 注册支持包内相对导入
            mod.__dict__["FlowBenchPlugin"] = FlowBenchPlugin
            mod.__dict__["NetPulsePlugin"] = FlowBenchPlugin     # plugins from before the rename
            mod.__dict__["PluginContext"] = PluginContext
            sys.modules[mod_name] = mod
            spec.loader.exec_module(mod)
            cls = self._find_plugin_class(mod)
            if cls is None:
                raise RuntimeError(
                    f"no FlowBenchPlugin subclass found in {os.path.basename(rec.path)}")
            plugin = cls()
            plugin.id = pid
            plugin.on_load(PluginContext(pid))
            rec.plugin = plugin
            rec._cache_meta()
            from app.ui.i18n import L
            log.info(L(f"插件已加载：{pid}", f"Plugin loaded: {pid}"))
            self.loaded.emit(plugin)
            self.changed.emit()
            return True
        except Exception as e:
            rec.error = f"{e}"
            self._cleanup_registrations(pid)
            mod_prefix = f"flowbench_plugin_{pid}"
            for key in list(sys.modules.keys()):
                if key == mod_prefix or key.startswith(mod_prefix + "."):
                    del sys.modules[key]
            detail = traceback.format_exc(limit=3)
            from app.ui.i18n import L
            log.error(L(f"插件加载失败：{pid} — {e}", f"Plugin load failed: {pid} — {e}")
                      + "\n" + detail)
            self.changed.emit()
            return False

    @staticmethod
    def _find_plugin_class(mod):
        """在模块命名空间中查找 FlowBenchPlugin 子类（取最后定义的）。"""
        found = None
        for obj in vars(mod).values():
            if (isinstance(obj, type) and issubclass(obj, FlowBenchPlugin)
                    and obj is not FlowBenchPlugin and obj.__module__ == mod.__name__):
                found = obj
        return found

    def unload(self, pid: str) -> bool:
        """卸载插件（调用 on_unload 并通知界面移除页面）。"""
        rec = self.record(pid)
        if rec is None or rec.plugin is None:
            return False
        try:
            rec.plugin.on_unload()
        except Exception:
            pass
        rec.plugin = None
        self._cleanup_registrations(pid)
        # 从 sys.modules 清除旧模块，避免更新时加载到缓存的旧代码
        mod_prefix = f"flowbench_plugin_{pid}"
        for key in list(sys.modules.keys()):
            if key == mod_prefix or key.startswith(mod_prefix + "."):
                del sys.modules[key]
        from app.ui.i18n import L
        log.info(L(f"插件已卸载：{pid}", f"Plugin unloaded: {pid}"))
        self.unloaded.emit(pid)
        self.changed.emit()
        return True

    # ---------- 启停 / 导入 / 删除 ----------
    def set_enabled(self, pid: str, enabled: bool):
        """启用/禁用插件（禁用立即卸载，启用立即加载）。"""
        dis = set(settings.plugins_disabled or [])
        if enabled:
            dis.discard(pid)
        else:
            dis.add(pid)
        if not settings.set("plugins_disabled", sorted(dis)):
            return False
        if not enabled:
            self.unload(pid)
        else:
            rec = self.record(pid)
            if rec and rec.plugin is None:
                return self.load(rec)
        return True

    def reload(self, pid: str) -> bool:
        """重新加载插件（先卸载再加载）。"""
        rec = self.record(pid)
        if rec is None:
            return False
        self.unload(pid)
        self.discover()
        rec = self.record(pid)
        return self.load(rec) if rec else False

    def import_from(self, src: str):
        """把 .py 文件或文件夹复制进插件目录并加载。返回 (成功?, 消息)。

        安装/更新即视为用户要启用，会自动从禁用列表移除。
        """
        from app.ui.i18n import L
        src = os.path.abspath(os.path.expanduser(str(src)))
        if not os.path.exists(src):
            return False, L("源路径不存在", "Source path not found")
        root = plugins_dir()
        base = os.path.basename(os.path.abspath(src).rstrip("\\/"))
        if not base or base in (".", ".."):
            return False, L("无效的插件路径", "Invalid plugin path")
        dst = os.path.join(root, base)

        # 先计算目标 pid，如果已加载则先卸载（Windows 下文件会被占用，必须先释放）
        target_pid = os.path.splitext(base)[0] if base.lower().endswith(".py") else base
        existing = self.record(target_pid)
        if existing is not None and existing.plugin is not None:
            self.unload(target_pid)

        # 安装/更新即视为启用，从禁用列表移除
        dis = set(settings.plugins_disabled or [])
        if target_pid in dis:
            dis.discard(target_pid)
            settings.set("plugins_disabled", sorted(dis))

        entry = None
        try:
            if os.path.isdir(src):
                if not os.path.isfile(os.path.join(src, "main.py")):
                    return False, L("文件夹插件必须包含 main.py", "Folder plugin must contain main.py")
                # Importing a folder that is already inside the plugin
                # directory must not delete its own source before copying.
                same_path = (os.path.normcase(os.path.abspath(src))
                             == os.path.normcase(os.path.abspath(dst)))
                if not same_path:
                    if os.path.exists(dst):
                        shutil.rmtree(dst)
                    shutil.copytree(src, dst)
                entry = os.path.join(dst, "main.py")
            else:
                if not src.lower().endswith(".py"):
                    return False, L("仅支持 .py 插件文件", "Only .py plugin files supported")
                # 源和目标相同则跳过复制（文件已就位）
                if os.path.normcase(os.path.abspath(src)) != os.path.normcase(os.path.abspath(dst)):
                    shutil.copy2(src, dst)
                entry = dst
        except Exception as e:
            return False, L(f"复制失败：{e}", f"Copy failed: {e}")

        self.discover()
        rec = self.record(target_pid)
        if rec is None:
            return False, L("插件记录未找到", "Plugin record not found")
        ok = self.load(rec)
        if ok:
            return True, L(f"插件已导入：{base}", f"Plugin imported: {base}")
        msg = L("插件导入但加载失败，请检查插件代码", "Imported but failed to load; check plugin code")
        if rec and rec.error:
            msg += f"\n{rec.error}"
        return False, msg

    def remove(self, pid: str) -> bool:
        """卸载并删除插件文件/文件夹。"""
        from app.ui.i18n import L
        rec = self.record(pid)
        if rec is None:
            return False
        self.unload(pid)
        root = plugins_dir()
        # 计算插件根路径（文件夹或 .py 文件）
        d = os.path.dirname(rec.path)
        target = d if os.path.dirname(d) == root else rec.path
        try:
            if os.path.isdir(target):
                shutil.rmtree(target)
            else:
                os.remove(target)
            # 同时清理市场图标缓存
            icon_f = os.path.join(root, f"{pid}.icon.png")
            if os.path.isfile(icon_f):
                os.remove(icon_f)
        except Exception as e:
            log.error(L(f"插件删除失败：{pid} — {e}", f"Plugin remove failed: {pid} — {e}"))
            return False
        self.discover()
        self.changed.emit()
        return True



# 全局单例（与 settings/log 同风格）
plugin_manager = PluginManager()
