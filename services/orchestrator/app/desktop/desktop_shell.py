from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False) and getattr(sys, "_MEIPASS", None))


def _meipass() -> Optional[Path]:
    p = getattr(sys, "_MEIPASS", None)
    return Path(p) if p else None


def _orchestrator_dir() -> Path:
    # Source layout: .../services/orchestrator/app/desktop/desktop_shell.py -> orchestrator is parents[2]
    # Frozen layout (PyInstaller): __file__ may be just "desktop_shell.py" under sys._MEIPASS.
    if _is_frozen() and _meipass():
        return _meipass()
    p = Path(__file__).resolve()
    return p.parents[2] if len(p.parents) >= 3 else p.parent


def _repo_root_from_orchestrator(orchestrator_dir: Path) -> Path:
    # .../services/orchestrator -> repo is parents[1]
    return orchestrator_dir.parents[1]


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_http_ok(url: str, timeout_s: float = 20.0) -> None:
    import urllib.request

    deadline = time.time() + timeout_s
    last_err: Optional[BaseException] = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:  # nosec - local loopback
                if 200 <= int(getattr(r, "status", 200)) < 500:
                    return
        except BaseException as e:  # noqa: BLE001 - best-effort wait loop
            last_err = e
        time.sleep(0.2)
    raise RuntimeError(f"Backend did not become ready: {url}. Last error: {last_err}")


def _default_product_data_root() -> Path:
    base = os.getenv("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    base_path = _short_path(Path(base))
    return base_path / "OpenAgentWorkbench"


def _short_path(path: Path) -> Path:
    """
    Best-effort conversion to Windows 8.3 short paths.
    This avoids edge cases where some native components struggle with non-ASCII user profile paths.
    """
    if os.name != "nt":
        return path
    try:
        import ctypes

        buf = ctypes.create_unicode_buffer(4096)
        res = ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, len(buf))  # type: ignore[attr-defined]
        if res and buf.value:
            return Path(buf.value)
    except Exception:
        return path
    return path


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        if not key:
            continue
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        # Do not override explicitly-set env vars.
        if os.getenv(key) is None or os.getenv(key) == "":
            os.environ[key] = val


def _ensure_desktop_env(orchestrator_dir: Path) -> None:
    repo_root: Optional[Path] = None
    if not _is_frozen():
        repo_root = _repo_root_from_orchestrator(orchestrator_dir)

    # Load .env so a packaged desktop build can be configured without a shell.
    if _is_frozen():
        data_root = _default_product_data_root()
        _load_dotenv(data_root / ".env")
        _load_dotenv(Path(sys.executable).resolve().parent / ".env")
    elif repo_root is not None:
        _load_dotenv(repo_root / ".env")

    # Store runtime data under AppData for the desktop build unless user overrides.
    if not os.getenv("DATA_DIR"):
        if _is_frozen():
            data_root = _default_product_data_root()
            os.environ["DATA_DIR"] = str((data_root / "data").resolve())
        else:
            if repo_root is None:
                os.environ["DATA_DIR"] = str((Path.cwd() / "data").resolve())
            else:
                os.environ["DATA_DIR"] = str((repo_root / "data").resolve())

    data_dir = Path(os.environ["DATA_DIR"]).resolve()
    # Ensure folders exist before the backend initializes SQLite/WAL files.
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("DB_PATH", str((data_dir / "workbench.db").resolve()))
    os.environ.setdefault("WORKSPACES_DIR", str((data_dir / "workspaces").resolve()))
    os.environ.setdefault("ARTIFACTS_DIR", str((data_dir / "artifacts").resolve()))
    os.environ.setdefault("LOGS_DIR", str((data_dir / "logs").resolve()))

    Path(os.environ["WORKSPACES_DIR"]).mkdir(parents=True, exist_ok=True)
    Path(os.environ["ARTIFACTS_DIR"]).mkdir(parents=True, exist_ok=True)
    Path(os.environ["LOGS_DIR"]).mkdir(parents=True, exist_ok=True)
    Path(os.environ["DB_PATH"]).resolve().parent.mkdir(parents=True, exist_ok=True)

    # Apply persisted runtime env (provider + desktop settings) if present.
    _apply_runtime_env_json(data_dir)

    # Skills location: bundle into the .exe, fall back to repo ./skills.
    if not os.getenv("SKILLS_DIR"):
        if _is_frozen() and _meipass():
            bundled = _meipass() / "skills"
            os.environ["SKILLS_DIR"] = str(bundled.resolve())
        else:
            if repo_root is None:
                os.environ["SKILLS_DIR"] = str((Path.cwd() / "skills").resolve())
            else:
                os.environ["SKILLS_DIR"] = str((repo_root / "skills").resolve())

    # Desktop app should bind to loopback only.
    os.environ.setdefault("APP_HOST", "127.0.0.1")
    os.environ.setdefault("OWB_DESKTOP", "1")

    # Store Playwright browsers under the product data directory so installs persist across updates.
    # (Only used by the browser tool; download happens on-demand.)
    try:
        data_dir = Path(os.environ["DATA_DIR"]).resolve()
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str((data_dir / "playwright-browsers").resolve()))
    except Exception:
        pass

    # If user copied .env.example, it may contain the docker-internal LiteLLM URL.
    # In desktop/local mode, default to 0-0.pro unless overridden.
    if not os.getenv("OPENAI_BASE_URL") or os.getenv("OPENAI_BASE_URL") == "http://litellm:4000/v1":
        os.environ["OPENAI_BASE_URL"] = "https://0-0.pro/v1"


