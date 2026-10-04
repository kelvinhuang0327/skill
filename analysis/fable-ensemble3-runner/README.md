# FABLE_ENSEMBLE3_R1 執行層套件

Planner 工作包 2A（執行設定與身分檢查）、2B-PREP（探測腳本離線實作）、3A（正式 runner 的離線實作）。

這個套件把已封存的實驗室（`../fable-ensemble3-lab`、`../fable-ensemble3-r1`、`../fable-ensemble3-bank`，封存提交
`32d5c9991c71d5b4025c015c395c9d41226a6d45`）接成一條可以崩潰後復原、帳務可稽核的流程。全部在離線狀態下驗證過。

## 它是什麼，不是什麼

- **是**：provider → checker → ledger → scorer 的執行流程；SIMULATION 與 LIVE 兩種模式共用同一條程式路徑；
  每個風險點（崩潰、重送、超額、身分漂移）都有測試，每個守門條件都有一個「拿掉它就會讓指定測試以斷言失敗」的變異體。
- **不是**：模型結果、`BENCHMARK_READY` 的判定、LIVE 核准。本套件不寫核准檔、不讀取也不印出 API 金鑰（只看環境變數存不存在）。
  執行器與探針的測試把 `urlopen` 和 `socket.connect` 封掉，任何連線嘗試都會讓測試失敗；設定檔的測試本來就不碰網路。
  探針只產出證據，候選介面的狀態固定為 `PROBE_EVIDENCE_PENDING_JUDGE`。
- SIMULATION 的回答取自題庫的標準答案，所以它只證明管線，**不是**任何模型能力的證據，報告也這樣標示
  （`SIMULATION_NOT_A_RESULT`、`statistical_claims_allowed: false`）。

## 固定的身分

| 項目 | 值 |
|---|---|
| 封存樹（實驗室、R2、題庫，共 28 個檔案） | `32d5c9991c71d5b4025c015c395c9d41226a6d45` |
| skill 來源（F7 已修） | `d037a9a8c612957540ff03c2d46b5b3ab8f0583a` |
| 指令包、題庫 v5、收據 | 由執行設定檔逐一釘住，`check` 會重新雜湊 |
| 模型、推理強度 | `gpt-5.6-luna`、`medium`（與 adapter 常數互相核對） |
| 單次嘗試保留額 | US$0.55；每格兩次嘗試；46 題 × 5 輪 → 整輪保留 US$253.00 |

## 檔案

| 檔案 | 作用 |
|---|---|
| `execution_profile.py` | 建立並檢查執行設定檔；`check` 在任何生成之前阻擋缺欄位、雜湊不符、過期或含衝突標記的指令包、題庫／收據／協議互相不一致、封存來源漂移 |
| `exploration_runner.py` | 正式探索執行器：`run`、`resume`、`replay`、`check`；帳本、崩潰復原、重試規則、報告 |
| `probe_2b.py` | 階段 2B 的探針（A–D 四步、最多 7 個請求、零重試）：`register`、`run`、`recover`、`report` |
| `simulated_transport.py` | 離線的 Responses 端點替身：確定性答案、可注入故障、伺服器端請求紀錄 |
| `_lab.py` | 找出封存實驗室並確認每個模組都是從那裡載入的 |
| `crash_driver.py` | 崩潰測試用的子行程，會在請求中途被 SIGKILL |
| `test_*.py` | 三個測試模組（見「驗證」） |
| `mutation_runner.py`、`mutation_cases.py` | 變異驗證：逐一破壞守門條件，要求指定測試以斷言失敗 |

## 帳務與崩潰安全規則

這是整個套件的重點，下列每一條都有對應測試。

1. 每個請求之前，`START_INTENT` 先寫入並 fsync；崩潰時留在 `RUNNING` 的格子，復原時記為 `INCOMPLETE` 並計入單次上限，**永遠不重送**。
2. 原始回應連同 usage 在 adapter 解析之前就已落地；adapter 拒絕的回應也依它自己的 usage 計費，用量不可信時才計上限，不會變成免費。
3. 成本未知就計上限，絕不計零；成本高於上限就計實際金額、停止整個執行。
4. 只有「證明沒有進入生成」的失敗，或明確的「未處理」回應（429／503），才重試一次；生成請求送出後的逾時與不確定錯誤一律不重送。
5. 身分或能力違規（模型、服務層級不符）、被拒絕的請求、非預期錯誤都會**停止**執行：已完成的格子保留、不評分，也不能續跑。
6. 每一整輪（含重試保留）要嘛完整預留、要嘛不開始；正式執行少於三輪不得開始，LIVE 正式執行必須五輪全部預留。
7. 已存在的執行目錄不會被重啟；`resume` 只續跑被中斷的執行，並且必須與原本的身分完全一致（含 runner 程式碼的雜湊）。
8. 收據模式不符、狀態未知、成本或狀態讀不出來、輸出與 adapter 說法不一致，都算「付了錢但不可用」，停止執行。
9. 帳本在寫入「已停止」之前被中斷，續跑時以 `LEDGER_CONFLICT` 結束，不會留下一個永遠無法關閉的殭屍執行。
10. 核准檔綁在設定檔（或探針的註冊檔）的雜湊上，而它們都釘住了 runner 的五個程式檔：核准涵蓋的是「會跑的程式碼」，不只是「會讀的資料」。
11. 所有讀取 run 狀態的入口先經過 `inspect_run`：manifest、state、ledger 與日誌須可解析、形狀有效且相互一致。
    日誌領先帳本就拒絕；帳本領先日誌可復原，`RESUME.reconciliation.ledger_ahead` 明示缺少的日誌事實。
    帳本不存在只在持久空日誌且 state 為 RUNNING 的第一次存檔前允許初始化。缺失、損壞或尾端撕裂的日誌一律拒絕。
