# AI Waker：Claude + Codex 定時喚醒（Linux VPS 版）

在常開的 Linux VPS 上，每天四個時段各送出一次簡短請求，依序呼叫已登入的 Claude Code 與 Codex CLI。使用訂閱帳號，不需要 API key。

預設從 **08:00** 開始，每隔 **5 小時 1 分鐘**觸發：

| 第幾班 | 時間（預設 Asia/Taipei） |
|---|---|
| 1 | 08:00 |
| 2 | 13:01 |
| 3 | 18:02 |
| 4 | 23:03 |

這是每日固定班表。每天都回到第一班起算，並非每天累加往後延；最後一班到隔日第一班是夜間空檔。多留的 1 分鐘只是排程緩衝，不能保證服務的額度視窗與這份班表一致。兩個服務各自計算使用量，實際重置時間以帳號內顯示為準；喚醒本身也會消耗額度，不會增加總額度。

## 環境

- 常開的 Linux VPS，已啟用 cron，Python 3.9 以上及系統時區資料。
- 已安裝、登入 Claude Code 與 Codex CLI；兩邊必須使用你要喚醒的訂閱帳號。
- Codex CLI 0.162.1 與 Claude Code 2.1.260 已實測；新版本應先試跑。這組 Claude npm 套件需要 Node.js 22 以上。
- Python 不需要安裝額外套件。Windows 工作排程不在本版範圍。

已有 Node.js 22 以上時，可在自己的使用者目錄安裝已驗證的 CLI 版本：

```bash
npm config set prefix "$HOME/.npm-global"
export PATH="$HOME/.npm-global/bin:$PATH"
npm install -g @openai/codex@0.162.1 @anthropic-ai/claude-code@2.1.260
codex login --device-auth
```

Claude 登入可在 `claude` 互動介面執行 `/login`。登入資訊由各 CLI 自己保管，請勿放入本專案或 GitHub。

## 設定與啟用

完整操作入口是 `setup.sh`。它會檢查 CLI 版本參數、訂閱登入、cron 與時區，跑離線測試並預覽排程；加 `--apply` 才啟用。缺少前置條件時會停止並列出處理方式，不會安裝系統套件或自動登入帳號。

已備妥上節環境時：

```bash
git clone https://github.com/shao1002/ai-waker.git "$HOME/ai-waker"
cd "$HOME/ai-waker"
bash setup.sh
# 檢查成功、確認設定及排程預覽後：
bash setup.sh --apply
```

首次缺少 CLI 時，先安裝對應 CLI 並在這台 VPS 登入；若 Codex 剛好不在 PATH，也可依設定說明指定絕對路徑。

將專案放在自己的 Linux 目錄；路徑請使用英數、底線或連字號，不含空白。

```bash
cp scripts/config.example.json scripts/config.local.json
chmod +x scripts/wake.sh
python3 -m unittest discover -s scripts -p 'test_*.py' -v
bash scripts/wake.sh --dry-run
```

修改 `scripts/config.local.json`：

- `start_time`：第一班時間，格式為 `HH:MM`。
- `runs_per_day`：每日班數，預設 4。
- `interval_minutes`：每班間隔，預設 301，即 5 小時 1 分鐘。
- `timezone`：預設 `Asia/Taipei`。必須和 VPS 系統時區一致；安裝程式會檢查，不會代改系統時區。
- `claude_model`：預設 `haiku`。
- `codex_model`：留空，使用 CLI 預設模型；可自行填入帳號可用的模型。
- `timeout_seconds`：每邊請求逾時，預設 180 秒。
- `claude_bin`、`codex_bin`：選填，自訂 CLI 絕對路徑；未填時從 PATH 或 `~/.npm-global/bin` 尋找。

先各做一次實際請求，再看排程差異並安裝：

```bash
bash scripts/wake.sh
python3 scripts/install_cron.py
python3 scripts/install_cron.py --apply
crontab -l
```

`install_cron.py` 預設只預覽；`--apply` 才安裝。它會備份目前 crontab，只新增或更新自己標記的區塊，保留其他工作。若要取代既有喚醒排程，可指定舊腳本絕對路徑：

```bash
python3 scripts/install_cron.py --replace-legacy /absolute/path/to/old/wake.sh
python3 scripts/install_cron.py --replace-legacy /absolute/path/to/old/wake.sh --apply
```

只接受 0 或 4 條符合的舊排程；其他狀態停止，供人工檢查。其他專案的排程請先確認用途。

## 執行與紀錄

- 同一班先 Claude、後 Codex，降低小型 VPS 的記憶體壓力。任一邊失敗仍嘗試另一邊。
- 每次只要求回覆 `ok`。Claude 關閉工具與 session 儲存；Codex 使用唯讀 sandbox、短暫 session、不載入使用者 config。
- 移除子程序的 API key 環境變數，Codex 另確認是 ChatGPT 登入；不自動改用 API 付費請求。
- 以 CLI 結束碼、JSON 成功狀態及實際回覆判斷成功；登入過期、串流未完成等情況記 FAIL 並回傳非零碼。
- 逾時會停止該次程序群組；鎖定機制避免整班重疊。
- `scripts/logs/wake.jsonl` 保存各服務的時間、成功／失敗、耗時與 token 用量；`scripts/logs/cron.log` 保存排程輸出。不記錄原始回應或登入資料。
- 登入錯誤時，在 VPS 重新執行對應 CLI 的登入流程，再手動試跑。本版未另設對外通知。

```bash
tail -n 10 scripts/logs/wake.jsonl
```

取消排程：用 `crontab -e` 刪除 `BEGIN ai-waker managed schedule` 至 `END ai-waker managed schedule` 的完整區塊。回復先前版本：先確認備份內容，再執行 `crontab scripts/backups/crontab-<時間>.txt`；此操作會回復整份 crontab，備份後新增的其他工作需另保留。

## 官方文件

- [Codex 非互動模式與 JSONL](https://learn.chatgpt.com/docs/non-interactive-mode)
- [Codex 登入與裝置授權](https://learn.chatgpt.com/docs/auth)
- [Codex CLI 參數](https://learn.chatgpt.com/docs/developer-commands?surface=cli)

授權：MIT，見 LICENSE。