def _webview2_installed() -> bool:
    """
    Best-effort detection of Microsoft Edge WebView2 Runtime.
    pywebview can fall back to legacy MSHTML, but we require WebView2 for a modern desktop UX.
    """
    if os.name != "nt":
        return True
    try:
        import winreg

        # Require .NET >= 4.6.2 (matches pywebview's winforms check).
        net_key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full")
        try:
            version, _ = winreg.QueryValueEx(net_key, "Release")
            if int(version or 0) < 394802:
                return False
        finally:
            try:
                winreg.CloseKey(net_key)
            except Exception:
                pass

        def _parse_ver(v: str) -> tuple[int, int, int, int]:
            parts = [p for p in str(v or "").split(".") if p.strip().isdigit()]
            nums = [int(p) for p in parts[:4]]
            while len(nums) < 4:
                nums.append(0)
            return nums[0], nums[1], nums[2], nums[3]

        def _ge(a: str, b: str) -> bool:
            return _parse_ver(a) >= _parse_ver(b)

        # WebView2 Runtime registry IDs (same as pywebview).
        candidates = [
            "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",  # Runtime
            "{2CD8A007-E189-409D-A2C8-9AF4EF3C72AA}",  # Beta
            "{0D50BFEC-CD6A-4F9A-964C-C7416E3ACB10}",  # Dev
            "{65C35B14-6C1D-4122-AC46-7148CC9D6497}",  # Canary
        ]
        min_ver = "86.0.622.0"

        for key in candidates:
            for root_name in ("HKEY_CURRENT_USER", "HKEY_LOCAL_MACHINE"):
                root = getattr(winreg, root_name)
                # pywebview checks WOW6432Node for non-x86. Keep both for robustness.
                paths = [
                    rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{key}",
                    rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{key}",
                ]
                for p in paths:
                    try:
                        reg = winreg.OpenKey(root, p)
                    except Exception:
                        continue
                    try:
                        ver, _ = winreg.QueryValueEx(reg, "pv")
                        if isinstance(ver, str) and _ge(ver, min_ver):
                            return True
                    except Exception:
                        pass
                    finally:
                        try:
                            winreg.CloseKey(reg)
                        except Exception:
                            pass
    except Exception:
        # If detection fails, don't block startup; pywebview will still try.
        return True
    return False


def _message_box(*, title: str, text: str, flags: int) -> int:
    if os.name != "nt":
        return 0
    try:
        import ctypes

        return int(ctypes.windll.user32.MessageBoxW(None, str(text), str(title), int(flags)))  # type: ignore[attr-defined]
    except Exception:
        return 0


def _open_url(url: str) -> None:
    try:
        if os.name == "nt":
            os.startfile(url)  # type: ignore[attr-defined]
            return
    except Exception:
        pass
    try:
        import webbrowser

        webbrowser.open(url)
    except Exception:
        pass


