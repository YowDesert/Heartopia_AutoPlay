#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
AutoPlayQt.py (PySide6 / Qt6)
讀取 MIDI 檔，依照鍵盤對照表自動按鍵（現代化 UI + 資料夾清單 + 播放清單 + 循環/下一首）。

修復項目:
1. 播放清單可滾動且顯示編號
2. MIDI 和設定區域放大
3. 修復 QThread 錯誤
"""
import os
import sys
import time
import shutil
import threading
from time import perf_counter

# ---- Qt 高 DPI：先設環境變數再 import Qt ----
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")

import mido
from pynput.keyboard import Controller, KeyCode

from PySide6.QtCore import Qt, QObject, Signal, Slot, QThread, QSettings
from PySide6.QtGui import QFont, QPalette, QColor
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QVBoxLayout, QHBoxLayout, QGridLayout,
    QGroupBox, QLabel, QLineEdit, QPushButton,
    QFileDialog, QMessageBox, QSplitter,
    QListWidget, QListWidgetItem, QAbstractItemView,
    QCheckBox, QSpinBox, QDoubleSpinBox,
    QPlainTextEdit, QStatusBar, QGraphicsDropShadowEffect,QStyle,QSizePolicy
)

# ====== 你的鍵盤對照表（依你圖片）======
MIDI_TO_KEY = {
    60:'q', 61:'2', 62:'w', 63:'3', 64:'e', 65:'r',
    66:'5', 67:'t', 68:'6', 69:'y', 70:'7', 71:'u', 72:'i',

    48:'z', 49:'s', 50:'x', 51:'d', 52:'c', 53:'v',
    54:'g', 55:'b', 56:'h', 57:'n', 58:'j', 59:'m',

    36:',', 37:'l', 38:'.', 39:';', 40:'/', 41:'o',
    42:'0', 43:'p', 44:'-', 45:'[', 46:'=', 47:'j',
}

def build_timed_events(mid: "mido.MidiFile"):
    """把 MIDI 合併成一條時間序列（秒），支援 tempo 變化。回傳 [(t_sec, msg), ...]"""
    ticks_per_beat = mid.ticks_per_beat
    tempo = 500000  # default 120 BPM
    events = []
    abs_sec = 0.0

    merged = mido.merge_tracks(mid.tracks)
    for msg in merged:
        dt_ticks = msg.time
        if dt_ticks:
            abs_sec += mido.tick2second(dt_ticks, ticks_per_beat, tempo)
        events.append((abs_sec, msg))
        if msg.type == "set_tempo":
            tempo = msg.tempo
    return events

def pick_best_transpose(timed, mapping, candidates=(-36, -24, -12, 0, 12, 24, 36)):
    """掃描 note_on，找在候選移調中命中 mapping 最多的 transpose。"""
    notes = []
    for _, msg in timed:
        if msg.type == "note_on" and getattr(msg, "velocity", 0) > 0:
            notes.append(int(msg.note))
    if not notes:
        return 0, 0, 0

    total = len(notes)
    best_tr, best_hit = 0, -1
    for tr in candidates:
        hit = 0
        for n in notes:
            if (n + tr) in mapping:
                hit += 1
        if hit > best_hit:
            best_hit = hit
            best_tr = tr
    return best_tr, best_hit, total

def unique_dest_path(folder: str, filename: str) -> str:
    """若檔名已存在，自動產生 xxx (1).mid 這種不重名檔名。"""
    base, ext = os.path.splitext(filename)
    dest = os.path.join(folder, filename)
    if not os.path.exists(dest):
        return dest
    i = 1
    while True:
        cand = os.path.join(folder, f"{base} ({i}){ext}")
        if not os.path.exists(cand):
            return cand
        i += 1

class PlayWorker(QObject):
    log = Signal(str)
    status = Signal(str)
    finished = Signal()
    select_folder_index = Signal(int)
    select_playlist_index = Signal(int)

    def __init__(self, *, mode: str, play_list: list[str], start_index: int, settings: dict):
        super().__init__()
        self.mode = mode                 # "playlist" | "folder" | "single"
        self.play_list = play_list[:]    # full paths
        self.idx = start_index
        self.settings = settings
        self.stop_event = threading.Event()
        self.kb = Controller()
        self.pressed = set()

    def stop(self):
        self.stop_event.set()

    def _release_all(self):
        for k in list(self.pressed):
            try:
                self.kb.release(KeyCode.from_char(k))
            except Exception:
                pass
        self.pressed.clear()

    def _play_one(self, path: str) -> bool:
        """播放單首（回傳 True=正常播完，False=停止）"""
        mid = mido.MidiFile(path)
        timed = build_timed_events(mid)

        transpose = int(self.settings["transpose"])
        auto_transpose = bool(self.settings["auto_transpose"])
        velocity_th = int(self.settings["velocity"])
        countdown = float(self.settings["countdown"])
        release_all_end = bool(self.settings["release_all_at_end"])

        # auto transpose
        if auto_transpose:
            best_tr, hit, total = pick_best_transpose(timed, MIDI_TO_KEY)
            transpose = best_tr
            if total > 0:
                self.log.emit(f"🎯 Auto Transpose：{transpose:+d}（可彈 {hit}/{total} = {hit/total:.1%}）")
        else:
            self.log.emit(f"🎚 使用手動 Transpose：{transpose:+d}")

        self.log.emit(f"✅ 載入：{path}")
        self.log.emit(f"   tracks={len(mid.tracks)}, ticks_per_beat={mid.ticks_per_beat}")
        self.log.emit(f"   velocity threshold={velocity_th}")
        self.log.emit(f"⏳ {countdown} 秒後開始…請切到遊戲視窗（建議點一下讓遊戲取得焦點）")
        self.status.emit("倒數中…")

        t_end = time.time() + max(0.0, countdown)
        while time.time() < t_end:
            if self.stop_event.is_set():
                self.log.emit("🛑 已停止（倒數中）")
                return False
            time.sleep(0.05)

        self.status.emit("播放中…")
        t0 = perf_counter()

        try:
            for t_sec, msg in timed:
                if self.stop_event.is_set():
                    self.log.emit("🛑 已停止（播放中）")
                    break

                # 穩定等待（sleep + 微忙等）
                while True:
                    now = perf_counter() - t0
                    wait = t_sec - now
                    if wait <= 0:
                        break
                    if wait > 0.004:
                        time.sleep(wait - 0.002)

                if msg.type not in ("note_on", "note_off"):
                    continue

                note = int(msg.note) + transpose
                key = MIDI_TO_KEY.get(note)
                if not key:
                    continue

                is_note_on = (msg.type == "note_on" and msg.velocity >= velocity_th)
                is_note_off = (msg.type == "note_off") or (msg.type == "note_on" and msg.velocity == 0)

                if is_note_on:
                    if key not in self.pressed:
                        self.kb.press(KeyCode.from_char(key))
                        self.pressed.add(key)

                if is_note_off:
                    if key in self.pressed:
                        self.kb.release(KeyCode.from_char(key))
                        self.pressed.remove(key)

        finally:
            if release_all_end:
                self._release_all()

        return not self.stop_event.is_set()

    @Slot()
    def run(self):
        auto_next = bool(self.settings["auto_next"])
        loop_playlist = bool(self.settings["loop_playlist"])

        try:
            while not self.stop_event.is_set():
                if self.idx < 0 or self.idx >= len(self.play_list):
                    break

                cur = self.play_list[self.idx]

                # 同步 UI highlight
                if self.mode == "playlist":
                    self.select_playlist_index.emit(self.idx)
                elif self.mode == "folder":
                    self.select_folder_index.emit(self.idx)

                ok = self._play_one(cur)
                if not ok:
                    break

                self.log.emit("✅ 此曲播放完畢")
                self.status.emit("就緒")

                if not auto_next:
                    break

                self.idx += 1
                if self.idx >= len(self.play_list):
                    if self.mode == "playlist" and loop_playlist:
                        self.log.emit("🔁 播放清單循環：回到第一首")
                        self.idx = 0
                    else:
                        self.log.emit("🏁 已到最後一首，停止。")
                        break

            self.log.emit("✅ 結束")

        except Exception as e:
            self.log.emit(f"❌ 發生錯誤：{e}")
            try:
                self._release_all()
            except Exception:
                pass
        finally:
            self.status.emit("就緒")
            self.finished.emit()

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MIDI AutoPlay — Modern (Fixed v2)")

        self.resize(1000, 700)
        self.setMinimumSize(900, 650)

        self.mid_files: list[str] = []
        self.playlist: list[str] = []

        self.worker_thread: QThread | None = None
        self.worker: PlayWorker | None = None

        self._build_ui()
        self._set_std_icon(self.btn_pick_folder, "SP_DialogOpenButton")
        self._set_std_icon(self.btn_refresh,     "SP_BrowserReload")
        self._set_std_icon(self.btn_import,      "SP_FileDialogNewFolder")
        self._set_std_icon(self.btn_pick_file,   "SP_FileDialogContentsView")
        self._set_std_icon(self.btn_add,         "SP_ArrowRight")
        self._set_std_icon(self.btn_remove,      "SP_TrashIcon")
        self._set_std_icon(self.btn_up,          "SP_ArrowUp")
        self._set_std_icon(self.btn_down,        "SP_ArrowDown")
        self._set_std_icon(self.btn_clear,       "SP_DialogResetButton")
        self._set_std_icon(self.btn_start,       "SP_MediaPlay")
        self._set_std_icon(self.btn_stop,        "SP_MediaStop")

        self.ed_folder.setPlaceholderText("選擇包含 .mid / .midi 的資料夾…")
        self.ed_midi.setPlaceholderText("選擇一個 MIDI 檔案…（或從清單雙擊）")

        self.refresh_midi_list()

    def _default_folder(self) -> str:
        try:
            return os.path.dirname(os.path.abspath(__file__))
        except Exception:
            return os.getcwd()

    def _build_ui(self):
        dark = self._load_theme_pref()
        self._apply_theme(dark)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(14)

        # =======================
        # 1) 上方：資料夾 + 清單（主區域）
        # =======================
        self.g_folder = QGroupBox("MIDI 資料夾 / 清單 / 播放清單")
        self._card_shadow(self.g_folder, alpha=self._shadow_alpha)
        root.addWidget(self.g_folder, 4)  # 給大比例

        v_folder = QVBoxLayout(self.g_folder)
        v_folder.setSpacing(10)

        # row: folder path + buttons
        row = QHBoxLayout()
        v_folder.addLayout(row)

        row.addWidget(QLabel("資料夾："))
        self.ed_folder = QLineEdit(self._default_folder())
        row.addWidget(self.ed_folder, 1)

        self.btn_pick_folder = QPushButton("選擇資料夾")
        self.btn_refresh = QPushButton("重新整理")
        self.btn_import = QPushButton("加入 MIDI（複製到此資料夾）")
        row.addWidget(self.btn_pick_folder)
        row.addWidget(self.btn_refresh)
        row.addWidget(self.btn_import)

        # splitter: folder list + playlist
        splitter = QSplitter(Qt.Horizontal)
        v_folder.addWidget(splitter, 1)
        splitter.setChildrenCollapsible(False)

        # left: folder midi list
        left_box = QWidget()
        left_layout = QVBoxLayout(left_box)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)
        left_layout.addWidget(QLabel("資料夾內 MIDI（可 Ctrl/Shift 多選）"))

        self.list_folder = QListWidget()
        self.list_folder.setSelectionMode(QAbstractItemView.ExtendedSelection)
        # ★★★ 強制設定垂直滾動條為永遠顯示 ★★★
        self.list_folder.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.list_folder.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        # ★★★ 確保最小和最大高度讓它可以滾動 ★★★
        # self.list_folder.setMinimumHeight(450)
        # self.list_folder.setMaximumHeight(9999)  # 移除最大高度限制
        left_layout.addWidget(self.list_folder, 1)

        # right: playlist
        right_box = QWidget()
        right_layout = QVBoxLayout(right_box)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)
        right_layout.addWidget(QLabel("播放清單（你安排的順序，帶編號）"))

        self.list_playlist = QListWidget()
        self.list_playlist.setSelectionMode(QAbstractItemView.ExtendedSelection)
        # ★★★ 強制設定垂直滾動條為永遠顯示 ★★★
        self.list_playlist.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.list_playlist.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        # ★★★ 確保最小和最大高度讓它可以滾動 ★★★
        # self.list_playlist.setMinimumHeight(450)
        # self.list_playlist.setMaximumHeight(9999)  # 移除最大高度限制
        right_layout.addWidget(self.list_playlist, 1)

        # ✅ 讓清單跟著版面自適應，滾輪/捲軸一定會有範圍
        for lw in (self.list_folder, self.list_playlist):
            lw.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            lw.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
            lw.setHorizontalScrollMode(QAbstractItemView.ScrollPerItem)
            lw.setFocusPolicy(Qt.StrongFocus)  # 保證滑鼠滾輪事件能吃到

        # playlist controls
        rowp = QHBoxLayout()
        right_layout.addLayout(rowp)

        self.btn_add = QPushButton("加入 → 播放清單")
        self.btn_remove = QPushButton("移除")
        self.btn_up = QPushButton("上移")
        self.btn_down = QPushButton("下移")
        self.btn_clear = QPushButton("清空")
        self.chk_loop = QCheckBox("循環播放清單")
        self.chk_loop.setChecked(True)

        rowp.addWidget(self.btn_add)
        rowp.addWidget(self.btn_remove)
        rowp.addWidget(self.btn_up)
        rowp.addWidget(self.btn_down)
        rowp.addWidget(self.btn_clear)
        rowp.addStretch(1)
        rowp.addWidget(self.chk_loop)

        splitter.addWidget(left_box)
        splitter.addWidget(right_box)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)

        # =======================
        # 2) 中間：目前 MIDI + 設定（放大）
        # =======================
        bottom = QHBoxLayout()
        bottom.setSpacing(12)
        root.addLayout(bottom, 0)

        # 左：目前 MIDI
        self.g_cur = QGroupBox("目前 MIDI")
        self.g_cur.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.g_cur.setMinimumHeight(100)
        self._card_shadow(self.g_cur, alpha=self._shadow_alpha)
        bottom.addWidget(self.g_cur, 3)

        rowc = QHBoxLayout(self.g_cur)
        rowc.setContentsMargins(14, 12, 14, 12)
        rowc.setSpacing(10)
        rowc.addWidget(QLabel("檔案："), 0)
        self.ed_midi = QLineEdit("")
        self.ed_midi.setMinimumHeight(36)
        rowc.addWidget(self.ed_midi, 1)
        self.btn_pick_file = QPushButton("選擇檔案")
        self.btn_pick_file.setMinimumHeight(36)
        rowc.addWidget(self.btn_pick_file)

        # 右：設定（放大）
        self.g_set = QGroupBox("設定")
        self.g_set.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.g_set.setMinimumHeight(160)
        self._card_shadow(self.g_set, alpha=self._shadow_alpha)
        bottom.addWidget(self.g_set, 2)

        grid = QGridLayout(self.g_set)
        grid.setContentsMargins(14, 12, 14, 12)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)

        label_font = QFont()
        label_font.setPointSize(10)
        
        lbl_tr = QLabel("移調 (Tr):")
        lbl_tr.setFont(label_font)
        self.sp_transpose = QSpinBox()
        self.sp_transpose.setRange(-60, 60)
        self.sp_transpose.setValue(0)
        self.sp_transpose.setMinimumHeight(32)
        self.sp_transpose.setMinimumWidth(80)

        lbl_vel = QLabel("Velocity ≥")
        lbl_vel.setFont(label_font)
        self.sp_velocity = QSpinBox()
        self.sp_velocity.setRange(0, 127)
        self.sp_velocity.setValue(1)
        self.sp_velocity.setMinimumHeight(32)
        self.sp_velocity.setMinimumWidth(80)

        lbl_count = QLabel("倒數 (秒):")
        lbl_count.setFont(label_font)
        self.sp_countdown = QDoubleSpinBox()
        self.sp_countdown.setRange(0, 30)
        self.sp_countdown.setSingleStep(0.5)
        self.sp_countdown.setValue(3.0)
        self.sp_countdown.setMinimumHeight(32)
        self.sp_countdown.setMinimumWidth(80)

        self.chk_auto_tr = QCheckBox("Auto Transpose")
        self.chk_auto_tr.setChecked(True)
        self.chk_auto_tr.setFont(label_font)

        self.chk_release = QCheckBox("結束放鍵")
        self.chk_release.setChecked(True)
        self.chk_release.setFont(label_font)

        self.chk_auto_next = QCheckBox("自動下一首")
        self.chk_auto_next.setChecked(True)
        self.chk_auto_next.setFont(label_font)

        self.chk_dark = QCheckBox("深色")
        self.chk_dark.setChecked(dark)
        self.chk_dark.setFont(label_font)
        self.chk_dark.toggled.connect(self._toggle_dark)

        grid.addWidget(lbl_tr, 0, 0, Qt.AlignRight)
        grid.addWidget(self.sp_transpose, 0, 1)
        grid.addWidget(lbl_vel, 0, 2, Qt.AlignRight)
        grid.addWidget(self.sp_velocity, 0, 3)
        grid.addWidget(lbl_count, 0, 4, Qt.AlignRight)
        grid.addWidget(self.sp_countdown, 0, 5)

        grid.addWidget(self.chk_auto_tr,   1, 0, 1, 2)
        grid.addWidget(self.chk_release,   1, 2, 1, 2)
        grid.addWidget(self.chk_auto_next, 1, 4, 1, 2)
        grid.addWidget(self.chk_dark,      2, 0, 1, 2, Qt.AlignLeft)

        self.btn_start = QPushButton("▶ 開始")
        self.btn_start.setObjectName("primary")
        self.btn_start.setMinimumHeight(40)
        self.btn_start.setMinimumWidth(100)
        
        self.btn_stop = QPushButton("■ 停止")
        self.btn_stop.setObjectName("danger")
        self.btn_stop.setEnabled(False)
        self.btn_stop.setMinimumHeight(40)
        self.btn_stop.setMinimumWidth(100)

        grid.addWidget(self.btn_start, 2, 4, 1, 1)
        grid.addWidget(self.btn_stop,  2, 5, 1, 1)

        # =======================
        # 3) 下方：Log
        # =======================
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(120)
        root.addWidget(self.log, 1)

        sb = QStatusBar()
        self.setStatusBar(sb)
        self.statusBar().showMessage("就緒")

        # =======================
        # signals
        # =======================
        self.btn_pick_folder.clicked.connect(self.pick_folder)
        self.btn_refresh.clicked.connect(self.refresh_midi_list)
        self.btn_import.clicked.connect(self.import_midis)
        self.btn_pick_file.clicked.connect(self.pick_file)

        self.list_folder.itemSelectionChanged.connect(self.on_folder_select)
        self.list_folder.itemDoubleClicked.connect(self.on_folder_double)

        self.btn_add.clicked.connect(self.add_selected_to_playlist)
        self.btn_remove.clicked.connect(self.remove_selected_from_playlist)
        self.btn_up.clicked.connect(lambda: self.move_playlist(-1))
        self.btn_down.clicked.connect(lambda: self.move_playlist(+1))
        self.btn_clear.clicked.connect(self.clear_playlist)

        self.list_playlist.itemDoubleClicked.connect(self.on_playlist_double)

        self.btn_start.clicked.connect(self.start)
        self.btn_stop.clicked.connect(self.stop)

        self._log("✅ 系統就緒！請選擇 MIDI 資料夾或檔案開始播放\n")

    # ---------- Theme ----------
    def _theme_qss(self, dark: bool) -> str:
        if dark:
            return """
                * { font-size: 13px; }
                QWidget { color: #E5E7EB; background: transparent; }
                QMainWindow { background: #0B1220; }

                QGroupBox {
                    border-radius: 12px;
                    margin-top: 10px;
                    padding: 10px;
                }

                QGroupBox::title {
                    subcontrol-origin: margin;
                    left: 14px;
                    padding: 0 8px;
                    color: #F9FAFB;
                    font-weight: 700;
                    font-size: 14px;
                }

                QLabel { color: #D1D5DB; }

                QLineEdit, QSpinBox, QDoubleSpinBox {
                    background: #0B1220;
                    border: 1px solid rgba(255,255,255,0.10);
                    border-radius: 10px;
                    padding: 8px 12px;
                    selection-background-color: #2563EB;
                    font-size: 13px;
                }
                QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {
                    border: 1px solid rgba(59,130,246,0.85);
                }

                QListWidget {
                    background: #0B1220;
                    border: 1px solid rgba(255,255,255,0.10);
                    border-radius: 14px;
                    padding: 8px;
                    outline: none;
                }
                QListWidget::item {
                    padding: 12px 12px;
                    border-radius: 10px;
                    margin: 3px;
                    color: #E5E7EB;
                    font-size: 13px;
                }
                QListWidget::item:hover { background: rgba(255,255,255,0.06); }
                QListWidget::item:selected {
                    background: rgba(59,130,246,0.25);
                    border: 1px solid rgba(59,130,246,0.55);
                    color: #FFFFFF;
                }

                QPushButton {
                    background: rgba(255,255,255,0.04);
                    border: 1px solid rgba(255,255,255,0.10);
                    border-radius: 10px;
                    padding: 8px 14px;
                    font-size: 13px;
                }
                QPushButton:hover { background: rgba(255,255,255,0.08); }
                QPushButton:pressed { background: rgba(255,255,255,0.12); }
                QPushButton:disabled { color: rgba(229,231,235,0.35); border-color: rgba(255,255,255,0.06); }

                QPushButton#primary {
                    background: #3B82F6;
                    border: 1px solid #3B82F6;
                    color: white;
                    font-weight: 700;
                    font-size: 14px;
                }
                QPushButton#primary:hover { background: #2563EB; border-color: #2563EB; }
                QPushButton#danger {
                    background: #EF4444;
                    border: 1px solid #EF4444;
                    color: white;
                    font-weight: 700;
                    font-size: 14px;
                }
                QPushButton#danger:hover { background: #DC2626; border-color: #DC2626; }

                QCheckBox {
                    spacing: 8px;
                    font-size: 13px;
                }
                QCheckBox::indicator {
                    width: 18px;
                    height: 18px;
                    border-radius: 4px;
                    border: 1px solid rgba(255,255,255,0.20);
                    background: #0B1220;
                }
                QCheckBox::indicator:checked {
                    background: #3B82F6;
                    border-color: #3B82F6;
                }

                QPlainTextEdit {
                    background: #060A14;
                    border: 1px solid rgba(255,255,255,0.10);
                    border-radius: 14px;
                    padding: 12px;
                    color: #D1D5DB;
                    font-family: Consolas, "JetBrains Mono", monospace;
                    font-size: 12px;
                }

                QSplitter::handle {
                    background: rgba(255,255,255,0.05);
                    border-radius: 6px;
                    width: 6px;
                    margin: 2px;
                }
                QSplitter::handle:hover { background: rgba(255,255,255,0.10); }

                QScrollBar:vertical {
                    background: rgba(255,255,255,0.03);
                    width: 14px;
                    margin: 0px;
                    border-radius: 7px;
                }
                QScrollBar::handle:vertical {
                    background: rgba(255,255,255,0.20);
                    border-radius: 7px;
                    min-height: 30px;
                    margin: 2px;
                }
                QScrollBar::handle:vertical:hover { 
                    background: rgba(255,255,255,0.30); 
                }
                QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { 
                    height: 0px; 
                }
                QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { 
                    background: transparent; 
                }
            """
        else:
            return """
                * { font-size: 13px; }
                QMainWindow { background: #F5F6F8; }
                QLabel { color: #111827; }

                QGroupBox {
                    background: #FFFFFF;
                    border: 1px solid rgba(17,24,39,0.08);
                    border-radius: 14px;
                    margin-top: 14px;
                    padding: 12px;
                }
                QGroupBox::title {
                    subcontrol-origin: margin;
                    left: 14px;
                    padding: 0 8px;
                    color: #111827;
                    font-weight: 700;
                    font-size: 14px;
                }

                QLineEdit, QSpinBox, QDoubleSpinBox {
                    background: #FFFFFF;
                    border: 1px solid rgba(17,24,39,0.12);
                    border-radius: 10px;
                    padding: 8px 12px;
                    selection-background-color: #0A84FF;
                    font-size: 13px;
                }
                QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {
                    border: 1px solid rgba(10,132,255,0.85);
                }

                QListWidget {
                    background: #FFFFFF;
                    border: 1px solid rgba(17,24,39,0.10);
                    border-radius: 14px;
                    padding: 8px;
                    outline: none;
                }
                QListWidget::item {
                    padding: 12px 12px;
                    border-radius: 10px;
                    margin: 3px;
                    color: #111827;
                    font-size: 13px;
                }
                QListWidget::item:hover { background: rgba(0,0,0,0.04); }
                QListWidget::item:selected {
                    background: rgba(10,132,255,0.14);
                    border: 1px solid rgba(10,132,255,0.35);
                    color: #0B2A55;
                }

                QPushButton {
                    background: #FFFFFF;
                    border: 1px solid rgba(17,24,39,0.12);
                    border-radius: 10px;
                    padding: 8px 14px;
                    font-size: 13px;
                }
                QPushButton:hover { background: rgba(0,0,0,0.03); }
                QPushButton:pressed { background: rgba(0,0,0,0.06); }
                QPushButton:disabled { color: rgba(17,24,39,0.35); border-color: rgba(17,24,39,0.08); }

                QPushButton#primary {
                    background: #0A84FF;
                    border: 1px solid #0A84FF;
                    color: white;
                    font-weight: 800;
                    font-size: 14px;
                }
                QPushButton#primary:hover { background: #0077EE; border-color: #0077EE; }

                QPushButton#danger {
                    background: #FF3B30;
                    border: 1px solid #FF3B30;
                    color: white;
                    font-weight: 800;
                    font-size: 14px;
                }
                QPushButton#danger:hover { background: #E6352B; border-color: #E6352B; }

                QCheckBox {
                    spacing: 8px;
                    font-size: 13px;
                }
                QCheckBox::indicator {
                    width: 18px;
                    height: 18px;
                    border-radius: 4px;
                    border: 1px solid rgba(17,24,39,0.20);
                    background: #FFFFFF;
                }
                QCheckBox::indicator:checked {
                    background: #0A84FF;
                    border-color: #0A84FF;
                }

                QPlainTextEdit {
                    background: #FFFFFF;
                    border: 1px solid rgba(17,24,39,0.10);
                    border-radius: 14px;
                    padding: 12px;
                    color: #111827;
                    font-family: Consolas, "JetBrains Mono", monospace;
                    font-size: 12px;
                }

                QSplitter::handle {
                    background: rgba(0,0,0,0.06);
                    border-radius: 6px;
                    width: 6px;
                    margin: 2px;
                }
                QSplitter::handle:hover { background: rgba(0,0,0,0.10); }

                QScrollBar:vertical {
                    background: rgba(0,0,0,0.03);
                    width: 14px;
                    margin: 0px;
                    border-radius: 7px;
                }
                QScrollBar::handle:vertical {
                    background: rgba(0,0,0,0.20);
                    border-radius: 7px;
                    min-height: 30px;
                    margin: 2px;
                }
                QScrollBar::handle:vertical:hover { 
                    background: rgba(0,0,0,0.30); 
                }
                QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { 
                    height: 0px; 
                }
                QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { 
                    background: transparent; 
                }
            """

    def _apply_theme(self, dark: bool):
        QApplication.setStyle("Fusion")

        pal = QPalette()
        if dark:
            pal.setColor(QPalette.Window, QColor("#0B1220"))
            pal.setColor(QPalette.WindowText, QColor("#E5E7EB"))
            pal.setColor(QPalette.Base, QColor("#0F172A"))
            pal.setColor(QPalette.AlternateBase, QColor("#111827"))
            pal.setColor(QPalette.Text, QColor("#E5E7EB"))
            pal.setColor(QPalette.Button, QColor("#111827"))
            pal.setColor(QPalette.ButtonText, QColor("#E5E7EB"))
            pal.setColor(QPalette.Highlight, QColor("#3B82F6"))
            pal.setColor(QPalette.HighlightedText, QColor("#FFFFFF"))
        else:
            pal.setColor(QPalette.Window, QColor("#F5F6F8"))
            pal.setColor(QPalette.WindowText, QColor("#111827"))
            pal.setColor(QPalette.Base, QColor("#FFFFFF"))
            pal.setColor(QPalette.AlternateBase, QColor("#F3F4F6"))
            pal.setColor(QPalette.Text, QColor("#111827"))
            pal.setColor(QPalette.Button, QColor("#FFFFFF"))
            pal.setColor(QPalette.ButtonText, QColor("#111827"))
            pal.setColor(QPalette.Highlight, QColor("#0A84FF"))
            pal.setColor(QPalette.HighlightedText, QColor("#FFFFFF"))

        QApplication.setPalette(pal)
        self.setStyleSheet(self._theme_qss(dark))

        self._shadow_alpha = 45 if not dark else 80

    def _load_theme_pref(self) -> bool:
        s = QSettings("AutoPlayQt", "MIDI-AutoPlay")
        return bool(s.value("dark_mode", False, type=bool))

    def _save_theme_pref(self, dark: bool):
        s = QSettings("AutoPlayQt", "MIDI-AutoPlay")
        s.setValue("dark_mode", bool(dark))

    @Slot(bool)
    def _toggle_dark(self, checked: bool):
        self._apply_theme(checked)
        self._save_theme_pref(checked)
        self._card_shadow(self.g_folder, alpha=self._shadow_alpha)
        self._card_shadow(self.g_cur, alpha=self._shadow_alpha)
        self._card_shadow(self.g_set, alpha=self._shadow_alpha)

    def _log(self, s: str):
        self.log.appendPlainText(s)

    # -------- folder / list --------
    def pick_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "選擇含 MIDI 的資料夾", self.ed_folder.text() or os.getcwd())
        if folder:
            self.ed_folder.setText(folder)
            self.refresh_midi_list()

    def refresh_midi_list(self):
        folder = self.ed_folder.text().strip().strip('"')
        if not folder or not os.path.isdir(folder):
            self._log(f"⚠️ 資料夾不存在：{folder}")
            self.list_folder.clear()
            self.mid_files = []
            return

        names = []
        for name in os.listdir(folder):
            low = name.lower()
            if low.endswith(".mid") or low.endswith(".midi"):
                names.append(name)
        names.sort(key=lambda s: s.lower())

        self.mid_files = [os.path.join(folder, n) for n in names]
        self.list_folder.clear()
        for n in names:
            self.list_folder.addItem(QListWidgetItem(n))

        self._log(f"📁 已載入資料夾：{folder}（{len(names)} 個 MIDI）")

        if self.mid_files and not self.ed_midi.text().strip():
            self.ed_midi.setText(self.mid_files[0])
            self.list_folder.setCurrentRow(0)

    def on_folder_select(self):
        items = self.list_folder.selectedItems()
        if not items:
            return
        row = self.list_folder.row(items[0])
        if 0 <= row < len(self.mid_files):
            self.ed_midi.setText(self.mid_files[row])

    def on_folder_double(self, _item: QListWidgetItem):
        self.start()

    # -------- import midi --------
    def import_midis(self):
        folder = self.ed_folder.text().strip().strip('"')
        if not folder or not os.path.isdir(folder):
            QMessageBox.critical(self, "錯誤", f"資料夾不存在：{folder}")
            return

        paths, _ = QFileDialog.getOpenFileNames(
            self, "選擇要加入的 MIDI（會複製到目前資料夾）",
            os.getcwd(),
            "MIDI files (*.mid *.midi);;All files (*.*)"
        )
        if not paths:
            return

        ok = 0
        fail = 0
        for src in paths:
            try:
                if not os.path.isfile(src):
                    continue
                name = os.path.basename(src)
                low = name.lower()
                if not (low.endswith(".mid") or low.endswith(".midi")):
                    continue
                dest = unique_dest_path(folder, name)
                shutil.copy2(src, dest)
                ok += 1
            except Exception as e:
                fail += 1
                self._log(f"❌ 複製失敗：{src} -> {e}")

        self._log(f"⬆️ 已加入 {ok} 個 MIDI 到：{folder}" + (f"（失敗 {fail}）" if fail else ""))
        self.refresh_midi_list()

    # -------- pick file --------
    def pick_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "選擇 MIDI 檔",
            os.getcwd(),
            "MIDI files (*.mid *.midi);;All files (*.*)"
        )
        if not path:
            return
        self.ed_midi.setText(path)

        folder = os.path.dirname(path)
        if folder and os.path.isdir(folder):
            self.ed_folder.setText(folder)
            self.refresh_midi_list()
            base = os.path.basename(path).lower()
            for i, fp in enumerate(self.mid_files):
                if os.path.basename(fp).lower() == base:
                    self.list_folder.setCurrentRow(i)
                    break

    # -------- playlist （★ 加上編號）--------
    def refresh_playlist_ui(self):
        """刷新播放清單，顯示順序編號"""
        self.list_playlist.clear()
        for idx, p in enumerate(self.playlist, start=1):
            display_name = f"[{idx}] {os.path.basename(p)}"
            self.list_playlist.addItem(QListWidgetItem(display_name))

    def add_selected_to_playlist(self):
        rows = sorted({self.list_folder.row(i) for i in self.list_folder.selectedItems()})
        if not rows:
            QMessageBox.information(self, "提示", "請先在左邊清單選取一首或多首 MIDI。")
            return

        added = 0
        for r in rows:
            if 0 <= r < len(self.mid_files):
                p = self.mid_files[r]
                if p not in self.playlist:
                    self.playlist.append(p)
                    added += 1

        self.refresh_playlist_ui()
        self._log(f"➕ 已加入 {added} 首到播放清單")

        if self.playlist and not self.ed_midi.text().strip():
            self.ed_midi.setText(self.playlist[0])
            self.list_playlist.setCurrentRow(0)

    def remove_selected_from_playlist(self):
        rows = sorted({self.list_playlist.row(i) for i in self.list_playlist.selectedItems()}, reverse=True)
        if not rows:
            return
        for r in rows:
            if 0 <= r < len(self.playlist):
                self.playlist.pop(r)
        self.refresh_playlist_ui()

    def move_playlist(self, delta: int):
        items = self.list_playlist.selectedItems()
        if len(items) != 1:
            return
        i = self.list_playlist.row(items[0])
        j = i + delta
        if j < 0 or j >= len(self.playlist):
            return
        self.playlist[i], self.playlist[j] = self.playlist[j], self.playlist[i]
        self.refresh_playlist_ui()
        self.list_playlist.setCurrentRow(j)

    def clear_playlist(self):
        self.playlist.clear()
        self.refresh_playlist_ui()

    def on_playlist_double(self, _item: QListWidgetItem):
        row = self.list_playlist.currentRow()
        if 0 <= row < len(self.playlist):
            self.ed_midi.setText(self.playlist[row])
        self.start()

    # -------- play control --------
    def _settings(self) -> dict:
        return dict(
            transpose=self.sp_transpose.value(),
            auto_transpose=self.chk_auto_tr.isChecked(),
            velocity=self.sp_velocity.value(),
            countdown=self.sp_countdown.value(),
            release_all_at_end=self.chk_release.isChecked(),
            auto_next=self.chk_auto_next.isChecked(),
            loop_playlist=self.chk_loop.isChecked(),
        )

    def _playlist_selected_index(self) -> int:
        row = self.list_playlist.currentRow()
        if row >= 0:
            return row
        cur = os.path.basename(self.ed_midi.text().strip().strip('"')).lower()
        for i, fp in enumerate(self.playlist):
            if os.path.basename(fp).lower() == cur:
                return i
        return -1

    def _folder_selected_index(self) -> int:
        row = self.list_folder.currentRow()
        if row >= 0:
            return row
        cur = os.path.basename(self.ed_midi.text().strip().strip('"')).lower()
        for i, fp in enumerate(self.mid_files):
            if os.path.basename(fp).lower() == cur:
                return i
        return -1

    def start(self):
        # ★★★ 修復 QThread 錯誤：檢查 thread 是否有效 ★★★
        try:
            if self.worker_thread and self.worker_thread.isRunning():
                QMessageBox.information(self, "正在播放", "目前正在播放中。")
                return
        except RuntimeError:
            # Thread 已被刪除，重設為 None
            self.worker_thread = None
            self.worker = None

        path = self.ed_midi.text().strip().strip('"')
        if not path or not os.path.exists(path):
            QMessageBox.critical(self, "錯誤", "請先選擇有效的 MIDI 檔案。")
            return

        # 決定播放清單優先順序
        if self.playlist:
            mode = "playlist"
            play_list = list(self.playlist)
            idx = self._playlist_selected_index()
            if idx == -1:
                base = os.path.basename(path).lower()
                idx = 0
                for i, fp in enumerate(play_list):
                    if os.path.basename(fp).lower() == base:
                        idx = i
                        break
        else:
            base = os.path.basename(path).lower()
            idx = -1
            for i, fp in enumerate(self.mid_files):
                if os.path.basename(fp).lower() == base:
                    idx = i
                    break
            if idx == -1:
                mode = "single"
                play_list = [path]
                idx = 0
            else:
                mode = "folder"
                play_list = list(self.mid_files)

        # ✅ 顯示這次會用哪種模式播放
        mode_name = {"playlist":"播放清單", "folder":"資料夾順播", "single":"單曲"}.get(mode, mode)
        self._log(f"🎬 播放模式：{mode_name}（起始第 {idx+1} 首 / 共 {len(play_list)} 首）")
        self.statusBar().showMessage(f"播放模式：{mode_name}")

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.statusBar().showMessage("播放中…")
        self._log("▶ 開始播放")

        # ★★★ 創建新的 thread，不重用舊的 ★★★
        self.worker_thread = QThread()
        self.worker = PlayWorker(mode=mode, play_list=play_list, start_index=idx, settings=self._settings())
        self.worker.moveToThread(self.worker_thread)

        self.worker_thread.started.connect(self.worker.run)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker_thread.finished.connect(self.worker_thread.deleteLater)

        self.worker.log.connect(self._log)
        self.worker.status.connect(self.statusBar().showMessage)

        self.worker.select_folder_index.connect(self._select_folder_row)
        self.worker.select_playlist_index.connect(self._select_playlist_row)

        # ★★★ 當 thread 結束時，重設變數 ★★★
        self.worker_thread.finished.connect(self._on_play_finished)
        self.worker_thread.finished.connect(lambda: setattr(self, 'worker_thread', None))
        self.worker_thread.finished.connect(lambda: setattr(self, 'worker', None))
        
        self.worker_thread.start()

    def _set_std_icon(self, btn: QPushButton, name: str):
        try:
            sp = getattr(QStyle.StandardPixmap, name)
        except Exception:
            return
        btn.setIcon(self.style().standardIcon(sp))

    def _card_shadow(self, widget: QWidget, alpha: int = 70):
        eff = QGraphicsDropShadowEffect(self)
        eff.setBlurRadius(22)
        eff.setXOffset(0)
        eff.setYOffset(8)
        eff.setColor(QColor(0, 0, 0, alpha))
        widget.setGraphicsEffect(eff)

    def stop(self):
        if self.worker:
            self.worker.stop()
            self._log("🛑 收到停止指令…")

    @Slot()
    def _on_play_finished(self):
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.statusBar().showMessage("就緒")

    @Slot(int)
    def _select_folder_row(self, idx: int):
        if 0 <= idx < self.list_folder.count():
            self.list_folder.setCurrentRow(idx)
            if idx < len(self.mid_files):
                self.ed_midi.setText(self.mid_files[idx])

    @Slot(int)
    def _select_playlist_row(self, idx: int):
        if 0 <= idx < self.list_playlist.count():
            self.list_playlist.setCurrentRow(idx)
            if idx < len(self.playlist):
                self.ed_midi.setText(self.playlist[idx])

def main():
    app = QApplication(sys.argv)

    try:
        app.setFont(QFont("Segoe UI", 10))
    except Exception:
        pass

    w = MainWindow()
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
