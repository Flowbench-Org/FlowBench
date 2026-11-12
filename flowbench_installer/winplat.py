"""Windows integration: folders, shortcuts, the uninstall entry and processes.

Everything here is a thin wrapper over a documented Win32 API.  On other
platforms the functions degrade to harmless no-ops so the installer logic can
be tested headless.  Only the standard library is imported at module level,
so the silent uninstall path never needs Qt or other third-party packages.
"""
from __future__ import annotations

import datetime as _dt
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable, List, Optional

IS_WINDOWS = sys.platform == "win32"

APP_NAME = "FlowBench"
APP_USER_MODEL_ID = "FlowBench.App"          # must match main.py
PUBLISHER = "FlowBench"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\FlowBench"
# Inno Setup builds up to 1.2.7, and the older NetPulse name.
LEGACY_APP_IDS = (
    "{652A3085-2CA0-4898-B675-C69E34D5AE6D}_is1",
    "{8F4A2C1E-9B3D-4E6A-B5C7-1A2B3C4D5E6F}_is1",
)
_UNINSTALL_ROOT = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"

FOLDERID_DESKTOP = "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}"
FOLDERID_PROGRAMS = "{A77F5D77-2E2B-44C3-A6A2-ABA601054A51}"


# ----------------------------------------------------------- environment --

def clean_child_env(env: Optional[dict] = None) -> dict:
    """Environment for starting another PyInstaller program.

    A frozen parent leaves ``_PYI_*`` variables behind; a frozen child that
    inherits them can mistake itself for the parent's helper process and fail
    to start.  ``PYINSTALLER_RESET_ENVIRONMENT`` asks PyInstaller >= 6.9 to
    reset the rest.
    """
    src = dict(os.environ if env is None else env)
    out = {k: v for k, v in src.items()
           if not k.upper().startswith("_PYI_") and k.upper() != "_MEIPASS2"}
    out["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return out


def launch_detached(exe, args: Iterable[str] = (), cwd=None) -> subprocess.Popen:
    """Start a program that outlives this process."""
    cmd = [str(exe), *[str(a) for a in args]]
    kwargs = {"cwd": str(cwd) if cwd else None, "env": clean_child_env(),
              "close_fds": True, "stdin": subprocess.DEVNULL,
              "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if IS_WINDOWS:
        import ctypes
        # The onefile bootloader points the DLL search path at its temp
        # folder; children must not inherit that.
        try:
            ctypes.windll.kernel32.SetDllDirectoryW(None)
        except Exception:
            pass
        kwargs["creationflags"] = (subprocess.DETACHED_PROCESS
                                   | subprocess.CREATE_NEW_PROCESS_GROUP)
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(cmd, **kwargs)


# ---------------------------------------------------------------- folders --

def _known_folder(folder_id: str) -> Optional[str]:
    if not IS_WINDOWS:
        return None
    import ctypes
    guid = _guid(folder_id)
    ptr = ctypes.c_void_p()
    try:
        ctypes.oledll.shell32.SHGetKnownFolderPath(
            ctypes.byref(guid), 0, None, ctypes.byref(ptr))
        return ctypes.wstring_at(ptr.value)
    except OSError:
        return None
    finally:
        if ptr.value:
            ctypes.windll.ole32.CoTaskMemFree(ptr)


def desktop_dir() -> Path:
    found = _known_folder(FOLDERID_DESKTOP)
    return Path(found) if found else Path.home() / "Desktop"


def start_menu_programs_dir() -> Path:
    found = _known_folder(FOLDERID_PROGRAMS)
    if found:
        return Path(found)
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def default_install_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Programs" / APP_NAME


def shortcut_paths() -> dict:
    return {"start_menu": start_menu_programs_dir() / f"{APP_NAME}.lnk",
            "desktop": desktop_dir() / f"{APP_NAME}.lnk"}


# -------------------------------------------------------------- shortcuts --

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    class _GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                    ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

    class _PROPERTYKEY(ctypes.Structure):
        _fields_ = [("fmtid", _GUID), ("pid", ctypes.c_ulong)]

    class _PROPVARIANT(ctypes.Structure):
        # vt + 3 reserved WORDs, then the 16-byte value union.
        _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort),
                    ("r2", ctypes.c_ushort), ("r3", ctypes.c_ushort),
                    ("value", ctypes.c_void_p), ("pad", ctypes.c_void_p)]

    class _SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong),
                    ("hwnd", wintypes.HWND), ("lpVerb", wintypes.LPCWSTR),
                    ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                    ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int),
                    ("hInstApp", wintypes.HINSTANCE), ("lpIDList", ctypes.c_void_p),
                    ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                    ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE),
                    ("hProcess", wintypes.HANDLE)]

    def _guid(text: str) -> "_GUID":
        guid = _GUID()
        ctypes.oledll.ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(guid))
        return guid

    def _com(obj, index, *argtypes):
        """Bind vtable slot ``index`` of a COM interface pointer."""
        vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        proto = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)
        fn = proto(vtable[index])
        return lambda *args: fn(obj, *args)

    def _release(obj):
        if obj:
            vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])(obj)

    def _query(obj, iid_text):
        out = ctypes.c_void_p()
        _com(obj, 0, ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p))(
            ctypes.byref(_guid(iid_text)), ctypes.byref(out))
        return out
