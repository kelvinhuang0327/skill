# ENSEMBLE3 工程工具

這個目錄是新建的研究工具，不是 Fable skill。`../fable-ensemble3-r1/` 是獨立 DELTA 已驗證的 R2 候選；
本目錄不修改該候選或正式 skill。最新階段狀態見
[PROGRESS.md](/Users/kelvin/VibeCoding-WorkSpace/fable-ensemble3-r1-evidence/PROGRESS.md)。

## 已有能力與邊界

| 工具 | 能驗證的事 | 仍需完成的事 |
|---|---|---|
| `build_engineering_bank.py` | 8 題具來源/family 與共同 oracle 的工程 fixture；4 題應採用，4 題應 escalation | 新的正式 hard set、來源審核、正式 split |
| `decision_engine.py` | 以登錄的公開 facts/checks 查 gate 與反證；單跑及三票使用同一政策；完全不讀 oracle | 真實外部狀態的 checker adapter；fixture 的 bool 不能冒充 production authority |
| `budget_ledger.py` | 單寫者 atomic ledger、整輪含一次 infra retry 預留、未知成本按 cap 記帳、不確定啟動不重複 | 實際 provider 的 cap 驗證與用量收據 |
| `offline_replay.py` | 模擬的 round-robin、固定分母、失敗、timeout、retry、中斷恢復與重播 | 真實 provider 整合；此命令拒絕正式題庫 |
| `responses_adapter.py` | 無工具、無前次對話的 API 請求格式；回報 model/usage、輸入預檢及生成上限的契約 | 有認證的 LIVE capability probe；尚未呼叫 API，也未接入正式 runner |
| `exploration_readiness.py` | 草稿/工程題庫/欠缺身分與 cap 證據/不足預算/變更 artifact 都拒絕進入正式探索 | 來源與 oracle 的正確性需獨立審核；機械驗證不能代替此審核 |

公開資料禁止在任意巢狀 JSON 欄位帶入 `acceptable`、`unsafe` 或 oracle 路徑。
模型只接收單題 public JSON；scorer 與題庫驗證者才可讀 private oracle。
測試會改寫、刪除並以 macOS sandbox 阻擋 oracle，從 fresh process 比對 aggregator 輸出逐 byte 相同；
同一 sandbox 同時有允許讀 public 的正向對照。這驗證工程 aggregator 的隔離，不是對未執行的 LIVE API 的驗證。

`offline_replay.py` 的 provider 故意用 oracle 給正確答案，作為管線正向對照。
所有報告明確標 `SIMULATED_ENGINEERING_ONLY`；其成功率、成本及分類都不是模型實績，不能拿來主張 uplift。
工程 fixture 作者看過所有 oracle；它們永遠是 `ENGINEERING_ONLY`，不能升格 confirmation。

## 重現

在本目錄執行（不需 API key、不呼叫模型）：

```sh
python3 -B -m unittest -q test_lab test_responses_adapter test_exploration_readiness
python3 -B mutation_lab.py
```

mutation 只在外部 TemporaryDirectory 的副本修改 source。先跑原版基準；每個 mutant 必須編譯及匯入成功，
並由指定測試的行為斷言抓到才算 KILLED。語法/匯入錯誤、實作自己的 AssertionError 或其他例外不算。
`mutation_lab.py` 保存 mutant 名稱、patch、目標測試；它重用凍結 R2 的 traceback 判定器。

正式探索預檢（目前應輸出 BLOCKED 並以 exit 2 結束；不會啟動模型）：

```sh
python3 -B exploration_readiness.py /Users/kelvin/VibeCoding-WorkSpace/fable-ensemble3-r1-evidence/exploration-design/protocol.draft.json
```

工程樣本可在新的輸出目錄重建並重播；請保留既有證據與 ledger，不覆寫、不隱含重啟。
`Ledger` 的 cap 是對 advertised provider cap 的預留；超限會持久記錄 OVERRUN 並停止後續啟動，
不代表 ledger 本身能阻止一個違反契約的 provider 已經花掉的費用。

## 呼叫與成本

本輪只執行過 1 次 `gpt-5.6-luna/medium` 的 CLI reply probe，沒有注入 Fable、沒有 benchmark 題。
回傳結構化答覆及 token 用量，但沒有 model ID 或 billed USD；不能據此宣稱正式介面已通過。
本機 model/list 的 Luna 是 5.6，與桌面工具的 6 版本不同；5.6/medium 只是暫用設定，未證明與 H1/H2 同一設定。

已準備 PR #63 版本的 Codex worker 26 份 Markdown 參考與逐檔 hash，並保存 decision-only 指令候選。
這是把參考檔內嵌的工具禁用版本；未在模型上驗證，不等於現行 Fable 的完整執行路由。
後續對照需要把這個指令包的效果也與聚合效果分開，不能全部歸因給 schema。

API adapter 只讀 `OPENAI_API_KEY` 環境值作認證，不輸出/保存 key。不存在認證時，在 transport 前阻擋。
單元測試全部使用明確標為 `SIMULATED_TRANSPORT` 的測試 transport。
USD 的 `estimated_usd` 與 `cost_upper_usd` 是固定標價基礎的估算；`actual_usd` 保持 null。
同成本對照需先固定同一成本口徑，不能把 ChatGPT token 用量假裝成 API 發票。
