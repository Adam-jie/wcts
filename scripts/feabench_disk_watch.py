"""Pause one FEA-Bench reconstruction process group when disk space runs low."""
import os
from pathlib import Path
import signal
import sys
import time


pid, group, workspace = int(sys.argv[1]), int(sys.argv[2]), Path(sys.argv[3])
while True:
    try:
        command = Path(f'/proc/{pid}/cmdline').read_bytes()
        if b'reconstruct_official.py' not in command or os.getpgid(pid) != group:
            print('Reconstruction process has changed; monitor stopped.', flush=True)
            break
        disk = os.statvfs(workspace)
        available = disk.f_bavail * disk.f_frsize
        if available < 5 * 1024**3:
            os.killpg(group, signal.SIGSTOP)
            print(f'Paused reconstruction group {group}; free bytes: {available}.', flush=True)
            break
    except ProcessLookupError:
        print('Reconstruction process ended; monitor stopped.', flush=True)
        break
    except FileNotFoundError:
        print('Reconstruction process ended; monitor stopped.', flush=True)
        break
    time.sleep(10)
