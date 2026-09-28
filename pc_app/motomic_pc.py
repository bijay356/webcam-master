import concurrent.futures
import ctypes
import io
import json
import math
import os
import queue
import re
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
import wave
import winreg
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageGrab, ImageTk
import sounddevice as sd

try:
    import pyvirtualcam
    HAS_PYVIRTUALCAM = True
except Exception:
    HAS_PYVIRTUALCAM = False


MAGIC = b"MMIC"
PACKET_AUDIO = 1
PACKET_HANDSHAKE = 2
PACKET_PING = 3
PACKET_VIDEO = 4
PACKET_CONTROL = 5

AUDIO_PORT = 8989
PHONE_WIRELESS_PORT = 8988
DISCOVERY_PORT = 8990
TELEMETRY_PORT = 8991
DEFAULT_SAMPLE_RATE = 48000

TRANS_COLOR_KEY = "#010203"
TRANS_RGB = (1, 2, 3)

# Windows 10/11 Display Affinity constant: Excludes window from ALL screen recordings (Filmora, OBS, GDI, etc.)
WDA_NONE = 0x00000000
WDA_EXCLUDEFROMCAPTURE = 0x00000011


def set_window_exclude_from_capture(tk_win, exclude: bool = True) -> bool:
    """
    Uses Windows DWM SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE) so the Floating
    Stop/Record Control Bar is visible to the user on the monitor, but 100% INVISIBLE in any recorded video!
    """
    if os.name != "nt" or tk_win is None:
        return False
    try:
        tk_win.update_idletasks()
        flag = WDA_EXCLUDEFROMCAPTURE if exclude else WDA_NONE
        handles = set()
        try:
            p_hwnd = ctypes.windll.user32.GetParent(tk_win.winfo_id())
            if p_hwnd:
                handles.add(p_hwnd)
        except Exception:
            pass
        try:
            wm_f = tk_win.wm_frame()
            if wm_f and wm_f != "0x0":
                handles.add(int(wm_f, 16))
        except Exception:
            pass
        handles.add(tk_win.winfo_id())
        ok = False
        for h in handles:
            if h:
                res = ctypes.windll.user32.SetWindowDisplayAffinity(int(h), int(flag))
                if res:
                    ok = True
        return ok
    except Exception:
        return False


def _no_window_startupinfo():
    if os.name == "nt":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        return si
    return None


def clean_device_name(raw: str) -> str:
    if not raw or raw == "None":
        return "Motorola Edge 50 Neo"
    words = raw.strip().split()
    dedup = []
    for w in words:
        if not dedup or dedup[-1].lower() != w.lower():
            dedup.append(w.capitalize() if w.islower() else w)
    return " ".join(dedup) if dedup else "Motorola Edge 50 Neo"


def get_offline_network_info() -> dict:
    """100% Offline Network & Hotspot Inspector (NEVER requires Internet)."""
    local_ips = []
    gateways = []
    broadcasts = ["255.255.255.255"]
    arp_neighbors = []
    wireless_mode_hint = "Offline Local Wireless (No Internet Required)"

    si = _no_window_startupinfo()
    try:
        proc = subprocess.run(
            ["ipconfig"],
            capture_output=True, text=True, timeout=3, startupinfo=si,
        )
        current_adapter = ""
        for raw_line in proc.stdout.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if not raw_line.startswith(" ") and line.endswith(":"):
                current_adapter = line.lower()
                continue
            if "ipv4 address" in line.lower() or "ip address" in line.lower():
                m = re.search(r"(\d+\.\d+\.\d+\.\d+)", line)
                if m:
                    ip = m.group(1)
                    if ip.startswith("127.") or ip.startswith("169.254."):
                        continue
                    is_virtual = ("vethernet" in current_adapter or "wsl" in current_adapter)
                    if not is_virtual:
                        local_ips.insert(0, ip)
                    else:
                        local_ips.append(ip)
                    parts = ip.split(".")
                    if len(parts) == 4 and not is_virtual:
                        bcast = f"{parts[0]}.{parts[1]}.{parts[2]}.255"
                        if bcast not in broadcasts:
                            broadcasts.append(bcast)
                    if ip.startswith("192.168.137."):
                        wireless_mode_hint = "📡 PC Offline Hotspot (No Router/Internet Needed)"
                    elif ip.startswith("192.168.43.") or ip.startswith("192.168.215."):
                        wireless_mode_hint = "📡 Phone Offline Hotspot (No Internet Needed)"
            elif "default gateway" in line.lower():
                m = re.search(r"(\d+\.\d+\.\d+\.\d+)", line)
                if m:
                    gw = m.group(1)
                    if gw and not gw.startswith("127.") and gw not in gateways:
                        gateways.append(gw)
    except Exception:
        pass

    if not local_ips:
        try:
            hostname = socket.gethostname()
            for ip in socket.gethostbyname_ex(hostname)[2]:
                if not ip.startswith("127.") and not ip.startswith("169.254."):
                    local_ips.append(ip)
        except Exception:
            pass

    primary_ip = local_ips[0] if local_ips else "127.0.0.1"
    primary_prefix = ".".join(primary_ip.split(".")[:3]) + "." if primary_ip != "127.0.0.1" else ""

    try:
        arp_proc = subprocess.run(
            ["arp", "-a"],
            capture_output=True, text=True, timeout=3, startupinfo=si,
        )
        for line in arp_proc.stdout.splitlines():
            line_s = line.strip().lower()
            if "dynamic" in line_s:
                m = re.search(r"(\d+\.\d+\.\d+\.\d+)\s+([0-9a-f\-]{17})", line_s)
                if m:
                    ip = m.group(1)
                    mac = m.group(2)
                    if ip.endswith(".255") or ip.startswith("224.") or ip.startswith("239."):
                        continue
                    if primary_prefix and ip.startswith(primary_prefix):
                        arp_neighbors.insert(0, (ip, mac))
                    else:
                        arp_neighbors.append((ip, mac))
    except Exception:
        pass

    return {
        "primary_ip": primary_ip,
        "local_ips": local_ips,
        "gateways": gateways,
        "broadcasts": broadcasts,
        "arp_neighbors": arp_neighbors,
        "wireless_mode_hint": wireless_mode_hint,
    }


def get_local_ip() -> str:
    return get_offline_network_info()["primary_ip"]


def find_adb() -> str:
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        os.path.join(local_app_data, "Android", "Sdk", "platform-tools", "adb.exe"),
        r"C:\Users\admin\AppData\Local\Android\Sdk\platform-tools\adb.exe",
        "adb.exe",
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return "adb"


def find_ffmpeg() -> str:
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.isfile(exe):
            return exe
    except Exception:
        pass
    return "ffmpeg"


def ensure_virtual_camera_registered():
    """Registers Webcam Master (UnityCapture DirectShow Filter) in HKCU without needing Admin."""
    dll64 = r"F:\pc software\drivers\unitycapture\UnityCapture-master\Install\UnityCaptureFilter64.dll"
    dll32 = r"F:\pc software\drivers\unitycapture\UnityCapture-master\Install\UnityCaptureFilter32.dll"
    if not os.path.isfile(dll64):
        return False

    clsid64 = "{5C2CD55C-92AD-4999-8666-912BD3E70010}"
    clsid32 = "{5C2CD55C-92AD-4999-8666-912BD3E70020}"
    cat_clsid = "{860BB310-5D01-11d0-BD3B-00A0C911CE86}"
    friendly_name = "Webcam Master (Phone)"
    filter_data = bytes([
        0x02, 0x00, 0x00, 0x00, 0x00, 0x00, 0x20, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        0x30, 0x70, 0x69, 0x33, 0x08, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x30, 0x74, 0x79, 0x33, 0x00, 0x00, 0x00, 0x00,
        0x38, 0x00, 0x00, 0x00, 0x48, 0x00, 0x00, 0x00, 0x76, 0x69, 0x64, 0x73, 0x00, 0x00, 0x10, 0x00,
        0x80, 0x00, 0x00, 0xAA, 0x00, 0x38, 0x9B, 0x71, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    ])
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\CLSID\{clsid64}") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, friendly_name)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\CLSID\{clsid64}\InprocServer32") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, dll64)
            winreg.SetValueEx(k, "ThreadingModel", 0, winreg.REG_SZ, "Both")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\CLSID\{cat_clsid}\Instance\{clsid64}") as k:
            winreg.SetValueEx(k, "CLSID", 0, winreg.REG_SZ, clsid64)
            winreg.SetValueEx(k, "FriendlyName", 0, winreg.REG_SZ, friendly_name)
            winreg.SetValueEx(k, "DevicePath", 0, winreg.REG_SZ, "motocam:virtual64")
            winreg.SetValueEx(k, "FilterData", 0, winreg.REG_BINARY, filter_data)

        if os.path.isfile(dll32):
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\WOW6432Node\CLSID\{clsid32}") as k:
                winreg.SetValueEx(k, "", 0, winreg.REG_SZ, friendly_name)
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\WOW6432Node\CLSID\{clsid32}\InprocServer32") as k:
                winreg.SetValueEx(k, "", 0, winreg.REG_SZ, dll32)
                winreg.SetValueEx(k, "ThreadingModel", 0, winreg.REG_SZ, "Both")
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\WOW6432Node\CLSID\{cat_clsid}\Instance\{clsid32}") as k:
                winreg.SetValueEx(k, "CLSID", 0, winreg.REG_SZ, clsid32)
                winreg.SetValueEx(k, "FriendlyName", 0, winreg.REG_SZ, friendly_name)
                winreg.SetValueEx(k, "DevicePath", 0, winreg.REG_SZ, "motocam:virtual32")
                winreg.SetValueEx(k, "FilterData", 0, winreg.REG_BINARY, filter_data)
        return True
    except Exception:
        return False


def configure_windows_default_mic_for_recorders(restore_physical_mic: bool = False) -> str:
    """
    Automatically enables Windows Stereo Mix / VB-Cable Output and sets it as the
    Windows Default Recording Device (eConsole, eMultimedia, eCommunications) using
    the Windows CoreAudio IPolicyConfig COM interface (no Admin required).
    This ensures Wondershare Filmora, OBS, Bandicam, Camtasia, and ALL external screen
    recorders/video editors automatically capture the phone's voice and video in the SAME file!
    """
    if os.name != "nt":
        return "System Default Mic"

    cable_ep = None
    cable_name = None
    stereo_ep = None
    stereo_name = None
    phys_ep = None
    phys_name = None
    phys_eps = []

    try:
        cap_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Capture"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, cap_path, 0, winreg.KEY_READ) as k:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(k, i)
                    i += 1
                except OSError:
                    break
                try:
                    dev_state = 1
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"{cap_path}\\{sub}", 0, winreg.KEY_READ) as sk:
                        dev_state, _ = winreg.QueryValueEx(sk, "DeviceState")
                    # Skip endpoints that are NotPresent (0x4) or Unplugged (0x8)
                    if (int(dev_state) & 0xF) not in (0x1, 0x2):
                        continue
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"{cap_path}\\{sub}\\Properties", 0, winreg.KEY_READ) as pk:
                        dev_desc = ""
                        iface_name = ""
                        j = 0
                        while True:
                            try:
                                vname, vval, _ = winreg.EnumValue(pk, j)
                                j += 1
                                if vname.lower() == "{a45c254e-df1c-4efd-8020-67d146a850e0},2" and isinstance(vval, str):
                                    dev_desc = vval
                                elif vname.lower() == "{b3f8fa53-0004-438e-9003-51a46e139bfc},6" and isinstance(vval, str):
                                    iface_name = vval
                            except OSError:
                                break
                        full_label = f"{dev_desc} ({iface_name})" if iface_name else dev_desc
                        dl = full_label.lower()
                        ep_id = f"{{0.0.1.00000000}}.{sub}"
                        if "cable output" in dl or "vb-audio" in dl:
                            cable_ep = ep_id
                            cable_name = full_label
                        elif "stereo mix" in dl or "what u hear" in dl or "wave out" in dl:
                            stereo_ep = ep_id
                            stereo_name = full_label
                        elif ("microphone" in dl or "mic" in dl) and "pc speaker" not in dl:
                            phys_eps.append(ep_id)
                            if phys_ep is None or "realtek" in dl or "array" in dl:
                                phys_ep = ep_id
                                phys_name = full_label
                except Exception:
                    continue
    except Exception:
        pass

    try:
        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", ctypes.c_ulong),
                ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort),
                ("Data4", ctypes.c_ubyte * 8),
            ]

            def __init__(self, g_str):
                s = g_str.strip("{}").replace("-", "")
                self.Data1 = int(s[0:8], 16)
                self.Data2 = int(s[8:12], 16)
                self.Data3 = int(s[12:16], 16)
                b = bytes.fromhex(s[16:32])
                for idx_b in range(8):
                    self.Data4[idx_b] = b[idx_b]

        ole32 = ctypes.windll.ole32
        ole32.CoInitialize(None)
        clsid_policy = GUID("{870af99c-171d-4f9e-af0d-e63df40c2bc9}")
        iid_policy = GUID("{f8679f50-850a-41cf-9c72-430f290290c8}")
        ptr = ctypes.c_void_p()
        hr = ole32.CoCreateInstance(
            ctypes.byref(clsid_policy),
            None,
            1,
            ctypes.byref(iid_policy),
            ctypes.byref(ptr),
        )
        if hr == 0 and ptr.value:
            vtable = ctypes.cast(
                ctypes.cast(ptr, ctypes.POINTER(ctypes.c_void_p))[0],
                ctypes.POINTER(ctypes.c_void_p),
            )
            SetDefaultEndpoint = ctypes.WINFUNCTYPE(
                ctypes.c_long, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int
            )(vtable[13])
            SetEndpointVisibility = ctypes.WINFUNCTYPE(
                ctypes.c_long, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int
            )(vtable[14])

            # Always ensure both Virtual Cable Output and Stereo Mix are enabled & visible in Windows
            for ep in (cable_ep, stereo_ep):
                if ep:
                    SetEndpointVisibility(ptr, ep, 1)

            if restore_physical_mic and phys_ep and not cable_ep:
                for p_ep in phys_eps:
                    SetEndpointVisibility(ptr, p_ep, 1)
                target_ep, target_name = phys_ep, phys_name
            elif cable_ep:
                for p_ep in phys_eps:
                    SetEndpointVisibility(ptr, p_ep, 0)
                target_ep, target_name = cable_ep, cable_name
            elif stereo_ep:
                for p_ep in phys_eps:
                    SetEndpointVisibility(ptr, p_ep, 0)
                target_ep, target_name = stereo_ep, stereo_name
            else:
                for p_ep in phys_eps:
                    SetEndpointVisibility(ptr, p_ep, 1)
                target_ep, target_name = phys_ep, (phys_name or "Windows Default Mic")

            if target_ep:
                for role in (0, 1, 2):  # eConsole, eMultimedia, eCommunications
                    SetDefaultEndpoint(ptr, target_ep, role)

            chosen_name = target_name or "Stereo Mix (Realtek(R) Audio)"
            filmora_pref_mic = cable_name or stereo_name or chosen_name
            try:
                filmora_xml = os.path.join(
                    os.environ.get("APPDATA", ""),
                    "Wondershare",
                    "Wondershare Filmora",
                    "Settingdatas.xml",
                )
                if os.path.isfile(filmora_xml):
                    with open(filmora_xml, "r", encoding="utf-8", errors="ignore") as f_in:
                        xml_txt = f_in.read()
                    new_xml = re.sub(
                        r"<m_sSelectedAudioDeviceName>.*?</m_sSelectedAudioDeviceName>",
                        f"<m_sSelectedAudioDeviceName>{filmora_pref_mic}</m_sSelectedAudioDeviceName>",
                        xml_txt,
                    )
                    new_xml = re.sub(
                        r"<AudioSilent>.*?</AudioSilent>",
                        "<AudioSilent>false</AudioSilent>",
                        new_xml,
                    )
                    if new_xml != xml_txt:
                        with open(filmora_xml, "w", encoding="utf-8") as f_out:
                            f_out.write(new_xml)
            except Exception:
                pass
            return chosen_name
    except Exception:
        pass

    return cable_name or stereo_name or phys_name or "Windows Default Mic"


