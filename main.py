import base64
import ctypes
from ctypes import wintypes
import os
import threading
import json
import subprocess
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from urllib.parse import quote
import webbrowser

import requests
from PIL import Image, ImageOps, ImageTk


WIDGET_OPACITY = 210
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
LWA_ALPHA = 0x00000002


DEFAULT_PLACE_ID = "8554990808"
PLACE_SETTINGS_KEY = "__settings__"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ACCOUNTS_FILE = os.path.join(BASE_DIR, "accounts.json")
INTRO_IMAGE_FILE = os.path.join(BASE_DIR, "intro.png")
DISCORD_INVITE = "https://discord.gg/af5XmBDfHV"


class DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte))
    ]


def protect_cookie(cookie):
    if os.name != "nt":
        raise OSError("Cookie şifreleme yalnızca Windows'ta kullanılabilir.")

    raw_cookie = cookie.encode("utf-8")
    input_buffer = ctypes.create_string_buffer(raw_cookie)
    input_blob = DataBlob(
        len(raw_cookie),
        ctypes.cast(input_buffer, ctypes.POINTER(ctypes.c_byte))
    )
    output_blob = DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(DataBlob),
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(DataBlob)
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    if not crypt32.CryptProtectData(
        ctypes.byref(input_blob),
        "Roblox launcher account cookie",
        None,
        None,
        None,
        0,
        ctypes.byref(output_blob)
    ):
        raise ctypes.WinError(ctypes.get_last_error())

    try:
        encrypted = ctypes.string_at(
            output_blob.pbData,
            output_blob.cbData
        )
        return base64.b64encode(encrypted).decode("ascii")
    finally:
        kernel32.LocalFree(output_blob.pbData)


