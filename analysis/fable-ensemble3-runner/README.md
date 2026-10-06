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
11. 所有讀取 run 狀態的入口（inspect、resume、replay、報告、write ledger、CLI 的 resume／replay／check）都先經過同一個
    `inspect_run`：manifest、state、ledger 與日誌須可解析、形狀有效且相互一致。是否放行只取決於 manifest、state、ledger 與
    日誌中的**具體事實紀錄**（`ROUND_RESERVED`、`START`、`SETTLED`）。
    - 每一筆具體紀錄都要對得上帳本（輪次已預留、嘗試已開始、結算內容等於帳本收據）、不得重複，也不得出現在它所依賴的事實之前：
      輪次依序；`START` 在其輪次之後，重試在第一次結算之後；`SETTLED` 與 `REQUEST`／`RESPONSE`／`ERROR` 在其 `START` 之後
      （`ATTEMPTS_DUPLICATE_*`、`ATTEMPTS_OUT_OF_ORDER:*`、`LEDGER_BEHIND_*`）。
    - 帳本有、日誌沒有具體紀錄的事實是「缺失」；實際缺失數超過 `MAX_UNACKNOWLEDGED_AHEAD`（3）就拒絕（`ATTEMPTS_BEHIND_LEDGER`），
      沒有任何折抵。
    - **日誌不為自己背書**：`RESUME` 只是稽核註記，gate 不讀它的任何欄位（只要求是有時間的 JSON 物件），所以它既不能抵銷缺失，
      也不能造成拒絕。
    - 每次寫帳本之後、下一次寫帳本之前，都先寫它的那一筆日誌紀錄，所以合法崩潰最多留下 1 筆缺失。`resume` 在做任何其他事之前，
      先把每一筆缺失依因果順序寫成**各自獨立**的具體紀錄（`reconciled: true`、`derived_from: "ledger.json"`、
      `original_payload: "LOST"`，內容只取自帳本），再復原未結算的嘗試（復原也寫一筆 `SETTLED`，
      `kind: RECOVER_UNCERTAIN_NO_RELAUNCH`），最後才寫 `RESUME`，然後才可能有新請求。
      回補途中被殺：已寫的那幾筆就是具體紀錄，剩下的由下一次 resume 補完（順序保證每一筆都在它依賴的事實之後）；
      回補完成但 `RESUME` 未寫：沒有影響；復原已寫入帳本但紀錄未寫：下一次 resume 以回補補上；連續多次崩潰同理，缺失數只會減少。
    帳本不存在只在持久空日誌且 state 為 RUNNING 的第一次存檔前允許初始化。缺失、損壞或尾端撕裂的日誌一律拒絕。
12. 守門階段被拒絕的入口（上列 8 個）不寫任何檔案，包含鎖的 PID，也不送出任何請求。`_lock` 只 flock；通過所有檢查才寫 PID。
    已送出請求的呼叫仍可能在最後收尾被拒絕；BLOCKED 的 `generation_calls` 回報本次呼叫實際嘗試送出的生成請求數，不抹成 0。
13. SIMULATION transport 必須明確標記 `is_simulated = True`；Runner 與 Probe 在任何檔案寫入前檢查。
    指令、題庫 manifest 與 fixture 都從同一次讀取的位元組雜湊並消費，送出的指令另核對設定 pin。
14. **終局不缺事實，判定只在共用 gate 裡做一次。** 合法執行在狀態轉為 COMPLETE／HALTED 之前就寫完所有日誌紀錄，所以
    `inspect_run` 對已結束的執行：缺任何帳本事實就拒絕（`TERMINAL_RUN_JOURNAL_INCOMPLETE:<狀態>:<n>_MISSING`），所有入口同一個原因、
    零請求、目錄不變。唯一例外是 `PERSISTENCE` 停止（日誌寫不進去的終局）：放行但帶旗標 `INCOMPLETE_JOURNAL_BY_PERSISTENCE_HALT`
    與缺失清單，永遠不是乾淨的結果。RUNNING 的 1～3 筆缺失照舊可續跑，帶旗標 `RESUMABLE_JOURNAL_GAP`；回補過的紀錄帶旗標
    `RECONCILED_FROM_LEDGER`。`_finalize` 以「即將寫入的終局狀態」呼叫同一個 gate（`closing`），所以會被拒絕的收尾一個位元組都
    不寫，執行停在 RUNNING，由 resume 先回補；PERSISTENCE 停止則帶旗標寫成 HALTED（D0-3 選擇 (a)）。
