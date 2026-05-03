import os
import signal
import subprocess
import sys


def start_worker(job_id: str, config_path: str, log_path: str, cwd: str) -> int:
    os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
    log_file = open(log_path, "ab")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "podcast_tool.worker",
            "run",
            "--job-id",
            job_id,
            "--config",
            config_path,
        ],
        cwd=cwd,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        close_fds=True,
    )
    return proc.pid


def is_pid_running(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def terminate_process(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.killpg(int(pid), signal.SIGTERM)
        return True
    except ProcessLookupError:
        return False
    except Exception:
        try:
            os.kill(int(pid), signal.SIGTERM)
            return True
        except Exception:
            return False