else:  # pragma: no cover - keeps names defined for type checkers
    def _guid(text):
        raise OSError("Windows only")


CLSID_SHELL_LINK = "{00021401-0000-0000-C000-000000000046}"
IID_ISHELL_LINK_W = "{000214F9-0000-0000-C000-000000000046}"
IID_IPERSIST_FILE = "{0000010B-0000-0000-C000-000000000046}"
IID_IPROPERTY_STORE = "{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}"
PKEY_APP_USER_MODEL = "{9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}"  # pid 5 = ID
VT_LPWSTR = 31


def create_shortcut(link: Path, target: Path, workdir: Path, icon: Path,
                    description: str = "", app_id: str = APP_USER_MODEL_ID) -> None:
    """Create a .lnk carrying the AppUserModelID tray toasts rely on."""
    if not IS_WINDOWS:
        return
    link = Path(link)
    link.parent.mkdir(parents=True, exist_ok=True)
    ole32 = ctypes.windll.ole32
    init = ole32.CoInitializeEx(None, 0x2)   # COINIT_APARTMENTTHREADED
    shell_link = ctypes.c_void_p()
    store = persist = None
    try:
        ctypes.oledll.ole32.CoCreateInstance(
            ctypes.byref(_guid(CLSID_SHELL_LINK)), None, 1,      # INPROC_SERVER
            ctypes.byref(_guid(IID_ISHELL_LINK_W)), ctypes.byref(shell_link))
        _com(shell_link, 20, wintypes.LPCWSTR)(str(target))               # SetPath
        _com(shell_link, 9, wintypes.LPCWSTR)(str(workdir))               # SetWorkingDirectory
        _com(shell_link, 7, wintypes.LPCWSTR)(description)                # SetDescription
        _com(shell_link, 17, wintypes.LPCWSTR, ctypes.c_int)(str(icon), 0)  # SetIconLocation
        if app_id:
            store = _query(shell_link, IID_IPROPERTY_STORE)
            key = _PROPERTYKEY(_guid(PKEY_APP_USER_MODEL), 5)
            text = ctypes.create_unicode_buffer(app_id)
            value = _PROPVARIANT(VT_LPWSTR, 0, 0, 0, ctypes.addressof(text), None)
            _com(store, 6, ctypes.POINTER(_PROPERTYKEY), ctypes.POINTER(_PROPVARIANT))(
                ctypes.byref(key), ctypes.byref(value))                   # SetValue
            _com(store, 7)()                                              # Commit
        persist = _query(shell_link, IID_IPERSIST_FILE)
        _com(persist, 6, wintypes.LPCWSTR, wintypes.BOOL)(str(link), True)  # Save
    finally:
        _release(store)
        _release(persist)
        _release(shell_link)
        if init in (0, 1):
            ole32.CoUninitialize()


def remove_files(paths: Iterable) -> None:
    for path in paths:
        try:
            Path(path).unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def notify_shell() -> None:
    """Ask Explorer to refresh icons after shortcuts change."""
    if IS_WINDOWS:
        try:
            ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)
        except Exception:
            pass


# --------------------------------------------------------------- registry --

def write_uninstall_entry(target: Path, version: str, size_bytes: int) -> None:
    if not IS_WINDOWS:
        return
    import winreg
    exe = Path(target) / "FlowBench.exe"
    uninstaller = Path(target) / "FlowBench-Uninstall.exe"
    values = {
        "DisplayName": APP_NAME,
        "DisplayVersion": version,
        "Publisher": PUBLISHER,
        "DisplayIcon": f"{exe},0",
        "InstallLocation": str(target),
        "UninstallString": f'"{uninstaller}"',
        "QuietUninstallString": f'"{uninstaller}" --quiet',
        "InstallDate": _dt.date.today().strftime("%Y%m%d"),
    }
    dwords = {"EstimatedSize": max(1, int(size_bytes) // 1024),
              "NoModify": 1, "NoRepair": 1}
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY, 0,
                            winreg.KEY_WRITE) as key:
        for name, value in values.items():
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        for name, value in dwords.items():
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, value)


def delete_uninstall_entry() -> None:
    if not IS_WINDOWS:
        return
    import winreg
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except FileNotFoundError:
        pass


def registered_install_dir() -> Optional[Path]:
    if not IS_WINDOWS:
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            value, _ = winreg.QueryValueEx(key, "InstallLocation")
        return Path(value) if value else None
    except OSError:
        return None