def _ensure_webview2_or_install() -> None:
    if _webview2_installed():
        return

    title = "OpenAgent Workbench"
    # Desktop UX: do not ask the user to manually download/install.
    # Attempt a silent install first (installer bundles the bootstrapper; this is a fallback).

    setup_path: Optional[Path] = None
    try:
        if _is_frozen() and _meipass():
            cand = _meipass() / "vendor" / "webview2" / "MicrosoftEdgeWebView2Setup.exe"
            if cand.exists():
                setup_path = cand
    except Exception:
        setup_path = None

    # Fallback: download bootstrapper into DATA_DIR if it's not bundled.
    if setup_path is None:
        data_dir = Path(os.getenv("DATA_DIR", str(Path.cwd() / "data"))).resolve()
        data_dir.mkdir(parents=True, exist_ok=True)
        setup_path = data_dir / "MicrosoftEdgeWebView2Setup.exe"
        if not setup_path.exists():
            import urllib.request

            urllib.request.urlretrieve("https://go.microsoft.com/fwlink/p/?LinkId=2124703", str(setup_path))  # nosec - trusted MS URL

    try:
        popen_kwargs: Dict[str, Any] = {}
        if os.name == "nt":
            popen_kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
            try:
                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                si.wShowWindow = subprocess.SW_HIDE
                popen_kwargs["startupinfo"] = si
            except Exception:
                pass

        # WebView2 bootstrapper supports silent install.
        subprocess.run(  # noqa: S603,S607 - intended installer bootstrapper
            [str(setup_path), "/silent", "/install"],
            check=False,
            **popen_kwargs,
        )
    except Exception as e:
        _message_box(
            title=title,
            text=f"无法启动 WebView2 安装程序：{e}\n\n请手动安装后再启动应用。",
            flags=0x00 | 0x10,  # MB_OK | MB_ICONERROR
        )
        _open_url("https://go.microsoft.com/fwlink/p/?LinkId=2124703")
        raise RuntimeError("WebView2 Runtime install failed.") from e

    if not _webview2_installed():
        _message_box(
            title=title,
            text="WebView2 安装完成后仍未检测到可用运行时。\n\n请重启电脑后再启动应用，或手动安装 Evergreen Runtime。",
            flags=0x00 | 0x30,  # MB_OK | MB_ICONWARNING
        )
        _open_url("https://go.microsoft.com/fwlink/p/?LinkId=2124703")
        raise RuntimeError("WebView2 Runtime is required.")


def _ensure_admin_token() -> str:
    existing = os.getenv("UI_ADMIN_TOKEN")
    if existing:
        return existing

    data_dir = Path(os.getenv("DATA_DIR", str(Path.cwd() / "data"))).resolve()
    token_path = data_dir / "ui_admin_token.txt"
    if token_path.exists():
        token = token_path.read_text(encoding="utf-8").strip()
        if token:
            os.environ["UI_ADMIN_TOKEN"] = token
            return token

    import secrets

    token = secrets.token_hex(16)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(token, encoding="utf-8")
    os.environ["UI_ADMIN_TOKEN"] = token
    return token


def _apply_runtime_env_json(data_dir: Path) -> None:
    p = data_dir / "runtime_env.json"
    if not p.exists():
        return
    try:
        import json

        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return
    except Exception:
        return
    for k, v in data.items():
        if not isinstance(k, str) or not isinstance(v, str):
            continue
        if os.getenv(k) is None or os.getenv(k) == "":
            os.environ[k] = v


def _ensure_window_icon_file() -> Optional[str]:
    """
    Return a filesystem path to an .ico for the window/taskbar icon.
    pywebview expects a real file path on Windows (even in onefile builds).
    """
    try:
        from .icon_assets import write_ico

        data_dir = Path(os.getenv("DATA_DIR", str(Path.cwd() / "data"))).resolve()
        data_dir.mkdir(parents=True, exist_ok=True)
        icon_path = data_dir / "app-icon.ico"
        write_ico(icon_path)
        return str(icon_path)
    except Exception:
        return None


def _start_backend_subprocess(orchestrator_dir: Path, port: int) -> subprocess.Popen:
    logs_dir = Path(os.getenv("LOGS_DIR", str(Path.cwd() / "data" / "logs"))).resolve()
    logs_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = logs_dir / "desktop-backend.out.log"
    stderr_path = logs_dir / "desktop-backend.err.log"

    if _is_frozen():
        cmd = [sys.executable, "--backend", "--port", str(port)]
        cwd = None
    else:
        cmd = [sys.executable, "-m", "app.desktop.desktop_shell", "--backend", "--port", str(port)]
        cwd = str(orchestrator_dir)

    # Detach from console; desktop UX should be windowed.
    creationflags = 0
    if os.name == "nt":
        creationflags = 0x08000000  # CREATE_NO_WINDOW

    env = os.environ.copy()
    # Ensure the backend can self-terminate if the desktop UI process exits unexpectedly.
    try:
        env.setdefault("OWB_PARENT_PID", str(os.getpid()))
    except Exception:
        pass

    return subprocess.Popen(  # noqa: S603 - intended local child process
        cmd,
        cwd=cwd,
        # Append so users keep historical logs for debugging (do not truncate on every launch).
        stdout=stdout_path.open("a", encoding="utf-8"),
        stderr=stderr_path.open("a", encoding="utf-8"),
        env=env,
        creationflags=creationflags,
    )