15. **每個出口逐字帶出 gate 的判定**（`integrity`：`status` CLEAN／FLAGGED、`state`、`halt_reason`、`missing`、`missing_count`、
    `reconciled`、`flags`）。gate 通過之後才做的拒絕（已結束、已認證、身分不符）也帶著它（`RunBlocked.integrity`，命令列印成
    `integrity`）。命令列：`check --out` 與 `replay` 對帶旗標的執行回 `FLAGGED`／結束碼 `4`，不會印出無旗標的 PASS。
16. **write ledger 只寫一次。** 只為已結束的執行寫（RUNNING 時 `WRITE_LEDGER_RUN_NOT_TERMINAL`），已存在就拒絕
    （`WRITE_LEDGER_ALREADY_CERTIFIED`，目錄不變；以 hard link 發布，存在即失敗），內容含同一份 `integrity`。已結束的執行每次經過 gate
    都重新核對 write ledger：列出的檔案位元組、沒有多出的檔案、判定相同，否則拒絕（`WRITE_LEDGER_MISMATCH:<檔案>`、
    `WRITE_LEDGER_UNLISTED:<檔案>`、`WRITE_LEDGER_INTEGRITY_MISMATCH`、`WRITE_LEDGER_INVALID_OR_UNREADABLE`）。
    發布後、清除暫存名之前被殺留下的第二個連結，只有在同目錄、名稱為 `write_ledger.json.<32 位小寫十六進位>.tmp`、
    是一般檔案且與 write ledger 的裝置及 inode 相同時才豁免。這只辨認 write ledger 本身的第二個連結，不證明由發布流程建立。
    同內容的複本、符號連結、子目錄裡的 hard link、錯誤長度或大寫的檔名都不豁免。
    把已認證的執行的狀態改回 RUNNING：唯讀 gate 帶旗標 `WRITE_LEDGER_EXISTS_NOT_RECERTIFIED`；`resume` 以 `RUN_IS_CERTIFIED`
    拒絕，帶同一判定、零新請求、目錄不變。收尾仍不重寫既有 write ledger，作為第二道防線。

| 入口 | 類別 | 帶出的完整性欄位 |
|---|---|---|
| `inspect_run` | 唯讀 | `integrity`、`certificate`（ABSENT／VERIFIED／PRESENT_WHILE_RUNNING） |
| `replay_run` | 唯讀（含重建 report） | `integrity`、`certificate`；COMPLETE 時重建的 `report.integrity` |
| `exploration_runner.py check --out` | 唯讀 | `run_integrity`、`run_certificate`；`status` PASS／FLAGGED／BLOCKED，結束碼 0／4／2 |
| `exploration_runner.py replay` | 唯讀 | `integrity`、`certificate`；結束碼 0（CLEAN）／4（FLAGGED）／2 |
| `Runner.start`／`run` | 寫入（建立執行） | 回傳與命令列的 `integrity`；`report.json`、`write_ledger.json` 的 `integrity` |
| `Runner.resume`／`resume` | 寫入；已認證執行一律拒絕 | 回傳與命令列的 `integrity`（收尾後）；拒絕時 `RunBlocked.integrity`／命令列 `integrity` |
| `Runner._finalize`（report 與 write ledger，run／resume 的最後一步） | 寫入（只對 RUNNING 收尾一次） | `report.json` 與回傳的 `integrity`；對已結束的執行拒絕（`RUN_IS_<狀態>`，帶判定）且不寫 |
| `Runner._write_accounting`（write ledger） | 寫入（一次） | `write_ledger.json` 的 `integrity`；拒絕時帶判定 |

