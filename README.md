# MIDI AutoPlay (PySide6 / Qt6) — AutoPlayUIQT

>好下面全都是GPT生成的
> 一句話 用管理員模式開exe檔 接著就可以用了呦!!! 我真貼心!!!
> 想看可以看 不想看 可以不用看

用 MIDI 檔案自動按鍵的桌面工具（Windows），支援：
- 選擇 MIDI 資料夾，顯示資料夾內 MIDI 清單
- 建立播放清單（可調順序、可循環）
- Auto Transpose（自動選擇最佳移調，提升可彈比例）
- Velocity 閾值、倒數、結束放鍵、自動下一首
- PySide6 現代化 UI

> ⚠️ 本工具會「模擬鍵盤按鍵」。請自行確認遊戲/平台規範與使用情境。

---

## 1) 環境需求

開發/執行原始碼需要：
- Windows 10/11
- Python 3.10+（建議 3.10/3.11）
- 套件：PySide6 / mido / pynput

若使用「打包後 exe」：
- 通常不需要安裝 Python 與套件（可直接執行）
- 部分電腦可能需要 Microsoft Visual C++ Runtime（見常見問題）

---

## 2) 直接執行原始碼（開發者）

### (1) 建立虛擬環境
在專案根目錄（例：`D:\Heartopia_AutoPlay`）開 CMD：

```bat
cd /d D:\Heartopia_AutoPlay
python -m venv .venv
```

### (2) 啟動虛擬環境
```bat
.\.venv\Scripts\activate.bat
```

看到前綴 `(.venv)` 代表成功。

### (3) 安裝套件
```bat
python -m pip install -U pip
python -m pip install PySide6 mido pynput
```

### (4) 執行
```bat
python AutoPlayUIQT.py
```

---

## 3) 使用方式（UI）

### A. 載入 MIDI
1. 點「選擇資料夾」→ 指向含 `.mid/.midi` 的資料夾
2. 左側會顯示資料夾內 MIDI 清單（可 Ctrl/Shift 多選）
3. 也可用「選擇檔案」直接挑單一 MIDI

### B. 播放清單
- 左側選好 → 點「加入 → 播放清單」
- 右側顯示 `[編號] 檔名`
- 可「上移 / 下移」調整順序
- 「移除」刪掉選取項
- 「清空」清掉整個播放清單
- 勾選「循環播放清單」可無限循環（播放清單模式有效）

---

## 4) 播放模式規則（很重要：到底播播放清單還是資料夾？）

程式按「開始」時依下列規則決定：

### ✅ 規則 1：只要「播放清單不是空」→ 一律播播放清單
- 不管你左邊資料夾清單選哪首
- 只要右邊播放清單有任何歌曲 → 以播放清單為準

### ✅ 規則 2：若「播放清單是空」→ 才會播資料夾/單曲
- 若目前 MIDI 在資料夾清單內找得到 → 以「資料夾順播」
- 若找不到（例如挑了資料夾外檔案）→ 只播「單曲」

> 想用「資料夾順播」：請先把播放清單按「清空」。

---

## 5) 設定項目

- **移調 (Tr)**：整體移調（半音）
- **Auto Transpose**：自動挑命中鍵盤對照表最多的移調值（建議開）
- **Velocity ≥**：只在 velocity 大於等於此值時才按鍵
- **倒數(秒)**：開始播放前倒數（用來切到遊戲視窗）
- **結束放鍵**：停止/結束時釋放所有按住的鍵（建議開）
- **自動下一首**：播放完自動播放下一首

---

## 6) 打包成 EXE（PyInstaller）

> 建議在已啟動 `.venv` 的狀態下打包。

### (1) 安裝 PyInstaller
```bat
python -m pip install pyinstaller
```

### (2) 打包（單一 exe）
```bat
pyinstaller --noconfirm --clean --onefile --noconsole AutoPlayUIQT.py
```

完成後輸出在：
- `dist\AutoPlayUIQT.exe`

### (3) 常見打包錯誤：WinError 5 存取被拒
通常是 `dist\AutoPlayUIQT.exe` 正在執行或被防毒鎖住。

解法：
```bat
taskkill /f /im AutoPlayUIQT.exe
rmdir /s /q dist
rmdir /s /q build
del /q AutoPlayUIQT.spec
pyinstaller --noconfirm --clean --onefile --noconsole AutoPlayUIQT.py
```

---

## 7) 如何確認 EXE 是「獨立可用」（沒有 Python/套件也能跑）

### 快速自測（同一台電腦）
1. 不要啟動 venv（關掉所有終端機後重開也行）
2. 把 `dist\AutoPlayUIQT.exe` 複製到桌面/其他資料夾
3. 直接雙擊執行
- 能正常開 UI、選資料夾、顯示清單 → 基本 OK

### 最保險測法
把 exe 拿到「沒有安裝 Python」的另一台電腦試跑。

> 注意：部分 Windows 可能需要安裝 Microsoft Visual C++ Runtime，否則可能缺 DLL 或閃退。

---

## 8) GitHub 要放哪裡？（推薦方式）

### ✅ 推薦：原始碼放 Repo，EXE 放 GitHub Releases
- Repo 放：`*.py`、`README.md`、`requirements.txt`、圖片/資源
- EXE 放：GitHub Releases（使用者下載最方便）

操作：
1. Push 原始碼到 GitHub repo
2. GitHub repo → **Releases** → **Create a new release**
3. Tag 例如 `v1.0.0`
4. 上傳 `dist\AutoPlayUIQT.exe` 當附件並發布

### ❌ 不建議：把 dist/ 直接 commit 到 repo
exe 很大、每次更新 repo 會變肥。除非你確定要這樣做。

---

## 9) .gitignore 建議（避免把打包產物與虛擬環境推上去）

建立 `.gitignore`（放在專案根目錄）：

```gitignore
.venv/
__pycache__/
*.pyc
build/
dist/
*.spec
```

---

## 10) 常見問題（FAQ）

### Q1：程式有按鍵，但遊戲沒反應？
- 請確認遊戲視窗有取得焦點（倒數期間切到遊戲並點一下）
- 全螢幕獨占模式可能擋輸入：建議用「無邊框視窗/視窗化」
- 部分遊戲防外掛會阻擋模擬輸入

### Q2：怎麼測按鍵真的有輸出？
用記事本測：
1. 打開記事本
2. 開始播放倒數時切到記事本並點一下
3. 若記事本開始輸出 `qwer...` 等字元代表按鍵正常

### Q3：EXE 在別台電腦閃退 / 缺 DLL？
可能需要安裝 Microsoft Visual C++ Redistributable（VC++ Runtime）。

---

## License
此專案供學習與個人用途使用。