def _backend_main(port: int) -> None:
    # In PyInstaller --windowed mode, stdout/stderr may be None, which can confuse some loggers/servers.
    try:
        if sys.stdout is None:  # type: ignore[truthy-bool]
            sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
        if sys.stderr is None:  # type: ignore[truthy-bool]
            sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    except Exception:
        pass

    # If launched as a child of the desktop UI, exit automatically when the UI process is gone.
    parent_pid = 0
    try:
        parent_pid = int(os.getenv("OWB_PARENT_PID") or 0)
    except Exception:
        parent_pid = 0

    def _pid_is_running(pid: int) -> bool:
        if pid <= 0:
            return False
        if os.name != "nt":
            try:
                os.kill(pid, 0)
                return True
            except Exception:
                return False
        try:
            import ctypes
            from ctypes import wintypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
            if not handle:
                return False
            try:
                exit_code = wintypes.DWORD()
                ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
                if ok == 0:
                    return True
                STILL_ACTIVE = 259
                return int(exit_code.value) == STILL_ACTIVE
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            # If we can't check reliably, do not terminate.
            return True

    if parent_pid > 0:
        def _watch_parent() -> None:
            while True:
                try:
                    if not _pid_is_running(parent_pid):
                        try:
                            sys.stderr.write(f"[owb] parent process {parent_pid} exited; stopping backend\n")
                            sys.stderr.flush()
                        except Exception:
                            pass
                        os._exit(0)
                except Exception:
                    pass
                time.sleep(2.0)

        threading.Thread(target=_watch_parent, daemon=True).start()

    try:
        sys.stderr.write(
            f"[owb] backend mode env DATA_DIR={os.getenv('DATA_DIR','')} DB_PATH={os.getenv('DB_PATH','')}\n"
        )
        sys.stderr.flush()
    except Exception:
        pass
    # Run uvicorn in-process for the backend child mode.
    import uvicorn

    host = os.getenv("APP_HOST", "127.0.0.1")
    os.environ["APP_PORT"] = str(port)
    # Import explicitly so PyInstaller can see the dependency (uvicorn string import is dynamic).
    from app.main import app as asgi_app

    uvicorn.run(asgi_app, host=host, port=port, log_level="info")


def _create_tray_icon(window, backend_proc: Optional[subprocess.Popen], start_url: str):
    try:
        import pystray
        from PIL import Image
    except Exception:  # noqa: BLE001 - optional dependency
        return None

    def _icon_image() -> "Image.Image":
        try:
            from .icon_assets import icon_image

            return icon_image(size=64)
        except Exception:
            # Fallback: simple dot icon
            img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            for y in range(16, 48):
                for x in range(16, 48):
                    img.putpixel((x, y), (14, 165, 233, 255))
            return img

    def _show(_icon, _item):
        try:
            window.show()
        except Exception:  # noqa: BLE001
            pass

    def _hide(_icon, _item):
        try:
            window.hide()
        except Exception:  # noqa: BLE001
            pass

    def _reload(_icon, _item):
        try:
            window.load_url(start_url)
        except Exception:  # noqa: BLE001
            pass

    def _open_data(_icon, _item):
        data_dir = Path(os.getenv("DATA_DIR", str(Path.cwd() / "data"))).resolve()
        try:
            os.startfile(str(data_dir))  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass

    def _open_logs(_icon, _item):
        logs_dir = Path(os.getenv("LOGS_DIR", str(Path.cwd() / "data" / "logs"))).resolve()
        try:
            os.startfile(str(logs_dir))  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass

    def _quit(_icon, _item):
        def _stop_backend() -> None:
            try:
                if backend_proc is None:
                    return
            except Exception:
                return
            try:
                backend_proc.terminate()
            except Exception:
                pass
            try:
                backend_proc.wait(timeout=4.0)
                return
            except Exception:
                pass
            try:
                backend_proc.kill()
            except Exception:
                pass

        try:
            window.destroy()
        except Exception:  # noqa: BLE001
            pass
        try:
            _stop_backend()
        except Exception:  # noqa: BLE001
            pass
        _icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Show", _show, default=True),
        pystray.MenuItem("Hide", _hide),
        pystray.MenuItem("Reload", _reload),
        pystray.MenuItem("Open Data Folder", _open_data),
        pystray.MenuItem("Open Logs Folder", _open_logs),
        pystray.MenuItem("Quit", _quit),
    )
    icon = pystray.Icon("OpenAgentWorkbench", _icon_image(), "OpenAgent Workbench", menu)

    t = threading.Thread(target=icon.run, daemon=True)
    t.start()
    return icon


