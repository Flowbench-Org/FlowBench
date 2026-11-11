"""应用设置（JSON 持久化到 %APPDATA%/FlowBench）。"""
import copy
import json
import os
import tempfile
import threading
import time


class AppSettings:
    BACKUP_SCHEMA = "flowbench.settings.backup"
    BACKUP_SCHEMA_VERSION = 1
    # 插件市场搜索历史最多保留的条数（插件页每行 3 个，正好两行）。
    SEARCH_HISTORY_LIMIT = 6

    # 仅这些非敏感偏好可以进入可分享的设置备份。授权目标、上次压测目标、
    # GitHub Token、插件私有数据和搜索历史等运行状态一律不会导出或导入。
    SAFE_BACKUP_KEYS = (
        "theme",
        "theme_color",
        "language",
        "animations_enabled",
        "default_threads",
        "default_timeout_ms",
        "default_rate",
        "default_duration",
        "default_packet_size",
        "minimize_to_tray",
        "auto_check_updates",
    )

    _INT_RANGES = {
        "default_threads": (1, 1024),
        "default_timeout_ms": (500, 60000),
        "default_rate": (1, 100000),
        "default_duration": (1, 3600),
        "default_packet_size": (1, 1024 * 1024),
    }

    DEFAULTS = {
        "theme": "light",             # dark / light
        "theme_color": "#0078D4",     # 主题强调色（按钮、进度条等）
        "language": "auto",            # auto（跟随系统）/ zh-CN / en-US
        "animations_enabled": True,    # 页面切换与控件浮现动画
        "_lang_migrated": True,        # 新安装无需执行旧版固定语言迁移
        "default_threads": 8,
        "default_timeout_ms": 5000,
        "default_rate": 100,
        "default_duration": 30,
        "default_packet_size": 64,
        "log_dir": "",                # 空则使用默认目录
        "disclaimer_accepted": False,
        "authorized": [],             # [{host, note, ts}]
        "stress_form": {},            # 上次压测表单：{target, port, protocol, threads, rate, dur, dur_unit}
        "minimize_to_tray": True,     # 关闭时最小化到托盘
        "tray_notified": False,       # 托盘最小化提示是否已弹过（永久只提示一次）
        "auto_check_updates": True,   # 启动时自动检查更新
        "skip_version": "",           # 跳过的版本号（不再提示该版本）
        "plugins_disabled": [],       # 已禁用插件 ID 列表
        "plugin_data": {},            # 插件私有配置 {pid: {key: value}}
        "plugin_market_search_history": [],  # 插件市场搜索历史（最近优先）
        "plugin_market_favorites": [],       # 插件市场本地收藏 ID
        "github_token": "",           # GitHub Personal Access Token（一键发布插件用）
        "agent_hub_url": "",          # 服务器节点控制 Hub 地址
        "agent_hub_token": "",        # 服务器节点控制令牌
        "agent_hub_ca": "",           # 一体化包生成的自签名 CA 文件
        "github_login": "",           # 最近一次成功授权的 GitHub 登录名（离线识别作者）
    }

    def __init__(self):
        root = os.environ.get("APPDATA", os.path.expanduser("~"))
        base = os.path.join(root, "FlowBench")
        old = os.path.join(root, "FlowBenchPy", "settings.json")
        # 一次性迁移：旧版（FlowBenchPy）配置复制到新目录
        if not os.path.exists(os.path.join(base, "settings.json")) and os.path.exists(old):
            try:
                with open(old, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._atomic_write_json(os.path.join(base, "settings.json"), data)
            except Exception:
                pass
        try:
            os.makedirs(base, exist_ok=True)
        except OSError:
            # Read-only/redirected profiles should still allow the app to
            # start with an isolated temporary settings store.
            base = os.path.join(tempfile.gettempdir(), "FlowBench")
            os.makedirs(base, exist_ok=True)
        self.path = os.path.join(base, "settings.json")
        # 默认值里含有 list/dict；必须深拷贝，避免运行期修改污染类级默认值，
        # 否则“恢复默认”可能恢复到已经被改过的对象。
        self._data = copy.deepcopy(self.DEFAULTS)
        self._last_error = ""
        # Settings can be changed by UI callbacks and background plugin/collab
        # threads at the same time.  Serialize both memory updates and the
        # atomic file replacement so one writer cannot overwrite another.
        self._lock = threading.RLock()
        self.load()

    def load(self):
        with self._lock:
            try:
                if os.path.exists(self.path):
                    with open(self.path, "r", encoding="utf-8") as f:
                        raw = json.load(f)
                    clean = self._sanitize_loaded_data(raw)
                    self._data.update(clean)
                    needs_save = clean != raw
                    # 一次性迁移：旧配置的固定语言改为跟随系统（仅在从未迁移过时执行）
                    if raw.get("_lang_migrated") is not True:
                        self._data["language"] = "auto"
                        self._data["_lang_migrated"] = True
                        needs_save = True
                    if needs_save:
                        if not self.save():
                            return False
                self._last_error = ""
                return True
            except Exception as e:
                self._last_error = str(e)
                return False

    @classmethod
    def _sanitize_loaded_data(cls, raw):
        """修复 settings.json 中已知字段的错误类型，避免损坏配置拖垮 UI。"""
        if type(raw) is not dict:
            raise ValueError("settings root must be an object")
        clean = copy.deepcopy(raw)  # 保留未来版本或插件可能写入的未知字段

        for key in cls.SAFE_BACKUP_KEYS:
            if key not in raw:
                continue
            try:
                clean[key] = cls._validate_safe_value(key, raw[key])
            except ValueError:
                clean[key] = copy.deepcopy(cls.DEFAULTS[key])

        for key in ("disclaimer_accepted", "tray_notified"):
            if type(raw.get(key, cls.DEFAULTS[key])) is not bool:
                clean[key] = cls.DEFAULTS[key]

        for key in ("log_dir", "skip_version", "github_token", "github_login"):
            if type(raw.get(key, cls.DEFAULTS[key])) is not str:
                clean[key] = cls.DEFAULTS[key]

        if type(raw.get("stress_form", {})) is not dict:
            clean["stress_form"] = {}
        if type(raw.get("plugin_data", {})) is not dict:
            clean["plugin_data"] = {}

        # 字符串列表配置去掉损坏条目并去重，搜索历史与市场 UI 一致保留 SEARCH_HISTORY_LIMIT 条。
        for key in ("plugins_disabled", "plugin_market_search_history",
                    "plugin_market_favorites"):
            value = raw.get(key, cls.DEFAULTS[key])
            if type(value) is not list:
                clean[key] = copy.deepcopy(cls.DEFAULTS[key])
                continue
            items = []
            for item in value:
                if type(item) is str and item and item not in items:
                    items.append(item)
            if key == "plugin_market_search_history":
                items = items[:cls.SEARCH_HISTORY_LIMIT]
            clean[key] = items

        authorized = raw.get("authorized", cls.DEFAULTS["authorized"])
        if type(authorized) is not list:
            clean["authorized"] = []
        else:
            clean["authorized"] = [
                copy.deepcopy(item) for item in authorized
                if type(item) is dict and type(item.get("host")) is str
                and item["host"].strip()
            ]

        if type(raw.get("_lang_migrated", True)) is not bool:
            clean["_lang_migrated"] = True
        return clean

    @staticmethod
    def _atomic_write_json(path, data):
        """把 JSON 原子写入 ``path``；失败时保留原文件并抛出异常。"""
        target = os.path.abspath(path)
        folder = os.path.dirname(target) or os.getcwd()
        os.makedirs(folder, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(
            prefix=f".{os.path.basename(target)}.", suffix=".tmp", dir=folder)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                fd = -1
                json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, target)
        finally:
            if fd >= 0:
                os.close(fd)
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    def save(self):
        with self._lock:
            try:
                self._atomic_write_json(self.path, self._data)
                self._last_error = ""
                return True
            except Exception as e:
                self._last_error = str(e)
                return False

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        with self._lock:
            try:
                return self._data[name]
            except KeyError:
                return self.DEFAULTS.get(name)

    def set(self, name, value):
        with self._lock:
            existed = name in self._data
            old_value = self._data.get(name)
            self._data[name] = value
            if self.save():
                return True
            # 落盘失败时回滚内存状态，避免界面看似已保存、重启后却丢失。
            if existed:
                self._data[name] = old_value
            else:
                self._data.pop(name, None)
            return False

    @property
    def last_error(self):
        with self._lock:
            return self._last_error

    @classmethod
    def _validate_safe_value(cls, key, value):
        """校验并返回一个可导入的安全偏好值。"""
        if key == "theme":
            if type(value) is not str or value not in ("light", "dark"):
                raise ValueError("theme must be 'light' or 'dark'")
            return value
        if key == "theme_color":
            if (type(value) is not str or len(value) != 7 or not value.startswith("#")
                    or any(ch not in "0123456789abcdefABCDEF" for ch in value[1:])):
                raise ValueError("theme_color must be #RRGGBB")
            return value.upper()
        if key == "language":
            if type(value) is not str or value not in ("auto", "zh-CN", "en-US"):
                raise ValueError("language must be auto, zh-CN or en-US")
            return value
        if key in cls._INT_RANGES:
            lo, hi = cls._INT_RANGES[key]
            if type(value) is not int or not lo <= value <= hi:
                raise ValueError(f"{key} must be an integer in [{lo}, {hi}]")
            return value
        if key in ("animations_enabled", "minimize_to_tray",
                   "auto_check_updates"):
            if type(value) is not bool:
                raise ValueError(f"{key} must be a boolean")
            return value
        raise ValueError(f"setting is not allowed in a backup: {key}")

    def safe_snapshot(self):
        """返回适合导出/诊断的脱敏偏好副本。"""
        with self._lock:
            result = {}
            for key in self.SAFE_BACKUP_KEYS:
                value = self._data.get(key, self.DEFAULTS[key])
                result[key] = self._validate_safe_value(key, copy.deepcopy(value))
            return result

    def export_backup(self, path, app_version=""):
        """导出版本化、脱敏的设置备份，返回写出的安全设置数量。"""
        safe = self.safe_snapshot()
        payload = {
            "schema": self.BACKUP_SCHEMA,
            "schema_version": self.BACKUP_SCHEMA_VERSION,
            "app_version": str(app_version or ""),
            "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "settings": safe,
        }
        self._atomic_write_json(path, payload)
        return len(safe)

    @classmethod
    def _validate_backup_payload(cls, payload):
        if type(payload) is not dict:
            raise ValueError("backup root must be an object")
        if payload.get("schema") != cls.BACKUP_SCHEMA:
            raise ValueError("unrecognized backup schema")
        version = payload.get("schema_version")
        if type(version) is not int or version != cls.BACKUP_SCHEMA_VERSION:
            raise ValueError(f"unsupported backup schema version: {version}")
        raw = payload.get("settings")
        if type(raw) is not dict:
            raise ValueError("backup settings must be an object")

        allowed = set(cls.SAFE_BACKUP_KEYS)
        validated = {}
        ignored = []
        for key, value in raw.items():
            if key not in allowed:
                ignored.append(str(key))
                continue
            validated[key] = cls._validate_safe_value(key, value)
        if not validated:
            raise ValueError("backup contains no supported settings")
        return validated, sorted(ignored)

    def import_backup(self, path):
        """校验并导入设置备份；提交前把完整当前设置原子保存为 settings.json.bak。"""
        with self._lock:
            if os.path.getsize(path) > 1024 * 1024:
                raise ValueError("backup file is larger than 1 MiB")
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            validated, ignored = self._validate_backup_payload(payload)

            candidate = copy.deepcopy(self._data)
            candidate.update(validated)
            backup_path = self.path + ".bak"
            # 先完成完整本地备份；若备份失败，则绝不覆盖当前设置。
            self._atomic_write_json(backup_path, self._data)
            self._atomic_write_json(self.path, candidate)
            self._data = candidate
            self._last_error = ""
            return {
                "applied": sorted(validated),
                "ignored": ignored,
                "backup_path": backup_path,
            }

    def reset_preferences(self):
        """仅恢复可备份的普通偏好，不触碰授权、Token、插件数据等敏感状态。"""
        with self._lock:
            candidate = copy.deepcopy(self._data)
            changed = []
            for key in self.SAFE_BACKUP_KEYS:
                default = copy.deepcopy(self.DEFAULTS[key])
                if candidate.get(key) != default:
                    changed.append(key)
                candidate[key] = default
            self._atomic_write_json(self.path, candidate)
            self._data = candidate
            self._last_error = ""
            return sorted(changed)


settings = AppSettings()