12. 被拒絕的 resume/replay/check 不寫任何檔案，包含鎖的 PID。`_lock` 只 flock；通過所有檢查才寫 PID。
13. SIMULATION transport 必須明確標記 `is_simulated = True`；Runner 與 Probe 在任何檔案寫入前檢查。
    指令、題庫 manifest 與 fixture 都從同一次讀取的位元組雜湊並消費，送出的指令另核對設定 pin。

## 指令

```bash
# 建立設定檔（預設 DRAFT_NOT_FROZEN；只有通過 SIMULATION 檢查才會寫出，且絕不覆蓋）
python3 execution_profile.py build --protocol P --receipt R --bank-manifest M --pack K --pack-manifest KM \
    --source-commit C --sealed-commit S --repo REPO --out PROFILE.json
python3 execution_profile.py check PROFILE.json [--mode SIMULATION|LIVE] [--deep]

# 執行器
python3 exploration_runner.py check  --profile PROFILE.json [--mode LIVE] [--out DIR]
python3 exploration_runner.py run    --profile PROFILE.json --out DIR --mode SIMULATION [--allow-formal-simulation]
python3 exploration_runner.py resume --profile PROFILE.json --out DIR --mode SIMULATION
python3 exploration_runner.py replay --out DIR
python3 exploration_runner.py run    --profile PROFILE.json --out DIR --mode LIVE --approval-file F   # 條件齊全前一律被擋

# 探針
python3 probe_2b.py register --profile PROFILE.json --engineering-bank DIR --out REGISTRATION.json
python3 probe_2b.py run      --registration REGISTRATION.json --out DIR --mode SIMULATION
python3 probe_2b.py run      --registration REGISTRATION.json --out DIR --mode LIVE --approval-file F   # 條件齊全前一律被擋
python3 probe_2b.py recover  --out DIR
python3 probe_2b.py report   --out DIR
```

結束碼：`0` 完成、`2` 被擋（沒有送出任何請求）、`3` 執行或探針停止。

LIVE 的守門條件由命令列入口強制執行：Owner 簽署並綁定到這份設定檔（或註冊檔）的核准、環境裡已有 `OPENAI_API_KEY`、
設定檔已凍結、介面已被 Judge 判為 `BENCHMARK_READY`、總保留額已凍結且涵蓋整輪。直接 import 類別會繞過這些條件
（測試就是這樣做的）；唯一被認可的花錢路徑是命令列。

## 驗證

```bash
python3 -B -m unittest -q test_execution_profile test_exploration_runner test_probe_2b
python3 -B mutation_runner.py --check-patterns     # 每個變異體恰好命中一處，且指定的測試真的存在
python3 -B mutation_runner.py --workers 2           # 全部變異體；可用 --results-json 保留每個子程序完整輸出
```

- 三個測試模組都不開任何 socket、不呼叫任何模型；崩潰測試用真的子行程與真的 SIGKILL。
- 變異驗證沿用已驗證的 R2 判準：變異後的來源要能編譯、能匯入，指定測試要在測試模組內以**斷言**失敗；
  崩潰、逾時或由被測程式自己丟出的錯誤不算殺死。
- 確切的測試數、變異體數與封存樹上的執行紀錄見證據根目錄的 `runner-impl/`。

## 已知限制（照實列出）

- 價格與 usage 公式來自已封存的 adapter 的上界；真實 usage 欄位的語意只有階段 2B-LIVE 才能驗證，目前沒有。
- 探針的輸入計數保留額 US$0.05 是保守的保留值，不是計價事實。
- `raw_upper_cost` 是 adapter 上界公式的複本（測試保證兩者一致）；adapter 若改版必須同步。
- 題庫本身的已知限制（L1、N1、N2、位置線索）不在這個套件的範圍內，沿用收據的原樣記錄。
- 這裡沒有任何東西能證明模型的行為；它只保證：若之後真的跑了，錢花得有據、崩潰不會重複花、證據完整可重播。
