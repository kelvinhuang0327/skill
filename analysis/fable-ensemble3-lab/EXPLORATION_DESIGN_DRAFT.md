# FABLE_ENSEMBLE3_R1 後續探索設計草稿

狀態：`DRAFT_NOT_FROZEN`。這份是階段 4 的準備，沒有正式題庫、沒有 confirmation split，也沒有模型實驗結果。
Owner 已要求續行全部階段；各階段出口仍適用。R2 原規格的「不授權任何 run」是舊草稿地位，
不抵消後來的 Owner 授權；本草稿沒有理由以此重問同一個許可。未定數值則不得冒充已凍結的實驗參數。

## 階段 4 凍結前的必要資產

1. 正式來源清單：至少 30 道新的 DECISION 題、至少 6 個來源 family，依 R2 的部署可觀察特徵選題。
   這只是探索地板，不是 power 保證；優先增加不同來源而不是同源變體。H1/H2、ablation 與 8 題工程樣本均已曝光，
   不進 confirmation。不得根據同批 run 的成功率挑題。
2. 每題共用 oracle：封閉 enum 含 ESCALATE；acceptable 與 unsafe 不重疊；至少包含應執行及應 escalation 的題；
   oracle 需經獨立來源裁決審核。題庫 manifest 綁 public 與 private hash。行為檢查只引用部署也可見的證據，
   不從正解反推 gate 的真假。工程 checker 目前只認已固定的 facts，不能直接冒充真實 production 狀態查證。
3. family 紀錄：`source_ref`、`lineage_ref`、family 與 append-only exposure。模板重寫、同事件不同問法，
   永遠是同一來源 family。來源真實獨立性需獨立審查，程式檢查只能抓已標明的同源拆分。
4. 模型執行介面：暫用 gpt-5.6-luna、medium；必須取得可辨認模型、用量與生成 cap 的 LIVE provider 收據，
   並固定無工具、無歷史、無記憶的請求。API adapter 目前只有離線契約，不能當作 LIVE 成功。
5. Fable instruction closure：以 PR #63 對應樹版本為候選，逐檔保存必需 worker/routing/authority 參考與 hash；
   明確記錄 decision-only 請求和現行完整 Fable 路由的差異。未完成 closure，不稱為 Luna+Fable benchmark。
   已準備 Codex worker 的 26 份 Markdown 參考、與 HEAD 的逐檔比對，以及約 210 KB 的指令包候選。
   此候選把參考內嵌、禁用工具與 executor/Judge；尚未做 LIVE 驗證。階段 6 還要分開量這項改編的效果，
   不能把指令包與 schema 相對現行路由的所有差異，統稱為 schema 效果。
6. 同一實作內容的離線驗證、變更後重新審核；正式題庫/模型/指令/政策任何一項變更都開新 epoch。

## R1 的預定比較

每題 N=5 次 fresh independent session；一個 arm：同 schema 的 Luna+Fable。
以同一批 5 次 run 計算全部 10 個三人組的平均。5 次是取樣次數，10 個三人組不是 10 個推論樣本。
完整題池計分；同題單跑是成員平均，沒有另跑一份不同 prompt 的 single。

主要效果為 `aggregation_effect = gated - single_gated`；同時分開報 checker_effect、total_effect、
crit_reduction、候選覆蓋、policy loss 的三段、正確/可避免 escalation、失敗與 unresolved。
`always_escalate` 基準與應執行題的成功率並列，防止把不執行當免費成功。
`FALSE_COMPLETION`、`WRONG_TAKEOVER` 只能在階段 8 的 executor/Judge 實驗量，R1 不宣稱改善它們。

policy、分母、缺失處理、DECISION 範圍與 interval 使用已驗證 R2 原實現。
family 為推論單位；CR1 + t 的 90% 區間是候選近似方法，不是涵蓋率保證。
R2 的探索分類允許 aggregation 或 critical reduction 任一訊號觸發 HARVESTABLE_SIGNAL；
這仍是探索、多指標篩選，不能直接拿來作確認性顯著結果。
δ_min=0.03、N=5、seed=6302 是本輪草稿預設，需連同最終題庫與費用上限一起凍結。

## 預算與停止

暫用 API adapter 的 conservative standard-list-price 每次預留 $0.55；含一次 infra retry，
30 題 × 5 次 × 2 attempts × $0.55 = **$165 預留上限示例**。
這是標價基礎的保守計算，不是本輪已花費、平均成本、發票，亦未登錄為可用總預算。
定稿時重新核對價格、service tier、token/caching 規則與計費資料；所有 arm 比較同一口徑。
原版公開價格來源：[gpt-5.6-luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna)。

開跑前依整輪含 retry 的最壞成本固定可跑輪數；少於 3 輪就不啟動。實際成本較低不追加輪數。
timeout、budget halt、invalid output、infra retry 用完與中斷不丟棄；成本未知按 cap 保守記帳。
有 durable START_INTENT 卻無結束收據的 run 記 INCOMPLETE，不重複呼叫；未開始才是 NOT_RUN。
總 cap 或停止分類成立就結束，不臨時加題/加run追求好結果。

## 階段 5–10 的條件路徑

| 階段 | 執行內容 | 決定下一步的證據 |
|---|---|---|
| 5 同質探索 | 上述 Luna+Fable ×N，完整登錄題池離線算政策 | checker、aggregation、critical、成本及 family 不確定性；允許 INCONCLUSIVE |
| 6 實務對照 | 現行 Fable 真實 route（含 STOP）、schema single、single+checker、ensemble、同成本強模型；異質組另立假設 | schema 本身與聚合效果分開；較簡單方案是否已足夠 |
| 7 獨立確認 | 選定一個候選與主要對照後，先凍結新的確認協議，再使用未參與選擇的新來源 family | 明確的主要效果與安全門檻、成本口徑、一次性樣本數/停止；未過不宣稱導入價值 |
| 8 完整執行 | 在可丟棄環境接 executor/Judge；實際驗證接管、重複執行、恢復、完成宣稱 | 決策優點能否轉成完整安全任務成功；此時才寫具體工作包 |
| 9 Fable 整合 | 掛既有路由，客觀觸發、停用、回退、觀測及獨立審查 | 僅對階段 7–8 支持的範圍整合；不另建重複 trigger 清單 |
| 10 有限啟用 | 有界範圍與可回退的配置；觀測收益/回歸 | 實際效益成立才擴大；需要 production 動作時先提出已驗證、可審閱的具體變更 |

確認題目前不建立/展示，不把探索後改寫的題誤叫 holdout。正式來源池在任何 run 前以 family 分割，
依 R2 的 salt+family 機械規則作指派，分割清單及曝光都封存；confirmation 在協定凍結前不做 smoke。
確認樣本量依探索估計的 family 變異與預定最小效果計算；R2 的 Beta 示例不替代這個計算。
安全門檻用預登記的 absolute-risk/paired-difference 區間判斷，零個已觀察事故不等於風險為零。

只有 checker 有效則轉向單 agent；只有候選覆蓋提高而聚合拿不到則研究選擇器；
同質設定無效只否定該設定。stage 8–10 目前只保留方向，沒有預設 ensemble 最終會導入。