def unprotect_cookie(encrypted_cookie):
    if os.name != "nt":
        raise OSError("Cookie şifre çözme yalnızca Windows'ta kullanılabilir.")

    encrypted = base64.b64decode(encrypted_cookie, validate=True)
    input_buffer = ctypes.create_string_buffer(encrypted)
    input_blob = DataBlob(
        len(encrypted),
        ctypes.cast(input_buffer, ctypes.POINTER(ctypes.c_byte))
    )
    output_blob = DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(DataBlob)
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    if not crypt32.CryptUnprotectData(
        ctypes.byref(input_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(output_blob)
    ):
        raise ctypes.WinError(ctypes.get_last_error())

    try:
        return ctypes.string_at(
            output_blob.pbData,
            output_blob.cbData
        ).decode("utf-8")
    finally:
        kernel32.LocalFree(output_blob.pbData)


# ── Multi-instance mutex ──────────────────────────────────────────────────────
_k32          = ctypes.WinDLL("kernel32", use_last_error=True)
_mutex_handle = None
_mutex_lock   = threading.Lock()

def hold_mutex() -> bool:
    """
    Claim the ROBLOX_singletonEvent mutex so Roblox does not block
    additional instances from starting.
    """
    global _mutex_handle
    with _mutex_lock:
        if _mutex_handle:
            return True
        h = _k32.CreateMutexW(None, True, "ROBLOX_singletonEvent")
        if h:
            _mutex_handle = h
            threading.Thread(target=lambda: time.sleep(9e9), daemon=True).start()
            return True
    return False

def find_player():
    versions = os.path.join(
        os.environ.get("LOCALAPPDATA", ""),
        "Roblox",
        "Versions"
    )

    if not os.path.isdir(versions):
        return ""

    paths = [
        os.path.join(
            versions,
            folder,
            "RobloxPlayerBeta.exe"
        )
        for folder in os.listdir(versions)
    ]

    paths = [
        path
        for path in paths
        if os.path.isfile(path)
    ]

    return (
        max(paths, key=os.path.getmtime)
        if paths
        else ""
    )


def get_auth_ticket(cookie: str):
    try:
        session = requests.Session()

        session.cookies.set(
            ".ROBLOSECURITY",
            cookie.strip(),
            domain=".roblox.com"
        )

        base_headers = {
            "User-Agent": "Roblox/WinInet",
            "Referer": "https://www.roblox.com/",
            "Origin": "https://www.roblox.com",
            "Content-Type": "application/json",
            "Content-Length": "0"
        }

        print("[Auth] CSRF token alınıyor...")

        r1 = session.post(
            "https://auth.roblox.com/v1/authentication-ticket",
            headers=base_headers,
            timeout=15
        )

        print(
            f"[Auth] Step1 status: {r1.status_code}"
        )

        csrf = (
            r1.headers.get("x-csrf-token")
            or r1.headers.get("X-CSRF-TOKEN", "")
        )

        if not csrf:
            print(
                "[Auth] CSRF token alınamadı."
            )

            print(
                f"[Auth] Response: {r1.text[:200]}"
            )

            return None

        print(
            f"[Auth] CSRF OK: {csrf[:20]}..."
        )

        r2 = session.post(
            "https://auth.roblox.com/v1/authentication-ticket",
            headers={
                **base_headers,
                "x-csrf-token": csrf
            },
            timeout=15
        )

        print(
            f"[Auth] Step2 status: {r2.status_code}"
        )

        ticket = r2.headers.get(
            "rbx-authentication-ticket",
            ""
        )

        if ticket:
            print(
                f"[Auth] Ticket OK: {ticket[:20]}..."
            )

            return ticket

        print(
            "[Auth] Ticket alınamadı."
        )

        print(
            f"[Auth] Body: {r2.text[:200]}"
        )

        return None

    except Exception as error:
        print(
            f"[Auth] Exception: {error}"
        )

        return None


def build_uri(session_info, place_id):
    launcher_url = (
        "https://assetgame.roblox.com/game/PlaceLauncher.ashx"
        f"?placeId={quote(place_id, safe='')}"
        "&request=RequestGame"
    )

    return (
        "roblox-player:1+launchmode:play"
        f"+gameinfo:{quote(session_info, safe='-._~')}"
        f"+placelauncherurl:{quote(launcher_url, safe='')}"
    )


class Launcher:

    def __init__(self, root):

        self.root = root

        self.accounts = {}
        self.encrypted_cookies = {}
        self.places = {}

        root.title(
            "Roblox Multi-Account Launcher"
        )

        root.geometry(
            "760x560"
        )

        root.minsize(
            650,
            500
        )

        self.background_source = Image.open(
            os.path.join(BASE_DIR, "download.jpg")
        )
        self.background_image = None
        self.background_size = None
        self.background_resize_job = None

        frame = ttk.Frame(
            root,
            padding=16
        )

        frame.pack(
            fill="both",
            expand=True
        )

        self.background = tk.Label(
            frame,
            bd=0,
            bg="#f0f0f0"
        )
        self.background.place(
            relwidth=1,
            relheight=1
        )
        frame.bind(
            "<Configure>",
            self.resize_background
        )
        self.resize_background()

        frame.columnconfigure(
            1,
            weight=1
        )

        frame.rowconfigure(
            5,
            weight=1
        )

        ttk.Label(
            frame,
            text="RobloxPlayerBeta.exe"
        ).grid(
            row=0,
            column=0,
            sticky="w",
            padx=(0, 8)
        )

        self.exe = tk.StringVar(
            value=find_player()
        )

        ttk.Entry(
            frame,
            textvariable=self.exe
        ).grid(
            row=0,
            column=1,
            sticky="ew"
        )

        ttk.Button(
            frame,
            text="Browse...",
            command=self.browse
        ).grid(
            row=0,
            column=2,
            padx=(8, 0)
        )

        ttk.Button(
            frame,
            text="Save path",
            command=self.save_executable
        ).grid(
            row=0,
            column=3,
            padx=(8, 0)
        )

        ttk.Label(
            frame,
            text="Place ID"
        ).grid(
            row=1,
            column=0,
            sticky="w",
            pady=8
        )

        self.place_id = tk.StringVar(
            value=DEFAULT_PLACE_ID
        )

        ttk.Entry(
            frame,
            textvariable=self.place_id
        ).grid(
            row=1,
            column=1,
            sticky="ew",
            pady=8
        )

        ttk.Button(
            frame,
            text="Save Place",
            command=self.save_place
        ).grid(
            row=1,
            column=2,
            padx=(8, 0),
            pady=8
        )

        ttk.Label(
            frame,
            text="Saved Place"
        ).grid(
            row=2,
            column=0,
            sticky="w",
            padx=(0, 8),
            pady=(0, 8)
        )

        self.saved_place = tk.StringVar()

        self.place_selector = ttk.Combobox(
            frame,
            textvariable=self.saved_place,
            state="readonly"
        )

        self.place_selector.grid(
            row=2,
            column=1,
            sticky="ew",
            pady=(0, 8)
        )

        self.place_selector.bind(
            "<<ComboboxSelected>>",
            self.select_place
        )

        ttk.Button(
            frame,
            text="Delete Selected",
            command=self.remove_place
        ).grid(
            row=2,
            column=2,
            padx=(8, 0),
            pady=(0, 8)
        )

        form = ttk.LabelFrame(
            frame,
            text="Account",
            padding=10
        )

        form.grid(
            row=3,
            column=0,
            columnspan=4,
            sticky="ew",
            pady=(2, 10)
        )

        form.columnconfigure(
            1,
            weight=1
        )

        ttk.Label(
            form,
            text="Account Name"
        ).grid(
            row=0,
            column=0,
            sticky="w",
            padx=(0, 8)
        )

        self.name = tk.StringVar()

        ttk.Entry(
            form,
            textvariable=self.name
        ).grid(
            row=0,
            column=1,
            sticky="ew",
            pady=(0, 8)
        )

        ttk.Label(
            form,
            text="Roblox Cookie"
        ).grid(
            row=1,
            column=0,
            sticky="w",
            padx=(0, 8)
        )

        self.cookie = tk.StringVar()

        ttk.Entry(
            form,
            textvariable=self.cookie,
            show="*"
        ).grid(
            row=1,
            column=1,
            sticky="ew",
            pady=(0, 8)
        )

        ttk.Button(
            form,
            text="Add / Update",
            command=self.save
        ).grid(
            row=2,
            column=1,
            sticky="e"
        )

        ttk.Label(
            frame,
            text="Accounts"
        ).grid(
            row=4,
            column=0,
            columnspan=4,
            sticky="w",
            pady=(0, 5)
        )

        self.listbox = tk.Listbox(
            frame,
            exportselection=False
        )

        self.listbox.grid(
            row=5,
            column=0,
            columnspan=4,
            sticky="nsew"
        )

        self.listbox.bind(
            "<<ListboxSelect>>",
            self.select
        )

        actions = ttk.Frame(
            frame
        )

        actions.grid(
            row=6,
            column=0,
            columnspan=4,
            sticky="ew",
            pady=(10, 0)
        )

        ttk.Button(
            actions,
            text="Delete Account",
            command=self.remove
        ).pack(
            side="left"
        )

        ttk.Button(
            actions,
            text="Clear Selection",
            command=self.clear_selection
        ).pack(
            side="left",
            padx=(8, 0)
        )

        ttk.Button(
            actions,
            text="Launch Selected",
            command=self.launch_selected
        ).pack(
            side="right",
            padx=(8, 0)
        )

        ttk.Button(
            actions,
            text="Launch All",
            command=self.launch_all
        ).pack(
            side="right"
        )

        self.load_accounts()
        self.apply_widget_opacity()

    def apply_widget_opacity(self):
        if os.name != "nt":
            raise OSError("Arayüz saydamlığı yalnızca Windows'ta kullanılabilir.")

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetWindowLongW.restype = ctypes.c_long
        user32.SetWindowLongW.argtypes = [
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_long
        ]
        user32.SetWindowLongW.restype = ctypes.c_long
        user32.SetLayeredWindowAttributes.argtypes = [
            wintypes.HWND,
            wintypes.DWORD,
            wintypes.BYTE,
            wintypes.DWORD
        ]
        user32.SetLayeredWindowAttributes.restype = wintypes.BOOL

        def apply_to_children(parent):
            for widget in parent.winfo_children():
                if widget is not self.background:
                    hwnd = widget.winfo_id()
                    exstyle = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                    if not exstyle & WS_EX_LAYERED:
                        user32.SetWindowLongW(
                            hwnd,
                            GWL_EXSTYLE,
                            exstyle | WS_EX_LAYERED
                        )

                    if not user32.SetLayeredWindowAttributes(
                        hwnd,
                        0,
                        WIDGET_OPACITY,
                        LWA_ALPHA
                    ):
                        raise ctypes.WinError(ctypes.get_last_error())

                apply_to_children(widget)

        apply_to_children(self.root)

    def resize_background(self, _event=None):
        if self.background_resize_job is not None:
            self.root.after_cancel(self.background_resize_job)

        self.background_resize_job = self.root.after(
            40,
            self._render_background
        )

    def _render_background(self):
        self.background_resize_job = None
        width = self.background.winfo_width()
        height = self.background.winfo_height()
        if width <= 1 or height <= 1:
            return

        scale = min(
            width / self.background_source.width,
            height / self.background_source.height
        )
        size = (
            max(1, round(self.background_source.width * scale)),
            max(1, round(self.background_source.height * scale))
        )
        if size == self.background_size:
            return

        resized = self.background_source.resize(
            size,
            Image.Resampling.LANCZOS
        )
        self.background_image = ImageTk.PhotoImage(resized)
        self.background.configure(
            image=self.background_image
        )
        self.background_size = size

    def load_accounts(self):

        if not os.path.isfile(
            ACCOUNTS_FILE
        ):
            return

        try:

            with open(
                ACCOUNTS_FILE,
                "r",
                encoding="utf-8"
            ) as file:

                data = json.load(file)

            if isinstance(data, dict):

                settings = data.get(PLACE_SETTINGS_KEY, {})
                if not isinstance(settings, dict):
                    settings = {}

                saved_place_id = settings.get("place_id", "")
                if isinstance(saved_place_id, str) and saved_place_id.isdigit():
                    self.place_id.set(saved_place_id)

                executable = settings.get("exe_path", "")
                if isinstance(executable, str) and executable:
                    self.exe.set(executable)

                places = settings.get("places", {})
                if isinstance(places, dict):
                    self.places = {
                        name: place_id
                        for name, place_id in places.items()
                        if isinstance(name, str)
                        and isinstance(place_id, str)
                        and place_id.isdigit()
                    }

                self.place_selector.configure(
                    values=tuple(self.places)
                )

                account_names = []
                cookie_error = False

                for name, account in data.items():

                    if name == PLACE_SETTINGS_KEY or not isinstance(account, dict):
                        continue

                    account_names.append(name)
                    encrypted_cookie = account.get("cookie_encrypted", "")
                    self.encrypted_cookies[name] = encrypted_cookie

                    try:
                        self.accounts[name] = (
                            unprotect_cookie(encrypted_cookie)
                            if encrypted_cookie
                            else ""
                        )
                    except (OSError, ValueError, ctypes.ArgumentError):
                        self.accounts[name] = ""
                        cookie_error = True

                    self.listbox.insert(tk.END, name)

                if not saved_place_id and account_names:
                    self.place_id.set(
                        data[account_names[0]].get(
                            "place_id",
                            DEFAULT_PLACE_ID
                        )
                    )

                if cookie_error:
                    messagebox.showwarning(
                        "Cookie açılamadı",
                        "Bazı cookie kayıtları bu Windows kullanıcısıyla çözülemedi. "
                        "Çalıştırmak için cookie'yi tekrar girip hesabı güncelle."
                    )

        except Exception as error:

            messagebox.showerror(
                "Kayıt yüklenemedi",
                f"accounts.json okunamadı:\n\n{error}"
            )

    def save_accounts(self):

        try:

            data = {
                PLACE_SETTINGS_KEY: {
                    "place_id": self.place_id.get().strip(),
                    "exe_path": self.exe.get().strip(),
                    "places": self.places.copy()
                }
            }

            for name, cookie in self.accounts.items():

                encrypted_cookie = (
                    protect_cookie(cookie)
                    if cookie
                    else self.encrypted_cookies.get(name, "")
                )

                if encrypted_cookie:
                    self.encrypted_cookies[name] = encrypted_cookie

                data[name] = {
                    "place_id": self.place_id.get().strip(),
                    "cookie_encrypted": encrypted_cookie
                }

            with open(
                ACCOUNTS_FILE,
                "w",
                encoding="utf-8"
            ) as file:

                json.dump(
                    data,
                    file,
                    ensure_ascii=False,
                    indent=4
                )

            return True

        except Exception as error:

            messagebox.showerror(
                "Kayıt edilemedi",
                f"accounts.json yazılamadı:\n\n{error}"
            )

            return False

    def save_place(self):

        place_id = self.place_id.get().strip()

        if not place_id.isdigit():

            messagebox.showerror(
                "Geçersiz Place ID",
                "Place ID yalnızca rakamlardan oluşmalı."
            )

            return

        name = simpledialog.askstring(
            "Place kaydet",
            "Bu Place için bir ad gir:",
            parent=self.root
        )

        if not name or not name.strip():
            return

        name = name.strip()
        self.places[name] = place_id
        self.saved_place.set(name)
        self.place_selector.configure(
            values=tuple(self.places)
        )

        if self.save_accounts():

            messagebox.showinfo(
                "Place kaydedildi",
                f"{name} kaydedildi."
            )

    def select_place(self, _event=None):

        place_id = self.places.get(
            self.saved_place.get()
        )

        if place_id:
            self.place_id.set(place_id)
            self.save_accounts()

    def remove_place(self):

        name = self.saved_place.get()
        if not name or name not in self.places:
            return

        del self.places[name]
        self.saved_place.set("")
        self.place_selector.configure(
            values=tuple(self.places)
        )
        self.save_accounts()

    def save_executable(self):

        executable = self.exe.get().strip()
        if not executable or not os.path.isfile(executable):
            messagebox.showerror(
                "Geçersiz dosya",
                "Geçerli bir RobloxPlayerBeta.exe yolu seç."
            )
            return

        if self.save_accounts():
            messagebox.showinfo(
                "Yol kaydedildi",
                "RobloxPlayerBeta.exe yolu kaydedildi."
            )

    def browse(self):

        path = filedialog.askopenfilename(
            title="RobloxPlayerBeta.exe seç",
            filetypes=[
                (
                    "Roblox Player",
                    "RobloxPlayerBeta.exe"
                ),
                (
                    "Tüm dosyalar",
                    "*.*"
                )
            ]
        )

        if path:

            self.exe.set(
                path
            )
            self.save_accounts()

    def save(self):

        name = self.name.get().strip()

        cookie = self.cookie.get().strip()

        place_id = self.place_id.get().strip()

        if not name:

            messagebox.showerror(
                "Eksik bilgi",
                "Hesap adı gerekli."
            )

            return

        if not cookie:

            messagebox.showerror(
                "Eksik bilgi",
                "Roblox Cookie gerekli."
            )

            return

        if not place_id.isdigit():

            messagebox.showerror(
                "Geçersiz Place ID",
                "Place ID yalnızca rakamlardan oluşmalı."
            )

            return

        selected = self.listbox.curselection()

        if selected:

            old_name = self.listbox.get(
                selected[0]
            )

            if old_name != name:

                if old_name in self.accounts:

                    del self.accounts[
                        old_name
                    ]

                self.listbox.delete(
                    selected[0]
                )

        self.accounts[name] = cookie

        names = list(
            self.listbox.get(
                0,
                tk.END
            )
        )

        if name not in names:

            self.listbox.insert(
                tk.END,
                name
            )

        index = list(
            self.listbox.get(
                0,
                tk.END
            )
        ).index(name)

        self.listbox.selection_clear(
            0,
            tk.END
        )

        self.listbox.selection_set(
            index
        )

        self.cookie.set("")

        self.save_accounts()

    def select(self, _event=None):

        selected = self.listbox.curselection()

        if not selected:
            return

        name = self.listbox.get(
            selected[0]
        )

        self.name.set(
            name
        )

        cookie = self.accounts.get(
            name,
            ""
        )

        self.cookie.set(
            cookie
        )

    def clear_selection(self):

        self.listbox.selection_clear(
            0,
            tk.END
        )

        self.name.set("")
        self.cookie.set("")

    def remove(self):

        selected = self.listbox.curselection()

        if not selected:
            return

        name = self.listbox.get(
            selected[0]
        )

        if name in self.accounts:

            del self.accounts[name]

        self.encrypted_cookies.pop(name, None)

        self.listbox.delete(
            selected[0]
        )

        self.name.set("")
        self.cookie.set("")

        self.save_accounts()

    def launch(self, name):

        executable = self.exe.get().strip()

        place_id = self.place_id.get().strip()

        if (
            not executable
            or not os.path.isfile(executable)
        ):

            raise ValueError(
                "RobloxPlayerBeta.exe bulunamadı; "
                "dosya yolunu kontrol et."
            )

        if not place_id.isdigit():

            raise ValueError(
                "Place ID yalnızca rakamlardan oluşmalı."
            )

        cookie = self.accounts.get(
            name,
            ""
        )

        if not cookie:

            raise ValueError(
                f"{name} hesabının Cookie bilgisi "
                "bu oturumda bulunmuyor. "
                "Cookie'yi tekrar girip "
                "Ekle / güncelle yap."
            )

        print()
        print("=" * 50)
        print(
            f"[Launcher] Hesap: {name}"
        )
        print(
            "[Launcher] Authentication ticket alınıyor..."
        )
        print("=" * 50)

        ticket = get_auth_ticket(
            cookie
        )

        if not ticket:

            raise ValueError(
                f"{name} hesabı için "
                "authentication ticket alınamadı."
            )

        uri = build_uri(
            ticket,
            place_id
        )

        print(
            "[Launcher] Roblox başlatılıyor..."
        )

        subprocess.Popen(
            [
                executable,
                "--browser",
                uri
            ]
        )

    def launch_selected(self):

        selected = self.listbox.curselection()

        if not selected:

            messagebox.showerror(
                "Hesap seçilmedi",
                "Önce bir hesap seç."
            )

            return

        self.launch_with_errors(
            [
                self.listbox.get(
                    selected[0]
                )
            ]
        )

    def launch_all(self):

        if not self.accounts:

            messagebox.showerror(
                "Hesap yok",
                "Önce en az bir hesap ekle."
            )

            return

        self.launch_with_errors(
            list(self.accounts)
        )

    def launch_with_errors(
        self,
        names
    ):

        try:

            for name in names:

                self.launch(
                    name
                )

        except (
            OSError,
            ValueError
        ) as error:

            messagebox.showerror(
                "Başlatılamadı",
                str(error)
            )


class IntroScreen:

    def __init__(self, root, on_start):
        self.root = root
        self.on_start = on_start
        self.image_source = Image.open(INTRO_IMAGE_FILE).convert("RGB")
        self.image = None
        self.image_size = None
        self.resize_job = None

        self.window = tk.Toplevel(root)
        self.window.title("Roblox Multi-Account Launcher")
        self.window.geometry("960x540")
        self.window.minsize(640, 360)
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        self.image_label = tk.Label(
            self.window,
            bd=0,
            bg="black"
        )
        self.image_label.place(
            x=0,
            y=0,
            relwidth=1,
            relheight=1
        )

        self.start_button = tk.Button(
            self.window,
            text="Start",
            command=self.start,
            font=("Segoe UI", 13, "bold"),
            bg="#f2f2f2",
            fg="#111111",
            activebackground="#d9d9d9",
            relief="flat",
            padx=24,
            pady=8,
            cursor="hand2"
        )
        self.start_button.place(
            relx=0.97,
            rely=0.95,
            anchor="se"
        )

        self.window.bind("<Configure>", self.resize_image)
        self.window.after_idle(self.resize_image)

    def resize_image(self, _event=None):
        if self.resize_job is not None:
            self.window.after_cancel(self.resize_job)

        self.resize_job = self.window.after(
            40,
            self._render_image
        )

    def _render_image(self):
        self.resize_job = None
        width = self.image_label.winfo_width()
        height = self.image_label.winfo_height()
        if width <= 1 or height <= 1:
            return

        size = (width, height)
        if size == self.image_size:
            return

        resized = ImageOps.fit(
            self.image_source,
            size,
            method=Image.Resampling.LANCZOS
        )
        self.image = ImageTk.PhotoImage(resized, master=self.window)
        self.image_label.configure(image=self.image)
        self.image_size = size

    def start(self):
        self.window.destroy()
        self.on_start()

    def close(self):
        self.window.destroy()
        self.root.destroy()


if __name__ == "__main__":

    window = tk.Tk()
    window.withdraw()

    hold_mutex()

    def start_launcher():
        Launcher(window)
        window.deiconify()

    intro = IntroScreen(window, start_launcher)

    def open_discord_invite():
        try:
            if not webbrowser.open(DISCORD_INVITE, new=2):
                raise webbrowser.Error("Varsayılan tarayıcı bağlantıyı açamadı.")
        except (webbrowser.Error, OSError) as error:
            messagebox.showwarning(
                "Discord bağlantısı açılamadı",
                f"Sunucu bağlantısı tarayıcıda açılamadı:\n{error}",
                parent=intro.window
            )

    window.after(100, open_discord_invite)

    window.mainloop()