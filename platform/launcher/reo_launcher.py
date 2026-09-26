"""REO Platform Launcher.

A single-file, stdlib-only desktop app that takes a machine with nothing
but Docker Desktop installed to a running Renewable Energy Orchestrator
dashboard open in the browser:

  1. Check the `docker` CLI is on PATH and the daemon is reachable.
  2. Download this repo's `platform/` source from GitHub (if not already
     present locally) and extract it into a per-user app-data folder.
  3. Ask for an LLM API key (or "skip, use the free mock provider") and
     write it into `infrastructure/.env`.
  4. `docker compose up -d --build`, streaming output into the window.
  5. Poll the API health endpoint, then run the (idempotent) seed script.
  6. Open the dashboard in the default browser.

Deliberately pure stdlib (tkinter, subprocess, urllib, zipfile) so building
it with PyInstaller needs nothing beyond `pip install pyinstaller` — no
extra runtime dependency for the launcher to fail to resolve on a machine
that has nothing else set up yet.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
import zipfile
from pathlib import Path
from queue import Empty, Queue
from tkinter import BOTH, END, LEFT, StringVar, Tk, Toplevel, X, ttk
from tkinter.scrolledtext import ScrolledText

REPO_OWNER = "akash-8991"
REPO_NAME = "renewable_energy_orchestrator"
REPO_BRANCH = "master"
ZIP_URL = f"https://github.com/{REPO_OWNER}/{REPO_NAME}/archive/refs/heads/{REPO_BRANCH}.zip"

HEALTH_URL = "http://localhost:8000/health"
DASHBOARD_URL = "http://localhost:5173"
APP_NAME = "REOPlatform"

# Suppresses the flashing console window every subprocess call would
# otherwise pop up under a --windowed PyInstaller build on Windows.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

# label -> (MODEL_PROVIDER value, .env key for the API key, default model)
PROVIDERS: dict[str, tuple[str, str | None, str | None]] = {
    "OpenRouter — one key, many models (recommended)": ("openrouter", "OPENROUTER_API_KEY", "openai/gpt-4o-mini"),
    "Anthropic (Claude)": ("anthropic", "ANTHROPIC_API_KEY", "claude-sonnet-5"),
    "OpenAI": ("openai", "OPENAI_API_KEY", "gpt-4o"),
    "Skip — free demo mode (deterministic mock, no real AI reasoning)": ("mock", None, None),
}

MODEL_VAR_FOR_PROVIDER = {
    "openrouter": "OPENROUTER_MODEL",
    "anthropic": "ANTHROPIC_MODEL",
    "openai": "OPENAI_MODEL",
}


# --------------------------------------------------------------------------
# Filesystem / process helpers — no Tk dependency, so these are unit-testable
# on their own.
# --------------------------------------------------------------------------


def app_data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    directory = base / APP_NAME
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def repo_root() -> Path:
    return app_data_dir() / "repo"


def platform_dir() -> Path:
    return repo_root() / "platform"


def infra_dir() -> Path:
    return platform_dir() / "infrastructure"


def run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        creationflags=_NO_WINDOW,
    )


def check_docker() -> tuple[bool, str]:
    try:
        version = run(["docker", "--version"])
    except FileNotFoundError:
        return False, "Docker isn't installed, or isn't on PATH."
    if version.returncode != 0:
        return False, "Docker isn't installed, or isn't on PATH."
    info = run(["docker", "info"])
    if info.returncode != 0:
        return False, "Docker is installed but the daemon isn't running — start Docker Desktop and retry."
    return True, version.stdout.strip()


def download_and_extract_source(progress: callable[[str], None]) -> None:
    """(Re)fetches platform/ from GitHub into repo_root(), preserving any
    existing infrastructure/.env across the refresh (it's gitignored, so a
    fresh checkout never contains it)."""
    saved_env = None
    env_path = infra_dir() / ".env"
    if env_path.exists():
        saved_env = env_path.read_text()

    progress("Downloading platform source from GitHub...")
    tmp_zip = app_data_dir() / "source.zip"
    request = urllib.request.Request(ZIP_URL, headers={"User-Agent": "REO-Launcher"})
    with urllib.request.urlopen(request, timeout=60) as resp, open(tmp_zip, "wb") as out:
        total = int(resp.headers.get("Content-Length", 0))
        read = 0
        while chunk := resp.read(1 << 16):
            out.write(chunk)
            read += len(chunk)
            if total:
                progress(f"Downloading platform source... {read * 100 // total}%")

    progress("Extracting...")
    extract_dir = app_data_dir() / "source_extract"
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    with zipfile.ZipFile(tmp_zip) as zf:
        zf.extractall(extract_dir)
    tmp_zip.unlink(missing_ok=True)

    top_level = [p for p in extract_dir.iterdir() if p.is_dir()]
    if not top_level:
        raise RuntimeError("Downloaded archive was empty.")
    if not (top_level[0] / "platform").exists():
        raise RuntimeError("Downloaded archive didn't contain a platform/ folder.")

    if repo_root().exists():
        shutil.rmtree(repo_root())
    shutil.move(str(top_level[0]), str(repo_root()))
    shutil.rmtree(extract_dir, ignore_errors=True)

    if saved_env is not None:
        infra_dir().mkdir(parents=True, exist_ok=True)
        (infra_dir() / ".env").write_text(saved_env)


def write_env_file(provider: str, api_key: str | None, model: str | None) -> None:
    infra = infra_dir()
    infra.mkdir(parents=True, exist_ok=True)
    env_path = infra / ".env"
    example_path = platform_dir() / ".env.example"
    if not env_path.exists():
        shutil.copy(example_path, env_path)
    text = env_path.read_text()

    def upsert(source: str, name: str, value: str) -> str:
        pattern = re.compile(rf"^#?{re.escape(name)}=.*$", re.MULTILINE)
        line = f"{name}={value}"
        if pattern.search(source):
            return pattern.sub(line, source, count=1)
        return source.rstrip("\n") + f"\n{line}\n"

    text = upsert(text, "MODEL_PROVIDER", provider)
    key_var = PROVIDERS_VALUE_TO_KEYVAR.get(provider)
    if key_var and api_key:
        text = upsert(text, key_var, api_key)
        model_var = MODEL_VAR_FOR_PROVIDER.get(provider)
        if model_var and model:
            text = upsert(text, model_var, model)
    env_path.write_text(text)


PROVIDERS_VALUE_TO_KEYVAR = {value[0]: value[1] for value in PROVIDERS.values() if value[1]}


def compose(*args: str) -> list[str]:
    return ["docker", "compose", *args]


def stream_process(cmd: list[str], cwd: Path, on_line: callable[[str], None]) -> int:
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        creationflags=_NO_WINDOW,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        on_line(line.rstrip())
    return proc.wait()


def wait_for_health(timeout: int = 600, on_tick: callable[[], None] | None = None) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(HEALTH_URL, timeout=5) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        if on_tick:
            on_tick()
        time.sleep(3)
    return False


# --------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------


class LauncherApp:
    def __init__(self) -> None:
        self.root = Tk()
        self.root.title("Renewable Energy Orchestrator — Launcher")
        self.root.geometry("640x480")
        self.root.minsize(560, 420)
        self.log_queue: Queue[str] = Queue()
        self.provider_var = StringVar(value=next(iter(PROVIDERS)))
        self.api_key_var = StringVar()
        self.model_var = StringVar()
        self.frame: ttk.Frame | None = None
        self._show_docker_check()
        self.root.after(100, self._drain_log_queue)

    def run(self) -> None:
        self.root.mainloop()

    # -- frame management ---------------------------------------------

    def _clear(self) -> None:
        if self.frame is not None:
            self.frame.destroy()
        self.frame = ttk.Frame(self.root, padding=20)
        self.frame.pack(fill=BOTH, expand=True)

    # -- screen 1: docker check -----------------------------------------

    def _show_docker_check(self) -> None:
        self._clear()
        ttk.Label(self.frame, text="Checking Docker...", font=("", 14, "bold")).pack(anchor="w")
        status = ttk.Label(self.frame, text="")
        status.pack(anchor="w", pady=10)
        self.root.update()

        ok, message = check_docker()
        if ok:
            status.config(text=f"Docker OK ({message})")
            self.root.after(600, self._show_api_key_screen)
            return

        status.config(text=message, foreground="red", wraplength=560)
        ttk.Label(
            self.frame,
            text="Docker Desktop is required to run the platform's Postgres, Redis, object-storage and "
            "application containers. Install it, make sure it's running, then click Retry.",
            wraplength=560,
        ).pack(anchor="w", pady=10)
        buttons = ttk.Frame(self.frame)
        buttons.pack(anchor="w", pady=10)
        ttk.Button(
            buttons, text="Download Docker Desktop",
            command=lambda: webbrowser.open("https://www.docker.com/products/docker-desktop/"),
        ).pack(side=LEFT, padx=(0, 10))
        ttk.Button(buttons, text="Retry", command=self._show_docker_check).pack(side=LEFT)

    # -- screen 2: LLM provider / API key --------------------------------

    def _show_api_key_screen(self) -> None:
        self._clear()
        ttk.Label(self.frame, text="Connect an AI model provider", font=("", 14, "bold")).pack(anchor="w")
        ttk.Label(
            self.frame,
            text="The platform's agent layer calls an LLM to explain decisions and prepare "
            "evidence — the deterministic optimizer still makes every actual decision. "
            "Pick a provider and paste an API key, or skip for the free mock provider.",
            wraplength=560,
        ).pack(anchor="w", pady=(5, 15))

        for label in PROVIDERS:
            ttk.Radiobutton(
                self.frame, text=label, value=label, variable=self.provider_var,
                command=self._on_provider_change,
            ).pack(anchor="w")

        key_frame = ttk.Frame(self.frame)
        key_frame.pack(fill=X, pady=(15, 0))
        ttk.Label(key_frame, text="API key:").pack(side=LEFT)
        self.key_entry = ttk.Entry(key_frame, textvariable=self.api_key_var, show="*", width=45)
        self.key_entry.pack(side=LEFT, padx=10)

        model_frame = ttk.Frame(self.frame)
        model_frame.pack(fill=X, pady=(10, 0))
        ttk.Label(model_frame, text="Model (optional):").pack(side=LEFT)
        ttk.Entry(model_frame, textvariable=self.model_var, width=30).pack(side=LEFT, padx=10)

        self._on_provider_change()

        ttk.Button(self.frame, text="Continue", command=self._start_provisioning).pack(anchor="e", pady=25)

    def _on_provider_change(self) -> None:
        _provider, key_var, default_model = PROVIDERS[self.provider_var.get()]
        needs_key = key_var is not None
        self.key_entry.config(state="normal" if needs_key else "disabled")
        if not needs_key:
            self.api_key_var.set("")
        self.model_var.set(default_model or "")

    # -- screen 3: provisioning progress ---------------------------------

    def _start_provisioning(self) -> None:
        provider, key_var, _ = PROVIDERS[self.provider_var.get()]
        if key_var and not self.api_key_var.get().strip():
            self._show_api_key_screen()
            self._flash_error("An API key is required for this provider, or choose the free mock option.")
            return

        self._clear()
        ttk.Label(self.frame, text="Setting up the platform", font=("", 14, "bold")).pack(anchor="w")
        self.status_label = ttk.Label(self.frame, text="Starting...")
        self.status_label.pack(anchor="w", pady=(5, 10))
        self.log_box = ScrolledText(self.frame, height=16, state="disabled", font=("Courier", 9))
        self.log_box.pack(fill=BOTH, expand=True)

        provider_value = provider
        api_key = self.api_key_var.get().strip() or None
        model = self.model_var.get().strip() or None
        thread = threading.Thread(
            target=self._provision_worker, args=(provider_value, api_key, model), daemon=True,
        )
        thread.start()

    def _log(self, line: str) -> None:
        self.log_queue.put(line)

    def _set_status(self, text: str) -> None:
        self.log_queue.put(f"__STATUS__{text}")

    def _drain_log_queue(self) -> None:
        try:
            while True:
                item = self.log_queue.get_nowait()
                if item.startswith("__STATUS__"):
                    if hasattr(self, "status_label"):
                        self.status_label.config(text=item[len("__STATUS__"):])
                elif item.startswith("__DONE__"):
                    self._show_done_screen()
                elif item.startswith("__ERROR__"):
                    self._show_error_screen(item[len("__ERROR__"):])
                elif hasattr(self, "log_box"):
                    self.log_box.config(state="normal")
                    self.log_box.insert(END, item + "\n")
                    self.log_box.see(END)
                    self.log_box.config(state="disabled")
        except Empty:
            pass
        self.root.after(100, self._drain_log_queue)

    def _provision_worker(self, provider: str, api_key: str | None, model: str | None) -> None:
        try:
            # Always re-fetches (not just on the very first run): this project is
            # actively evolving, and a stale locally-cached copy has already once
            # meant a real, already-pushed fix (e.g. a broken Docker image
            # reference) couldn't reach someone who'd already run the launcher
            # before — re-downloading a ~2MB source tree every launch is cheap
            # insurance against that, worth it over saving a few seconds.
            self._set_status("Downloading the latest platform source...")
            download_and_extract_source(self._log)

            self._set_status("Writing configuration...")
            write_env_file(provider, api_key, model)

            self._set_status("Building and starting containers (first run can take 5-15 minutes)...")
            code = stream_process(compose("up", "-d", "--build"), infra_dir(), self._log)
            if code != 0:
                self.log_queue.put(f"__ERROR__docker compose exited with code {code} — see log above.")
                return

            self._set_status("Waiting for the API to become healthy...")
            healthy = wait_for_health(timeout=600, on_tick=lambda: self._log("...still waiting for the API"))
            if not healthy:
                self.log_queue.put("__ERROR__The API never became healthy within 10 minutes.")
                return

            self._set_status("Seeding the reference demo tenant (safe to repeat — skips if already seeded)...")
            seed_code = stream_process(
                compose("run", "--rm", "api", "python", "/app/platform/database/seed.py"),
                infra_dir(),
                self._log,
            )
            if seed_code != 0:
                self.log_queue.put(f"__ERROR__Seeding the demo tenant failed (exit code {seed_code}) — see log above.")
                return

            self._set_status("Opening the dashboard...")
            webbrowser.open(DASHBOARD_URL)
            self.log_queue.put("__DONE__")
        except Exception as exc:  # noqa: BLE001 — surfaced to the user, not swallowed
            self.log_queue.put(f"__ERROR__{exc}")

    def _flash_error(self, message: str) -> None:
        popup = Toplevel(self.root)
        popup.title("Missing information")
        ttk.Label(popup, text=message, padding=20, wraplength=360).pack()
        ttk.Button(popup, text="OK", command=popup.destroy).pack(pady=(0, 15))

    # -- screen 4: error --------------------------------------------------

    def _show_error_screen(self, message: str) -> None:
        self._clear()
        ttk.Label(self.frame, text="Setup ran into a problem", font=("", 14, "bold"), foreground="red").pack(anchor="w")
        ttk.Label(self.frame, text=message, wraplength=560).pack(anchor="w", pady=15)
        ttk.Label(
            self.frame,
            text=f"Files live in {app_data_dir()} — you can also run "
            f"`docker compose logs` from {infra_dir()} for the full history.",
            wraplength=560,
        ).pack(anchor="w")
        buttons = ttk.Frame(self.frame)
        buttons.pack(anchor="w", pady=25)
        ttk.Button(buttons, text="Retry", command=self._show_api_key_screen).pack(side=LEFT, padx=(0, 10))
        ttk.Button(buttons, text="Quit", command=self.root.destroy).pack(side=LEFT)

    # -- screen 5: done -----------------------------------------------------

    def _show_done_screen(self) -> None:
        self._clear()
        ttk.Label(self.frame, text="The platform is running", font=("", 14, "bold")).pack(anchor="w")
        ttk.Label(self.frame, text=f"Dashboard: {DASHBOARD_URL}", wraplength=560).pack(anchor="w", pady=(10, 15))

        creds = ttk.LabelFrame(self.frame, text="Demo login (full account list in docs/DEPLOYMENT.md)", padding=10)
        creds.pack(fill=X)
        ttk.Label(creds, text="Tenant slug: demo-utility").pack(anchor="w")
        ttk.Label(creds, text="Password (all accounts): Password123!").pack(anchor="w")
        ttk.Label(creds, text="Try: tenant.admin@demo-utility.test  or  platform.admin@demo-utility.test").pack(anchor="w")

        buttons = ttk.Frame(self.frame)
        buttons.pack(anchor="w", pady=25)
        ttk.Button(buttons, text="Open dashboard", command=lambda: webbrowser.open(DASHBOARD_URL)).pack(side=LEFT, padx=(0, 10))
        ttk.Button(buttons, text="Stop platform", command=self._stop_platform).pack(side=LEFT, padx=(0, 10))
        ttk.Button(buttons, text="Quit launcher", command=self.root.destroy).pack(side=LEFT)
        ttk.Label(
            self.frame,
            text="Quitting the launcher leaves the platform running in the background — "
            "use \"Stop platform\" to shut its containers down.",
            wraplength=560,
        ).pack(anchor="w", side="bottom")

    def _stop_platform(self) -> None:
        run(compose("down"), cwd=infra_dir())
        self._flash_error("Platform stopped. Re-run this launcher any time to bring it back up.")


def main() -> None:
    LauncherApp().run()


if __name__ == "__main__":
    main()
