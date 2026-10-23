"""Windows' own sources, through ctypes (nothing to install, works for NVIDIA, Intel Arc and AMD alike):

  DXGI                  the graphics adapters: name, VRAM size, and the LUID that ties them to the counters
  Performance counters  (PDH) per-process GPU memory and engine use: "GPU Process Memory", "GPU Engine",
                        "GPU Adapter Memory", the same numbers Task Manager shows
  Process names         QueryFullProcessImageNameW
"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

PDH_FMT_DOUBLE = 0x00000200
PDH_FMT_NOCAP100 = 0x00008000
PDH_MORE_DATA = 0x800007D2
PDH_CSTATUS_VALID_DATA = 0x0
PDH_CSTATUS_NEW_DATA = 0x1
DXGI_ERROR_NOT_FOUND = 0x887A0002
DXGI_ADAPTER_FLAG_SOFTWARE = 2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
COUNTERS = {
    "engine": r"\GPU Engine(*)\Utilization Percentage",
    "process_dedicated": r"\GPU Process Memory(*)\Dedicated Usage",
    "process_shared": r"\GPU Process Memory(*)\Shared Usage",
    "adapter_dedicated": r"\GPU Adapter Memory(*)\Dedicated Usage",
    "adapter_shared": r"\GPU Adapter Memory(*)\Shared Usage",
}


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


class LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class DXGI_ADAPTER_DESC1(ctypes.Structure):
    _fields_ = [("Description", wintypes.WCHAR * 128), ("VendorId", wintypes.UINT), ("DeviceId", wintypes.UINT),
                ("SubSysId", wintypes.UINT), ("Revision", wintypes.UINT), ("DedicatedVideoMemory", ctypes.c_size_t),
                ("DedicatedSystemMemory", ctypes.c_size_t), ("SharedSystemMemory", ctypes.c_size_t),
                ("AdapterLuid", LUID), ("Flags", wintypes.UINT)]


class _VALUE(ctypes.Union):
    _fields_ = [("longValue", wintypes.LONG), ("doubleValue", ctypes.c_double), ("largeValue", ctypes.c_longlong),
                ("AnsiStringValue", ctypes.c_char_p), ("WideStringValue", ctypes.c_wchar_p)]


class PDH_FMT_COUNTERVALUE(ctypes.Structure):
    _fields_ = [("CStatus", wintypes.DWORD), ("value", _VALUE)]


class PDH_FMT_COUNTERVALUE_ITEM_W(ctypes.Structure):
    _fields_ = [("szName", ctypes.c_wchar_p), ("FmtValue", PDH_FMT_COUNTERVALUE)]


IID_IDXGIFactory1 = GUID(0x770AAE78, 0xF26F, 0x4DBA, (ctypes.c_ubyte * 8)(0xA8, 0x29, 0x25, 0x3C, 0x83, 0xD1, 0xB3, 0x87))
VENDORS = {0x10DE: "NVIDIA", 0x8086: "Intel", 0x1002: "AMD", 0x1414: "Microsoft"}


def luid_key(high: int, low: int) -> str:
    """The form the performance counters use: luid_0x00000000_0x0000d1a5."""
    return f"luid_0x{high & 0xFFFFFFFF:08x}_0x{low & 0xFFFFFFFF:08x}"


def _method(obj: ctypes.c_void_p, index: int, *argtypes):
    vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(vtable[index])


def adapters() -> list[dict]:
    """The real graphics adapters (software renderers left out)."""
    factory = ctypes.c_void_p()
    hr = ctypes.windll.dxgi.CreateDXGIFactory1(ctypes.byref(IID_IDXGIFactory1), ctypes.byref(factory))
    if hr != 0:
        raise OSError(f"DXGI isn't available (0x{hr & 0xFFFFFFFF:08X})")
    found = []
    try:
        enum_adapters1 = _method(factory, 12, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p))
        index = 0
        while True:
            adapter = ctypes.c_void_p()
            hr = enum_adapters1(factory, index, ctypes.byref(adapter))
            if hr & 0xFFFFFFFF == DXGI_ERROR_NOT_FOUND:
                break
            index += 1
            if hr != 0:
                continue
            try:
                desc = DXGI_ADAPTER_DESC1()
                if _method(adapter, 10, ctypes.POINTER(DXGI_ADAPTER_DESC1))(adapter, ctypes.byref(desc)) == 0:
                    software = bool(desc.Flags & DXGI_ADAPTER_FLAG_SOFTWARE)
                    name = desc.Description.strip() + (" (software: drawn by the CPU)" if software else "")
                    found.append({"name": name, "vendor": VENDORS.get(desc.VendorId, hex(desc.VendorId)),
                                  "vram": int(desc.DedicatedVideoMemory), "software": software,
                                  "luid": luid_key(desc.AdapterLuid.HighPart, desc.AdapterLuid.LowPart)})
            finally:
                _method(adapter, 2)(adapter)  # Release
    finally:
        _method(factory, 2)(factory)
    unique = {}
    for item in found:  # one adapter can be listed more than once (e.g. per output); keep the first
        unique.setdefault(item["luid"], item)
    return list(unique.values())


class Counters:
    """An open performance-counter query. Utilization is a rate, so the first read after opening is all zeros."""

    def __init__(self) -> None:
        self.pdh = ctypes.windll.pdh
        self.query = ctypes.c_void_p()
        status = self.pdh.PdhOpenQueryW(None, 0, ctypes.byref(self.query))
        if status != 0:
            raise OSError(f"Performance counters aren't available (0x{status & 0xFFFFFFFF:08X})")
        self.handles = {}
        for name, path in COUNTERS.items():
            handle = ctypes.c_void_p()
            if self.pdh.PdhAddEnglishCounterW(self.query, path, 0, ctypes.byref(handle)) == 0:
                self.handles[name] = handle
        if not self.handles:
            raise OSError("This Windows has no GPU performance counters (needs Windows 10 1709 or newer).")
        self.pdh.PdhCollectQueryData(self.query)

    def read(self) -> dict[str, dict[str, float]]:
        """{counter: {instance name: value}} for every GPU counter."""
        self.pdh.PdhCollectQueryData(self.query)
        result = {}
        for name, handle in self.handles.items():
            size, count = wintypes.DWORD(0), wintypes.DWORD(0)
            status = self.pdh.PdhGetFormattedCounterArrayW(handle, PDH_FMT_DOUBLE | PDH_FMT_NOCAP100,
                                                           ctypes.byref(size), ctypes.byref(count), None)
            values: dict[str, float] = {}
            if status & 0xFFFFFFFF == PDH_MORE_DATA and size.value:
                buffer = ctypes.create_string_buffer(size.value)
                status = self.pdh.PdhGetFormattedCounterArrayW(handle, PDH_FMT_DOUBLE | PDH_FMT_NOCAP100,
                                                               ctypes.byref(size), ctypes.byref(count), buffer)
                if status == 0:
                    items = ctypes.cast(buffer, ctypes.POINTER(PDH_FMT_COUNTERVALUE_ITEM_W))
                    for i in range(count.value):
                        item = items[i]
                        if item.FmtValue.CStatus in (PDH_CSTATUS_VALID_DATA, PDH_CSTATUS_NEW_DATA):
                            key = item.szName.lower()
                            values[key] = values.get(key, 0.0) + item.FmtValue.value.doubleValue
            result[name] = values
        return result

    def close(self) -> None:
        if self.query:
            self.pdh.PdhCloseQuery(self.query)
            self.query = ctypes.c_void_p()


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]


def process_table() -> dict[int, tuple[str, int]]:
    """{pid: (file name, parent pid)} for every process, protected ones included (nothing is opened)."""
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.Process32FirstW.argtypes = kernel32.Process32NextW.argtypes = [wintypes.HANDLE,
                                                                           ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if not snapshot or snapshot == wintypes.HANDLE(-1).value:
        return {}
    table = {}
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            table[entry.th32ProcessID] = (entry.szExeFile, entry.th32ParentProcessID)
            more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return table


def process_name(pid: int) -> str:
    """The program's file name, or '' when Windows won't say (protected system processes)."""
    if pid == 0:
        return ""
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = wintypes.HANDLE  # the default int would cut 64-bit handles short
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                    ctypes.POINTER(wintypes.DWORD)]
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return os.path.basename(buffer.value)
        return ""
    finally:
        kernel32.CloseHandle(handle)
