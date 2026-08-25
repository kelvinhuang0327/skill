# TSD Workflow

## Goal

從新的 `patch` 或 `git` 提交紀錄出發，分析實際異動內容，並輸出一份符合 `assets/base_template.docx` 風格與結構的新版 TSD。

---

## Inputs

至少接受以下其中一種來源：

1. `patch` 檔
2. `git diff` / `git show`
3. `commit range`

建議同時補充：

- 專案路徑
- TSD 基底模板路徑
- 輸出版本號
- 模組代碼，例如 `EOS`
- 文件日期

---

## Required Validation Sources

建立 TSD 時，應優先使用：

1. 實際 patch / diff 內容
2. commit message / commit list
3. 目標專案中的實際檔案路徑與類別名稱
4. 既有校正規則

若 patch 與 commit message 衝突，以 patch / diff 為主。

---

## Build Steps

1. 解析 patch 或 commit 範圍，取得異動檔案清單與 diff 內容。
2. 將異動依以下條件分群，並對照 `CLASSIFICATION_RULES.md` 決定是否合併：
   - 相同業務意圖（同一功能流程）
   - 相同技術變更模式（e.g., 全部是 javax→jakarta）
   - 相同 persistence 層組合（Entity + Repository + Service）
   - 相同外部系統整合目標
   - 相同 DTO / BO / Response 用途
   
   不得因為「歸為同一分類」就省略個別檔案路徑。每個 IN_SCOPE 檔案均需在附錄中逐一列出。
3. 依 `CLASSIFICATION_RULES.md` 判斷每群屬於：
   - 業務功能
   - 技術遷移
   - schema / persistence 配套
   - connector / web service 支援
   - 無邏輯異動
4. 為每群產生：
   - 項目名稱
   - 3.x.1 異動說明（說明應標明涵蓋的檔案數量與共同模式）
   - 3.x.2 異動程式清單：內容與 placement 依 `CHANGE_PROGRAM_CONTRACT.md`（依 template mode 決定
     是 appendix_only 或 inline_per_file）
   - 3.x.3 代表範例 diff（1–2 個，代表整群的核心模式，不需每個檔案都有範例）
   - 附錄完整異動程式清單：內容依 `CHANGE_PROGRAM_CONTRACT.md`
5. 將結果套入 `EOS_v28_0421` 結構，保留：
   - 目錄（TOC）：透過 `update_toc_entry()` 更新，只改 content runs，不破壞 `<w:hyperlink>` 結構與 PAGEREF
   - 附錄列表：以 `appendix_title_idx + 2` 取 `proto_bullet`（`v24ListBullet`），不可用 `+1`（`v24heading2`）
   - 表格型 diff 範例：逐行建立帶顏色的 `<w:r>` runs，詳見 `OUTPUT_SPEC.md` OOXML Style Constraints
6. 在輸出最終 docx 之前，對每個 3.x.3 代表範例 diff cell 執行 `tools/rule21_linter.py`（Rule 21.1–21.3 決定性檢查：git metadata / ellipsis / 是否逐字可追溯至 patch）。輸入為已擷取的 cell 文字與對應 patch 原文，不需先產生 docx；規則全文與語意權威見 `SKILL.md` Rule 21，本步驟只是機械化執行。任一 unit 出現 violation 即為 FAIL，須修正代表範例後重新產生，不得略過或事後補登。Rule 21.4（節錄說明段落須在表格外）為 DOCX 結構性檢查，不在此腳本範圍內，仍依 `SKILL.md` Rule 21 既有方式確認。
7. 輸出新版本 docx，必要時同步輸出 md。

---

## Hard Constraints

- 不可只根據檔名猜功能。
- 不可只根據 commit title 判斷業務意圖。
- 不可把 `jakarta` / `swagger` / `openapi` / `cxf` / `axis` / `@Id class` / import 調整直接寫成新業務功能。
- 不可因為有 controller / service 名稱就自動認定為完整功能新增。
- 不可將同一技術遷移模式（e.g., javax→jakarta）拆成多個獨立項目來「撐大」文件。
- 不可因為合群就省略個別檔案路徑；檔案完整性要求由 `CHANGE_PROGRAM_CONTRACT.md` Layer 3 定義。
- 若多個檔案為相同模式的技術遷移（e.g., 全部是 OpenAPI annotation 替換），應合併為同一項目，不可逐檔重複。
- 若證據不足，需標記 `[unknown]` 或維持技術支援層描述。

---

## Output Contract

輸出至少包含：

1. 新版 TSD `.docx`
2. 可追溯的結構化摘要 `.md`
3. 若有爭議分類，附帶 review / validation 結果

詳細欄位請參考 `OUTPUT_SPEC.md`。
