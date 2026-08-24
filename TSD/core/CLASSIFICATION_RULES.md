# EOS TSD Classification Rules

## Primary Rule

分類時以「實際 patch 證據」優先，不以名稱直覺或業務想像為主。

---

## Category Definitions

### 1. 業務功能

只有在 patch 清楚顯示以下任一情況時，才能歸為業務功能：

- 新增完整流程入口
- 新增或明確擴充 service / controller / domain flow
- 新增可辨識的 request / response / orchestration 邏輯
- commit message 與 patch 同時支持該功能目的

### 2. 技術遷移

符合以下任一條件，優先歸為技術遷移：

- `javax -> jakarta`
- Swagger 2 註解改成 OpenAPI 3
- Spring / Hibernate / Security 升版相容
- import / annotation / configuration 調整

### 3. schema / persistence 配套

符合以下情況：

- `@Embeddable` / `@EmbeddedId`
- `IdClass`
- entity annotation / repository mapping 調整
- store procedure / repository persistence 配套

### 4. connector / web service 支援

符合以下情況：

- Axis -> CXF / JAX-WS 遷移
- CXF 生成類別
- SOAP / WS connector / QName / properties 調整
- 外部服務連線參數與設定檔變更

### 5. 無邏輯異動

符合以下情況：

- 純 import 更名
- 純 annotation 更換
- 設定檔路徑、命名、環境整理
- 不影響流程行為的工具類別補強

---

## Known Guardrails

以下類型預設不可誤寫成新業務功能：

- `MidSignRecord / DeviceBindingStatusLog` 類型若主要是 `jakarta persistence`
- `TMX` 類型若主要是 `jakarta + openapi`
- `FXRestClient` 類型若主要是註解 / import 遷移
- `Autopay` 類型若主要是 OpenAPI 註解替換
- `TO` 大量異動若主要是 `jakarta / swagger / openapi`
- `Service` 大量異動若主要是 import / annotation / connector 支援

---

## Conflict Handling

如果人工判斷、patch、commit message 三者衝突：

1. 先記錄衝突點
2. 以 patch 為主
3. commit message 只作輔助
4. 無法確認時標記 `[unknown]`

---

## Grouping Logic — 何時可以合併為一個 TSD 項目

### 允許合併（Merge）的條件

符合以下任一條件，多個檔案 **可以** 合併至同一 TSD 項目：

1. **相同業務意圖** — 服務相同的使用者流程或功能步驟
2. **相同技術變更模式** — 相同的遷移類型套用於多個檔案（e.g., 全部都是 javax→jakarta）
3. **相同 persistence / schema 層** — Entity + Repository + Service 組合支援同一功能
4. **相同外部系統整合** — Connector / Client / Config 全部指向同一外部 API
5. **相同 DTO / BO / Response 目的** — 多個資料傳輸物件均服務同一流程
6. **相同框架遷移** — javax→jakarta、Swagger 2→OpenAPI 3 等統一套用於多個檔案

### 禁止合併（Do Not Over-Merge）的條件

以下情況 **不得** 合併至同一項目：

- 屬於不同業務流程或不同業務子系統
- 整合不同的外部系統
- 具有不同的錯誤處理或 rollback 策略
- 技術遷移與業務邏輯變更混合在同一檔案集
- 合併後的項目說明過於模糊，無法支撐代表範例的追蹤

---

## New Object Handling — 新增物件的處理規則

### 新增物件的分群

若 patch 新增多個類別，以下條件符合時可分為一群：

- 全部支援同一個 API endpoint 或交易流程
- 形成自然的層次組合：Controller + Service + BO + Request + Response + Entity + Repository
- 全部屬於同一個業務領域

### 新增物件的檔案清單要求（CRITICAL）

即使合併為一個項目，仍然需要：

- **每個新增檔案必須逐一列在附錄完整異動程式清單中**（列舉位置與 placement 依
  `CHANGE_PROGRAM_CONTRACT.md`）
- 不得因為「已分群」就省略任何檔案的路徑記錄
- 項目說明應標明「N 個新增類別，支援 X 功能」

### 新增物件的代表範例選擇

- 每項目選 1–2 個代表範例
- 優先選：Controller endpoint method 或 Service method（能呈現業務邏輯）
- 避免選：純 POJO / getter-setter（除非該項目主旨即為資料模型）

---

