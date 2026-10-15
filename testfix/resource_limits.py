"""Fail-closed resource limits for the disposable candidate interpreter.

Call ``apply_limits`` before reading or executing candidate input and retain its
return value until the worker exits. These limits supplement the parent's wall
timeout; they are not a filesystem or network sandbox.
"""
from __future__ import annotations

import ctypes
import os
import sys
from dataclasses import dataclass, field

DEFAULT_MEMORY_BYTES = 512 * 1024 * 1024
DEFAULT_CPU_SECONDS = 5


class ResourceLimitError(RuntimeError):
    """A required operating-system limit could not be established.

    The worker must exit on this error rather than execute candidate code.
    """


@dataclass(frozen=True)
class ResourceLimitGuard:
    """Keep the Windows job handle alive for the worker's entire lifetime.

    There is deliberately no finalizer or context-manager close operation:
    closing the last KILL_ON_JOB_CLOSE handle terminates this worker. Windows
    closes the non-inheritable handle when the worker exits, including when the
    parent terminates it. POSIX limits are process state and need no handle.
    """

    backend: str
    memory_bytes: int
    cpu_seconds: int
    _job_handle: int | None = field(default=None, repr=False)
    _kernel32: object | None = field(default=None, repr=False, compare=False)


# Windows SDK layouts, with native pointer alignment. DWORD is always 32 bits;
# using c_ulong would incorrectly widen it on an LP64 non-Windows host.
# https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_basic_limit_information
# https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_extended_limit_information
class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _IOCounters(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_uint64)
        for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
        )
    ]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimitInformation),
        ("IoInfo", _IOCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_PROCESS_TIME = 0x00000002
_JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
_JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_REQUIRED_JOB_FLAGS = (
    _JOB_OBJECT_LIMIT_PROCESS_TIME | _JOB_OBJECT_LIMIT_PROCESS_MEMORY
    | _JOB_OBJECT_LIMIT_JOB_MEMORY | _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
)


def _windows_api():
    """Bind every Win32 signature explicitly to avoid truncating handles."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = ctypes.c_void_p
    boolean = ctypes.c_int32
    dword = ctypes.c_uint32
    signatures = {
        "CreateJobObjectW": ([ctypes.c_void_p, ctypes.c_wchar_p], handle),
        "SetInformationJobObject": ([handle, ctypes.c_int, ctypes.c_void_p, dword], boolean),
        "QueryInformationJobObject": (
            [handle, ctypes.c_int, ctypes.c_void_p, dword, ctypes.POINTER(dword)], boolean,
        ),
        "AssignProcessToJobObject": ([handle, handle], boolean),
        "GetCurrentProcess": ([], handle),
        "CloseHandle": ([handle], boolean),
    }
    for name, (argtypes, restype) in signatures.items():
        function = getattr(kernel32, name)
        function.argtypes = argtypes
        function.restype = restype
    return kernel32


def _win_error(operation: str) -> ResourceLimitError:
    return ResourceLimitError(f"{operation} failed (Win32 error {ctypes.get_last_error()})")


def _apply_windows_limits(memory_bytes: int, cpu_seconds: int) -> ResourceLimitGuard:
    try:
        kernel32 = _windows_api()
    except (AttributeError, OSError) as exc:
        raise ResourceLimitError("Windows Job Object APIs are unavailable") from exc

    expected_size = 144 if ctypes.sizeof(ctypes.c_void_p) == 8 else 112
    if ctypes.sizeof(_ExtendedLimitInformation) != expected_size:
        raise ResourceLimitError("Unsupported Windows Job Object structure layout")

    # NULL security attributes make this an anonymous, non-inheritable handle.
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise _win_error("CreateJobObjectW")
    assigned = False
    try:
        limits = _ExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = _REQUIRED_JOB_FLAGS
        limits.BasicLimitInformation.PerProcessUserTimeLimit = cpu_seconds * 10_000_000
        limits.ProcessMemoryLimit = memory_bytes
        limits.JobMemoryLimit = memory_bytes
        size = ctypes.sizeof(limits)
        if not kernel32.SetInformationJobObject(
            job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(limits), size,
        ):
            raise _win_error("SetInformationJobObject")

        # Verify accepted limits before assigning ourselves: closing the job is
        # safe while it is empty, but would terminate us after assignment.
        observed = _ExtendedLimitInformation()
        if not kernel32.QueryInformationJobObject(
            job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(observed), ctypes.sizeof(observed), None,
        ):
            raise _win_error("QueryInformationJobObject")
        if (
            observed.BasicLimitInformation.LimitFlags & _REQUIRED_JOB_FLAGS != _REQUIRED_JOB_FLAGS
            or observed.ProcessMemoryLimit != memory_bytes
            or observed.JobMemoryLimit != memory_bytes
            or observed.BasicLimitInformation.PerProcessUserTimeLimit != cpu_seconds * 10_000_000
        ):
            raise ResourceLimitError("Windows did not accept all required job limits")

        guard = ResourceLimitGuard("windows-job", memory_bytes, cpu_seconds, job, kernel32)
        # A compatible existing parent job can permit nesting. Any assignment
        # failure (including ERROR_ACCESS_DENIED) is fatal, never a fallback.
        if not kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess()):
            raise _win_error("AssignProcessToJobObject")
        assigned = True
        return guard
    finally:
        if not assigned:
            kernel32.CloseHandle(job)


def _apply_posix_limits(memory_bytes: int, cpu_seconds: int) -> ResourceLimitGuard:
    try:
        import resource

        requested = ((resource.RLIMIT_AS, memory_bytes), (resource.RLIMIT_CPU, cpu_seconds))
        chosen = []
        for kind, maximum in requested:
            soft, hard = resource.getrlimit(kind)
            # Never loosen a pre-existing stricter limit, including its soft cap.
            effective = min(
                [maximum] + [value for value in (soft, hard) if value != resource.RLIM_INFINITY],
            )
            chosen.append((kind, effective))
        for kind, effective in chosen:
            resource.setrlimit(kind, (effective, effective))
        for kind, effective in chosen:
            if resource.getrlimit(kind) != (effective, effective):
                raise ResourceLimitError("POSIX did not accept all required resource limits")
        return ResourceLimitGuard("posix-rlimit", chosen[0][1], chosen[1][1])
    except (ImportError, AttributeError, OSError, ValueError) as exc:
        # A partial limit installation is not a reason to run unbounded code.
        raise ResourceLimitError("Required POSIX CPU/address-space limits are unavailable") from exc


def apply_limits(
    memory_bytes: int = DEFAULT_MEMORY_BYTES,
    cpu_seconds: int = DEFAULT_CPU_SECONDS,
) -> ResourceLimitGuard:
    """Limit the current worker or raise before candidate execution.

    Windows caps committed memory for both each process and the whole job,
    limits per-process user CPU time, and kills descendants when the worker's
    job handle closes. POSIX caps address space and total CPU time via rlimits.
    Invoke only in a disposable child, never in the application/server process.
    """
    if type(memory_bytes) is not int or memory_bytes <= 0:
        raise ResourceLimitError("memory_bytes must be a positive integer")
    if type(cpu_seconds) is not int or cpu_seconds <= 0:
        raise ResourceLimitError("cpu_seconds must be a positive integer")
    if memory_bytes > (1 << (8 * ctypes.sizeof(ctypes.c_size_t))) - 1:
        raise ResourceLimitError("memory_bytes exceeds the native address-space range")
    if cpu_seconds > ((1 << 63) - 1) // 10_000_000:
        raise ResourceLimitError("cpu_seconds exceeds the supported time range")
    if sys.platform == "win32":
        return _apply_windows_limits(memory_bytes, cpu_seconds)
    if os.name == "posix":
        return _apply_posix_limits(memory_bytes, cpu_seconds)
    raise ResourceLimitError("Resource limits are unsupported on this platform")