def find_legacy_installs() -> List[dict]:
    """Inno Setup installs of FlowBench 1.2.7 and earlier (incl. NetPulse)."""
    if not IS_WINDOWS:
        return []
    import winreg
    views = [(winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY, True),
             (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY, True),
             (winreg.HKEY_CURRENT_USER, 0, False)]
    found, seen = [], set()
    for hive, view, admin in views:
        for app_id in LEGACY_APP_IDS:
            try:
                with winreg.OpenKey(hive, rf"{_UNINSTALL_ROOT}\{app_id}", 0,
                                    winreg.KEY_READ | view) as key:
                    def get(name):
                        try:
                            return str(winreg.QueryValueEx(key, name)[0])
                        except OSError:
                            return ""
                    uninstall = get("UninstallString").strip().strip('"')
                    entry = {"app_id": app_id, "admin": admin,
                             "uninstall": uninstall,
                             "location": get("InstallLocation").rstrip("\\/"),
                             "version": get("DisplayVersion"),
                             "name": get("DisplayName")}
            except OSError:
                continue
            if uninstall and uninstall.lower() not in seen:
                seen.add(uninstall.lower())
                found.append(entry)
    return found


def run_legacy_uninstall(entry: dict, timeout: float = 300) -> int:
    """Run an old Inno uninstaller silently; elevate when it was a machine install.

    Returns the exit code, or 1223 (ERROR_CANCELLED) if the UAC prompt was
    declined.
    """
    if not IS_WINDOWS:
        return 0
    params = "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART"
    info = _SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x40 | 0x100                 # NOCLOSEPROCESS | NOASYNC
    info.lpVerb = "runas" if entry.get("admin") else "open"
    info.lpFile = entry["uninstall"]
    info.lpParameters = params
    info.nShow = 0
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(_SHELLEXECUTEINFOW)]
    shell32.ShellExecuteExW.restype = wintypes.BOOL
    # ShellExecuteEx may use COM; this runs on a worker thread.
    init = ctypes.windll.ole32.CoInitializeEx(None, 0x2 | 0x4)  # STA | DISABLE_OLE1DDE
    try:
        if not shell32.ShellExecuteExW(ctypes.byref(info)):
            return ctypes.get_last_error() or 1
    finally:
        if init in (0, 1):
            ctypes.windll.ole32.CoUninitialize()
    kernel32 = ctypes.windll.kernel32
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    code = wintypes.DWORD(0)
    try:
        kernel32.WaitForSingleObject(info.hProcess, int(timeout * 1000))
        kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
    finally:
        kernel32.CloseHandle(info.hProcess)
    # Inno hands the work to a temporary copy of itself; wait for the files
    # to actually disappear before reporting success.
    exe = Path(entry.get("location") or "") / "FlowBench.exe"
    deadline = time.monotonic() + 60
    while entry.get("location") and exe.exists() and time.monotonic() < deadline:
        time.sleep(0.5)
    return int(code.value)


# --------------------------------------------------------------- processes --

def running_instances(exe_paths: Iterable) -> List[int]:
    """PIDs of processes started from any of ``exe_paths``."""
    wanted = {os.path.normcase(os.path.abspath(str(p))) for p in exe_paths}
    if not wanted:
        return []
    try:
        import psutil
    except ImportError:
        return []
    pids = []
    for proc in psutil.process_iter(["exe"]):
        exe = proc.info.get("exe")
        if exe and os.path.normcase(os.path.abspath(exe)) in wanted:
            pids.append(proc.pid)
    return pids


def wait_for_exit(pid: int, timeout: float) -> bool:
    """Wait for ``pid`` to exit; True when it is gone.

    Never uses ``os.kill(pid, 0)`` on Windows: there it terminates the process.
    """
    if IS_WINDOWS:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.OpenProcess(0x00100000, False, int(pid))   # SYNCHRONIZE
        if not handle:
            return True        # already gone
        try:
            return kernel32.WaitForSingleObject(handle, int(timeout * 1000)) == 0
        finally:
            kernel32.CloseHandle(handle)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            pass
        time.sleep(0.1)
    return False


# ---------------------------------------------------------------- language --

def system_prefers_chinese() -> bool:
    if IS_WINDOWS:
        try:
            return (ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF) == 0x04
        except Exception:
            pass
    lang = os.environ.get("LANG", "") or os.environ.get("LANGUAGE", "")
    return lang.lower().startswith("zh")


def animations_enabled() -> bool:
    """Windows' "Animation effects" setting (Accessibility → Visual effects)."""
    if not IS_WINDOWS:
        return True
    try:
        value = wintypes.BOOL()
        SPI_GETCLIENTAREAANIMATION = 0x1042
        if ctypes.windll.user32.SystemParametersInfoW(
                SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(value), 0):
            return bool(value.value)
    except Exception:
        pass
    return True


def set_dark_title_bar(hwnd: int, dark: bool) -> None:
    """Match the native title bar to the window theme (Windows 10 1809+)."""
    if not IS_WINDOWS:
        return
    value = ctypes.c_int(1 if dark else 0)
    for attribute in (20, 19):   # DWMWA_USE_IMMERSIVE_DARK_MODE (new, old)
        try:
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    wintypes.HWND(hwnd), attribute, ctypes.byref(value),
                    ctypes.sizeof(value)) == 0:
                return
        except Exception:
            return
