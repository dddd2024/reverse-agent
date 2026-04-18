#!/usr/bin/env python3
"""
Memory Extraction Script for samplereverse.exe

Extracts the decrypted buffer content by attaching to the running process
and searching for "flag{" patterns in process memory.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

# Windows API definitions
try:
    import ctypes
    import ctypes.wintypes as wintypes

    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

    # Process access rights
    PROCESS_QUERY_INFORMATION = 0x0400
    PROCESS_VM_READ = 0x0010

    # Memory protection constants
    PAGE_EXECUTE_READWRITE = 0x40
    PAGE_READWRITE = 0x04

    class MEMORY_BASIC_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BaseAddress", ctypes.c_size_t),
            ("AllocationBase", ctypes.c_size_t),
            ("AllocationProtect", ctypes.c_ulong),
            ("RegionSize", ctypes.c_size_t),
            ("State", ctypes.c_ulong),
            ("Protect", ctypes.c_ulong),
            ("Type", ctypes.c_ulong),
        ]

    WINDOWS_API_AVAILABLE = True
except (ImportError, AttributeError):
    WINDOWS_API_AVAILABLE = False


FLAG_PATTERN_UTF16LE = b"f\x00l\x00a\x00g\x00{\x00"
BUFFER_MAX_CHARS = 16
DEFAULT_DUMMY_INPUT = "AAAAAAA"
DEFAULT_TIMEOUT = 10


def find_process_by_name(process_name: str) -> int | None:
    """Find process ID by executable name."""
    if not PSUTIL_AVAILABLE:
        return None

    try:
        for proc in psutil.process_iter(['pid', 'name']):
            if proc.info['name'] and proc.info['name'].lower() == process_name.lower():
                return proc.info['pid']
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        pass

    return None


def read_process_memory(pid: int, address: int, size: int) -> bytes | None:
    """Read memory from target process."""
    if not WINDOWS_API_AVAILABLE:
        return None

    process_handle = kernel32.OpenProcess(
        PROCESS_VM_READ | PROCESS_QUERY_INFORMATION,
        False,
        pid
    )
    if not process_handle:
        return None

    try:
        buffer = ctypes.create_string_buffer(size)
        bytes_read = ctypes.c_size_t(0)

        if kernel32.ReadProcessMemory(
            process_handle,
            ctypes.c_void_p(address),
            buffer,
            size,
            ctypes.byref(bytes_read)
        ):
            return buffer.raw[:bytes_read.value]
    finally:
        kernel32.CloseHandle(process_handle)

    return None


def search_pattern_in_memory(pid: int, pattern: bytes, start_addr: int = 0x400000, end_addr: int = 0x7FFFFFFF) -> list[int]:
    """Search for byte pattern in process memory."""
    if not WINDOWS_API_AVAILABLE or not PSUTIL_AVAILABLE:
        return []

    results = []
    current_addr = start_addr

    while current_addr < end_addr:
        try:
            mbi = MEMORY_BASIC_INFORMATION()

            # Open process for query
            query_handle = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION, False, pid)
            if not query_handle:
                current_addr += 0x10000
                continue

            try:
                if kernel32.VirtualQueryEx(
                    query_handle,
                    ctypes.c_void_p(current_addr),
                    ctypes.byref(mbi),
                    ctypes.sizeof(mbi)
                ):
                    if mbi.Protect in (PAGE_READWRITE, PAGE_EXECUTE_READWRITE):
                        # Read this memory region
                        region_data = read_process_memory(pid, current_addr, min(4096, mbi.RegionSize))
                        if region_data:
                            offset = region_data.find(pattern)
                            if offset != -1:
                                results.append(current_addr + offset)
                    current_addr += mbi.RegionSize
                else:
                    current_addr += 0x10000
            finally:
                kernel32.CloseHandle(query_handle)

        except Exception:
            current_addr += 0x10000

    return results


def extract_wide_string(memory_data: bytes, max_chars: int = 16) -> str:
    """Extract UTF-16LE wide string from memory data."""
    wide_chars = []
    for i in range(0, min(len(memory_data), max_chars * 2), 2):
        if i + 1 >= len(memory_data):
            break
        char_code = memory_data[i] | (memory_data[i+1] << 8)
        if char_code == 0:  # Wide null terminator
            break
        if 32 <= char_code <= 126:  # Printable ASCII
            wide_chars.append(chr(char_code))
        elif 0x4E00 <= char_code <= 0x9FFF:  # CJK characters
            wide_chars.append(chr(char_code))
    return ''.join(wide_chars)


def launch_and_extract(target_path: Path, dummy_input: str = DEFAULT_DUMMY_INPUT, timeout_seconds: int = DEFAULT_TIMEOUT) -> dict:
    """Launch target and extract buffer from memory."""
    if not WINDOWS_API_AVAILABLE or not PSUTIL_AVAILABLE:
        return {
            "success": False,
            "error": "Required dependencies (ctypes/psutil) not available"
        }

    target_exe_name = target_path.name
    proc = None

    try:
        # Launch target with stdin/stdout captured
        proc = subprocess.Popen(
            [str(target_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=target_path.parent
        )

        # Send dummy input to trigger decryption
        proc.stdin.write(dummy_input + "\n")
        proc.stdin.flush()

        # Give time for decryption to occur
        time.sleep(2)

        # Find process
        pid = find_process_by_name(target_exe_name)
        if not pid:
            # Try alternative names
            alt_names = [target_exe_name, target_exe_name.lower(), target_exe_name.upper()]
            for name in alt_names:
                pid = find_process_by_name(name)
                if pid:
                    break

        if not pid:
            return {
                "success": False,
                "error": f"Process not found: {target_exe_name}"
            }

        # Search for "flag{" pattern in memory (UTF-16LE)
        addresses = search_pattern_in_memory(pid, FLAG_PATTERN_UTF16LE)

        if addresses:
            # Read buffer at found address
            buffer_data = read_process_memory(pid, addresses[0], 64)
            if buffer_data:
                decrypted_buffer = extract_wide_string(buffer_data)
                if decrypted_buffer:
                    return {
                        "success": True,
                        "decrypted_buffer": decrypted_buffer,
                        "buffer_hex": buffer_data.hex(),
                        "address": hex(addresses[0]),
                        "pid": pid
                    }

        return {
            "success": False,
            "error": "Buffer pattern not found in memory"
        }

    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": "Process execution timeout"
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Extraction error: {str(e)}"
        }
    finally:
        # Clean up process
        if proc:
            try:
                proc.kill()
            except:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract decrypted buffer using direct memory analysis"
    )
    parser.add_argument("--target", required=True, help="Path to target executable")
    parser.add_argument("--out", required=True, help="Output JSON path")
    parser.add_argument(
        "--dummy-input",
        default=DEFAULT_DUMMY_INPUT,
        help=f"Dummy input to trigger decryption (default: {DEFAULT_DUMMY_INPUT})"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help="Timeout in seconds (default: {DEFAULT_TIMEOUT})"
    )

    args = parser.parse_args()

    target = Path(args.target)
    if not target.exists():
        print(f"Error: Target file not found: {target}", file=sys.stderr)
        return 1

    output_path = Path(args.out)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Check dependencies
    if not WINDOWS_API_AVAILABLE or not PSUTIL_AVAILABLE:
        payload = {
            "summary": "Memory extraction not available - missing dependencies",
            "success": False,
            "error": "Required dependencies (ctypes/psutil) not available",
            "decrypted_buffer": "",
            "buffer_hex": "",
            "evidence": [
                "runtime_memory:error=missing_dependencies",
                "note:Install psutil for memory analysis: pip install psutil"
            ],
            "candidates": []
        }
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    # Perform extraction
    result = launch_and_extract(
        target_path=target,
        dummy_input=args.dummy_input,
        timeout_seconds=args.timeout
    )

    # Build output payload
    if result["success"]:
        payload = {
            "summary": f"Memory extraction successful: {result['decrypted_buffer']}",
            "success": True,
            "decrypted_buffer": result["decrypted_buffer"],
            "buffer_hex": result["buffer_hex"],
            "address": result["address"],
            "pid": result["pid"],
            "evidence": [
                f"runtime_memory:address={result['address']}",
                f"runtime_memory:pid={result['pid']}",
                f"runtime_memory:buffer={result['decrypted_buffer']}",
                f"runtime_memory:hex={result['buffer_hex']}",
                "runtime_memory:method=pattern_search_utf16le",
            ],
            "candidates": [
                {
                    "value": result["decrypted_buffer"],
                    "source": "runtime_memory",
                    "confidence": 0.95,
                    "reason": "Directly extracted from process memory at runtime"
                }
            ]
        }
    else:
        payload = {
            "summary": f"Memory extraction failed: {result['error']}",
            "success": False,
            "error": result["error"],
            "decrypted_buffer": "",
            "buffer_hex": "",
            "evidence": [
                f"runtime_memory:error={result['error']}",
                "runtime_memory:method=pattern_search_utf16le",
            ],
            "candidates": []
        }

    # Write output
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