## 日誌與帳本的信任邊界（照實列出）

gate 只比對執行目錄裡的檔案，沒有外部錨點。它防的是崩潰與單一檔案的損壞或回退，不是認證。

**能辨認**（具名拒絕、伺服器端零請求、執行目錄逐位元組不變）：

- 日誌遺失、讀不出、中間有壞行、尾端撕裂。
- 日誌領先帳本：記了帳本沒有的輪次、嘗試或結算，結算內容與帳本收據不同，或記了超額而帳本沒有。帳本被換成較早但本身有效的快照，
  只要日誌記過之後的事實，就屬於這一類。
- 帳本領先日誌超過 3 筆：日誌被清空、截短，或刪掉 4 筆以上沒有後續依賴的紀錄。
- 刪掉一筆仍有後續紀錄依賴的事實（例如刪掉 `START` 而保留其請求與結算），把紀錄移到它所依賴的事實之前，或同一事實記兩次。
- 偽造的自述：任何 `RESUME`（或任何欄位）宣稱已回補、已確認、已驗證，一律不讀：不能讓應拒絕的變成可執行，也不能反過來。
- 帳本本身損壞、形狀錯誤或內部不一致；manifest／state 遺失或損壞；第一次存檔後帳本不見。
- 已結束（COMPLETE，或 PERSISTENCE 以外原因的 HALTED）的執行缺任何帳本事實：每個入口都拒絕；PERSISTENCE 停止缺事實：每個入口都帶旗標。
- write ledger 寫好之後，執行目錄裡任何列出的檔案多改一個位元組、多一個未豁免檔案（包括 `.DS_Store`）、少一個檔案，
  或 write ledger 本身被改：已結束的執行每個入口都拒絕；沒有自動修復，人工處置須另行核定。
  write ledger 本身的、符合規則 16 的第二個連結是唯一豁免。已認證的執行即使狀態被改成 RUNNING，也不能 resume。

**不能辨認**：

- 帳本與日誌被協同改寫成互相一致（例如兩者一起回退到較早的一致狀態，或一起截短）。
- 日誌被改成與帳本一致的偽造具體紀錄：刪掉真紀錄再寫上內容相符的替代（標不標 `reconciled` 都一樣），與真正的回補無法區分。
- 最多 3 筆沒有後續依賴的事實紀錄被刪掉（例如最後幾筆結算），與崩潰視窗無法區分。合法崩潰只需要 1 筆，另外 2 筆是固定的容忍量。
  resume 會依帳本把它們補回，帳務不受影響，但原紀錄的時間、`kind`、`detail` 就此遺失。
- `REQUEST`／`RESPONSE`／`ERROR` 被刪掉或修改：帳本沒有對應的事實。特別是刪掉一個未結算嘗試的 `RESPONSE`，復原就按上限計費，
  而不是按那個回應實際（可能超過上限）的用量。
- 彼此沒有因果關係的紀錄互換順序（例如兩個不同格子的紀錄對調）：接受，對帳務沒有影響。
- `RESUME` 被刪掉或偽造：不影響任何決定，也不會被發現（它本來就只是註記）。
- write ledger 不是外部錨點：帳本、日誌與 write ledger 被協同改寫成互相一致（或整個目錄被換成另一個自洽的目錄），無法辨認。
  單獨刪除 write ledger 也會讓終局回到未認證：若其餘檔案通過 gate，唯讀出口為 CLEAN，之後可為目前位元組重新寫入。
  這可掩蓋不涉及帳本事實的改動，不能掩蓋共用 gate 已偵測到的終局事實缺失。
  寫 write ledger 之前（例如終局狀態已寫、write ledger 未寫的崩潰窗口）對日誌做的、不缺帳本事實的改動（刪掉
  `REQUEST`／`RESPONSE`／`ERROR`、無因果關係的重排、與帳本一致的偽造紀錄），之後寫出的 write ledger 會照實列入，無法辨認。