## Technical Migration Merge Rule — 技術遷移合併規則

### 應合併的遷移類型

以下遷移類型相同時，**應合併**為一個項目（每種類型一項）：

| 遷移類型 | 說明 |
|---|---|
| javax → jakarta | import 更名，套用於所有受影響檔案 |
| Swagger 2 → OpenAPI 3 | @Api→@Tag、@ApiOperation→@Operation 等 annotation 替換 |
| DTO field annotation 遷移 | Jackson / validation annotation 統一調整 |
| Repository ID mapping | @Embeddable / @EmbeddedId / IdClass 模式變更 |
| CXF 生成類別更新 | WSDL-generated connector class set |
| Axis → CXF / JAX-WS | Connector 框架遷移 |

### 遷移合併的邊界限制

即使是相同遷移模式，以下情況仍應分開：

- 整合不同外部系統的遷移（每個外部系統一項）
- 技術遷移同時包含業務邏輯變更（需拆分為獨立項目）

### 遷移項目的代表範例要求

- 選 1–2 個最能呈現遷移模式的檔案作為範例
- 範例 diff 必須清楚顯示 before / after annotation / import 的變化
- 項目說明必須標明：「本項目涵蓋 N 個檔案，均為相同 \<遷移類型\>」

---

## File-Level Completeness Rule（CRITICAL）

每個出現在 patch 中的 IN_SCOPE 或 SUPPORTING_SCOPE 檔案，都必須有明確的文件記錄。

### IN_SCOPE 檔案

- **必須** 出現在附錄完整異動程式清單（此完整性要求由 `CHANGE_PROGRAM_CONTRACT.md` Layer 3 擁有）
- 是否同時直接出現在 3.x.2，取決於 template mode 的 placement；
  由 `CHANGE_PROGRAM_CONTRACT.md` Layer 4 定義，本檔不另行規定
- 即使已合併至某群項目，個別檔案路徑仍需逐一列出

### SUPPORTING_SCOPE 檔案

- 若被 IN_SCOPE 程式碼直接引用，必須出現在附錄並說明分類理由
  （此為**條件式**要求，非無條件要求）
- 不需計入異動程式清單的主計數

注意：`SUPPORTING_SCOPE` 與 `SKILL.md` Rule 23 的 `SUPPORTING_DOCUMENT` 為**不同分類**，
不得互相推論。`n` / `file_count` 的完整計數規則為 UNDEFINED，見
`CHANGE_PROGRAM_CONTRACT.md` U6。

### OUT_OF_SCOPE_REVIEW 檔案

- 必須記錄於 Scope Review Report，附上分類理由
- 不可出現在主 TSD 的任何清單中

### 禁止模式（Anti-Pattern）

若一個 IN_SCOPE 或 SUPPORTING_SCOPE 的檔案從權威的異動程式列舉（附錄）中消失，即為
**完整性缺失（Completeness Failure）**。

每個合群的項目必須明確列出所有檔案，不得僅提及「部分代表性檔案」。

---

## Representative Example Selection Rule — 代表範例選擇規則

### 預設：每項目 1–2 個代表範例

- 若只有 1 個有效的 patch 可追蹤檔案，可使用 1 個範例
- 若有 2 個以上的有效檔案，應有 2 個範例（參見 SKILL.md Rule 5）
- 範例必須呈現該項目的核心技術或業務模式

### 範例品質優先順序

| 優先 | 優先選用 | 理由 |
|---|---|---|
| 高 | Controller endpoint method（含業務邏輯） | 呈現 API 編排 |
| 高 | Service method（含 domain 邏輯） | 呈現核心演算法 |
| 中 | Repository method（含查詢） | 呈現資料存取 |
| 低 | BO / DTO（含欄位） | 呈現資料結構 |
| 避免 | 純 POJO（只有 getter/setter） | 無行為可追蹤 |
| 避免 | 設定檔 / properties | 無程式碼模式 |

### 範例代表群組，不代表每個檔案

- 1 個範例 ≠ 1 個檔案
- 範例是為了代表同群所有檔案的共同模式
- 其他所有檔案透過附錄提供追蹤路徑

### 特例：純 POJO 項目

若一個項目的所有異動均為 POJO 資料類別（e.g., 全部 Response 物件新增一個欄位），則 POJO 範例是可接受的，但項目說明必須明確標明「本項目為資料模型異動」。