def _desktop_main() -> int:
    parser = argparse.ArgumentParser(description="OpenAgent Workbench Desktop Shell (embedded WebView + tray).")
    parser.add_argument("--backend", action="store_true", help="Run backend server mode (internal).")
    parser.add_argument("--port", type=int, default=0, help="Backend port (0 = auto).")
    args = parser.parse_args()

    orchestrator_dir = _orchestrator_dir()
    _ensure_desktop_env(orchestrator_dir)

    port = int(args.port) if int(args.port) > 0 else _pick_free_port()

    if args.backend:
        _backend_main(port)
        return 0

    token = _ensure_admin_token()
    window_icon = _ensure_window_icon_file()
    _ensure_webview2_or_install()

    try:
        import webview
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            "Missing desktop dependencies. Install with: pip install -r requirements-desktop.txt"
        ) from e

    main_window = webview.create_window(
        title="OpenAgent Workbench",
        url="about:blank",
        width=1240,
        height=820,
        min_size=(1100, 720),
        hidden=False,
    )

    backend_proc: Optional[subprocess.Popen] = None
    tray_icon = None

    def after_start():
        nonlocal backend_proc, tray_icon
        host_mode = (os.getenv("OWB_HOST_MODE") or "local").strip().lower()
        remote_url = (os.getenv("OWB_REMOTE_URL") or "").strip()
        remote_token = (os.getenv("OWB_REMOTE_TOKEN") or "").strip() or token

        if host_mode == "remote" and remote_url:
            start_url = remote_url
            if "?" in start_url:
                start_url = start_url + f"&token={remote_token}"
            else:
                start_url = start_url + f"?token={remote_token}"
        else:
            backend_proc = _start_backend_subprocess(orchestrator_dir, port)
            # Do not force UI language here; let the web layer persist user choice via cookie.
            start_url = f"http://127.0.0.1:{port}/?token={token}"
            _wait_http_ok(f"http://127.0.0.1:{port}/", timeout_s=35.0)

        try:
            main_window.load_url(start_url)
            main_window.show()
        except Exception:  # noqa: BLE001
            pass

        tray_icon = _create_tray_icon(main_window, backend_proc, start_url)

        close_to_tray = (os.getenv("OWB_CLOSE_TO_TRAY") or "").strip() == "1"
        if close_to_tray:
            def _on_closing():
                # Optional: minimize-to-tray instead of quitting.
                if tray_icon is not None:
                    try:
                        main_window.hide()
                        return False
                    except Exception:  # noqa: BLE001
                        return True
                return True

            try:
                main_window.events.closing += _on_closing
            except Exception:  # noqa: BLE001
                pass

    try:
        # pywebview exposes `icon` on `webview.start` (not on `create_window`) for Windows taskbar/window icon.
        try:
            # Prefer modern Edge/WebView2 when available (better JS/CSS compatibility).
            webview.start(after_start, debug=False, icon=window_icon, gui="edgechromium")
        except Exception:
            webview.start(after_start, debug=False, icon=window_icon)
    finally:
        try:
            if tray_icon is not None:
                tray_icon.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            if backend_proc is not None:
                try:
                    backend_proc.terminate()
                except Exception:
                    pass
                try:
                    backend_proc.wait(timeout=4.0)
                except Exception:
                    try:
                        backend_proc.kill()
                    except Exception:
                        pass
        except Exception:  # noqa: BLE001
            pass

    return 0


def main() -> None:
    raise SystemExit(_desktop_main())


if __name__ == "__main__":
    main()
