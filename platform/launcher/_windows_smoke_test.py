"""One-off Windows-native smoke test for reo_launcher.py's core functions,
run via .github/workflows/windows-launcher-smoke-test.yml on a real
windows-latest GitHub Actions runner (not simulated on macOS/Linux) — the
one thing that can't be verified anywhere else, since this module's own
path/subprocess/env-var handling (LOCALAPPDATA, backslash paths,
CREATE_NO_WINDOW) only actually exercises Windows code paths on Windows.

Does NOT test `docker compose up` against the real stack: this platform's
images are all Linux-based, and GitHub-hosted windows-latest runners only
support Windows containers (verified separately, see
windows-docker-probe.yml) — that gap is orthogonal to this script and is
called out explicitly wherever it applies, not silently skipped.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import reo_launcher as launcher  # noqa: E402


def check(label: str, condition: bool, detail: str = "") -> bool:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" — {detail}" if detail else ""))
    return condition


def main() -> int:
    failures = 0

    print(f"sys.platform = {sys.platform}")
    if not check("running on native Windows", sys.platform == "win32"):
        failures += 1

    data_dir = launcher.app_data_dir()
    print(f"app_data_dir() = {data_dir}")
    if not check("app_data_dir uses LOCALAPPDATA, not a POSIX fallback", "AppData\\Local" in str(data_dir)):
        failures += 1
    if not check("app_data_dir exists on disk", data_dir.is_dir()):
        failures += 1

    logs: list[str] = []
    try:
        launcher.download_and_extract_source(logs.append)
        if not check("download_and_extract_source completed without raising", True):
            failures += 1
    except Exception:
        traceback.print_exc()
        check("download_and_extract_source completed without raising", False)
        failures += 1

    if not check("platform/infrastructure/docker-compose.yml present after download",
                 (launcher.infra_dir() / "docker-compose.yml").is_file()):
        failures += 1
    if not check("platform/.env.example present after download",
                 (launcher.platform_dir() / ".env.example").is_file()):
        failures += 1

    try:
        launcher.write_env_file("mock", None, None)
        env_text = (launcher.infra_dir() / ".env").read_text()
        if not check("write_env_file wrote MODEL_PROVIDER=mock", "MODEL_PROVIDER=mock" in env_text):
            failures += 1
    except Exception:
        traceback.print_exc()
        check("write_env_file completed without raising", False)
        failures += 1

    ok, message = launcher.check_docker()
    print(f"check_docker() -> ok={ok}, message={message!r}")
    check("check_docker ran without raising (Docker Desktop detected on this runner)", ok)
    # Not counted as a failure either way: this runner's Docker is in Windows-
    # container mode (see windows-docker-probe.yml), which is a CI-environment
    # fact, not something this script can change or should be graded on.

    log_lines: list[str] = []
    try:
        code = launcher.stream_process(launcher.compose("config", "-q"), launcher.infra_dir(), log_lines.append)
        if not check("stream_process/compose() ran `docker compose config -q` via subprocess.Popen on native Windows",
                      code == 0, f"exit code {code}"):
            failures += 1
    except Exception:
        traceback.print_exc()
        check("stream_process/compose() completed without raising", False)
        failures += 1

    print(f"\n{'ALL CHECKS PASSED' if failures == 0 else f'{failures} CHECK(S) FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