class SilentVirtualMicBridge:
    """
    Ensures ZERO sound comes out of the physical PC speakers when speaking into the mobile phone,
    while routing 100% digital-clarity voice into Windows Default Mic (Stereo Mix) for Filmora & all recorders!
    On Realtek HD Audio, Stereo Mix taps the digital Render bus BEFORE the physical speaker hardware mute switch
    (IAudioEndpointVolume::SetMute(1)). By hardware-muting the physical speaker DAC while voice frames are on the
    bus, the PC speakers emit 0.0% sound (no repeat/echo on PC) while Filmora records voice + video in the same file.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.speakers_muted_by_bridge = False
        self.muted_since = 0.0
        self.unmute_drain_started = 0.0
        self.last_voice_ts = 0.0
        self.last_pc_playback_ts = 0.0
        self.saved_spk_vol = 0.35
        self._tls = threading.local()
        self._ensure_clean_initial_state()

    def _ensure_clean_initial_state(self):
        ifaces = self._get_com_interfaces()
        if ifaces is not None:
            try:
                cur_m = ctypes.c_int()
                ifaces["spk_get_mute"](ifaces["p_spk_vol"], ctypes.byref(cur_m))
                cur_v = ctypes.c_float()
                ifaces["spk_get_vol"](ifaces["p_spk_vol"], ctypes.byref(cur_v))
                if 0.05 <= cur_v.value <= 0.85:
                    self.saved_spk_vol = float(cur_v.value)
                elif cur_v.value > 0.85:
                    ifaces["spk_set_vol"](ifaces["p_spk_vol"], 0.35, None)
                if cur_m.value != 0:
                    ifaces["spk_set_mute"](ifaces["p_spk_vol"], 0, None)
                ifaces["mic_set_mute"](ifaces["p_mic_vol"], 0, None)
                ifaces["mic_set_vol"](ifaces["p_mic_vol"], 1.0, None)
            except Exception:
                pass

    def _get_com_interfaces(self):
        if os.name != "nt":
            return None
        if getattr(self._tls, "initialized", False):
            return getattr(self._tls, "ifaces", None)
        try:
            class GUID(ctypes.Structure):
                _fields_ = [
                    ("Data1", ctypes.c_ulong),
                    ("Data2", ctypes.c_ushort),
                    ("Data3", ctypes.c_ushort),
                    ("Data4", ctypes.c_ubyte * 8),
                ]

                def __init__(self, g_str):
                    s = g_str.strip("{}").replace("-", "")
                    self.Data1 = int(s[0:8], 16)
                    self.Data2 = int(s[8:12], 16)
                    self.Data3 = int(s[12:16], 16)
                    b = bytes.fromhex(s[16:32])
                    for idx_b in range(8):
                        self.Data4[idx_b] = b[idx_b]

            ole32 = ctypes.windll.ole32
            ole32.CoInitialize(None)
            clsid_enum = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
            iid_enum = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
            p_enum = ctypes.c_void_p()
            hr = ole32.CoCreateInstance(
                ctypes.byref(clsid_enum), None, 1, ctypes.byref(iid_enum), ctypes.byref(p_enum)
            )
            if hr != 0 or not p_enum.value:
                self._tls.initialized = True
                self._tls.ifaces = None
                return None

            vt_enum = ctypes.cast(
                ctypes.cast(p_enum, ctypes.POINTER(ctypes.c_void_p))[0],
                ctypes.POINTER(ctypes.c_void_p),
            )
            GetDefaultAudioEndpoint = ctypes.WINFUNCTYPE(
                ctypes.c_long, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)
            )(vt_enum[4])

            iid_epvol = GUID("{5CDF2C82-841E-4546-9722-0CF74078229A}")
            iid_meter = GUID("{C02216F6-8C67-4B5B-9D00-D008E73E0064}")

            # eRender (0) = Speakers
            p_spk_dev = ctypes.c_void_p()
            GetDefaultAudioEndpoint(p_enum, 0, 0, ctypes.byref(p_spk_dev))
            vt_spk_dev = ctypes.cast(
                ctypes.cast(p_spk_dev, ctypes.POINTER(ctypes.c_void_p))[0],
                ctypes.POINTER(ctypes.c_void_p),
            )
            ActivateSpk = ctypes.WINFUNCTYPE(
                ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.c_ulong, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)
            )(vt_spk_dev[3])

            p_spk_vol = ctypes.c_void_p()
            ActivateSpk(p_spk_dev, ctypes.byref(iid_epvol), 1, None, ctypes.byref(p_spk_vol))
            vt_spk_vol = ctypes.cast(
                ctypes.cast(p_spk_vol, ctypes.POINTER(ctypes.c_void_p))[0],
                ctypes.POINTER(ctypes.c_void_p),
            )

            p_spk_meter = ctypes.c_void_p()
            ActivateSpk(p_spk_dev, ctypes.byref(iid_meter), 1, None, ctypes.byref(p_spk_meter))
            vt_spk_meter = ctypes.cast(
                ctypes.cast(p_spk_meter, ctypes.POINTER(ctypes.c_void_p))[0],
                ctypes.POINTER(ctypes.c_void_p),
            )

            # eCapture (1) = Default Mic (Stereo Mix)
            p_mic_dev = ctypes.c_void_p()
            GetDefaultAudioEndpoint(p_enum, 1, 0, ctypes.byref(p_mic_dev))
            vt_mic_dev = ctypes.cast(
                ctypes.cast(p_mic_dev, ctypes.POINTER(ctypes.c_void_p))[0],
                ctypes.POINTER(ctypes.c_void_p),
            )
            ActivateMic = ctypes.WINFUNCTYPE(
                ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.c_ulong, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)
            )(vt_mic_dev[3])
            p_mic_vol = ctypes.c_void_p()
            ActivateMic(p_mic_dev, ctypes.byref(iid_epvol), 1, None, ctypes.byref(p_mic_vol))
            vt_mic_vol = ctypes.cast(
                ctypes.cast(p_mic_vol, ctypes.POINTER(ctypes.c_void_p))[0],
                ctypes.POINTER(ctypes.c_void_p),
            )

            SetMuteFn = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p)
            GetMuteFn = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int))
            SetVolFn = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_float, ctypes.c_void_p)
            GetVolFn = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(ctypes.c_float))
            GetPeakFn = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(ctypes.c_float))

            ifaces = {
                "p_spk_vol": p_spk_vol,
                "spk_set_mute": SetMuteFn(vt_spk_vol[14]),
                "spk_get_mute": GetMuteFn(vt_spk_vol[15]),
                "spk_set_vol": SetVolFn(vt_spk_vol[7]),
                "spk_get_vol": GetVolFn(vt_spk_vol[9]),
                "p_spk_meter": p_spk_meter,
                "spk_get_peak": GetPeakFn(vt_spk_meter[3]),
                "p_mic_vol": p_mic_vol,
                "mic_set_mute": SetMuteFn(vt_mic_vol[14]),
                "mic_set_vol": SetVolFn(vt_mic_vol[7]),
            }
            self._tls.initialized = True
            self._tls.ifaces = ifaces
            return ifaces
        except Exception:
            self._tls.initialized = True
            self._tls.ifaces = None
            return None

    @property
    def allow_bus_output(self) -> bool:
        return self.speakers_muted_by_bridge and self.unmute_drain_started == 0.0

    def on_voice_activity(self):
        now = time.time()
        self.last_voice_ts = now
        with self.lock:
            self.unmute_drain_started = 0.0
            if not self.speakers_muted_by_bridge:
                ifaces = self._get_com_interfaces()
                if ifaces is not None:
                    try:
                        cur_m = ctypes.c_int()
                        ifaces["spk_get_mute"](ifaces["p_spk_vol"], ctypes.byref(cur_m))
                        cur_v = ctypes.c_float()
                        ifaces["spk_get_vol"](ifaces["p_spk_vol"], ctypes.byref(cur_v))
                        if cur_m.value == 0 and 0.05 <= cur_v.value <= 0.85:
                            self.saved_spk_vol = float(cur_v.value)
                        # 1. Hardware-mute physical speakers FIRST so zero voice comes out of PC speakers!
                        ifaces["spk_set_mute"](ifaces["p_spk_vol"], 1, None)
                        # 2. Set digital bus to 95% and Stereo Mix Default Mic to 100% unmuted for Filmora!
                        ifaces["spk_set_vol"](ifaces["p_spk_vol"], 0.95, None)
                        ifaces["mic_set_mute"](ifaces["p_mic_vol"], 0, None)
                        ifaces["mic_set_vol"](ifaces["p_mic_vol"], 1.0, None)
                    except Exception:
                        pass
                self.speakers_muted_by_bridge = True
                self.muted_since = now

    def check_idle_and_restore_speakers(self, audio_queue: queue.Queue, force: bool = False):
        now = time.time()
        with self.lock:
            if not self.speakers_muted_by_bridge:
                return
            if not force and (now - self.last_voice_ts) < 2.50:
                return
            if self.unmute_drain_started == 0.0:
                self.unmute_drain_started = now
                while not audio_queue.empty():
                    try:
                        audio_queue.get_nowait()
                    except Exception:
                        break
                if not force:
                    return
            if not force and (now - self.unmute_drain_started) < 0.090:
                return
            ifaces = self._get_com_interfaces()
            if ifaces is not None:
                try:
                    ifaces["spk_set_vol"](ifaces["p_spk_vol"], float(self.saved_spk_vol), None)
                    ifaces["spk_set_mute"](ifaces["p_spk_vol"], 0, None)
                except Exception:
                    pass
            self.speakers_muted_by_bridge = False
            self.unmute_drain_started = 0.0


class MotoMicEngine:
    def __init__(self):
        self.running = True
        self.stream_enabled = True
        self.connected = False
        self.client_addr = "None"
        self.device_name = "None"
        self.device_android = ""
        self.connection_type = "Disconnected"
        self.sample_rate = DEFAULT_SAMPLE_RATE

        net_info = get_offline_network_info()
        self.local_ip = net_info["primary_ip"]
        self.local_ips = set(net_info.get("local_ips", [self.local_ip]))
        self.pc_hostname = socket.gethostname()
        self.wireless_mode_hint = net_info["wireless_mode_hint"]
        self.phone_wifi_ip = ""
        self.auto_wireless_enabled = True

        self.discovered_devices = {}
        self.standby_phone_detected = False
        self.standby_phone_label = ""

        # App Mode: "WEBCAM_MIC", "MIC_ONLY", "WEBCAM_ONLY"
        self.app_mode = "WEBCAM_MIC"

        # Camera Source Choice: "PHONE" (Mobile Phone Camera), "PC_0"/"PC_1" (PC Webcam), or "OFF" (Camera OFF / Mic Only)
        self.camera_source = "PHONE"
        self.last_active_camera_source = "PHONE"
        self.available_pc_cameras = self._detect_pc_cameras()

        # Audio Processing & Virtual Mic / Output Routing (ON by default for Filmora & all external recorders!)
        self.gain = 1.0
        self.noise_gate_db = -54.0
        self.monitor_enabled = True
        self.virtual_mic_installed = False
        self.anti_echo_enabled = False
        self.selected_output_device = None
        self.output_device_name = "None"
        self.silent_bridge = SilentVirtualMicBridge()
        # Ensure physical speakers start unmuted at normal volume while Stereo Mix is Default Mic
        self.windows_default_mic_name = configure_windows_default_mic_for_recorders(restore_physical_mic=False)

        # Screen Recording Audio Options: Record PC System Sound + Voice Over (Phone Mic / PC Mic)
        self.record_pc_sound = True
        self.record_voice_over = True
        self.pc_system_sound_dev_id, self.pc_system_sound_dev_name = self._find_pc_system_sound_device()
        self._pc_sound_stream = None
        self._pc_mic_fallback_stream = None

        # Live Audio Metrics
        self.current_rms = 0.0
        self.current_db = -60.0
        self.peak_db = -60.0
        self.dominant_freq_hz = 0.0
        self.packets_received = 0
        self.bytes_received = 0
        self.last_packet_time = 0.0
        self._last_audio_seq = -1

        # Acoustic Feedback Guard
        self._last_speaker_play_time = 0.0
        self._last_speaker_play_rms = 0.0

        # Live Video State & Virtual Camera (for Filmora Camera Dropdown)
        self.video_frames_received = 0
        self.video_fps = 0.0
        self._fps_counter = 0
        self._fps_timer = time.time()
        self.latest_jpeg_bytes = None
        self.latest_pil_image = None
        self.video_resolution = "0x0"
        self.video_lock = threading.Lock()
        self._pending_video_item = None
        self._pending_video_event = threading.Event()
        self.virtual_cam = None
        self.virtual_cam_active = False
        self.virtual_cam_name = "Webcam Master (Phone)"

        # Floating Facecam State: DO NOT start automatically! Starts ONLY when user selects Round/Square/Full!
        self.facecam_visible = False
        self.facecam_shape = "OFF"    # "OFF" (default), "ROUND", "SQUARE", "FULLSCREEN"
        self.facecam_size = 250       # Diameter/Width in pixels (140..560)
        self.facecam_corner = "BR"    # "BR", "BL", "TR", "TL", "CUSTOM"
        self.rec_hud_visible = False
        self.rec_hud_excluded_from_video = False
        self.facecam_ui_callback = None
        self.ui_screenshot_callback = None

        # Active Client Socket
        self.active_socket = None
        self.socket_lock = threading.Lock()

        # Waveform History (256 points)
        self.vis_buffer = np.zeros(256, dtype=np.float32)
        self.vis_lock = threading.Lock()

        # Ultra-low-latency Audio Output Queue (max 2 frames = 40ms)
        self.audio_queue = queue.Queue(maxsize=2)
        self.audio_stream = None
        self.audio_stream_active = False

        # Save Directory Options (User can choose Documents, C:\ Drive, Videos, Desktop, Custom Folder, or Ask Every Time)
        self.base_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        if self.base_dir.endswith("pc_app") or "dist" in self.base_dir.lower():
            self.base_dir = r"f:\pc software" if os.path.exists(r"f:\pc software") else self.base_dir
        user_home = os.path.expanduser("~")
        self.docs_dir = os.path.join(user_home, "Documents", "WebcamMaster_Recordings")
        self.c_drive_dir = r"C:\WebcamMaster_Recordings"
        self.videos_dir = os.path.join(user_home, "Videos", "WebcamMaster_Recordings")
        self.desktop_dir = os.path.join(user_home, "Desktop", "WebcamMaster_Recordings")
        self.app_rec_dir = os.path.join(self.base_dir, "recordings")

        for d_path in (self.docs_dir, self.c_drive_dir, self.app_rec_dir):
            try:
                os.makedirs(d_path, exist_ok=True)
            except Exception:
                pass

        # Default to Documents folder (with instant dropdown choice for C:\ Drive, Documents, Videos, Desktop, or Ask Every Time)
        self.recordings_dir = self.docs_dir if os.path.exists(self.docs_dir) else self.app_rec_dir
        self.ask_save_every_time = False

        # Saved Media File Tracking
        self.last_saved_media_file = self._find_latest_saved_media()

        # WAV Recording State
        self.is_recording = False
        self.record_frames = []
        self.record_lock = threading.Lock()
        self.record_filepath = ""

        # Combined Screen / Video + PC System Sound + Voice Over MP4 Recording State (Single Media File)
        self.is_recording_mp4 = False
        self.record_screen_mode = True
        self.mp4_video_frames = []
        self.mp4_audio_frames = []      # Voice Over (Phone Mic / PC Mic)
        self.mp4_pc_sound_frames = []   # PC System Sound (Stereo Mix / Loopback)
        self.mp4_start_time = 0.0
        self.mp4_lock = threading.Lock()
        self.mp4_filepath = ""
        self._screen_capture_thread = None

        # USB ADB State
        self.usb_serial = None
        self.usb_reverse_active = False
        self.usb_status = "Searching for phone (Works Offline — No Internet Needed)..."

        self.logs = []
        self._init_virtual_camera()
        self._auto_detect_virtual_mic_device()

    def _detect_pc_cameras(self) -> list[tuple[str, str]]:
        """Returns camera source options immediately without blocking startup."""
        return [
            ("PHONE", "📱 Mobile Phone Camera (Wireless / USB)"),
            ("PC_0", "💻 PC Webcam 0 (Built-in / USB Camera)"),
            ("PC_1", "🎥 External Camera 1 (USB / Capture Card)"),
        ]

    def _find_pc_system_sound_device(self) -> tuple[int | None, str]:
        """Finds Windows Stereo Mix / PC Speaker Loopback device to record PC System Sound."""
        try:
            all_devs = sd.query_devices()
            for idx, d in enumerate(all_devs):
                if d.get("max_input_channels", 0) > 0:
                    nm = d.get("name", "")
                    nl = nm.lower()
                    if "stereo mix" in nl or "what u hear" in nl or "loopback" in nl or "wave out" in nl:
                        return idx, nm
        except Exception:
            pass
        return None, "Stereo Mix (System Sound)"

    def get_save_location_options(self) -> list[tuple[str, str]]:
        return [
            (f"📄 Documents ({self.docs_dir})", self.docs_dir),
            (f"💻 C:\\ Drive ({self.c_drive_dir})", self.c_drive_dir),
            (f"🎬 Videos ({self.videos_dir})", self.videos_dir),
            (f"🖥️ Desktop ({self.desktop_dir})", self.desktop_dir),
            (f"📂 App Folder ({self.app_rec_dir})", self.app_rec_dir),
            ("❓ Ask Where to Save Every Time (Save As...)", "ASK_EVERY_TIME"),
        ]

    def set_save_location(self, path_or_mode: str):
        if path_or_mode == "ASK_EVERY_TIME":
            self.ask_save_every_time = True
            self.log("Save Mode -> Ask Where to Save Every Time (Save As... Dialog)")
        else:
            self.ask_save_every_time = False
            try:
                os.makedirs(path_or_mode, exist_ok=True)
                self.recordings_dir = path_or_mode
                self.log(f"Save Location -> {self.recordings_dir}")
            except Exception as e:
                self.log(f"Could not create folder {path_or_mode}: {e}")

    def _find_latest_saved_media(self) -> str:
        search_dirs = [self.recordings_dir, self.docs_dir, self.c_drive_dir, self.app_rec_dir]
        candidates = []
        for d in search_dirs:
            try:
                if os.path.isdir(d):
                    for f in os.listdir(d):
                        if f.lower().endswith((".mp4", ".wav", ".avi")) and not f.endswith(".temp.avi") and not f.endswith(".temp.wav"):
                            candidates.append(os.path.join(d, f))
            except Exception:
                pass
        if candidates:
            candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
            return candidates[0]
        return ""

    def log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        entry = f"[{ts}] {msg}"
        print(entry, flush=True)
        self.logs.append(entry)
        if len(self.logs) > 100:
            self.logs = self.logs[-100:]

    def _register_discovered_device(self, ip: str, name: str = "Motorola Edge 50 Neo", status: str = "STANDBY", mode: str = "Offline Wireless"):
        if not ip or ip == "127.0.0.1" or ip == self.local_ip or ip in getattr(self, "local_ips", set()):
            return
        name = clean_device_name(name)
        existing = self.discovered_devices.get(ip, {})
        if name == "Android Phone" and existing.get("name"):
            name = existing["name"]
        self.discovered_devices[ip] = {
            "name": name,
            "ip": ip,
            "status": status,
            "mode": mode,
            "last_seen": time.time(),
        }

    def get_device_dropdown_items(self) -> list[str]:
        items = []
        if self.connected and self.device_name != "None":
            ip_part = self.client_addr.split(":")[0] if ":" in self.client_addr else self.phone_wifi_ip
            items.append(f"● {clean_device_name(self.device_name)} ({ip_part}) [CONNECTED]")
        for ip, info in list(self.discovered_devices.items()):
            if ip == self.local_ip or ip in getattr(self, "local_ips", set()):
                continue
            if self.connected and ip in self.client_addr:
                continue
            st = info.get("status", "STANDBY")
            nm = clean_device_name(info.get("name", "Motorola Edge 50 Neo"))
            badge = "READY" if st == "LIVE" else "ON WIRELESS"
            items.append(f"📱 {nm} ({ip}) [{badge}]")
        if not items:
            if self.phone_wifi_ip and self.phone_wifi_ip != self.local_ip and self.phone_wifi_ip not in getattr(self, "local_ips", set()):
                items.append(f"📱 Motorola Edge 50 Neo ({self.phone_wifi_ip})")
            else:
                items.append("📱 Auto-Discover Phone (Wi-Fi / Hotspot / USB)")
        return items

    def _init_virtual_camera(self):
        ensure_virtual_camera_registered()
        if not HAS_PYVIRTUALCAM:
            return
        try:
            self.virtual_cam = pyvirtualcam.Camera(width=640, height=480, fps=20, backend="unitycapture")
            self.virtual_cam_active = True
            blank = np.zeros((480, 640, 3), dtype=np.uint8)
            blank[:, :] = (15, 23, 42)
            cv2.putText(blank, "Webcam Master Ready", (125, 230), cv2.FONT_HERSHEY_SIMPLEX, 0.95, (56, 189, 248), 2)
            cv2.putText(blank, "Connect phone to stream live HD video", (95, 270), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (148, 163, 184), 1)
            self.virtual_cam.send(blank)
            self.log("Virtual Webcam Active: 'Webcam Master (Phone)' ready for Filmora!")
        except Exception as e:
            self.virtual_cam = None
            self.virtual_cam_active = False
            self.log(f"Virtual Webcam init note: {e}")

    def get_output_devices(self):
        devices = []
        try:
            all_devs = sd.query_devices()
            for idx, d in enumerate(all_devs):
                if d.get("max_output_channels", 0) > 0:
                    devices.append((idx, f"{idx}: {d['name']}"))
        except Exception as e:
            self.log(f"Audio device query error: {e}")
        return devices

    def _auto_detect_virtual_mic_device(self) -> bool:
        try:
            all_devs = sd.query_devices()
            fallback_idx = None
            fallback_name = None
            for idx, d in enumerate(all_devs):
                if d.get("max_output_channels", 0) > 0:
                    name_lower = d.get("name", "").lower()
                    if "cable input" in name_lower or "vb-audio" in name_lower:
                        self.virtual_mic_installed = True
                        self.selected_output_device = idx
                        self.output_device_name = d["name"]
                        self.monitor_enabled = True
                        self.windows_default_mic_name = configure_windows_default_mic_for_recorders(restore_physical_mic=False)
                        self.log(f"Auto-selected Virtual Mic Pipe: {d['name']} (Filmora Mic: {self.windows_default_mic_name})")
                        return True
                    if fallback_idx is None and ("realtek" in name_lower or "speakers" in name_lower or "sound mapper" in name_lower):
                        fallback_idx = idx
                        fallback_name = d["name"]
            if self.selected_output_device is None and fallback_idx is not None:
                self.selected_output_device = fallback_idx
                self.output_device_name = fallback_name
                self.monitor_enabled = True
                self.log(
                    f"Live Mic Active for Filmora & Recorders -> {fallback_name} + Default Mic ({self.windows_default_mic_name})"
                )
        except Exception:
            pass
        return False

    def toggle_stream(self) -> bool:
        if self.stream_enabled:
            self.stop_stream()
        else:
            self.start_stream()
        return self.stream_enabled

    def start_stream(self):
        self.stream_enabled = True
        self.log("🟢 Stream ACTIVE (ON)")
        if not self.connected:
            threading.Thread(
                target=self.scan_and_connect_offline,
                daemon=True,
            ).start()
        else:
            self.send_control_command({"cmd": "set_streaming", "value": True})

    def stop_stream(self):
        self.stream_enabled = False
        self.send_control_command({"cmd": "set_streaming", "value": False})
        with self.socket_lock:
            if self.active_socket is not None:
                try:
                    self.active_socket.close()
                except Exception:
                    pass
                self.active_socket = None
        self.connected = False
        self.current_rms = 0.0
        self.current_db = -60.0
        self.silent_bridge.check_idle_and_restore_speakers(self.audio_queue, force=True)
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except Exception:
                break
        self.log("⚪ Stream STOPPED (OFF)")

    def exit_phone_app(self):
        """Sends exit_app command to the connected mobile phone app so it closes completely."""
        self.send_control_command({"cmd": "set_streaming", "value": False})
        ok = self.send_control_command({"cmd": "exit_app"})
        if ok:
            self.log("📱 Sent Exit App command to connected phone")
        else:
            self.log("📱 Sent Stop command to phone (Tap '🚪 Exit' on phone screen to close)")

    def start_audio_output(self, sample_rate=DEFAULT_SAMPLE_RATE, device_idx=None):
        self.stop_audio_output()
        if device_idx is None:
            device_idx = self.selected_output_device

        def audio_callback(outdata, frames, time_info, status):
            # When using Stereo Mix without VB-Cable, ONLY output to the digital bus while
            # self.silent_bridge.allow_bus_output is True (i.e. physical PC speakers are 100% hardware-muted!).
            # This guarantees ZERO sound ever comes out of the PC speakers when speaking on the mobile phone,
            # while Filmora & all external recorders capture 100% of the voice from Default Mic (Stereo Mix)!
            if (
                not self.monitor_enabled
                or not self.stream_enabled
                or self.app_mode == "WEBCAM_ONLY"
                or (self.is_recording_mp4 and not self.virtual_mic_installed)
                or (not self.virtual_mic_installed and not self.silent_bridge.allow_bus_output)
            ):
                outdata.fill(0)
                return
            try:
                data = self.audio_queue.get_nowait()
                if len(data) < frames:
                    outdata[:len(data), 0] = data
                    outdata[len(data):, 0] = 0
                else:
                    outdata[:, 0] = data[:frames]
                rms_out = float(np.sqrt(np.mean(data * data)))
                if rms_out > 0.015:
                    self._last_speaker_play_time = time.time()
                    self._last_speaker_play_rms = rms_out
            except queue.Empty:
                outdata.fill(0)

        try:
            self.audio_stream = sd.OutputStream(
                samplerate=sample_rate,
                blocksize=960,
                device=device_idx,
                channels=1,
                dtype="float32",
                latency="low",
                callback=audio_callback,
            )
            self.audio_stream.start()
            self.audio_stream_active = True
        except Exception as e:
            self.audio_stream_active = False
            self.log(f"Could not open audio output stream: {e}")

    def stop_audio_output(self):
        self.silent_bridge.check_idle_and_restore_speakers(self.audio_queue, force=True)
        if self.audio_stream is not None:
            try:
                self.audio_stream.stop()
                self.audio_stream.close()
            except Exception:
                pass
            self.audio_stream = None
            self.audio_stream_active = False

    def send_control_command(self, cmd_dict: dict) -> bool:
        with self.socket_lock:
            sock = self.active_socket
            if sock is None:
                return False
            try:
                payload = json.dumps(cmd_dict).encode("utf-8")
                hdr = struct.pack(">4sBBHII", MAGIC, PACKET_CONTROL, 1, 480, 0, len(payload))
                sock.sendall(hdr + payload)
                return True
            except Exception as e:
                self.log(f"Control send failed: {e}")
                return False

    def _clear_video_state(self):
        self._pending_video_item = None
        with self.video_lock:
            self.latest_pil_image = None
            self.latest_jpeg_bytes = None
            self.video_fps = 0.0
            self.video_resolution = "0x0"
        if self.virtual_cam is not None:
            try:
                blank = np.zeros((480, 640, 3), dtype=np.uint8)
                blank[:, :] = (15, 23, 42)
                cv2.putText(blank, "Camera Turned OFF (Mic Only)", (115, 245), cv2.FONT_HERSHEY_SIMPLEX, 0.85, (148, 163, 184), 2)
                self.virtual_cam.send(blank)
            except Exception:
                pass

    def set_app_mode(self, mode: str, notify_phone: bool = True):
        if mode in ("MIC_ONLY", "WEBCAM_MIC", "WEBCAM_ONLY"):
            self.app_mode = mode
            if mode == "MIC_ONLY":
                if self.camera_source in ("PHONE", "PC_0", "PC_1"):
                    self.last_active_camera_source = self.camera_source
                self.camera_source = "OFF"
                self.facecam_shape = "OFF"
                self._clear_video_state()
                self.monitor_enabled = True
                self.windows_default_mic_name = configure_windows_default_mic_for_recorders(restore_physical_mic=False)
                if not self.audio_stream_active:
                    self.start_audio_output(self.sample_rate, self.selected_output_device)
                if notify_phone:
                    self.send_control_command({"cmd": "app_mode", "value": "MIC_ONLY"})
                    self.send_control_command({"cmd": "camera_source", "value": "OFF"})
                self.log(
                    f"🎙️ Mic Only Active: Camera OFF • Live Mic routed to Filmora & Recorders ({self.windows_default_mic_name})"
                )
            else:
                if self.camera_source == "OFF":
                    restore_cam = getattr(self, "last_active_camera_source", "PHONE")
                    if restore_cam == "PHONE" and not self.connected:
                        restore_cam = "PC_0"
                    self.camera_source = restore_cam
                if mode == "WEBCAM_MIC":
                    self.monitor_enabled = True
                    self.windows_default_mic_name = configure_windows_default_mic_for_recorders(restore_physical_mic=False)
                    if not self.audio_stream_active:
                        self.start_audio_output(self.sample_rate, self.selected_output_device)
                elif mode == "WEBCAM_ONLY":
                    configure_windows_default_mic_for_recorders(restore_physical_mic=True)
                if notify_phone:
                    self.send_control_command({"cmd": "app_mode", "value": mode})
                    self.send_control_command({"cmd": "camera_source", "value": self.camera_source})
                self.log(f"Switched Mode -> {mode} (Camera: {self.camera_source})")

    def set_camera_source(self, source_code: str, notify_phone: bool = True):
        if source_code not in ("PHONE", "PC_0", "PC_1", "OFF"):
            source_code = "PHONE"
        if source_code == "OFF":
            already_off = (self.camera_source == "OFF" and self.app_mode == "MIC_ONLY")
            if self.camera_source in ("PHONE", "PC_0", "PC_1"):
                self.last_active_camera_source = self.camera_source
            self.camera_source = "OFF"
            self.app_mode = "MIC_ONLY"
            self.facecam_shape = "OFF"
            self._clear_video_state()
            self.monitor_enabled = True
            self.windows_default_mic_name = configure_windows_default_mic_for_recorders(restore_physical_mic=False)
            if not self.audio_stream_active:
                self.start_audio_output(self.sample_rate, self.selected_output_device)
            if not already_off:
                if notify_phone:
                    self.send_control_command({"cmd": "app_mode", "value": "MIC_ONLY"})
                    self.send_control_command({"cmd": "camera_source", "value": "OFF"})
                self.log(f"🚫 Camera Turned OFF (Mic Only Active for Filmora: {self.windows_default_mic_name})")
            return

        self.last_active_camera_source = source_code
        if self.camera_source != source_code:
            self._clear_video_state()
        self.camera_source = source_code
        self.stream_enabled = True
        if self.app_mode == "MIC_ONLY":
            self.app_mode = "WEBCAM_MIC"
            self.monitor_enabled = True
            self.windows_default_mic_name = configure_windows_default_mic_for_recorders(restore_physical_mic=False)
            if notify_phone and source_code == "PHONE":
                self.send_control_command({"cmd": "app_mode", "value": "WEBCAM_MIC"})
        if notify_phone:
            self.send_control_command({"cmd": "camera_source", "value": source_code})
        cam_label = "📱 Mobile Phone Camera" if source_code == "PHONE" else f"💻 PC Camera ({source_code})"
        self.log(f"🎥 Switched Camera Source -> {cam_label}")

    def _pc_webcam_capture_loop(self):
        """
        Captures video from local PC Webcam when camera_source is 'PC_0', 'PC_1', etc.
        Immediately stops and releases hardware when Mic Only or Camera OFF is selected!
        """
        cap = None
        active_idx = -1
        while self.running:
            if not self.stream_enabled or self.app_mode == "MIC_ONLY" or not self.camera_source.startswith("PC_"):
                if cap is not None:
                    try:
                        cap.release()
                    except Exception:
                        pass
                    cap = None
                    active_idx = -1
                    self._clear_video_state()
                    self.log("💻 PC Webcam Stopped (Camera OFF)")
                time.sleep(0.10)
                continue

            try:
                idx = int(self.camera_source.split("_")[1])
            except Exception:
                idx = 0

            if cap is None or active_idx != idx or not cap.isOpened():
                if cap is not None:
                    try:
                        cap.release()
                    except Exception:
                        pass
                try:
                    cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
                    if not cap.isOpened():
                        cap = cv2.VideoCapture(idx)
                    if cap.isOpened():
                        try:
                            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                        except Exception:
                            pass
                        self.log(f"💻 PC Webcam {idx} Active & Streaming")
                    else:
                        if idx != 0:
                            self.camera_source = "PC_0"
                        time.sleep(0.4)
                        continue
                    active_idx = idx
                except Exception:
                    time.sleep(0.4)
                    continue

            ret, frame_bgr = cap.read()
            if not ret or frame_bgr is None:
                time.sleep(0.02)
                continue
            if not self.stream_enabled or self.app_mode == "MIC_ONLY" or not self.camera_source.startswith("PC_"):
                continue

            try:
                frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
                pil_img = Image.fromarray(frame_rgb)
                with self.video_lock:
                    self.latest_pil_image = pil_img
                    self.video_resolution = f"{pil_img.width}x{pil_img.height}"
                if self.is_recording_mp4 and not self.record_screen_mode:
                    with self.mp4_lock:
                        self.mp4_video_frames.append(pil_img)
                if self.virtual_cam is not None:
                    try:
                        h, w, _ = frame_rgb.shape
                        canvas_640_480 = np.zeros((480, 640, 3), dtype=np.uint8)
                        scale = min(640.0 / w, 480.0 / h)
                        nw, nh = int(w * scale), int(h * scale)
                        resized = cv2.resize(frame_rgb, (nw, nh), interpolation=cv2.INTER_LINEAR)
                        x0 = (640 - nw) // 2
                        y0 = (480 - nh) // 2
                        canvas_640_480[y0:y0 + nh, x0:x0 + nw] = resized
                        self.virtual_cam.send(canvas_640_480)
                    except Exception:
                        pass
                self.video_frames_received += 1
                self._fps_counter += 1
                now = time.time()
                elapsed = now - self._fps_timer
                if elapsed >= 1.0:
                    self.video_fps = round(self._fps_counter / elapsed, 1)
                    self._fps_counter = 0
                    self._fps_timer = now
            except Exception:
                pass
            time.sleep(0.005)

    def flip_phone_camera(self):
        self.send_control_command({"cmd": "flip_camera"})
        self.log("Sent Flip Camera (Front/Back) command to phone")

    def save_snapshot(self, custom_path: str = None) -> tuple[bool, str]:
        with self.video_lock:
            img = self.latest_pil_image.copy() if self.latest_pil_image is not None else None
        if img is None:
            return False, "No camera frame received yet"
        if not custom_path:
            fname = f"snap_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
            custom_path = os.path.join(self.recordings_dir, fname)
        try:
            img.save(custom_path, "JPEG", quality=94)
            self.last_saved_media_file = custom_path
            self.log(f"Saved camera snapshot ({img.width}x{img.height}) -> {custom_path}")
            return True, custom_path
        except Exception as e:
            return False, str(e)

    def _start_pc_sound_and_voiceover_streams(self):
        """Starts PC System Sound capture (Stereo Mix loopback) and fallback PC Mic if needed during Screen Record."""
        self._stop_pc_sound_and_voiceover_streams()

        # 1. PC System Sound Loopback (Stereo Mix)
        if self.record_pc_sound and self.pc_system_sound_dev_id is not None:
            try:
                dev_info = sd.query_devices(self.pc_system_sound_dev_id)
                ch = min(2, max(1, int(dev_info.get("max_input_channels", 1))))

                def pc_sound_cb(indata, frames, time_info, status):
                    if not self.is_recording_mp4 or not self.record_pc_sound:
                        return
                    mono = np.mean(indata, axis=1) if indata.ndim > 1 and indata.shape[1] > 1 else indata.reshape(-1)
                    pcm_bytes = (np.clip(mono, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
                    with self.mp4_lock:
                        self.mp4_pc_sound_frames.append(pcm_bytes)

                self._pc_sound_stream = sd.InputStream(
                    device=self.pc_system_sound_dev_id,
                    channels=ch,
                    samplerate=self.sample_rate,
                    dtype="float32",
                    callback=pc_sound_cb,
                )
                self._pc_sound_stream.start()
                self.log(f"🔊 Recording PC System Sound via '{self.pc_system_sound_dev_name}'")
            except Exception as e:
                self._pc_sound_stream = None
                self.log(f"PC System Sound capture note: {e}")

        # 2. Fallback PC Mic for Voice Over if Phone is not connected
        if self.record_voice_over and not self.connected:
            try:
                def pc_mic_cb(indata, frames, time_info, status):
                    if not self.is_recording_mp4 or not self.record_voice_over or self.connected:
                        return
                    mono = indata[:, 0] if indata.ndim > 1 else indata.reshape(-1)
                    pcm_bytes = (np.clip(mono * self.gain, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
                    with self.mp4_lock:
                        self.mp4_audio_frames.append(pcm_bytes)

                self._pc_mic_fallback_stream = sd.InputStream(
                    channels=1,
                    samplerate=self.sample_rate,
                    dtype="float32",
                    callback=pc_mic_cb,
                )
                self._pc_mic_fallback_stream.start()
                self.log("🎤 Voice Over active (using PC Mic fallback until Phone connects)")
            except Exception:
                self._pc_mic_fallback_stream = None

    def _stop_pc_sound_and_voiceover_streams(self):
        for attr in ("_pc_sound_stream", "_pc_mic_fallback_stream"):
            stm = getattr(self, attr, None)
            if stm is not None:
                try:
                    stm.stop()
                    stm.close()
                except Exception:
                    pass
                setattr(self, attr, None)

    def _screen_capture_worker(self):
        """
        Captures the live PC Screen (which includes the Round/Square Floating Camera if enabled,
        while the Floating Stop/Record Bar is 100% invisible due to WDA_EXCLUDEFROMCAPTURE!).
        """
        while self.running and self.is_recording_mp4 and self.record_screen_mode:
            t0 = time.time()
            try:
                scr = ImageGrab.grab()
                scr_hd = scr.resize((1280, 720), Image.Resampling.BILINEAR).convert("RGB")
                with self.mp4_lock:
                    if self.is_recording_mp4:
                        self.mp4_video_frames.append(scr_hd)
            except Exception:
                with self.video_lock:
                    cam_img = self.latest_pil_image
                if cam_img is not None:
                    with self.mp4_lock:
                        if self.is_recording_mp4:
                            self.mp4_video_frames.append(cam_img.copy())
            dt = time.time() - t0
            time.sleep(max(0.015, 0.080 - dt))  # ~12.5 FPS smooth screen capture

    def start_mp4_recording(self, custom_path: str = None, screen_mode: bool = True) -> str:
        with self.mp4_lock:
            self.mp4_video_frames = []
            self.mp4_audio_frames = []
            self.mp4_pc_sound_frames = []
            self.mp4_start_time = time.time()
            self.record_screen_mode = screen_mode
            if custom_path:
                os.makedirs(os.path.dirname(os.path.abspath(custom_path)), exist_ok=True)
                self.mp4_filepath = custom_path
            else:
                os.makedirs(self.recordings_dir, exist_ok=True)
                prefix = "ScreenRecord" if screen_mode else "CameraRecord"
                fname = f"WebcamMaster_{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
                self.mp4_filepath = os.path.join(self.recordings_dir, fname)
            self.is_recording_mp4 = True

        self._start_pc_sound_and_voiceover_streams()

        if screen_mode:
            self._screen_capture_thread = threading.Thread(target=self._screen_capture_worker, daemon=True)
            self._screen_capture_thread.start()

        audio_desc = []
        if self.record_pc_sound:
            audio_desc.append("PC System Sound")
        if self.record_voice_over:
            audio_desc.append("Voice Over Mic")
        aud_str = " + ".join(audio_desc) if audio_desc else "Muted Audio"
        self.log(f"🔴 Screen Record Started ({aud_str}) -> {self.mp4_filepath}")
        return self.mp4_filepath

    def stop_mp4_recording(self) -> str:
        self._stop_pc_sound_and_voiceover_streams()
        with self.mp4_lock:
            if not self.is_recording_mp4:
                return self.mp4_filepath
            self.is_recording_mp4 = False
            v_frames = list(self.mp4_video_frames)
            voice_bytes = b"".join(self.mp4_audio_frames) if self.record_voice_over else b""
            pc_bytes = b"".join(self.mp4_pc_sound_frames) if self.record_pc_sound else b""
            elapsed = max(0.5, time.time() - self.mp4_start_time)
            out_mp4 = self.mp4_filepath

        if not v_frames:
            with self.video_lock:
                if self.latest_pil_image is not None:
                    v_frames = [self.latest_pil_image.copy() for _ in range(10)]

        if not v_frames:
            self.log("No video frames captured during recording.")
            return out_mp4

        # Mix Voice Over (Phone/PC Mic) + PC System Sound (Stereo Mix) into a single clean audio track
        voice_arr = np.frombuffer(voice_bytes, dtype="<i2").astype(np.float32) / 32768.0 if len(voice_bytes) >= 2 else np.zeros(0, dtype=np.float32)
        pc_arr = np.frombuffer(pc_bytes, dtype="<i2").astype(np.float32) / 32768.0 if len(pc_bytes) >= 2 else np.zeros(0, dtype=np.float32)

        max_len = max(len(voice_arr), len(pc_arr))
        if max_len > 0:
            mixed = np.zeros(max_len, dtype=np.float32)
            if len(voice_arr) > 0:
                mixed[:len(voice_arr)] += voice_arr
            if len(pc_arr) > 0:
                mixed[:len(pc_arr)] += pc_arr
            mixed = np.clip(mixed, -1.0, 1.0)
            a_frames = (mixed * 32767.0).astype("<i2").tobytes()
        else:
            a_frames = b""

        temp_avi = out_mp4 + ".temp.avi"
        temp_wav = out_mp4 + ".temp.wav"

        try:
            if len(a_frames) > 0:
                with wave.open(temp_wav, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(self.sample_rate)
                    wf.writeframes(a_frames)

            first_img = v_frames[0]
            w, h = first_img.width, first_img.height
            w = (w // 2) * 2
            h = (h // 2) * 2
            actual_fps = max(6.0, min(30.0, len(v_frames) / elapsed))
            fourcc = cv2.VideoWriter_fourcc(*"MJPG")
            vw = cv2.VideoWriter(temp_avi, fourcc, actual_fps, (w, h))
            for frame_pil in v_frames:
                if frame_pil.width != w or frame_pil.height != h:
                    frame_pil = frame_pil.resize((w, h), Image.Resampling.BILINEAR)
                bgr = cv2.cvtColor(np.array(frame_pil), cv2.COLOR_RGB2BGR)
                vw.write(bgr)
            vw.release()

            ffmpeg_bin = find_ffmpeg()
            startupinfo = _no_window_startupinfo()

            if os.path.exists(temp_wav) and len(a_frames) > 1000:
                cmd = [
                    ffmpeg_bin, "-y",
                    "-i", temp_avi,
                    "-i", temp_wav,
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "128k",
                    "-shortest",
                    out_mp4,
                ]
            else:
                cmd = [
                    ffmpeg_bin, "-y",
                    "-i", temp_avi,
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    out_mp4,
                ]
            res = subprocess.run(cmd, capture_output=True, timeout=45, startupinfo=startupinfo)
            if res.returncode != 0 and os.path.exists(temp_avi):
                os.replace(temp_avi, out_mp4)

            for tmp in (temp_avi, temp_wav):
                if os.path.exists(tmp):
                    try:
                        os.remove(tmp)
                    except Exception:
                        pass

            if os.path.exists(out_mp4):
                self.last_saved_media_file = out_mp4
            self.log(f"✅ Media File Saved ({len(v_frames)} frames) -> {out_mp4}")
        except Exception as e:
            self.log(f"MP4 export error: {e}")
        return out_mp4

    def process_video_payload(self, payload: bytes):
        if not payload or not self.stream_enabled or self.app_mode == "MIC_ONLY" or self.camera_source != "PHONE":
            return
        self._pending_video_item = payload
        self._pending_video_event.set()

    def _video_decode_loop(self):
        while self.running:
            self._pending_video_event.wait(timeout=0.5)
            self._pending_video_event.clear()
            payload = self._pending_video_item
            if payload is None or not self.stream_enabled or self.app_mode == "MIC_ONLY" or self.camera_source != "PHONE":
                continue
            self._pending_video_item = None
            try:
                pil_img = Image.open(io.BytesIO(payload)).convert("RGB")
                if not self.stream_enabled or self.app_mode == "MIC_ONLY" or self.camera_source != "PHONE":
                    continue
                with self.video_lock:
                    self.latest_jpeg_bytes = payload
                    self.latest_pil_image = pil_img
                    self.video_resolution = f"{pil_img.width}x{pil_img.height}"
                if self.is_recording_mp4 and not self.record_screen_mode:
                    with self.mp4_lock:
                        self.mp4_video_frames.append(pil_img)

                if self.virtual_cam is not None:
                    try:
                        arr = np.array(pil_img)
                        h, w, _ = arr.shape
                        canvas_640_480 = np.zeros((480, 640, 3), dtype=np.uint8)
                        scale = min(640.0 / w, 480.0 / h)
                        nw, nh = int(w * scale), int(h * scale)
                        resized = cv2.resize(arr, (nw, nh), interpolation=cv2.INTER_LINEAR)
                        x0 = (640 - nw) // 2
                        y0 = (480 - nh) // 2
                        canvas_640_480[y0:y0 + nh, x0:x0 + nw] = resized
                        self.virtual_cam.send(canvas_640_480)
                    except Exception:
                        pass

                self.video_frames_received += 1
                self._fps_counter += 1
                now = time.time()
                elapsed = now - self._fps_timer
                if elapsed >= 1.0:
                    self.video_fps = round(self._fps_counter / elapsed, 1)
                    self._fps_counter = 0
                    self._fps_timer = now
            except Exception:
                pass

    def process_pcm_payload(self, payload: bytes, sample_rate: int, seq: int = -1):
        if not payload or len(payload) < 2 or not self.stream_enabled:
            return
        if seq != -1:
            if seq == self._last_audio_seq:
                return
            self._last_audio_seq = seq

        if sample_rate != self.sample_rate and sample_rate in (16000, 22050, 44100, 48000):
            self.sample_rate = sample_rate
            self.start_audio_output(sample_rate, self.selected_output_device)

        samples_i16 = np.frombuffer(payload, dtype="<i2")
        samples_f32 = samples_i16.astype(np.float32) / 32768.0

        if self.gain != 1.0:
            samples_f32 = np.clip(samples_f32 * self.gain, -1.0, 1.0)

        rms = float(np.sqrt(np.mean(samples_f32 * samples_f32))) if len(samples_f32) > 0 else 0.0
        db = 20.0 * math.log10(max(rms, 1e-6))
        self.current_rms = rms
        self.current_db = max(-60.0, min(0.0, db))
        if self.current_db > self.peak_db:
            self.peak_db = self.current_db

        # Allow Voice Over recording even if user is in WEBCAM_ONLY mode as long as record_voice_over is ON
        mute_for_mode = (self.app_mode == "WEBCAM_ONLY") and not (self.is_recording_mp4 and self.record_voice_over)
        if self.current_db < self.noise_gate_db or mute_for_mode:
            gated_f32 = np.zeros_like(samples_f32)
        else:
            gated_f32 = samples_f32

        with self.vis_lock:
            n = len(gated_f32)
            if n >= len(self.vis_buffer):
                self.vis_buffer[:] = gated_f32[-len(self.vis_buffer):]
            else:
                self.vis_buffer = np.roll(self.vis_buffer, -n)
                self.vis_buffer[-n:] = gated_f32

        if self.monitor_enabled and self.audio_stream_active and self.app_mode != "WEBCAM_ONLY":
            if self.virtual_mic_installed:
                can_queue = True
            else:
                if self.current_db >= self.noise_gate_db and not mute_for_mode:
                    self.silent_bridge.on_voice_activity()
                    can_queue = True
                else:
                    self.silent_bridge.check_idle_and_restore_speakers(self.audio_queue, force=False)
                    can_queue = self.silent_bridge.allow_bus_output

            if can_queue:
                bus_f32 = np.tanh(gated_f32 * 1.8).astype(np.float32) if not self.virtual_mic_installed else gated_f32
                block_size = 960
                for i in range(0, len(bus_f32), block_size):
                    blk = bus_f32[i:i + block_size]
                    while self.audio_queue.full():
                        try:
                            self.audio_queue.get_nowait()
                        except queue.Empty:
                            break
                    try:
                        self.audio_queue.put_nowait(blk)
                    except Exception:
                        pass

        pcm_out = (gated_f32 * 32767.0).astype("<i2").tobytes()
        if self.is_recording:
            with self.record_lock:
                self.record_frames.append(pcm_out)
        if self.is_recording_mp4 and self.record_voice_over:
            with self.mp4_lock:
                self.mp4_audio_frames.append(pcm_out)

    def start_recording(self, custom_path: str = None) -> str:
        with self.record_lock:
            self.record_frames = []
            if custom_path:
                os.makedirs(os.path.dirname(os.path.abspath(custom_path)), exist_ok=True)
                self.record_filepath = custom_path
            else:
                os.makedirs(self.recordings_dir, exist_ok=True)
                fname = f"WebcamMaster_Audio_{datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"
                self.record_filepath = os.path.join(self.recordings_dir, fname)
            self.is_recording = True
        self.log(f"Started WAV recording -> {self.record_filepath}")
        return self.record_filepath

    def stop_recording(self) -> str:
        with self.record_lock:
            if not self.is_recording:
                return self.record_filepath
            self.is_recording = False
            raw_bytes = b"".join(self.record_frames)
            filepath = self.record_filepath

        try:
            with wave.open(filepath, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(self.sample_rate)
                wf.writeframes(raw_bytes)
            self.last_saved_media_file = filepath
            self.log(f"Saved WAV Media File ({len(raw_bytes)} bytes) -> {filepath}")
        except Exception as e:
            self.log(f"Failed saving WAV: {e}")
        return filepath

    def start_servers(self):
        self.start_audio_output(self.sample_rate, self.selected_output_device)
        threading.Thread(target=self._video_decode_loop, daemon=True).start()
        threading.Thread(target=self._pc_webcam_capture_loop, daemon=True).start()
        threading.Thread(target=self._tcp_server_loop, daemon=True).start()
        threading.Thread(target=self._udp_audio_and_discovery_loop, daemon=True).start()
        threading.Thread(target=self._usb_and_wireless_watchdog_loop, daemon=True).start()
        threading.Thread(target=self._telemetry_http_loop, daemon=True).start()

    def connect_to_phone_wireless(self, phone_ip: str = None) -> tuple[bool, str]:
        if not self.stream_enabled:
            return False, "Stream is currently stopped"
        if self.connected and (time.time() - self.last_packet_time < 3.0):
            return True, f"Already connected to {self.client_addr}"

        if not hasattr(self, "_connect_attempt_lock"):
            self._connect_attempt_lock = threading.Lock()
            self._last_connect_attempt_ts = 0.0

        if not self._connect_attempt_lock.acquire(blocking=False):
            return False, "Connection attempt already in progress"

        try:
            now = time.time()
            if now - getattr(self, "_last_connect_attempt_ts", 0.0) < 1.2:
                return False, "Cooldown between connection attempts"
            self._last_connect_attempt_ts = now

            target_ip = (phone_ip or self.phone_wifi_ip).strip()
            if not target_ip:
                return False, "Phone Wireless IP is empty"
            if target_ip != "127.0.0.1" and (target_ip == self.local_ip or target_ip in getattr(self, "local_ips", set())):
                return False, f"Skipping local PC IP ({target_ip})"
            if target_ip != "127.0.0.1":
                self.phone_wifi_ip = target_ip

            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.settimeout(1.8)
            sock.connect((target_ip, PHONE_WIRELESS_PORT))

            if target_ip == "127.0.0.1":
                label = "🔌 USB Cable Bridge"
            elif target_ip.startswith("192.168.43.") or target_ip.startswith("192.168.215."):
                label = "📡 Phone Hotspot (Offline - No Net)"
            elif target_ip.startswith("192.168.137."):
                label = "📡 PC Hotspot (Offline - No Net)"
            else:
                label = "📶 Offline Wireless (No Net Needed)"

            with self.socket_lock:
                if self.active_socket is not None:
                    try:
                        self.active_socket.close()
                    except Exception:
                        pass
                self.active_socket = sock

            threading.Thread(
                target=self._handle_stream_socket,
                args=(sock, (target_ip, PHONE_WIRELESS_PORT), label),
                daemon=True,
            ).start()
            return True, f"Connecting to {target_ip}:{PHONE_WIRELESS_PORT} ({label})"
        except Exception as e:
            return False, f"Connection to {target_ip}:{PHONE_WIRELESS_PORT} failed: {e}"
        finally:
            self._connect_attempt_lock.release()

    def scan_and_connect_offline(self) -> tuple[bool, str]:
        if self.connected and (time.time() - self.last_packet_time < 3.0):
            return True, f"Connected to {self.device_name}"

        net = get_offline_network_info()
        self.local_ip = net["primary_ip"]
        self.local_ips = set(net.get("local_ips", [self.local_ip]))
        self.wireless_mode_hint = net["wireless_mode_hint"]

        candidates = []
        if self.phone_wifi_ip and self.phone_wifi_ip not in self.local_ips and self.phone_wifi_ip not in candidates:
            candidates.append(self.phone_wifi_ip)
        for ip, info in list(self.discovered_devices.items()):
            if ip in self.local_ips:
                self.discovered_devices.pop(ip, None)
                continue
            if ip not in candidates and info.get("status") == "LIVE":
                candidates.append(ip)
        for gw in net["gateways"]:
            if (gw.startswith("192.168.43.") or gw.startswith("192.168.215.")) and gw not in self.local_ips and gw not in candidates:
                candidates.append(gw)

        # Also check ARP table dynamic peers on the local Wi-Fi / Hotspot subnet
        si = _no_window_startupinfo()
        try:
            arp_proc = subprocess.run(
                ["arp", "-a"],
                capture_output=True, text=True, timeout=1.5, startupinfo=si,
            )
            for m in re.finditer(r"(\d+\.\d+\.\d+\.\d+)\s+[0-9a-fA-F-]{17}\s+dynamic", arp_proc.stdout):
                arp_ip = m.group(1)
                if (
                    arp_ip not in self.local_ips
                    and not arp_ip.endswith(".254")
                    and not arp_ip.endswith(".255")
                    and (not arp_ip.endswith(".1") or arp_ip.startswith("192.168.43.") or arp_ip.startswith("192.168.215."))
                    and arp_ip not in candidates
                ):
                    candidates.append(arp_ip)
        except Exception:
            pass

        # Directly attempt connection without opening/closing a separate probe socket
        for cand_ip in candidates[:4]:
            dev_entry = self.discovered_devices.get(cand_ip, {})
            if dev_entry.get("status") == "STANDBY":
                continue
            ok, msg = self.connect_to_phone_wireless(cand_ip)
            if ok:
                return True, msg

        # Only check Phone Hotspot gateways (192.168.43.1 / 192.168.215.1) via ping for offline hotspot standby detection
        hotspot_gws = [
            gw for gw in net["gateways"]
            if (gw.startswith("192.168.43.") or gw.startswith("192.168.215.")) and gw not in self.local_ips
        ]
        for p_ip in hotspot_gws:
            try:
                p = subprocess.run(
                    ["ping", "-n", "1", "-w", "250", p_ip],
                    capture_output=True, text=True, timeout=1.5, startupinfo=si,
                )
                if "ttl=" in p.stdout.lower():
                    self.standby_phone_detected = True
                    self.standby_phone_label = f"Phone Hotspot ({p_ip})"
                    self.phone_wifi_ip = p_ip
                    self._register_discovered_device(p_ip, "Motorola Edge 50 Neo", "STANDBY", self.wireless_mode_hint)
                    return False, f"Phone Hotspot detected at {p_ip} — Open 'Webcam Master' on phone!"
            except Exception:
                pass

        return False, "Searching for phone on Offline Wireless (Wi-Fi / Hotspot / USB)..."

    def _recv_exact(self, conn: socket.socket, length: int) -> bytes:
        buf = bytearray()
        while len(buf) < length:
            chunk = conn.recv(length - len(buf))
            if not chunk:
                return b""
            buf.extend(chunk)
        return bytes(buf)

    def _tcp_server_loop(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server.bind(("0.0.0.0", AUDIO_PORT))
            server.listen(5)
            self.log(f"Offline Wireless Server listening on {self.local_ip}:{AUDIO_PORT} (No Internet Required)")
        except Exception as e:
            self.log(f"TCP bind error: {e}")
            return

        while self.running:
            try:
                conn, addr = server.accept()
                if not self.stream_enabled:
                    try:
                        conn.close()
                    except Exception:
                        pass
                    continue
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                if self.connected and (time.time() - self.last_packet_time < 3.0):
                    try:
                        conn.close()
                    except Exception:
                        pass
                    continue
                is_usb = addr[0] == "127.0.0.1"
                label = "🔌 USB Cable Bridge" if is_usb else "📶 Offline Wireless (No Net Needed)"
                with self.socket_lock:
                    if self.active_socket is not None:
                        try:
                            self.active_socket.close()
                        except Exception:
                            pass
                    self.active_socket = conn
                threading.Thread(target=self._handle_stream_socket, args=(conn, addr, label), daemon=True).start()
            except Exception:
                time.sleep(0.5)

    def _handle_stream_socket(self, conn: socket.socket, addr, mode_label: str):
        addr_str = f"{addr[0]}:{addr[1]}"
        logged_connect = False

        try:
            conn.settimeout(2.5)
            pc_info_payload = json.dumps({
                "cmd": "pc_info",
                "pcName": self.pc_hostname,
                "pcIp": self.local_ip,
            }).encode("utf-8")
            pc_hdr = struct.pack(">4sBBHII", MAGIC, PACKET_CONTROL, 1, 480, 0, len(pc_info_payload))
            conn.sendall(pc_hdr + pc_info_payload)
            if self.stream_enabled:
                wake_payload = json.dumps({"cmd": "set_streaming", "value": True}).encode("utf-8")
                wake_hdr = struct.pack(">4sBBHII", MAGIC, PACKET_CONTROL, 1, 480, 0, len(wake_payload))
                conn.sendall(wake_hdr + wake_payload)
        except Exception:
            pass

        try:
            while self.running and self.stream_enabled and self.active_socket is conn:
                header = self._recv_exact(conn, 16)
                if not header or not self.stream_enabled or self.active_socket is not conn:
                    break
                if header[0:4] != MAGIC:
                    continue

                if not logged_connect:
                    logged_connect = True
                    conn.settimeout(None)
                    self.connected = True
                    self.standby_phone_detected = False
                    self.client_addr = addr_str
                    self.connection_type = mode_label
                    self.device_name = clean_device_name(self.device_name)
                    self.peak_db = -60.0
                    self._register_discovered_device(addr[0], self.device_name, "LIVE", mode_label)
                    self.log(f"Connected Device: {self.device_name} via {self.connection_type} ({addr_str})")

                pkt_type = header[4]
                sr_div100 = struct.unpack(">H", header[6:8])[0]
                seq = struct.unpack(">I", header[8:12])[0]
                payload_len = struct.unpack(">I", header[12:16])[0]

                if payload_len > 1048576:
                    break
                payload = self._recv_exact(conn, payload_len) if payload_len > 0 else b""
                if (payload_len > 0 and not payload) or not self.stream_enabled or self.active_socket is not conn:
                    break

                self.packets_received += 1
                self.bytes_received += 16 + len(payload)
                self.last_packet_time = time.time()
                self.connected = True

                if pkt_type == PACKET_HANDSHAKE:
                    try:
                        info = json.loads(payload.decode("utf-8", errors="ignore"))
                        raw_dev = info.get("device", self.device_name)
                        self.device_name = clean_device_name(raw_dev)
                        self.device_android = info.get("android", "")
                        sr = int(info.get("sampleRate", self.sample_rate))
                        self.sample_rate = sr
                        wip = info.get("wifiIp", "")
                        net_mode = info.get("netMode", "")
                        if net_mode and addr[0] != "127.0.0.1":
                            self.connection_type = net_mode
                        if wip and wip != "127.0.0.1" and wip not in getattr(self, "local_ips", set()):
                            self.phone_wifi_ip = wip
                            self._register_discovered_device(wip, self.device_name, "LIVE", self.connection_type)
                        self.log(f"Handshake verified: {self.device_name} ({self.phone_wifi_ip})")
                    except Exception:
                        pass
                elif pkt_type == PACKET_AUDIO:
                    sr = sr_div100 * 100 if sr_div100 > 0 else self.sample_rate
                    self.process_pcm_payload(payload, sr, seq)
                elif pkt_type == PACKET_VIDEO:
                    self.process_video_payload(payload)
                elif pkt_type == PACKET_CONTROL:
                    try:
                        cinfo = json.loads(payload.decode("utf-8", errors="ignore"))
                        act = cinfo.get("cmd", "")
                        if act == "camera_source":
                            self.set_camera_source(cinfo.get("value", "PHONE"), notify_phone=False)
                        elif act == "app_mode":
                            m = cinfo.get("value", "WEBCAM_MIC")
                            if m in ("MIC_ONLY", "WEBCAM_MIC", "WEBCAM_ONLY"):
                                self.set_app_mode(m, notify_phone=False)
                    except Exception:
                        pass
                elif pkt_type == PACKET_PING:
                    conn.sendall(b"MMIC\x03\x01\x01\xe0\x00\x00\x00\x00\x00\x00\x00\x04PONG")
        except Exception:
            pass
        finally:
            self.silent_bridge.check_idle_and_restore_speakers(self.audio_queue, force=True)
            if not logged_connect and addr[0] != "127.0.0.1" and addr[0] not in getattr(self, "local_ips", set()):
                self.standby_phone_detected = True
                self.standby_phone_label = f"{clean_device_name(self.device_name)} ({addr[0]})"
                self._register_discovered_device(addr[0], clean_device_name(self.device_name), "STANDBY", mode_label)
            try:
                conn.close()
            except Exception:
                pass
            with self.socket_lock:
                if self.active_socket is conn:
                    self.active_socket = None
                    self.connected = False
                    self.current_rms = 0.0
                    self.current_db = -60.0
                    self.dominant_freq_hz = 0.0

    def _udp_audio_and_discovery_loop(self):
        udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_sock.setsockopt(socket.SOL_SOCKET, socket.SOL_REUSEADDR, 1)
        try:
            udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        except Exception:
            pass
        try:
            udp_sock.bind(("0.0.0.0", DISCOVERY_PORT))
            udp_sock.settimeout(1.5)
        except Exception:
            return

        last_scan_bcast = 0.0
        while self.running:
            now = time.time()
            if now - last_scan_bcast >= 2.5:
                last_scan_bcast = now
                net = get_offline_network_info()
                self.local_ip = net["primary_ip"]
                self.local_ips = set(net.get("local_ips", [self.local_ip]))
                scan_pkt = ("MMIC_PC_SCAN:" + json.dumps({
                    "type": "MMIC_PC",
                    "ip": self.local_ip,
                    "port": AUDIO_PORT,
                    "hostname": self.pc_hostname,
                })).encode("utf-8")
                for bcast_ip in net["broadcasts"]:
                    try:
                        udp_sock.sendto(scan_pkt, (bcast_ip, DISCOVERY_PORT))
                    except Exception:
                        pass

            try:
                data, addr = udp_sock.recvfrom(65535)
                msg = data.decode("utf-8", errors="ignore")
                if msg.startswith("MMIC_DISCOVER"):
                    sender_ip = addr[0]
                    dev_name = "Motorola Edge 50 Neo"
                    net_mode = "Offline Wireless"
                    phone_is_streaming = True
                    if "{" in msg:
                        try:
                            j = json.loads(msg[msg.index("{"):])
                            dev_name = clean_device_name(j.get("device", dev_name))
                            net_mode = j.get("netMode", net_mode)
                            phone_is_streaming = bool(j.get("streaming", True))
                            rep_ip = j.get("ip", sender_ip)
                            if rep_ip and rep_ip != "127.0.0.1":
                                sender_ip = rep_ip
                        except Exception:
                            pass

                    if sender_ip == self.local_ip or sender_ip in getattr(self, "local_ips", set()):
                        continue

                    status_tag = "LIVE" if phone_is_streaming else "STANDBY"
                    self._register_discovered_device(sender_ip, dev_name, status_tag, net_mode)
                    if sender_ip and sender_ip != "127.0.0.1":
                        self.phone_wifi_ip = sender_ip

                    if not phone_is_streaming:
                        self.standby_phone_detected = True
                        self.standby_phone_label = f"{dev_name} ({sender_ip})"

                    reply = json.dumps({
                        "type": "MMIC_PC",
                        "ip": self.local_ip,
                        "port": AUDIO_PORT,
                        "hostname": self.pc_hostname,
                    }).encode("utf-8")
                    udp_sock.sendto(reply, addr)

                    if self.stream_enabled and phone_is_streaming and not self.connected:
                        threading.Thread(
                            target=lambda ip=sender_ip: self.connect_to_phone_wireless(ip),
                            daemon=True,
                        ).start()
            except Exception:
                pass

    def _usb_and_wireless_watchdog_loop(self):
        adb_bin = find_adb()
        startupinfo = _no_window_startupinfo()
        time.sleep(1.5)

        while self.running:
            if not self.connected or (self.last_packet_time > 0 and (time.time() - self.last_packet_time > 0.8)):
                self.silent_bridge.check_idle_and_restore_speakers(self.audio_queue, force=True)
            if not self.virtual_mic_installed:
                if self._auto_detect_virtual_mic_device():
                    self.start_audio_output(self.sample_rate, self.selected_output_device)
            if self.monitor_enabled and not self.audio_stream_active:
                self.start_audio_output(self.sample_rate, self.selected_output_device)

            try:
                proc = subprocess.run(
                    [adb_bin, "devices", "-l"],
                    capture_output=True, text=True, timeout=3, startupinfo=startupinfo,
                )
                lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip() and not ln.startswith("List of") and not ln.startswith("*")]
                found_usb_device = False
                for ln in lines:
                    parts = ln.split()
                    if len(parts) >= 2 and parts[1] == "device":
                        serial = parts[0]
                        self.usb_serial = serial
                        found_usb_device = True
                        if not self.usb_reverse_active:
                            r1 = subprocess.run(
                                [adb_bin, "-s", serial, "forward", f"tcp:{PHONE_WIRELESS_PORT}", f"tcp:{PHONE_WIRELESS_PORT}"],
                                capture_output=True, text=True, timeout=3, startupinfo=startupinfo,
                            )
                            r2 = subprocess.run(
                                [adb_bin, "-s", serial, "reverse", f"tcp:{AUDIO_PORT}", f"tcp:{AUDIO_PORT}"],
                                capture_output=True, text=True, timeout=3, startupinfo=startupinfo,
                            )
                            if r1.returncode == 0 or r2.returncode == 0:
                                self.usb_reverse_active = True
                        break
                if not found_usb_device:
                    self.usb_reverse_active = False
            except Exception:
                self.usb_reverse_active = False

            need_conn = (not self.connected) or (self.last_packet_time > 0 and (time.time() - self.last_packet_time > 5.0))
            if self.stream_enabled and self.auto_wireless_enabled and need_conn:
                if self.usb_reverse_active:
                    ok_usb, _ = self.connect_to_phone_wireless("127.0.0.1")
                    if ok_usb:
                        self.usb_status = "🔌 USB Cable Bridge Connected"
                        time.sleep(3.0)
                        continue
                ok, status_msg = self.scan_and_connect_offline()
                if ok:
                    self.usb_status = f"📶 Connected: {self.device_name} ({self.phone_wifi_ip})"
                else:
                    self.usb_status = status_msg

            time.sleep(3.0)

    def get_status_dict(self):
        return {
            "running": self.running,
            "stream_enabled": self.stream_enabled,
            "connected": self.connected,
            "client_addr": self.client_addr,
            "device_name": clean_device_name(self.device_name) if self.device_name != "None" else "None",
            "standby_phone_detected": self.standby_phone_detected,
            "standby_phone_label": self.standby_phone_label,
            "discovered_devices": self.discovered_devices,
            "connection_type": self.connection_type,
            "wireless_mode_hint": self.wireless_mode_hint,
            "internet_required": False,
            "app_mode": self.app_mode,
            "camera_source": self.camera_source,
            "available_pc_cameras": self.available_pc_cameras,
            "record_pc_sound": self.record_pc_sound,
            "record_voice_over": self.record_voice_over,
            "pc_system_sound_device": self.pc_system_sound_dev_name,
            "recordings_dir": self.recordings_dir,
            "ask_save_every_time": self.ask_save_every_time,
            "local_ip": self.local_ip,
            "pc_hostname": self.pc_hostname,
            "phone_wifi_ip": self.phone_wifi_ip,
            "sample_rate": self.sample_rate,
            "packets_received": self.packets_received,
            "bytes_received": self.bytes_received,
            "current_db": round(self.current_db, 2),
            "peak_db": round(self.peak_db, 2),
            "video_frames_received": self.video_frames_received,
            "video_fps": self.video_fps,
            "video_resolution": self.video_resolution,
            "virtual_cam_active": self.virtual_cam_active,
            "virtual_cam_name": self.virtual_cam_name,
            "facecam_visible": self.facecam_visible,
            "facecam_shape": self.facecam_shape,
            "facecam_size": self.facecam_size,
            "facecam_corner": self.facecam_corner,
            "rec_hud_visible": self.rec_hud_visible,
            "rec_hud_excluded_from_video": self.rec_hud_excluded_from_video,
            "last_saved_media_file": self.last_saved_media_file,
            "virtual_mic_installed": self.virtual_mic_installed,
            "output_device_name": self.output_device_name,
            "windows_default_mic": getattr(self, "windows_default_mic_name", "Stereo Mix (Realtek(R) Audio)"),
            "audio_stream_active": self.audio_stream_active,
            "monitor_enabled": self.monitor_enabled,
            "is_recording_wav": self.is_recording,
            "is_recording_mp4": self.is_recording_mp4,
            "recent_logs": self.logs[-12:],
        }

    def _telemetry_http_loop(self):
        engine = self

        class Handler(BaseHTTPRequestHandler):
            def _send_json(self, data: dict, status=200):
                body = json.dumps(data, indent=2).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                qs = urllib.parse.parse_qs(parsed.query)
                if parsed.path == "/status":
                    self._send_json(engine.get_status_dict())
                elif parsed.path == "/WebcamMaster.apk":
                    apk_fp = r"f:\pc software\WebcamMaster.apk"
                    if os.path.isfile(apk_fp):
                        with open(apk_fp, "rb") as f:
                            apk_bytes = f.read()
                        self.send_response(200)
                        self.send_header("Content-Type", "application/vnd.android.package-archive")
                        self.send_header("Content-Disposition", 'attachment; filename="WebcamMaster.apk"')
                        self.send_header("Content-Length", str(len(apk_bytes)))
                        self.end_headers()
                        self.wfile.write(apk_bytes)
                    else:
                        self._send_json({"error": "APK not found"}, 404)
                elif parsed.path == "/toggle_stream":
                    state = engine.toggle_stream()
                    self._send_json({"ok": True, "stream_enabled": state, "status": engine.get_status_dict()})
                elif parsed.path == "/connect_wireless":
                    ip = qs.get("ip", [engine.phone_wifi_ip])[0]
                    ok, msg = engine.connect_to_phone_wireless(ip)
                    self._send_json({"ok": ok, "message": msg, "status": engine.get_status_dict()})
                elif parsed.path == "/scan_offline":
                    ok, msg = engine.scan_and_connect_offline()
                    self._send_json({"ok": ok, "message": msg, "status": engine.get_status_dict()})
                elif parsed.path == "/facecam":
                    action = qs.get("action", ["show"])[0]
                    shape = qs.get("shape", [None])[0]
                    corner = qs.get("corner", [None])[0]
                    size = qs.get("size", [None])[0]
                    if engine.facecam_ui_callback:
                        engine.facecam_ui_callback(action=action, shape=shape, corner=corner, size=size)
                    self._send_json({"ok": True, "status": engine.get_status_dict()})
                elif parsed.path == "/set_mode":
                    m = qs.get("mode", ["WEBCAM_MIC"])[0]
                    engine.set_app_mode(m)
                    self._send_json({"ok": True, "app_mode": engine.app_mode, "status": engine.get_status_dict()})
                elif parsed.path == "/set_camera_source":
                    src = qs.get("source", ["PHONE"])[0]
                    engine.set_camera_source(src)
                    self._send_json({"ok": True, "camera_source": engine.camera_source, "status": engine.get_status_dict()})
                elif parsed.path == "/snapshot":
                    p = qs.get("path", [None])[0]
                    ok, saved = engine.save_snapshot(p)
                    self._send_json({"ok": ok, "path": saved, "status": engine.get_status_dict()})
                elif parsed.path == "/record_mp4_start":
                    p = qs.get("path", [None])[0]
                    fp = engine.start_mp4_recording(p)
                    self._send_json({"ok": True, "recording_mp4": fp})
                elif parsed.path == "/record_mp4_stop":
                    fp = engine.stop_mp4_recording()
                    self._send_json({"ok": True, "saved_mp4": fp, "status": engine.get_status_dict()})
                elif parsed.path == "/ui_screenshot":
                    p = qs.get("path", [os.path.join(engine.recordings_dir, "ui_preview.png")])[0]
                    if engine.ui_screenshot_callback:
                        ok, saved = engine.ui_screenshot_callback(p)
                        self._send_json({"ok": ok, "path": saved})
                    else:
                        self._send_json({"ok": False, "error": "UI not initialized"}, 500)
                else:
                    self._send_json({"error": "Not found"}, 404)

            def log_message(self, format, *args):
                return

        try:
            httpd = HTTPServer(("0.0.0.0", TELEMETRY_PORT), Handler)
            httpd.serve_forever()
        except Exception:
            pass


class MotoMicApp:
    def __init__(self, root: tk.Tk, engine: MotoMicEngine):
        self.root = root
        self.engine = engine
        self.engine.facecam_ui_callback = self._handle_facecam_remote_cmd
        self.engine.ui_screenshot_callback = self._capture_ui_window_screenshot
        self.root.title("Webcam Master — Wireless Phone Mic, Camera & Screen Recorder")
        self.root.geometry("1160x770")
        self.root.minsize(1020, 700)
        self.root.configure(bg="#090D16")

        self._app_icon_photo = None
        self._header_icon_photo = None
        bundle_dir = getattr(sys, "_MEIPASS", self.engine.base_dir)
        ico_candidates = [
            r"f:\pc software\assets\webcam_master.ico",
            os.path.join(bundle_dir, "assets", "webcam_master.ico"),
        ]
        png_candidates = [
            r"f:\pc software\assets\webcam_master.png",
            os.path.join(bundle_dir, "assets", "webcam_master.png"),
        ]
        try:
            for ipath in ico_candidates:
                if os.path.isfile(ipath):
                    self.root.iconbitmap(ipath)
                    break
            for ppath in png_candidates:
                if os.path.isfile(ppath):
                    pil_ico = Image.open(ppath).convert("RGBA")
                    self._app_icon_photo = ImageTk.PhotoImage(pil_ico.resize((64, 64), Image.Resampling.LANCZOS))
                    self._header_icon_photo = ImageTk.PhotoImage(pil_ico.resize((42, 42), Image.Resampling.LANCZOS))
                    self.root.iconphoto(True, self._app_icon_photo)
                    break
        except Exception:
            pass

        self.always_on_top = False
        self._blink_state = True
        self._last_blink_toggle = time.time()

        # 1. Movable Screen-Record Floating Facecam Window (OFF by default — only starts when user selects Round/Square/Full!)
        self.clean_feed_win = None
        self.clean_feed_canvas = None
        self.fs_toolbar_frame = None
        self._tk_cam_img = None
        self._tk_clean_img = None
        self._drag_start_x = 0
        self._drag_start_y = 0
        self._win_start_x = 0
        self._win_start_y = 0
        self._saved_pre_fs_geom = (250, 100, 100)

        # 2. Compact Stop Recording Pill (ONLY appears during active recording, 100% Excluded from Video via WDA_EXCLUDEFROMCAPTURE!)
        self.rec_hud_win = None
        self.hud_dot_lbl = None
        self.hud_rec_btn = None
        self.hud_shape_btns = {}
        self._hud_drag_x = 0
        self._hud_drag_y = 0
        self._hud_win_x = 0
        self._hud_win_y = 0

        self._build_ui()
        self._update_ui_loop()

    def _capture_ui_window_screenshot(self, save_path: str) -> tuple[bool, str]:
        """Brings the Webcam Master window to the front and captures a crisp screenshot of its UI."""
        done_evt = threading.Event()
        result = {"ok": False, "path": save_path}

        def _lift_and_schedule():
            try:
                self.root.deiconify()
                self.root.state("normal")
                self.root.lift()
                self.root.attributes("-topmost", True)
                self.root.update_idletasks()
                self.root.update()
            except Exception:
                pass
            self.root.after(280, _grab_now)

        def _grab_now():
            try:
                self.root.update_idletasks()
                full_scr = ImageGrab.grab()
                sw, sh = full_scr.size
                x = max(0, min(sw - 100, self.root.winfo_rootx() - 8))
                y = max(0, min(sh - 100, self.root.winfo_rooty() - 32))
                w = min(sw - x, self.root.winfo_width() + 16)
                h = min(sh - y, self.root.winfo_height() + 40)
                scr = full_scr.crop((x, y, x + w, y + h))
                os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)
                scr.save(save_path, "PNG")
                result["ok"] = True
            except Exception as e:
                result["path"] = str(e)
            finally:
                try:
                    self.root.attributes("-topmost", self.always_on_top)
                except Exception:
                    pass
                done_evt.set()

        self.root.after(0, _lift_and_schedule)
        done_evt.wait(timeout=3.5)
        return result["ok"], result["path"]

    def _exit_application(self):
        """Cleanly stops all recordings, streams, virtual camera, and exits the application."""
        try:
            if self.engine.is_recording_mp4:
                self.engine.stop_mp4_recording()
            if self.engine.is_recording:
                self.engine.stop_recording()
        except Exception:
            pass
        self.engine.running = False
        self.engine.stop_audio_output()
        try:
            configure_windows_default_mic_for_recorders(restore_physical_mic=True)
        except Exception:
            pass
        if self.engine.virtual_cam is not None:
            try:
                self.engine.virtual_cam.close()
            except Exception:
                pass
        try:
            self.root.destroy()
        except Exception:
            pass
        os._exit(0)

    def _exit_phone_app_clicked(self):
        """Closes/stops the mobile phone app remotely over wireless and shows instructions."""
        self.engine.exit_phone_app()

    def _handle_facecam_remote_cmd(self, action="show", shape=None, corner=None, size=None):
        def _apply():
            if size is not None:
                try:
                    self.engine.facecam_size = max(140, min(560, int(size)))
                except Exception:
                    pass
            if action == "hud":
                self._open_recording_hud_bar()
                return
            if action == "hide" or shape == "OFF":
                self._select_filmora_camera_option("OFF")
                return
            if shape in ("ROUND", "SQUARE", "FULLSCREEN"):
                self._select_filmora_camera_option(shape)
            if corner in ("BR", "BL", "TR", "TL"):
                self._snap_facecam_corner(corner)
            self._sync_facecam_buttons()
        self.root.after(0, _apply)

    def _install_virtual_drivers(self):
        bat_path = r"f:\pc software\drivers\install_virtual_devices.bat"
        if not os.path.isfile(bat_path):
            messagebox.showwarning("Driver Installer", f"Installer not found at {bat_path}")
            return
        try:
            subprocess.Popen(
                ["powershell", "-Command", f"Start-Process -FilePath '{bat_path}' -Verb RunAs"],
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            self.engine.log("Launched Filmora Virtual Mic (VB-Cable) & Cam installer (Accept Windows UAC prompt)")
        except Exception as e:
            self.engine.log(f"Driver installer launch failed: {e}")

    def _open_offline_hotspot_guide(self):
        try:
            os.startfile("ms-settings:network-mobilehotspot")
        except Exception:
            pass
        msg = (
            "📡 100% OFFLINE WIRELESS MODES (NO INTERNET REQUIRED!)\n\n"
            "Webcam Master works completely without Internet in all 3 Wireless Modes:\n\n"
            "1️⃣ PHONE HOTSPOT (No Router & No Internet):\n"
            "   • Turn ON 'Hotspot' on your Motorola Edge 50 Neo (Keep Mobile Data OFF!).\n"
            "   • Connect this PC's Wi-Fi to your phone's Hotspot.\n"
            "   • Webcam Master automatically detects the phone's Hotspot IP and connects!\n\n"
            "2️⃣ PC MOBILE HOTSPOT (No Router & No Internet):\n"
            "   • Turn ON 'Mobile Hotspot' in the Windows Settings window that just opened.\n"
            "   • Connect your phone's Wi-Fi to this PC's Hotspot (192.168.137.1).\n\n"
            "3️⃣ LOCAL WI-FI ROUTER WITHOUT INTERNET:\n"
            "   • Connect phone & PC to any Wi-Fi router even if Internet is unplugged/down."
        )
        messagebox.showinfo("📡 Offline Wireless (No Internet Required)", msg)

    def _toggle_always_on_top(self):
        self.always_on_top = not self.always_on_top
        self.root.attributes("-topmost", self.always_on_top)
        if self.always_on_top:
            self.pin_top_btn.config(text="📌 Keep on Top: ON", bg="#10B981")
            self.engine.log("Main Window pinned Always-On-Top (stays visible over Filmora & all software)")
        else:
            self.pin_top_btn.config(text="📌 Keep on Top: OFF", bg="#334155")
            self.engine.log("Main Window unpinned from Always-On-Top")

    def _build_ui(self):
        # =========================================================================
        # 1. TOP HEADER BAR (Clean Title + Live Blinking Status + Pin + Exit Buttons)
        # =========================================================================
        header = tk.Frame(self.root, bg="#111827", height=58)
        header.pack(fill="x", padx=14, pady=(8, 4))
        header.pack_propagate(False)

        if self._header_icon_photo is not None:
            tk.Label(header, image=self._header_icon_photo, bg="#111827").pack(side="left", padx=(12, 6), pady=8)

        title_col = tk.Frame(header, bg="#111827")
        title_col.pack(side="left", padx=6, pady=6)

        tk.Label(
            title_col,
            text="Webcam Master",
            font=("Segoe UI", 16, "bold"),
            fg="#F8FAFC",
            bg="#111827",
        ).pack(anchor="w")

        self.sub_lbl = tk.Label(
            title_col,
            text="Wireless Phone Mic & Camera  •  PC Screen Recorder",
            font=("Segoe UI", 9, "bold"),
            fg="#34D399",
            bg="#111827",
        )
        self.sub_lbl.pack(anchor="w")

        # Right side of Header: Exit PC App | Exit Phone App | Keep on Top | Live Status Pill
        tk.Button(
            header,
            text="✖ Exit PC App",
            font=("Segoe UI", 9, "bold"),
            fg="#FFFFFF",
            bg="#EF4444",
            activebackground="#DC2626",
            relief="flat",
            padx=12,
            pady=6,
            cursor="hand2",
            command=self._exit_application,
        ).pack(side="right", padx=(4, 12))

        tk.Button(
            header,
            text="📱 Exit Phone App",
            font=("Segoe UI", 9, "bold"),
            fg="#FFFFFF",
            bg="#B91C1C",
            activebackground="#991B1B",
            relief="flat",
            padx=12,
            pady=6,
            cursor="hand2",
            command=self._exit_phone_app_clicked,
        ).pack(side="right", padx=4)

        self.pin_top_btn = tk.Button(
            header,
            text="📌 Keep on Top: OFF",
            font=("Segoe UI", 9, "bold"),
            fg="#FFFFFF",
            bg="#334155",
            activebackground="#475569",
            relief="flat",
            padx=10,
            pady=6,
            cursor="hand2",
            command=self._toggle_always_on_top,
        )
        self.pin_top_btn.pack(side="right", padx=4)

        self.status_badge = tk.Label(
            header,
            text=" ● WAITING FOR PHONE ",
            font=("Segoe UI", 9, "bold"),
            fg="#FDE68A",
            bg="#78350F",
            padx=12,
            pady=6,
        )
        self.status_badge.pack(side="right", padx=6)

        # =========================================================================
        # 2. TOP PRIMARY ACTION BAR (Uncluttered: 3 Modes on Left | Screen Rec + Audio Toggles on Right)
        # =========================================================================
        bar = tk.Frame(self.root, bg="#1E293B", highlightthickness=1, highlightbackground="#334155")
        bar.pack(fill="x", padx=14, pady=(0, 6))

        tk.Label(
            bar, text="1. Select Mode:", font=("Segoe UI", 10, "bold"), fg="#E2E8F0", bg="#1E293B"
        ).pack(side="left", padx=(12, 8), pady=8)

        self.mode_buttons = {}
        modes = [
            ("MIC_ONLY", "🎙️ Mic Only"),
            ("WEBCAM_MIC", "📹 Cam + Mic"),
            ("WEBCAM_ONLY", "📷 Cam Only"),
        ]
        for code, label in modes:
            b = tk.Button(
                bar,
                text=label,
                font=("Segoe UI", 10, "bold"),
                fg="#FFFFFF",
                bg="#10B981" if code == self.engine.app_mode else "#334155",
                activebackground="#059669",
                relief="flat",
                padx=14,
                pady=5,
                cursor="hand2",
                command=lambda m=code: self._select_mode(m),
            )
            b.pack(side="left", padx=4, pady=6)
            self.mode_buttons[code] = b

        # Right side of Action Bar: Screen Recorder + Record PC Sound + Voice Over
        self.top_screen_rec_btn = tk.Button(
            bar,
            text="● Start Screen Record",
            font=("Segoe UI", 10, "bold"),
            fg="#FFFFFF",
            bg="#2563EB",
            activebackground="#1D4ED8",
            relief="flat",
            padx=14,
            pady=5,
            cursor="hand2",
            command=self._toggle_mp4_recording,
        )
        self.top_screen_rec_btn.pack(side="right", padx=(6, 12), pady=6)

        self.voice_over_btn = tk.Button(
            bar,
            text="🎤 Voice Over: ON",
            font=("Segoe UI", 9, "bold"),
            fg="#FFFFFF",
            bg="#0D9488",
            activebackground="#0F766E",
            relief="flat",
            padx=10,
            pady=5,
            cursor="hand2",
            command=self._toggle_record_voice_over,
        )
        self.voice_over_btn.pack(side="right", padx=4, pady=6)

        self.pc_sound_btn = tk.Button(
            bar,
            text="🔊 Record PC Sound: ON",
            font=("Segoe UI", 9, "bold"),
            fg="#FFFFFF",
            bg="#0D9488",
            activebackground="#0F766E",
            relief="flat",
            padx=10,
            pady=5,
            cursor="hand2",
            command=self._toggle_record_pc_sound,
        )
        self.pc_sound_btn.pack(side="right", padx=4, pady=6)

        # =========================================================================
        # 3. MAIN SPLIT WORKSPACE (Left = Video & Screen Recorder, Right = 3 Step Cards)
        # =========================================================================
        content = tk.Frame(self.root, bg="#090D16")
        content.pack(fill="both", expand=True, padx=14, pady=(0, 8))

        # -------------------------------------------------------------------------
        # LEFT PANEL: Camera Source, Floating Corner Overlay, Live Preview & Save Bar
        # -------------------------------------------------------------------------
        left_card = tk.Frame(content, bg="#1E293B", highlightthickness=1, highlightbackground="#334155")
        left_card.pack(side="left", fill="both", expand=True, padx=(0, 6))

        # Left Top Row: Prominent 1-Click Camera Source Choice (Mobile Camera | PC Camera) + Flip + Snapshot
        cam_top = tk.Frame(left_card, bg="#1E293B")
        cam_top.pack(fill="x", padx=12, pady=(8, 4))

        tk.Label(
            cam_top,
            text="🎥 Camera Choice:",
            font=("Segoe UI", 9, "bold"),
            fg="#38BDF8",
            bg="#1E293B",
        ).pack(side="left", padx=(0, 6))

        self.cam_src_btns = {}
        for src_code, src_lbl in [
            ("PHONE", "📱 Mobile Cam"),
            ("PC_0", "💻 PC Webcam"),
            ("OFF", "🚫 Cam Off"),
        ]:
            b = tk.Button(
                cam_top,
                text=src_lbl,
                font=("Segoe UI", 9, "bold"),
                fg="#FFFFFF",
                bg="#10B981" if (src_code == "PHONE" and self.engine.camera_source == "PHONE") or (src_code == "PC_0" and self.engine.camera_source.startswith("PC_")) else "#334155",
                activebackground="#059669",
                relief="flat",
                padx=8,
                pady=4,
                cursor="hand2",
                command=lambda sc=src_code: self._select_camera_source(sc),
            )
            b.pack(side="left", padx=2)
            self.cam_src_btns[src_code] = b

        self.cam_info_lbl = tk.Label(
            cam_top,
            text="0x0 @ 0 FPS",
            font=("Consolas", 8, "bold"),
            fg="#94A3B8",
            bg="#1E293B",
        )
        self.cam_info_lbl.pack(side="left", padx=(6, 2))

        tk.Button(
            cam_top,
            text="↻ Flip Cam",
            font=("Segoe UI", 8, "bold"),
            fg="#FFFFFF",
            bg="#0284C7",
            activebackground="#0369A1",
            relief="flat",
            padx=10,
            pady=4,
            cursor="hand2",
            command=self.engine.flip_phone_camera,
        ).pack(side="right", padx=2)

        tk.Button(
            cam_top,
            text="📷 Snapshot",
            font=("Segoe UI", 8, "bold"),
            fg="#FFFFFF",
            bg="#334155",
            activebackground="#475569",
            relief="flat",
            padx=10,
            pady=4,
            cursor="hand2",
            command=lambda: self.engine.save_snapshot(),
        ).pack(side="right", padx=4)

        # Left Second Box: Floating Corner Camera Overlay (Starts ONLY When Clicked — OFF by default)
        overlay_bar = tk.Frame(left_card, bg="#0F172A", highlightthickness=1, highlightbackground="#10B981")
        overlay_bar.pack(fill="x", padx=12, pady=(2, 6))

        row1 = tk.Frame(overlay_bar, bg="#0F172A")
        row1.pack(fill="x", padx=10, pady=(6, 3))

        tk.Label(
            row1,
            text="Corner Camera Overlay (Starts When Clicked):",
            font=("Segoe UI", 9, "bold"),
            fg="#34D399",
            bg="#0F172A",
        ).pack(side="left", padx=(0, 8))

        self.shape_btns = {}
        for sh_code, sh_label in [
            ("OFF", "✕ Off"),
            ("ROUND", "⭕ Round"),
            ("SQUARE", "⬜ Square"),
            ("FULLSCREEN", "🖥️ Full Screen"),
        ]:
            b = tk.Button(
                row1,
                text=sh_label,
                font=("Segoe UI", 9, "bold"),
                fg="#FFFFFF",
                bg="#10B981" if self.engine.facecam_shape == sh_code else "#334155",
                activebackground="#059669",
                relief="flat",
                padx=10,
                pady=3,
                cursor="hand2",
                command=lambda s=sh_code: self._select_filmora_camera_option(s),
            )
            b.pack(side="left", padx=3)
            self.shape_btns[sh_code] = b

        row2 = tk.Frame(overlay_bar, bg="#0F172A")
        row2.pack(fill="x", padx=10, pady=(2, 6))

        tk.Label(
            row2,
            text="Move / Snap Corner:",
            font=("Segoe UI", 8, "bold"),
            fg="#94A3B8",
            bg="#0F172A",
        ).pack(side="left", padx=(0, 6))

        for c_code, c_lbl in [("TL", "↖ Top-Left"), ("TR", "↗ Top-Right"), ("BL", "↙ Bot-Left"), ("BR", "↘ Bot-Right")]:
            tk.Button(
                row2,
                text=c_lbl,
                font=("Segoe UI", 8, "bold"),
                fg="#E2E8F0",
                bg="#1E293B",
                activebackground="#334155",
                relief="flat",
                padx=7,
                pady=2,
                cursor="hand2",
                command=lambda c=c_code: self._snap_facecam_corner(c),
            ).pack(side="left", padx=2)

        tk.Button(
            row2,
            text="+ Bigger",
            font=("Segoe UI", 8, "bold"),
            fg="#FFFFFF",
            bg="#0284C7",
            relief="flat",
            padx=8,
            pady=2,
            cursor="hand2",
            command=lambda: self._resize_facecam(35),
        ).pack(side="right", padx=2)

        tk.Button(
            row2,
            text="- Smaller",
            font=("Segoe UI", 8, "bold"),
            fg="#FFFFFF",
            bg="#334155",
            relief="flat",
            padx=8,
            pady=2,
            cursor="hand2",
            command=lambda: self._resize_facecam(-35),
        ).pack(side="right", padx=2)

        # Left Bottom Box: Save Location Choice + Primary Screen Record Button
        self.mp4_bar = tk.Frame(left_card, bg="#0F172A", highlightthickness=1, highlightbackground="#334155")
        self.mp4_bar.pack(side="bottom", fill="x", padx=12, pady=(0, 10))

        save_row = tk.Frame(self.mp4_bar, bg="#0F172A")
        save_row.pack(fill="x", padx=10, pady=(6, 4))

        tk.Label(
            save_row,
            text="💾 Save Location:",
            font=("Segoe UI", 9, "bold"),
            fg="#38BDF8",
            bg="#0F172A",
        ).pack(side="left", padx=(0, 6))

        self.save_loc_options = self.engine.get_save_location_options()
        save_loc_labels = [lbl for lbl, _ in self.save_loc_options]
        self.save_loc_combo = ttk.Combobox(
            save_row,
            values=save_loc_labels,
            state="readonly",
            width=40,
        )
        self.save_loc_combo.current(0)
        self.save_loc_combo.bind("<<ComboboxSelected>>", self._on_save_location_selected)
        self.save_loc_combo.pack(side="left", fill="x", expand=True, padx=4)

        tk.Button(
            save_row,
            text="📂 Browse Folder...",
            font=("Segoe UI", 8, "bold"),
            fg="#FFFFFF",
            bg="#0284C7",
            activebackground="#0369A1",
            relief="flat",
            padx=10,
            pady=3,
            cursor="hand2",
            command=self._choose_custom_save_folder,
        ).pack(side="right", padx=2)

        rec_act_row = tk.Frame(self.mp4_bar, bg="#0F172A")
        rec_act_row.pack(fill="x", padx=10, pady=(2, 8))

        self.mp4_toggle_btn = tk.Button(
            rec_act_row,
            text="● Start Screen Record (PC Screen + PC Sound + Voice Over -> .MP4)",
            font=("Segoe UI", 10, "bold"),
            fg="#FFFFFF",
            bg="#2563EB",
            activebackground="#1D4ED8",
            relief="flat",
            pady=7,
            cursor="hand2",
            command=self._toggle_mp4_recording,
        )
        self.mp4_toggle_btn.pack(side="left", fill="x", expand=True, padx=(0, 6))

        tk.Button(
            rec_act_row,
            text="📂 Open Save Folder",
            font=("Segoe UI", 9, "bold"),
            fg="#E2E8F0",
            bg="#334155",
            activebackground="#475569",
            relief="flat",
            padx=12,
            pady=7,
            cursor="hand2",
            command=self._open_recordings_folder,
        ).pack(side="right")

        # Center Live Video Preview Container
        self.video_container = tk.Frame(left_card, bg="#050811", highlightthickness=1, highlightbackground="#0F172A")
        self.video_container.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        self.video_container.pack_propagate(False)

        self.video_label = tk.Label(
            self.video_container,
            text="Waiting for Camera...\n\n• Click '⭕ Round', '⬜ Square', or '🖥️ Full Screen' above when needed!",
            font=("Segoe UI", 11, "bold"),
            fg="#64748B",
            bg="#050811",
            justify="center",
        )
        self.video_label.pack(fill="both", expand=True)

        # -------------------------------------------------------------------------
        # RIGHT PANEL: 3 Clean Step-by-Step Control Cards
        # -------------------------------------------------------------------------
        right_card = tk.Frame(content, bg="#1E293B", width=445, highlightthickness=1, highlightbackground="#334155")
        right_card.pack(side="right", fill="y", padx=(6, 0))
        right_card.pack_propagate(False)

        pad_frame = tk.Frame(right_card, bg="#1E293B")
        pad_frame.pack(fill="both", expand=True, padx=12, pady=8)

        # MASTER STREAM TOGGLE BUTTON
        self.stream_toggle_btn = tk.Button(
            pad_frame,
            text="● STREAM ACTIVE: ON (Click to Stop)",
            font=("Segoe UI", 11, "bold"),
            fg="#FFFFFF",
            bg="#10B981",
            activebackground="#059669",
            relief="flat",
            pady=8,
            cursor="hand2",
            command=self._toggle_stream_clicked,
        )
        self.stream_toggle_btn.pack(fill="x", pady=(0, 6))

        # CARD 1: CONNECTED PHONE & OFFLINE WIRELESS CONNECTION
        self.device_card = tk.Frame(pad_frame, bg="#0F172A", highlightthickness=1, highlightbackground="#334155")
        self.device_card.pack(fill="x", pady=(0, 6))

        self.device_title_lbl = tk.Label(
            self.device_card,
            text="● 1. Connected Phone: Searching...",
            font=("Segoe UI", 10, "bold"),
            fg="#FBBF24",
            bg="#0F172A",
            anchor="w",
            padx=10,
            pady=3,
        )
        self.device_title_lbl.pack(fill="x")

        self.conn_info_lbl = tk.Label(
            self.device_card,
            text="Offline Wireless: Wi-Fi, Phone Hotspot, or PC Hotspot",
            font=("Segoe UI", 8, "bold"),
            fg="#38BDF8",
            bg="#0F172A",
            anchor="w",
            padx=10,
        )
        self.conn_info_lbl.pack(fill="x")

        self.net_mode_lbl = tk.Label(
            self.device_card,
            text=f"PC IP: {self.engine.local_ip} ({self.engine.pc_hostname}) • No Internet Needed",
            font=("Consolas", 8),
            fg="#94A3B8",
            bg="#0F172A",
            anchor="w",
            padx=10,
            pady=2,
        )
        self.net_mode_lbl.pack(fill="x")

        # Device Picker + Offline Hotspot Row inside Card 1
        conn_row1 = tk.Frame(self.device_card, bg="#0F172A")
        conn_row1.pack(fill="x", padx=10, pady=(2, 3))

        self.device_combo = ttk.Combobox(
            conn_row1,
            values=self.engine.get_device_dropdown_items(),
            state="readonly",
            width=26,
        )
        self.device_combo.current(0)
        self.device_combo.bind("<<ComboboxSelected>>", self._on_phone_device_selected)
        self.device_combo.pack(side="left", fill="x", expand=True, padx=(0, 6))

        tk.Button(
            conn_row1,
            text="Offline Hotspot",
            font=("Segoe UI", 8, "bold"),
            fg="#FFFFFF",
            bg="#0D9488",
            activebackground="#0F766E",
            relief="flat",
            padx=8,
            pady=3,
            cursor="hand2",
            command=self._open_offline_hotspot_guide,
        ).pack(side="right")

        # IP Address + Connect/Scan Row inside Card 1
        conn_row2 = tk.Frame(self.device_card, bg="#0F172A")
        conn_row2.pack(fill="x", padx=10, pady=(1, 6))

        tk.Label(
            conn_row2,
            text="Phone IP:",
            font=("Segoe UI", 8, "bold"),
            fg="#CBD5E1",
            bg="#0F172A",
        ).pack(side="left", padx=(0, 6))

        self.wifi_ip_var = tk.StringVar(value=self.engine.phone_wifi_ip)
        tk.Entry(
            conn_row2,
            textvariable=self.wifi_ip_var,
            font=("Consolas", 9, "bold"),
            bg="#1E293B",
            fg="#38BDF8",
            insertbackground="#FFFFFF",
            relief="flat",
            width=14,
        ).pack(side="left", padx=(0, 6), ipady=2)

        tk.Button(
            conn_row2,
            text="Connect / Scan Phone",
            font=("Segoe UI", 8, "bold"),
            fg="#FFFFFF",
            bg="#0284C7",
            activebackground="#0369A1",
            relief="flat",
            padx=10,
            pady=3,
            cursor="hand2",
            command=self._connect_wireless_clicked,
        ).pack(side="left", fill="x", expand=True)

        # CARD 2: MICROPHONE & FILMORA AUDIO STUDIO
        mic_card = tk.Frame(pad_frame, bg="#0F172A", highlightthickness=1, highlightbackground="#334155")
        mic_card.pack(fill="x", pady=(0, 6))

        mic_hdr = tk.Frame(mic_card, bg="#0F172A")
        mic_hdr.pack(fill="x", padx=10, pady=(4, 2))

        tk.Label(
            mic_hdr,
            text="2. Microphone & Filmora Audio",
            font=("Segoe UI", 9, "bold"),
            fg="#F8FAFC",
            bg="#0F172A",
        ).pack(side="left")

        self.db_lbl = tk.Label(
            mic_hdr,
            text="-60.0 dB",
            font=("Consolas", 9, "bold"),
            fg="#22D3EE",
            bg="#0F172A",
        )
        self.db_lbl.pack(side="right")

        self.wave_canvas = tk.Canvas(
            mic_card, bg="#050811", height=58, highlightthickness=0
        )
        self.wave_canvas.pack(fill="x", padx=10, pady=(2, 4))

        self.vu_canvas = tk.Canvas(
            mic_card, bg="#050811", height=10, highlightthickness=0
        )
        self.vu_canvas.pack(fill="x", padx=10, pady=(0, 5))

        vol_row = tk.Frame(mic_card, bg="#0F172A")
        vol_row.pack(fill="x", padx=10, pady=2)
        self.vol_lbl = tk.Label(
            vol_row, text="Mic Volume: 100%", font=("Segoe UI", 8, "bold"), fg="#CBD5E1", bg="#0F172A"
        )
        self.vol_lbl.pack(side="left")

        self.vol_slider = ttk.Scale(
            vol_row, from_=0.2, to=3.0, value=1.0, command=self._on_vol_change
        )
        self.vol_slider.pack(side="right", fill="x", expand=True, padx=(10, 0))

        dev_row = tk.Frame(mic_card, bg="#0F172A")
        dev_row.pack(fill="x", padx=10, pady=(3, 4))
        tk.Label(
            dev_row, text="Audio Target:", font=("Segoe UI", 8, "bold"), fg="#CBD5E1", bg="#0F172A"
        ).pack(side="left")

        self.out_devs = self.engine.get_output_devices()
        dev_names = [name for _, name in self.out_devs]
        self.dev_combo = ttk.Combobox(dev_row, values=dev_names, state="readonly", width=28)
        if dev_names:
            sel_idx = 0
            for i, (d_id, _) in enumerate(self.out_devs):
                if d_id == self.engine.selected_output_device:
                    sel_idx = i
                    break
            self.dev_combo.current(sel_idx)
        self.dev_combo.bind("<<ComboboxSelected>>", self._on_device_selected)
        self.dev_combo.pack(side="right", fill="x", expand=True, padx=(6, 0))

        audio_btns = tk.Frame(mic_card, bg="#0F172A")
        audio_btns.pack(fill="x", padx=10, pady=(2, 6))

        self.spk_toggle_btn = tk.Button(
            audio_btns,
            text="Route to Audio Target: OFF",
            font=("Segoe UI", 8, "bold"),
            fg="#FFFFFF",
            bg="#334155",
            relief="flat",
            pady=4,
            cursor="hand2",
            command=self._toggle_speaker_monitor,
        )
        self.spk_toggle_btn.pack(fill="x", pady=(0, 4))

        rec_drv_row = tk.Frame(audio_btns, bg="#0F172A")
        rec_drv_row.pack(fill="x")

        self.wav_toggle_btn = tk.Button(
            rec_drv_row,
            text="● Record Voice (.WAV)",
            font=("Segoe UI", 8, "bold"),
            fg="#FFFFFF",
            bg="#0D9488",
            relief="flat",
            pady=4,
            cursor="hand2",
            command=self._toggle_wav_recording,
        )
        self.wav_toggle_btn.pack(side="left", fill="x", expand=True, padx=(0, 4))

        tk.Button(
            rec_drv_row,
            text="⚡ Install Virtual Mic",
            font=("Segoe UI", 8, "bold"),
            fg="#FFFFFF",
            bg="#7C3AED",
            relief="flat",
            padx=10,
            pady=4,
            cursor="hand2",
            command=self._install_virtual_drivers,
        ).pack(side="right")

        # CARD 3: SAVED MEDIA FILE (Play / Locate Saved Recording File)
        media_card = tk.Frame(pad_frame, bg="#0F172A", highlightthickness=1, highlightbackground="#2563EB")
        media_card.pack(fill="x", pady=(0, 5))

        self.media_file_lbl = tk.Label(
            media_card,
            text="3. Saved Media: None yet",
            font=("Segoe UI", 8, "bold"),
            fg="#93C5FD",
            bg="#0F172A",
            anchor="w",
            padx=10,
            pady=4,
        )
        self.media_file_lbl.pack(fill="x")

        media_btn_row = tk.Frame(media_card, bg="#0F172A")
        media_btn_row.pack(fill="x", padx=10, pady=(0, 6))

        tk.Button(
            media_btn_row,
            text="▶ Play Saved Media File",
            font=("Segoe UI", 8, "bold"),
            fg="#FFFFFF",
            bg="#2563EB",
            activebackground="#1D4ED8",
            relief="flat",
            padx=8,
            pady=4,
            cursor="hand2",
            command=self._play_latest_media_file,
        ).pack(side="left", fill="x", expand=True, padx=(0, 4))

        tk.Button(
            media_btn_row,
            text="📂 Show in Folder",
            font=("Segoe UI", 8, "bold"),
            fg="#E2E8F0",
            bg="#334155",
            relief="flat",
            padx=10,
            pady=4,
            cursor="hand2",
            command=self._show_latest_media_in_folder,
        ).pack(side="right")

        # COMPACT ACTIVITY STATUS LOG
        self.log_text = tk.Text(
            pad_frame,
            bg="#090D16",
            fg="#94A3B8",
            font=("Consolas", 8),
            height=2,
            relief="flat",
            state="disabled",
        )
        self.log_text.pack(fill="both", expand=True, pady=(2, 0))

    # ==================== SAVED MEDIA FILE ACTIONS ====================

    def _play_latest_media_file(self):
        self.engine.silent_bridge.check_idle_and_restore_speakers(self.engine.audio_queue, force=True)
        self.engine.silent_bridge.last_pc_playback_ts = time.time()
        fp = self.engine.last_saved_media_file or self.engine._find_latest_saved_media()
        if fp and os.path.isfile(fp):
            try:
                os.startfile(fp)
                self.engine.log(f"Playing saved media file -> {os.path.basename(fp)}")
            except Exception as e:
                self.engine.log(f"Could not open media file: {e}")
        else:
            self._open_recordings_folder()

    def _show_latest_media_in_folder(self):
        fp = self.engine.last_saved_media_file or self.engine._find_latest_saved_media()
        if fp and os.path.isfile(fp):
            try:
                subprocess.Popen(["explorer.exe", f"/select,{os.path.normpath(fp)}"])
                return
            except Exception:
                pass
        self._open_recordings_folder()

    # ==================== INVISIBLE-TO-VIDEO COMPACT STOP / RECORDING BUTTON ====================

    def _close_recording_hud_bar(self):
        if self.rec_hud_win is not None:
            try:
                self.rec_hud_win.destroy()
            except Exception:
                pass
        self.rec_hud_win = None
        self.hud_dot_lbl = None
        self.hud_rec_btn = None
        self.hud_shape_btns = {}
        self.engine.rec_hud_visible = False

    def _open_recording_hud_bar(self):
        """
        Compact Stop/Recording Pill that ONLY appears while actively recording screen video
        (never opens automatically on startup or when selecting Round/Square camera!)
        and is 100% excluded from the recorded video via WDA_EXCLUDEFROMCAPTURE.
        """
        if self.rec_hud_win is not None and self.rec_hud_win.winfo_exists():
            self.rec_hud_win.lift()
            set_window_exclude_from_capture(self.rec_hud_win, True)
            self.engine.rec_hud_visible = True
            return

        hud = tk.Toplevel(self.root)
        hud.title("Stop Screen Recording")
        hud.overrideredirect(True)
        hud.attributes("-topmost", True)
        hud.configure(bg="#0F172A", highlightthickness=2, highlightbackground="#EF4444")

        sw = self.root.winfo_screenwidth()
        w_hud, h_hud = 260, 40
        x_hud = max(20, sw - w_hud - 40)
        y_hud = 16
        hud.geometry(f"{w_hud}x{h_hud}+{x_hud}+{y_hud}")

        bar_frame = tk.Frame(hud, bg="#0F172A", cursor="fleur")
        bar_frame.pack(fill="both", expand=True, padx=6, pady=4)

        self.hud_dot_lbl = tk.Label(
            bar_frame,
            text="🔴 ● REC",
            font=("Segoe UI", 9, "bold"),
            fg="#EF4444",
            bg="#0F172A",
            padx=4,
            cursor="fleur",
        )
        self.hud_dot_lbl.pack(side="left")

        self.hud_rec_btn = tk.Button(
            bar_frame,
            text="⏹ Stop Recording",
            font=("Segoe UI", 9, "bold"),
            fg="#FFFFFF",
            bg="#EF4444",
            activebackground="#DC2626",
            relief="flat",
            padx=10,
            pady=2,
            cursor="hand2",
            command=self._toggle_mp4_recording,
        )
        self.hud_rec_btn.pack(side="left", padx=4)

        tk.Button(
            bar_frame,
            text="✖",
            font=("Segoe UI", 8, "bold"),
            fg="#94A3B8",
            bg="#1E293B",
            activebackground="#334155",
            relief="flat",
            padx=5,
            pady=2,
            cursor="hand2",
            command=self._close_recording_hud_bar,
        ).pack(side="right")

        self.hud_shape_btns = {}

        for wdg in (bar_frame, self.hud_dot_lbl):
            wdg.bind("<ButtonPress-1>", self._on_hud_drag_start)
            wdg.bind("<B1-Motion>", self._on_hud_drag_motion)

        self.rec_hud_win = hud
        self.engine.rec_hud_visible = True

        hud.update()
        excluded = set_window_exclude_from_capture(hud, True)
        self.engine.rec_hud_excluded_from_video = excluded
        self.root.after(150, lambda: set_window_exclude_from_capture(self.rec_hud_win, True))

    def _on_hud_drag_start(self, event):
        if self.rec_hud_win is None:
            return
        self._hud_drag_x = event.x_root
        self._hud_drag_y = event.y_root
        self._hud_win_x = self.rec_hud_win.winfo_x()
        self._hud_win_y = self.rec_hud_win.winfo_y()

    def _on_hud_drag_motion(self, event):
        if self.rec_hud_win is None:
            return
        nx = self._hud_win_x + (event.x_root - self._hud_drag_x)
        ny = self._hud_win_y + (event.y_root - self._hud_drag_y)
        self.rec_hud_win.geometry(f"260x40+{nx}+{ny}")

    # ==================== FILMORA-STYLE CAMERA OPTIONS (OFF BY DEFAULT, STARTS WHEN USER NEEDS) ====================

    def _select_filmora_camera_option(self, shape: str):
        """
        Filmora-style selector:
        - 'OFF': Hides the floating camera (Default on startup — does NOT start automatically!)
        - 'ROUND': Opens movable circular camera in corner (ONLY the Round camera — no extra rectangle bar!)
        - 'SQUARE': Opens movable square camera in corner (ONLY the Square camera — no extra rectangle bar!)
        - 'FULLSCREEN': Opens full-screen camera
        """
        if shape == "OFF":
            self.engine.facecam_shape = "OFF"
            self._close_floating_facecam()
            if self.engine.app_mode == "MIC_ONLY" or self.engine.camera_source != "OFF":
                self.engine.set_camera_source("OFF")
            self._sync_buttons()
            return

        # Restore or select active camera when opening Round / Square / Full Screen
        if self.engine.camera_source == "OFF":
            restore_cam = getattr(self.engine, "last_active_camera_source", "PHONE")
            if restore_cam == "PHONE" and not self.engine.connected:
                restore_cam = "PC_0"
            self.engine.set_camera_source(restore_cam)
        elif self.engine.camera_source == "PHONE" and (not self.engine.connected or self.engine.app_mode == "MIC_ONLY"):
            self.engine.set_camera_source("PC_0")

        self.engine.facecam_shape = shape
        if self.clean_feed_win is None or not self.clean_feed_win.winfo_exists():
            self._open_clean_feed_window()
        else:
            self._set_facecam_shape(shape)

        self._sync_buttons()

    def _close_floating_facecam(self):
        if self.clean_feed_win is not None:
            try:
                self.clean_feed_win.destroy()
            except Exception:
                pass
        self.clean_feed_win = None
        self.clean_feed_canvas = None
        self.fs_toolbar_frame = None
        self.engine.facecam_visible = False
        self._sync_facecam_buttons()

    def _open_clean_feed_window(self):
        if self.clean_feed_win is not None and self.clean_feed_win.winfo_exists():
            self.clean_feed_win.lift()
            self.engine.facecam_visible = True
            self._sync_facecam_buttons()
            return

        win = tk.Toplevel(self.root)
        win.title("Webcam Master Floating Camera")
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=TRANS_COLOR_KEY)
        try:
            win.attributes("-transparentcolor", TRANS_COLOR_KEY)
        except Exception:
            pass
        # Make sure the Round/Square Camera Overlay itself IS captured in screen recordings (WDA_NONE)
        set_window_exclude_from_capture(win, False)

        canvas = tk.Canvas(
            win,
            bg=TRANS_COLOR_KEY,
            highlightthickness=0,
            bd=0,
            cursor="fleur",
        )
        canvas.pack(fill="both", expand=True)

        # Right-click context menu on the Floating Facecam
        self.facecam_menu = tk.Menu(win, tearoff=0, bg="#0F172A", fg="#F8FAFC", activebackground="#10B981", font=("Segoe UI", 9, "bold"))
        self.facecam_menu.add_command(label="📱 Use Mobile Phone Camera", command=lambda: self._select_camera_source("PHONE"))
        self.facecam_menu.add_command(label="💻 Use PC Camera (Webcam)", command=lambda: self._select_camera_source("PC_0"))
        self.facecam_menu.add_separator()
        self.facecam_menu.add_command(label="⭕ Round Shape (Circle in Corner)", command=lambda: self._select_filmora_camera_option("ROUND"))
        self.facecam_menu.add_command(label="⬜ Square Shape", command=lambda: self._select_filmora_camera_option("SQUARE"))
        self.facecam_menu.add_command(label="🖥️ Full Screen Mode", command=lambda: self._select_filmora_camera_option("FULLSCREEN"))
        self.facecam_menu.add_command(label="✕ Turn Off Floating Camera", command=lambda: self._select_filmora_camera_option("OFF"))
        self.facecam_menu.add_separator()
        self.facecam_menu.add_command(label="↘ Snap to Bottom-Right Corner", command=lambda: self._snap_facecam_corner("BR"))
        self.facecam_menu.add_command(label="↙ Snap to Bottom-Left Corner", command=lambda: self._snap_facecam_corner("BL"))
        self.facecam_menu.add_command(label="↗ Snap to Top-Right Corner", command=lambda: self._snap_facecam_corner("TR"))
        self.facecam_menu.add_command(label="↖ Snap to Top-Left Corner", command=lambda: self._snap_facecam_corner("TL"))
        self.facecam_menu.add_separator()
        self.facecam_menu.add_command(label="+ Size: Large (340 px)", command=lambda: self._set_facecam_exact_size(340))
        self.facecam_menu.add_command(label="• Size: Medium (250 px)", command=lambda: self._set_facecam_exact_size(250))
        self.facecam_menu.add_command(label="- Size: Small (180 px)", command=lambda: self._set_facecam_exact_size(180))
        self.facecam_menu.add_separator()
        self.facecam_menu.add_command(label="Show Main Control Panel", command=self._restore_main_window)
        self.facecam_menu.add_command(label="✖ Exit Webcam Master App", command=self._exit_application)

        canvas.bind("<ButtonPress-1>", self._on_facecam_drag_start)
        canvas.bind("<B1-Motion>", self._on_facecam_drag_motion)
        canvas.bind("<Double-Button-1>", self._on_facecam_double_click)
        canvas.bind("<Button-3>", self._on_facecam_right_click)
        canvas.bind("<MouseWheel>", self._on_facecam_mousewheel)
        win.bind("<Escape>", lambda e: self._select_filmora_camera_option("ROUND") if self.engine.facecam_shape == "FULLSCREEN" else None)

        self.clean_feed_win = win
        self.clean_feed_canvas = canvas
        self.engine.facecam_visible = True

        if self.engine.facecam_shape == "OFF":
            self.engine.facecam_shape = "ROUND"

        self._snap_facecam_corner(self.engine.facecam_corner)
        self._set_facecam_shape(self.engine.facecam_shape)
        self._sync_facecam_buttons()
        self.engine.log(f"Opened Movable Floating Cam ({self.engine.facecam_shape}) — Drag anywhere to adjust!")

    def _restore_main_window(self):
        self.root.deiconify()
        self.root.lift()

    def _on_facecam_drag_start(self, event):
        if self.engine.facecam_shape == "FULLSCREEN" or self.clean_feed_win is None:
            return
        self._drag_start_x = event.x_root
        self._drag_start_y = event.y_root
        self._win_start_x = self.clean_feed_win.winfo_x()
        self._win_start_y = self.clean_feed_win.winfo_y()

    def _on_facecam_drag_motion(self, event):
        if self.engine.facecam_shape == "FULLSCREEN" or self.clean_feed_win is None:
            return
        dx = event.x_root - self._drag_start_x
        dy = event.y_root - self._drag_start_y
        nx = self._win_start_x + dx
        ny = self._win_start_y + dy
        sz = self.engine.facecam_size
        self.clean_feed_win.geometry(f"{sz}x{sz}+{nx}+{ny}")
        self.engine.facecam_corner = "CUSTOM"
        self._saved_pre_fs_geom = (sz, nx, ny)

    def _on_facecam_double_click(self, event):
        order = ["ROUND", "SQUARE", "FULLSCREEN"]
        cur = self.engine.facecam_shape
        nxt = order[(order.index(cur) + 1) % len(order)] if cur in order else "ROUND"
        self._select_filmora_camera_option(nxt)

    def _on_facecam_right_click(self, event):
        try:
            self.facecam_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.facecam_menu.grab_release()

    def _on_facecam_mousewheel(self, event):
        if self.engine.facecam_shape == "FULLSCREEN":
            return
        delta = 24 if event.delta > 0 else -24
        self._resize_facecam(delta)

    def _set_facecam_exact_size(self, new_size: int):
        if self.engine.facecam_shape == "FULLSCREEN":
            self._set_facecam_shape("ROUND")
        self.engine.facecam_size = max(140, min(560, new_size))
        if self.clean_feed_win is not None and self.clean_feed_win.winfo_exists():
            if self.engine.facecam_corner in ("BR", "BL", "TR", "TL"):
                self._snap_facecam_corner(self.engine.facecam_corner)
            else:
                x = self.clean_feed_win.winfo_x()
                y = self.clean_feed_win.winfo_y()
                sz = self.engine.facecam_size
                self.clean_feed_win.geometry(f"{sz}x{sz}+{x}+{y}")

    def _resize_facecam(self, delta: int):
        self._set_facecam_exact_size(self.engine.facecam_size + delta)

    def _snap_facecam_corner(self, corner: str):
        self.engine.facecam_corner = corner
        if self.clean_feed_win is None or not self.clean_feed_win.winfo_exists():
            if self.engine.facecam_shape == "OFF":
                self.engine.facecam_shape = "ROUND"
            self._open_clean_feed_window()
            return
        if self.engine.facecam_shape == "FULLSCREEN":
            self.engine.facecam_shape = "ROUND"

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        sz = self.engine.facecam_size
        margin_x = 28
        margin_y = 62

        if corner == "TL":
            x, y = margin_x, margin_x + 48
        elif corner == "TR":
            x, y = sw - sz - margin_x, margin_x + 48
        elif corner == "BL":
            x, y = margin_x, sh - sz - margin_y
        else:
            x, y = sw - sz - margin_x, sh - sz - margin_y

        self.clean_feed_win.geometry(f"{sz}x{sz}+{x}+{y}")
        self._saved_pre_fs_geom = (sz, x, y)
        self._sync_facecam_buttons()

    def _set_facecam_shape(self, shape: str):
        prev_shape = self.engine.facecam_shape
        self.engine.facecam_shape = shape
        if self.clean_feed_win is None or not self.clean_feed_win.winfo_exists():
            self._open_clean_feed_window()
            return

        if shape == "FULLSCREEN":
            if prev_shape != "FULLSCREEN":
                self._saved_pre_fs_geom = (
                    self.engine.facecam_size,
                    self.clean_feed_win.winfo_x(),
                    self.clean_feed_win.winfo_y(),
                )
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            self.clean_feed_win.geometry(f"{sw}x{sh}+0+0")
        else:
            if prev_shape == "FULLSCREEN":
                sz, x, y = self._saved_pre_fs_geom
                self.engine.facecam_size = sz
                self.clean_feed_win.geometry(f"{sz}x{sz}+{x}+{y}")
            else:
                sz = self.engine.facecam_size
                x = self.clean_feed_win.winfo_x()
                y = self.clean_feed_win.winfo_y()
                self.clean_feed_win.geometry(f"{sz}x{sz}+{x}+{y}")

        self.clean_feed_win.lift()
        if self.rec_hud_win is not None and self.rec_hud_win.winfo_exists():
            self.rec_hud_win.lift()
        self._sync_facecam_buttons()
        self.engine.log(f"Floating Camera Shape -> {shape}")

    def _sync_facecam_buttons(self):
        vis = self.clean_feed_win is not None and self.clean_feed_win.winfo_exists()
        self.engine.facecam_visible = vis
        active_code = self.engine.facecam_shape if vis else "OFF"
        for code, btn in self.shape_btns.items():
            btn.config(bg="#10B981" if code == active_code else "#334155")
        for code, btn in self.hud_shape_btns.items():
            if btn.winfo_exists():
                btn.config(bg="#10B981" if code == active_code else "#334155")

    def _render_shaped_facecam_image(self, src_pil: Image.Image) -> Image.Image:
        shape = self.engine.facecam_shape
        if shape == "FULLSCREEN":
            sw = max(320, self.root.winfo_screenwidth())
            sh = max(240, self.root.winfo_screenheight())
            bg = Image.new("RGB", (sw, sh), (9, 13, 22))
            if src_pil is not None:
                iw, ih = src_pil.size
                scale = min(sw / iw, sh / ih)
                nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
                resized = src_pil.resize((nw, nh), Image.Resampling.BILINEAR)
                bg.paste(resized, ((sw - nw) // 2, (sh - nh) // 2))
            return bg

        sz = max(140, self.engine.facecam_size)
        if src_pil is not None:
            iw, ih = src_pil.size
            min_dim = min(iw, ih)
            left = (iw - min_dim) // 2
            top = (ih - min_dim) // 2
            cropped = src_pil.crop((left, top, left + min_dim, top + min_dim))
            sq_img = cropped.resize((sz, sz), Image.Resampling.BILINEAR)
        else:
            sq_img = Image.new("RGB", (sz, sz), (15, 23, 42))
            d_ph = ImageDraw.Draw(sq_img)
            d_ph.text((sz // 2 - 55, sz // 2 - 16), "Webcam Master", fill=(52, 211, 153))
            d_ph.text((sz // 2 - 62, sz // 2 + 4), "Drag to Move Cam", fill=(148, 163, 184))

        out = Image.new("RGB", (sz, sz), TRANS_RGB)
        mask = Image.new("L", (sz, sz), 0)
        m_draw = ImageDraw.Draw(mask)
        pad = 4

        if shape == "ROUND":
            m_draw.ellipse((pad, pad, sz - pad - 1, sz - pad - 1), fill=255)
            out.paste(sq_img, (0, 0), mask)
            draw = ImageDraw.Draw(out)
            draw.ellipse((pad, pad, sz - pad - 1, sz - pad - 1), outline=(16, 185, 129), width=4)
        else:
            radius = 22
            m_draw.rounded_rectangle((pad, pad, sz - pad - 1, sz - pad - 1), radius=radius, fill=255)
            out.paste(sq_img, (0, 0), mask)
            draw = ImageDraw.Draw(out)
            draw.rounded_rectangle((pad, pad, sz - pad - 1, sz - pad - 1), radius=radius, outline=(56, 189, 248), width=4)

        return out

    # ==================== DEVICE & AUDIO CONTROLS ====================

    def _on_phone_device_selected(self, event=None):
        sel = self.device_combo.get()
        m = re.search(r"\((\d+\.\d+\.\d+\.\d+)\)", sel)
        if m:
            ip = m.group(1)
            self.wifi_ip_var.set(ip)
            self.engine.phone_wifi_ip = ip
            threading.Thread(
                target=lambda: self.engine.connect_to_phone_wireless(ip),
                daemon=True,
            ).start()

    def _select_camera_source(self, src_code: str):
        self.engine.set_camera_source(src_code)
        self._sync_buttons()

    def _on_save_location_selected(self, event=None):
        idx = self.save_loc_combo.current()
        if 0 <= idx < len(self.save_loc_options):
            _, path_or_mode = self.save_loc_options[idx]
            self.engine.set_save_location(path_or_mode)

    def _choose_custom_save_folder(self):
        chosen = filedialog.askdirectory(
            parent=self.root,
            initialdir=self.engine.recordings_dir if os.path.isdir(self.engine.recordings_dir) else r"C:\\",
            title="Select Folder to Save Recorded Videos (C:\\ Drive, Documents, etc.)",
        )
        if chosen:
            chosen = os.path.normpath(chosen)
            self.engine.set_save_location(chosen)
            custom_lbl = f"📁 Custom ({chosen})"
            self.save_loc_options.insert(0, (custom_lbl, chosen))
            self.save_loc_combo["values"] = [lbl for lbl, _ in self.save_loc_options]
            self.save_loc_combo.current(0)

    def _toggle_record_pc_sound(self):
        self.engine.record_pc_sound = not self.engine.record_pc_sound
        state_str = "ON" if self.engine.record_pc_sound else "OFF"
        self.engine.log(f"🔊 Record PC System Sound -> {state_str}")
        self._sync_buttons()

    def _toggle_record_voice_over(self):
        self.engine.record_voice_over = not self.engine.record_voice_over
        state_str = "ON" if self.engine.record_voice_over else "OFF"
        self.engine.log(f"🎤 Voice Over Recording -> {state_str}")
        self._sync_buttons()

    def _on_device_selected(self, event=None):
        idx = self.dev_combo.current()
        if 0 <= idx < len(self.out_devs):
            dev_id, dev_label = self.out_devs[idx]
            self.engine.selected_output_device = dev_id
            self.engine.output_device_name = dev_label
            self.engine.virtual_mic_installed = ("cable input" in dev_label.lower() or "vb-audio" in dev_label.lower())
            self.engine.start_audio_output(self.engine.sample_rate, dev_id)
            self.engine.log(f"Switched Audio Target -> {dev_label}")

    def _select_mode(self, mode: str):
        self.engine.set_app_mode(mode)
        if mode == "MIC_ONLY":
            self._close_floating_facecam()
        elif mode in ("WEBCAM_MIC", "WEBCAM_ONLY") and not self.engine.connected and self.engine.camera_source == "PHONE":
            # If no mobile phone is connected when selecting Cam mode, auto-enable PC Camera so camera works right away!
            self.engine.set_camera_source("PC_0")
        for code, btn in self.mode_buttons.items():
            btn.config(bg="#10B981" if code == self.engine.app_mode else "#334155")
        self._sync_buttons()

    def _connect_wireless_clicked(self):
        ip = self.wifi_ip_var.get().strip()
        if ip:
            self.engine.phone_wifi_ip = ip
        if not self.engine.stream_enabled:
            self.engine.start_stream()
        else:
            def _do_scan_conn():
                if ip:
                    ok, _ = self.engine.connect_to_phone_wireless(ip)
                    if ok:
                        return
                self.engine.scan_and_connect_offline()
            threading.Thread(target=_do_scan_conn, daemon=True).start()

    def _toggle_stream_clicked(self):
        self.engine.toggle_stream()
        self._sync_buttons()

    def _toggle_speaker_monitor(self):
        self.engine.monitor_enabled = not self.engine.monitor_enabled
        if self.engine.monitor_enabled:
            self.engine.windows_default_mic_name = configure_windows_default_mic_for_recorders(restore_physical_mic=False)
            if not self.engine.audio_stream_active:
                self.engine.start_audio_output(self.engine.sample_rate, self.engine.selected_output_device)
            self.engine.log(
                f"🎙️ Live Mic Routing -> ON for Filmora & Recorders (Default Mic: {self.engine.windows_default_mic_name})"
            )
        else:
            self.engine.log("○ Live Mic Routing -> OFF")
        self._sync_buttons()

    def _toggle_mp4_recording(self):
        if self.engine.is_recording_mp4:
            self._close_recording_hud_bar()
            threading.Thread(target=self.engine.stop_mp4_recording, daemon=True).start()
        else:
            custom_fp = None
            if self.engine.ask_save_every_time:
                default_name = f"WebcamMaster_ScreenRecord_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
                custom_fp = filedialog.asksaveasfilename(
                    parent=self.root,
                    initialdir=self.engine.recordings_dir if os.path.isdir(self.engine.recordings_dir) else self.engine.docs_dir,
                    initialfile=default_name,
                    defaultextension=".mp4",
                    filetypes=[("MP4 Video File", "*.mp4"), ("All Files", "*.*")],
                    title="Choose Where to Save Recording (C:\\ Drive, Documents, etc.)",
                )
                if not custom_fp:
                    return
            # Show the compact Stop Recording button ONLY while recording is active (excluded from video!)
            if self.rec_hud_win is None or not self.rec_hud_win.winfo_exists():
                self._open_recording_hud_bar()
            else:
                set_window_exclude_from_capture(self.rec_hud_win, True)
            self.engine.start_mp4_recording(custom_path=custom_fp, screen_mode=True)
        self._sync_buttons()

    def _toggle_wav_recording(self):
        if self.engine.is_recording:
            self.engine.stop_recording()
        else:
            custom_fp = None
            if self.engine.ask_save_every_time:
                default_name = f"WebcamMaster_VoiceOver_{datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"
                custom_fp = filedialog.asksaveasfilename(
                    parent=self.root,
                    initialdir=self.engine.recordings_dir if os.path.isdir(self.engine.recordings_dir) else self.engine.docs_dir,
                    initialfile=default_name,
                    defaultextension=".wav",
                    filetypes=[("WAV Audio File", "*.wav"), ("All Files", "*.*")],
                    title="Choose Where to Save Audio File (C:\\ Drive, Documents, etc.)",
                )
                if not custom_fp:
                    return
            self.engine.start_recording(custom_path=custom_fp)
        self._sync_buttons()

    def _on_vol_change(self, val):
        v = float(val)
        self.engine.gain = v
        self.vol_lbl.config(text=f"Mic Volume: {int(round(v * 100))}%")

    def _open_recordings_folder(self):
        try:
            os.makedirs(self.engine.recordings_dir, exist_ok=True)
            os.startfile(self.engine.recordings_dir)
        except Exception:
            pass

    def _sync_buttons(self):
        if self.engine.stream_enabled:
            self.stream_toggle_btn.config(
                text="● STREAM ACTIVE: ON (Click to Stop)",
                bg="#10B981",
                activebackground="#059669",
            )
        else:
            self.stream_toggle_btn.config(
                text="○ STREAM STOPPED: OFF (Click to Start)",
                bg="#475569",
                activebackground="#334155",
            )

        if hasattr(self, "cam_src_btns"):
            cam_off = (self.engine.app_mode == "MIC_ONLY" or self.engine.camera_source == "OFF")
            is_pc = (not cam_off) and self.engine.camera_source.startswith("PC_")
            is_phone = (not cam_off) and (self.engine.camera_source == "PHONE")
            if "PHONE" in self.cam_src_btns:
                self.cam_src_btns["PHONE"].config(bg="#10B981" if is_phone else "#334155")
            if "PC_0" in self.cam_src_btns:
                self.cam_src_btns["PC_0"].config(bg="#10B981" if is_pc else "#334155")
            if "OFF" in self.cam_src_btns:
                self.cam_src_btns["OFF"].config(bg="#EF4444" if cam_off else "#334155")

        if self.engine.monitor_enabled:
            self.spk_toggle_btn.config(
                text="● Live Mic for Filmora & Recorders: ON (Audio+Video in 1 File)",
                bg="#10B981",
            )
        else:
            self.spk_toggle_btn.config(
                text="○ Live Mic for Filmora & Recorders: OFF (Click to Enable)",
                bg="#334155",
            )

        if self.engine.record_pc_sound:
            self.pc_sound_btn.config(text="🔊 Record PC Sound: ON", bg="#0D9488")
        else:
            self.pc_sound_btn.config(text="🔇 Record PC Sound: OFF", bg="#475569")

        if self.engine.record_voice_over:
            self.voice_over_btn.config(text="🎤 Voice Over: ON", bg="#0D9488")
        else:
            self.voice_over_btn.config(text="🔇 Voice Over: OFF", bg="#475569")

        if self.engine.is_recording_mp4:
            elapsed = int(max(0, time.time() - self.engine.mp4_start_time))
            mm, ss = divmod(elapsed, 60)
            self.top_screen_rec_btn.config(
                text=f"■ Stop Screen Record ({mm:02d}:{ss:02d})",
                bg="#EF4444",
                activebackground="#DC2626",
            )
            self.mp4_toggle_btn.config(
                text=f"■ Stop Screen Record ({mm:02d}:{ss:02d}) — Save Media File (.MP4)",
                bg="#EF4444",
            )
            if self.hud_rec_btn is not None and self.hud_rec_btn.winfo_exists():
                self.hud_rec_btn.config(text=f"■ Stop Rec ({mm:02d}:{ss:02d})", bg="#EF4444")
        else:
            self.top_screen_rec_btn.config(
                text="● Start Screen Record",
                bg="#2563EB",
                activebackground="#1D4ED8",
            )
            self.mp4_toggle_btn.config(
                text="● Start Screen Record (PC Screen + PC Sound + Voice Over -> .MP4)",
                bg="#2563EB",
            )
            if self.hud_rec_btn is not None and self.hud_rec_btn.winfo_exists():
                self.hud_rec_btn.config(text="● Start Recording", bg="#2563EB")

        if self.engine.is_recording:
            self.wav_toggle_btn.config(text="■ Stop Voice (.WAV)", bg="#EF4444")
        else:
            self.wav_toggle_btn.config(text="● Record Voice (.WAV)", bg="#0D9488")

        for code, btn in self.mode_buttons.items():
            btn.config(bg="#10B981" if code == self.engine.app_mode else "#334155")

        if self.engine.last_saved_media_file:
            bname = os.path.basename(self.engine.last_saved_media_file)
            if len(bname) > 34:
                bname = bname[:15] + "..." + bname[-15:]
            self.media_file_lbl.config(text=f"3. Saved Media: {bname}", fg="#34D399")
        else:
            self.media_file_lbl.config(text="3. Saved Media: None yet", fg="#93C5FD")

        self._sync_facecam_buttons()

    def _update_ui_loop(self):
        now = time.time()
        if now - self._last_blink_toggle >= 0.45:
            self._last_blink_toggle = now
            self._blink_state = not self._blink_state

        # Auto-sync compact Stop Recording Pill with active screen recording state
        if self.engine.is_recording_mp4:
            if self.rec_hud_win is None or not self.rec_hud_win.winfo_exists():
                self._open_recording_hud_bar()
        else:
            if self.rec_hud_win is not None:
                self._close_recording_hud_bar()

        self._sync_buttons()

        items = self.engine.get_device_dropdown_items()
        if list(self.device_combo["values"]) != items:
            self.device_combo["values"] = items
            if items:
                self.device_combo.current(0)
        if self.engine.phone_wifi_ip and not self.wifi_ip_var.get().strip():
            self.wifi_ip_var.set(self.engine.phone_wifi_ip)

        blink_dot = "●" if self._blink_state else "○"
        live_dot = "●" if self._blink_state else "○"

        # Update Floating HUD Blinking Dot
        if self.hud_dot_lbl is not None and self.hud_dot_lbl.winfo_exists():
            if self.engine.is_recording_mp4:
                self.hud_dot_lbl.config(
                    text=f"{blink_dot} REC",
                    fg="#EF4444" if self._blink_state else "#F87171",
                )
            elif self.engine.connected and self.engine.stream_enabled:
                self.hud_dot_lbl.config(
                    text=f"{live_dot} PHONE LIVE",
                    fg="#34D399" if self._blink_state else "#A7F3D0",
                )
            else:
                self.hud_dot_lbl.config(text="○ STANDBY", fg="#94A3B8")

        if not self.engine.stream_enabled:
            self.status_badge.config(text=" ○ STREAM STOPPED (OFF) ", fg="#CBD5E1", bg="#334155")
            self.device_card.config(highlightbackground="#475569")
            self.device_title_lbl.config(
                text="○ 1. Connected Phone: Stream Paused (OFF)",
                fg="#94A3B8",
            )
            self.conn_info_lbl.config(
                text="Click the button above to re-enable streaming.",
                fg="#64748B",
            )
        elif self.engine.connected:
            dev_disp = clean_device_name(self.engine.device_name)
            short_dev = dev_disp if len(dev_disp) <= 22 else dev_disp[:20] + ".."
            self.status_badge.config(
                text=f" {blink_dot} LIVE: {short_dev.upper()} ",
                fg="#10B981" if self._blink_state else "#6EE7B7",
                bg="#064E3B",
            )
            self.device_card.config(highlightbackground="#10B981")
            self.device_title_lbl.config(
                text=f"{live_dot} 1. Connected: {dev_disp}",
                fg="#34D399",
            )
            self.conn_info_lbl.config(
                text=f"{self.engine.connection_type}  •  IP: {self.engine.client_addr}",
                fg="#38BDF8",
            )
        elif self.engine.standby_phone_detected:
            st_lbl = self.engine.standby_phone_label
            short_st = st_lbl if len(st_lbl) <= 24 else st_lbl[:22] + ".."
            self.status_badge.config(
                text=f" ● PHONE READY: {short_st.upper()} ",
                fg="#FDE68A",
                bg="#78350F",
            )
            self.device_card.config(highlightbackground="#F59E0B")
            self.device_title_lbl.config(
                text=f"● 1. Detected: {st_lbl}",
                fg="#FBBF24",
            )
            self.conn_info_lbl.config(
                text="Open 'Webcam Master' on phone (or tap Start) to connect!",
                fg="#FDE68A",
            )
        else:
            self.status_badge.config(
                text=" ● WAITING FOR PHONE (OFFLINE WIRELESS) ",
                fg="#FDE68A",
                bg="#78350F",
            )
            self.device_card.config(highlightbackground="#334155")
            self.device_title_lbl.config(
                text="○ 1. Connected Phone: Waiting for Phone...",
                fg="#94A3B8",
            )
            self.conn_info_lbl.config(
                text="Offline Wireless: Wi-Fi, Phone Hotspot, or PC Hotspot",
                fg="#38BDF8",
            )

        self.net_mode_lbl.config(
            text=f"PC IP: {self.engine.local_ip} ({self.engine.pc_hostname}) • No Internet Needed"
        )
        self.db_lbl.config(
            text=f"{self.engine.current_db:5.1f} dB"
        )
        self.cam_info_lbl.config(
            text=f"{self.engine.video_resolution} @ {self.engine.video_fps:.0f} FPS"
        )

        # Render Camera Frame in Main Window & Movable Shaped Floating Overlay (ONLY when Camera is ON and NOT in Mic Only!)
        cam_is_off = (self.engine.app_mode == "MIC_ONLY" or self.engine.camera_source == "OFF")
        if cam_is_off and self.clean_feed_win is not None:
            self._close_floating_facecam()

        with self.engine.video_lock:
            pil_img = None if cam_is_off else self.engine.latest_pil_image

        using_pc_cam = (not cam_is_off) and self.engine.camera_source.startswith("PC_")
        can_show_video = (
            self.engine.stream_enabled
            and not cam_is_off
            and pil_img is not None
            and (using_pc_cam or self.engine.connected)
        )

        if self.clean_feed_win is not None and self.clean_feed_win.winfo_exists() and self.clean_feed_canvas is not None:
            shaped_pil = self._render_shaped_facecam_image(pil_img if can_show_video else None)
            self._tk_clean_img = ImageTk.PhotoImage(shaped_pil)
            self.clean_feed_canvas.delete("all")
            self.clean_feed_canvas.create_image(0, 0, anchor="nw", image=self._tk_clean_img)

        if can_show_video:
            cw = max(240, self.video_container.winfo_width())
            ch = max(180, self.video_container.winfo_height())
            iw, ih = pil_img.size
            scale = min(cw / iw, ch / ih)
            nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
            resized = pil_img.resize((nw, nh), Image.Resampling.BILINEAR)
            self._tk_cam_img = ImageTk.PhotoImage(resized)
            self.video_label.config(image=self._tk_cam_img, text="")
        else:
            self._tk_cam_img = None
            if not self.engine.stream_enabled:
                msg = "○ Stream Stopped — Click the Stream Toggle Button on the Right"
            elif cam_is_off:
                def_mic = getattr(self.engine, "windows_default_mic_name", "Stereo Mix (Realtek(R) Audio)")
                msg = (
                    "🎙️ MIC ONLY — LIVE MICROPHONE FOR FILMORA & ALL RECORDERS\n\n"
                    "• Camera is completely OFF (Mobile Camera & PC Webcam stopped).\n"
                    f"• Windows Default Mic is auto-routed to your Phone Mic ({def_mic}).\n"
                    "• Record directly in Filmora or any Screen Recorder — Voice + Video save in the SAME file!\n"
                    "• To turn the camera ON anytime, click '📱 Mobile Cam' or '💻 PC Webcam' above."
                )
            elif self.engine.standby_phone_detected:
                msg = (
                    f"📱 Phone Detected on Offline Wireless: {self.engine.standby_phone_label}\n\n"
                    "• Open 'Webcam Master' on your phone for Mobile Camera,\n"
                    "  OR click '💻 PC Camera (Webcam)' above to use your PC Camera right now!"
                )
            else:
                msg = (
                    "Choose Your Camera Above:\n\n"
                    "• Click '📱 Mobile Camera' to use your connected phone's HD camera\n"
                    "• Click '💻 PC Camera (Webcam)' to use your PC's camera (even without phone connected!)\n"
                    "• Click '⭕ Round', '⬜ Square', or '🖥️ Full Screen' when needed!"
                )
            self.video_label.config(image="", text=msg)

        # Draw Waveform
        self.wave_canvas.delete("all")
        w = max(100, self.wave_canvas.winfo_width())
        h = max(40, self.wave_canvas.winfo_height())
        mid = h / 2.0
        with self.engine.vis_lock:
            buf = self.engine.vis_buffer.copy()
        n = len(buf)
        step = w / float(n)
        pts = []
        for i in range(0, n, 2):
            x = i * step
            y = mid - (buf[i] * mid * 1.9)
            pts.extend([x, max(2, min(h - 2, y))])
        if len(pts) >= 4:
            color = "#22D3EE" if (self.engine.connected and self.engine.stream_enabled) else "#475569"
            self.wave_canvas.create_line(*pts, fill=color, width=2)

        # Draw VU Meter
        self.vu_canvas.delete("all")
        vw = max(100, self.vu_canvas.winfo_width())
        vh = max(10, self.vu_canvas.winfo_height())
        norm = max(0.0, min(1.0, (self.engine.current_db + 60.0) / 60.0))
        bar_color = "#10B981"
        if self.engine.current_db > -6.0:
            bar_color = "#EF4444"
        elif self.engine.current_db > -18.0:
            bar_color = "#F59E0B"
        self.vu_canvas.create_rectangle(0, 0, vw * norm, vh, fill=bar_color, width=0)

        # Update Logs
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.insert("end", "\n".join(self.engine.logs[-4:]))
        self.log_text.config(state="disabled")

        self.root.after(60, self._update_ui_loop)


def main():
    if os.name == "nt":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("WebcamMaster.Studio.v3")
        except Exception:
            pass
    engine = MotoMicEngine()
    engine.start_servers()
    root = tk.Tk()
    app = MotoMicApp(root, engine)
    root.protocol("WM_DELETE_WINDOW", app._exit_application)
    root.mainloop()


if __name__ == "__main__":
    main()