- PERSISTENCE 停止「真的沒寫進去」的事實，與 PERSISTENCE 停止後、寫 write ledger 前被刪掉的事實，無法區分：兩者都帶旗標、列出缺失，
  都不是乾淨的結果。
- 收尾被拒絕（終局缺事實）時執行留在 RUNNING，停止原因從未落地；之後的 resume 會先回補再續跑。這與「停止的結算已寫、狀態尚未寫」
  時崩潰的既有行為相同（停止原因只在記憶體裡），本套件沒有改變它。
  主 session 的 19 種自然模擬故障未觸發拒絕收尾的形式；這只證明那 19 個案例未觸發，不能排除其他崩潰窗口，也不是驗收豁免。

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

結束碼：`0` 完成、`2` 被擋、`3` 執行或探針停止、`4`（`check --out`、`replay`）通過但帶完整性旗標，不是乾淨的結果。
執行器的 BLOCKED 輸出以 `generation_calls` 記本次呼叫已嘗試送出的生成請求數（不含 input-token 計數請求）；守門前拒絕為 0，收尾拒絕可以大於 0。

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
- `JournalOracleTests` 用一個與產品碼無關的極簡 oracle（集合差與位置）比對 gate 的決定：小型執行窮舉所有單筆／雙筆刪除、重複、
  重排，較大執行以固定種子 `20261005` 隨機產生 520 個損壞；每例都附加／不附加偽造的 `RESUME`（宣稱 1…8 筆）與偽造的
  reconciled 紀錄（帳本沒有的事實、帳本有的事實）。任何不一致，或 `RESUME` 改變了決定或原因，都是失敗。
- `TerminalOracleTests` 把同一個 oracle 擴充到終局：COMPLETE 與五種 HALTED（REJECTED、CAPABILITY、ATTEMPT_CAP_OVERRUN、OTHER、
  PERSISTENCE）各自窮舉小型執行的單筆／雙筆刪除、重複、重排（另加 R2 的 RUNNING 小型執行），較大執行以種子 `20261005:<狀態>`
  各隨機 520 例，認證與未認證兩種；規則是「終局缺事實且非 PERSISTENCE ⇒ 拒絕；PERSISTENCE ⇒ 帶旗標；認證後日誌改動 ⇒ 拒絕；
  其餘放行」。gate 的每一例都比對；八個入口在固定的子集上全部比對（判定類別、逐字帶出的判定、請求數、目錄雜湊）。
  `TerminalMatrixTests` 是 狀態 × 缺失 0～4 × 八個入口 × 兩種來源（乾淨結束後才改日誌但未認證／認證後才改日誌）的 480 格矩陣。
- bytecode 檢查以 `PYTHONPYCACHEPREFIX` 把任何未被抑制的寫入導到測試自己的目錄，並先證明拿掉抑制就會有檔案；
  原本的 `test_J` 另外以 `-X pycache_prefix`（優先於環境變數與 Apple 的使用者快取）再跑一次兩個入口。
- 變異驗證沿用已驗證的 R2 判準：變異後的來源要能編譯、能匯入，指定測試要在測試模組內以**斷言**失敗；
  崩潰、逾時或由被測程式自己丟出的錯誤不算殺死。
- 確切的測試數、變異體數與封存樹上的執行紀錄見證據根目錄的 `runner-impl/`。

## 已知限制（照實列出）

- 價格與 usage 公式來自已封存的 adapter 的上界；真實 usage 欄位的語意只有階段 2B-LIVE 才能驗證，目前沒有。
- 探針的輸入計數保留額 US$0.05 是保守的保留值，不是計價事實。
- `raw_upper_cost` 是 adapter 上界公式的複本（測試保證兩者一致）；adapter 若改版必須同步。
- 題庫本身的已知限制（L1、N1、N2、位置線索）不在這個套件的範圍內，沿用收據的原樣記錄。
- 離線測試只驗證已列出的工程性質，適用範圍受上述信任邊界限制；不代表模型能力、LIVE 介面或正式實驗已通過驗收。
