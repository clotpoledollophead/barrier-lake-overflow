# 包給別人用（收件人端零安裝）

適用情境：資料會一直更新，你想把整包東西丟給不會裝 Python / Docker 的人，
他們只要雙擊一個檔案就能看到最新結果。

**這份設定只有你（打包的人）需要做一次**，做完之後把整個資料夾
壓縮丟給誰，對方都不用裝任何東西。

---

## 一次性設定（在你自己的 Windows 電腦上做）

### 1. 下載「embeddable」Python（不是一般安裝版）

去 <https://www.python.org/downloads/windows/>，找目前的穩定版本
（例如 3.12.x），下載檔名類似：

```
python-3.12.x-embed-amd64.zip
```

**不是** `python-3.12.x-amd64.exe`（那個是要安裝的版本，不要下載那個）。
"embeddable" 版本就是單純一個 zip，解壓縮就能用，不會跳出安裝精靈、
不用系統管理員權限、也不會動到你電腦上其他 Python 環境。

### 2. 解壓縮到專案資料夾裡，資料夾名稱固定叫 `python-embed`

解壓縮後，專案根目錄應該長這樣：

```
barrier-lake-overflow/
├── python-embed/           ← 剛解壓縮的東西直接放這裡
│   ├── python.exe
│   ├── python312.dll
│   ├── python312._pth      ← 等一下要改這個檔案
│   └── ...
├── code/
├── data/
├── run_dashboard.bat
└── ...
```

### 3. 打開 pip 功能（embeddable 版預設關掉了）

用文字編輯器打開 `python-embed\python312._pth`（數字依你下載的版本而定），
找到這一行：

```
#import site
```

把最前面的 `#` 刪掉，變成：

```
import site
```

存檔。

### 4. 幫這個 embeddable Python 裝 pip

下載 <https://bootstrap.pypa.io/get-pip.py>，存到 `python-embed\` 資料夾裡，
然後在該資料夾開命令提示字元，執行：

```
python.exe get-pip.py
```

### 5. 安裝專案需要的套件

還是在 `python-embed\` 資料夾底下：

```
python.exe -m pip install pyproj PyYAML
```

（`pipeline` 這個套件本身不用裝，`run_dashboard.bat` 會用
`PYTHONPATH` 直接指到 `code/`，不需要 `pip install -e .` 那個步驟。）

### 6. 測試一次

回到專案根目錄，雙擊 `run_dashboard.bat`，應該會看到它跑完資料處理
訊息，然後自動開啟瀏覽器顯示儀表板。跑起來沒錯，才代表打包完成。

---

## 打包丟給別人

整個 `barrier-lake-overflow/` 資料夾（含裡面的 `python-embed/`）
壓縮成一個 zip，丟給對方。體積大概會是 +30~40MB（embeddable Python
本身很小，主要是裝了 pyproj 等套件）。

對方收到後：

1. 解壓縮
2. 雙擊 `run_dashboard.bat`
3. 看儀表板

如果只是要看目前的結果、不需要重新產生資料，其實對方也可以跳過
`.bat`，直接雙擊 `code/dashboard/index.html`——因為資料已經預先產生好
放在 repo 裡了。`.bat` 存在的意義是「資料源更新後，重新產生一次」。

---

## 之後你要更新資料時

1. 更新 `data/raw/` 底下的 CSV（或風險模型輸出）
2. 在你自己電腦上跑一次 `run_dashboard.bat`，確認資料正確
3. 把整個資料夾（含新的 `code/dashboard/data/*.js`、含 `python-embed/`）
   重新壓縮，丟給對方即可——不需要對方重跑，你這邊跑好、資料寫進
   `.js` 檔裡，直接把最新的整包丟過去就好。

換句話說：實務上大部分時候你甚至不需要對方跑 `.bat`，因為資料是
「你跑完、把結果一起打包過去」。`.bat` 是給比較常需要重新產生、
或你想讓對方自己更新的情境用的保險。
