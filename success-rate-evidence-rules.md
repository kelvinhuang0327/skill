# 成功率證據規則 — v0.1

## 地位聲明

這不是 Skill。這是一份極短的規則參考,供任何要宣稱「某方法提高了預測成功率」的
任務直接套用。是否日後抽成獨立 Skill(暫定名 `prediction-evidence`),由文件末尾
「何時才抽成 Skill」的不等式決定,不是預先排定的時程。

本檔案是 2026-09-21 一輪對話(GPT 初稿 → Claude 修正 → Owner 修正 → Claude 補漏)
的最終產出。修正鏈本身就是 Rule 2(資料使用紀錄)要求記錄的那種東西,所以留在這裡:
- GPT 提出 exploration/confirmation 分離、evidence class 分類 — 方向正確,細節有誤。
- Claude 第一輪修正 fresh-context 的統計含意、relative-claim 豁免、routing 複雜度 —
  三點裡有兩點本身是錯的(fresh context 恢復獨立性;相對比較對假設誤差 robust)。
  這兩個錯誤被 Owner 用具體反例(選拔偏差不因換評審而消失;support 不同的兩個
  portfolio 在真實測度下可反轉排名)推翻。
- Owner 給出 v0.1 定稿結構。Claude 覆核時發現 Rule 5 與 Rule 1 的宣稱範圍
  (`MODEL_INTERNAL_CLAIM` / `REAL_WORLD_GENERALIZATION`)之間有一條後門——
  Rule 5 是否可能被單獨滿足就視為過關,而不是在 Rule 1 之後才執行(見下方
  Rule 5 開頭的執行順序說明,以及其後「具體的失敗案例」小節),本檔案已把它
  堵上。

## Contents

- [0. 最高原則](#0-最高原則)
- [Rule 1 — 宣稱範圍分層](#rule-1--宣稱範圍分層)
- [Rule 2 — 資料使用紀錄](#rule-2--資料使用紀錄)
- [Rule 3 — 候選凍結](#rule-3--候選凍結)
- [Rule 4 — 公平比較](#rule-4--公平比較)
- [Rule 5 — 確認方式,依 Layer 選 Evidence Form](#rule-5--確認方式依-layer-選-evidence-form)
- [INCONCLUSIVE 的兩種](#inconclusive-的兩種)
- [Fresh Context 的正確與錯誤用途](#fresh-context-的正確與錯誤用途)
- [Evidence Checkpoint 1 / 2](#evidence-checkpoint-1--2)
- [對微弱 edge 的處理](#對微弱-edge-的處理)
- [本規則自身的證據狀態](#本規則自身的證據狀態)
- [何時才抽成 Skill](#何時才抽成-skill)
- [已知但刻意延後的邊界](#已知但刻意延後的邊界)
- [最終狀態區塊](#最終狀態區塊)

## 0. 最高原則

```text
PRIMARY_OBJECTIVE:
在固定研究資源下,最大化「真正有效的方法被發現、被正確辨識、並被實際採用」的數量。

FALSE_POSITIVE_COST:
把 noise 當成真提升並採用。

FALSE_NEGATIVE_COST:
把真提升當成 noise 而放棄。兩者都是成本,不能只防其中一種。

VALIDATION_STRENGTH:
不是固定門檻。必須隨 claim 的層級(見 Rule 1)、資料暴露程度(見 Rule 2)、
effect size、以及錯判代價聯立決定。
```

探索(exploration)不受本文件任何一條規則限制:可以自由重用資料、換模型、換
feature、換 K、換 window。本文件只管「confirmation」——也就是要把某個結果
寫成「已驗證的提升」並據以做 promotion 決策的那一刻。

## Rule 1 — 宣稱範圍分層

每個結果先聲明它證明的是哪一層,不能自動升格。

```text
MODEL_INTERNAL_CLAIM:
在指定的機制假設下(例如「6/49 均勻抽獎」),這個數學/組合結論成立。
證明方式:exact enumeration / proof。

REAL_WORLD_GENERALIZATION:
這個結論在現實開獎機制下也成立。
狀態:SUPPORTED | UNRESOLVED | REFUTED | NOT_CLAIMED
```

`REAL_WORLD_GENERALIZATION` 是**強制欄位,在任何情況下都不能完全不出現**——
只要這份結果會被呈現給任何不是明確侷限在「僅限模型內討論」場合的讀者(包含
promotion 決策、對外報告、下一輪任務的 handoff),就必須誠實寫
`SUPPORTED`、`UNRESOLVED`、`REFUTED` 三者之一。`NOT_CLAIMED` 是唯一的例外值,
只在 `CLAIM_SCOPE: MODEL_INTERNAL_ONLY`——也就是完全沒有宣稱任何現實世界的
預測/成功率推論——時才合法;`CLAIM_SCOPE: MODEL_INTERNAL_ONLY` 且未顯式寫
`REAL_WORLD_GENERALIZATION: NOT_CLAIMED`,和完全省略這個欄位一樣不合規。

**均勻假設本身可能是機制論證(關於抽獎機器/程序的宣稱),不必然是從歷史資料學出
來的。** 不要因為某個方法算的是「exact」數字,就把它需要的驗證形式套用到它背後
的機制假設上——機制假設有自己的證據要求(見 Rule 5 的 `mechanism claim`)。

**相對比較不會自動豁免這一層。** 兩個 portfolio A、B 若覆蓋不同的號碼組合
(support 不同),同 K、同模型假設不保證排名在真實測度下不反轉——只要真實分布
偏向 B 覆蓋而 A 沒覆蓋的那部分,模型內排名 A > B 可能對應真實排名 A < B。「相對」
與「絕對」都要分別過 `REAL_WORLD_GENERALIZATION`,不是其中一種天生比較安全。

## Rule 2 — 資料使用紀錄

Confirmation packet 必須攜帶 selection history。Fresh context 不能洗掉資料
暴露的事實——換評審只改變「誰在判斷」,不改變「這份資料已經被用來挑過候選」
這件已發生的事。

```text
SELECTION_HISTORY:

CANDIDATES_CONSIDERED:
<已知數量 / 有界估計 / UNKNOWN>

SELECTION_DATA:
<明確期間 / 資料集 / locator>

SELECTION_RULE:
<這個候選是怎麼被挑出來的>

VALIDATION_DATA_EXPOSURES:
<次數 或 UNKNOWN>

HYPERPARAMETER_WINDOW_METRIC_SEARCH:
<搜過哪些東西>

CANDIDATE_FROZEN_BEFORE_CONFIRMATION:
YES | NO
```

任何一項不知道,不代表停止研究,而是:

```text
SELECTION_BIAS_STATUS:
UNKNOWN
```

然後**降低可做的 claim 強度**,不是阻斷任務。這個欄位應該從 exploration 一
開始就記,而不是等進 confirmation 才回頭補——回頭補不出來的部分就誠實留
UNKNOWN,讓它自然拖低下游能說的話,這樣才會形成「早記錄」的誘因而不需要
強制稽核。

## Rule 3 — 候選凍結

進入 confirmation 前,先 freeze:candidate 本身、metric、K/budget、baseline、
主要 endpoint、stopping rule。Exploration 階段這些都可以自由變動;只有要做
confirmatory claim 的那一刻才要求凍結。

## Rule 4 — 公平比較

Candidate 與 incumbent 必須共用:universe、cutoff、K/budget、資料期、endpoint、
成本口徑。比較的重心是 effect size + uncertainty,不是單看 p-value 是否小於
某個門檻。

## Rule 5 — 確認方式,依 Layer 選 Evidence Form

**Rule 5 在 Rule 1 之後執行,不是與 Rule 1 平行的獨立檢查項。** Rule 1 先決定
一個 claim 落在哪一層(model-internal / real-world);Rule 5 只負責在那一層
之內選對的證據形式,它不會、也不能替 claim 決定要不要開 Layer 2。

```text
若 claim 是 DATA_LEARNED(從歷史結果學出來的預測效力):
  → strict as-of / OOS,cutoff 紀律適用。

若 claim 是 EXACT_COMBINATORIAL(在指定假設下的精確組合/機率結論):
  → proof / enumeration 足以驗證 MODEL_INTERNAL_CLAIM。
  → 但這不豁免 REAL_WORLD_GENERALIZATION 欄位,那個欄位仍要被顯式填寫。

若 claim 是 MECHANISM(關於抽獎機制本身的宣稱,例如「機器無偏」):
  → 用對應機制證據(設備稽核、大樣本卡方檢定、製造/監管驗證),
    不強塞歷史預測式的 walk-forward。
```

**具體的失敗案例(本檔案要堵住的洞)**:B649 sealed geometry 的
`K20 ANY_PRIZE = 51.9382%` 是 exact combinatorial claim。若只照 Rule 5 表面
文字檢查「exact combinatorial → proof/enumeration → 已提供 → 過關」,報告
可以整份完全不出現 `REAL_WORLD_GENERALIZATION` 欄位,而下游的 promotion 決策
會誤以為這個數字已經對現實成立。正確流程是:Rule 5 滿足只代表
`MODEL_INTERNAL_CLAIM: VALIDATED`;`REAL_WORLD_GENERALIZATION` 仍要被填,
沒有稽核過均勻假設就老實寫 `UNRESOLVED`。

## INCONCLUSIVE 的兩種

不能只有一個模糊的 `INCONCLUSIVE`,因為兩種情況的下一步完全不同。

```text
INCONCLUSIVE_INSUFFICIENT_POWER
→ 現有資料量不足以看清楚,真實 edge 可能存在。
→ 下一步:值不值得累積更多 forward evidence?需要多少?

INCONCLUSIVE_CONFLICTING_EVIDENCE
→ 不同合理證據指向不同方向。
→ 下一步:不是加同樣的資料,是重新檢查機制/population/regime/
   metric/實作/假設本身。
```

完整的 outcome 集合:

```text
VALIDATED
REFUTED
INCONCLUSIVE_INSUFFICIENT_POWER
INCONCLUSIVE_CONFLICTING_EVIDENCE
EXPLORATORY_ONLY
```

`INCONCLUSIVE_*` 都保留候選,不等於 `REJECT`。

## Fresh Context 的正確與錯誤用途

```text
Fresh Context Judge
≠ statistical independence
≠ holdout
≠ winner's-curse correction

Fresh Context Judge 實際解決:
implementation confirmation bias
acceptance interpretation bias
self-review errors
```

換評審可以改善「誰在判斷、判斷時有沒有動機污染」,不能改變「這份資料已經被
用來挑過候選」這個既成事實。Fresh-context judge 若要參與 confirmation,必須
連同 `SELECTION_HISTORY` 一起收到,而不是只收到 frozen candidate 本身——只給
候選不給選拔史,评审看到的是一個已经被选拔偏差污染但看起來乾淨的样本,獨立
評審反而會對它產生錯誤的信心。

## Evidence Checkpoint 1 / 2

（沿用先前討論的「Gate A/B」概念,改名以避免與 `fable-method` 既有的
`operational-gates.md` 中泛用的 "gate" 詞彙混淆。）

```text
EVIDENCE_CHECKPOINT_1  (進入 confirmation 前)
問:還剩什麼獨立證據?哪些資料已經被 selection 消耗?
    candidate 是否該 freeze?需要多少 power 才夠?
目的:保護「資料一旦被看過就回不去」這個不可逆資源。

EVIDENCE_CHECKPOINT_2  (宣稱提升 / promotion claim 前)
問:現有證據到底允許說多大的話?
    REAL_WORLD_GENERALIZATION 有沒有被明確填寫?
目的:claim 的最終把關。
```

Checkpoint 1 比 Checkpoint 2 更重要,因為 Checkpoint 2 抓到的問題有些已經
來不及了——驗證資料一旦被消耗,不能靠事後補紀律恢復。

## 對微弱 edge 的處理

若真正有價值的提升可能只有 +0.1pp ~ +0.5pp 這種量級,固定要求
`p < 0.05` + 固定 N + 固定 holdout,會系統性殺掉真實但微弱的提升——這是
false negative 成本被忽視的具體後果。規則要求同時報告:

```text
EFFECT_SIZE
UNCERTAINTY
DECISION_VALUE  (這個提升量級是否值得改變行為)
POWER
COST_OF_MORE_EVIDENCE
```

而不是單一 pass/fail 顯著性判斷。資料不足時的正確結果是
`INCONCLUSIVE_INSUFFICIENT_POWER`,不是 `REJECT`——候選被保留,而不是被誤殺。

## 本規則自身的證據狀態

```text
EVIDENCE_RULE_STATUS:
EXPLORATORY_ONLY
```

「採用這套規則會提高最終成功率」本身也是一個 claim,目前沒有證據支持,只有
論證。未來要觀察:

- 假冠軍(false positive promotion)是否減少;
- 真實但微弱的 edge 是否被誤殺(false negative)增加;
- confirmation 的 turnaround 時間是否被拖慢;
- 每單位研究資源最後留下的有效 candidate 數量。

若這套規則降低了 discovery throughput 而沒有對應換到更少的假冠軍,應該簡化
甚至撤銷部分規則——規則本身不豁免於 Rule 0 的 primary objective。

## 何時才抽成 Skill

不設「累積 N 個案例」這種硬門檻,改用不等式:

```text
expected recurring cost × expected frequency
>
Skill 建置 + 維護 + routing 成本
時,才值得抽成獨立 Skill(暫定名 prediction-evidence)。
```

觸發訊號範例:不同支線反覆需要相同 evidence contract;相同的 selection-history
錯誤反覆發生;Agent 反覆把 exploration 當 confirmation;手工套用本文件開始
產生明顯的重複勞動成本。在此之前,這份短規則檔案就是全部要做的事——不建
Skill 目錄、不做四平台 materialization、不進 `skill-lifecycle` 流程。

## 已知但刻意延後的邊界

`SELECTION_HISTORY`(Rule 2)目前的範圍是**單一 claim 內**的候選搜尋史。如果
同一份歷史資料被用在多次分開的 confirmation(例如同一批開獎資料在一年內被
用來對 50 個不同策略分別做 confirmation),跨 claim 的累積多重比較不會被任何
一次的 ledger 單獨捕捉到——因為每一次個別來看都合規。

這是真實的 gap,但目前沒有這種重用頻率,所以刻意不在 v0.1 處理。**如果同一
資料集開始被反覆用於分開的 confirmation,這本身就是「何時才抽成 Skill」一節
的觸發訊號之一**,屆時應該補上跨 claim 的曝光累積追蹤,而不是現在為了防一個
還沒發生的問題預先建置機制。

## 最終狀態區塊

```text
SUCCESS_RATE_EVIDENCE_RULES:
CREATED — success-rate-evidence-rules.md (this file)

SUCCESS_RATE_VALIDATION_SKILL:
DEFER

AUTO_ROUTING:
NOT YET

EXPLORATION:
UNRESTRICTED

CONFIRMATION:
EVIDENCE-AWARE

CLAIM_LAYERING:
MODEL_INTERNAL_CLAIM vs REAL_WORLD_GENERALIZATION
(REAL_WORLD_GENERALIZATION is a mandatory field, not optional)

SELECTION_HISTORY:
MANDATORY FOR CONFIRMATORY CLAIMS
SCOPE: single-claim only; cross-claim reuse tracking deferred (see above)

FRESH_CONTEXT:
REVIEW-INDEPENDENCE ONLY
NOT STATISTICAL-INDEPENDENCE
MUST RECEIVE SELECTION_HISTORY, NOT JUST THE FROZEN CANDIDATE

INCONCLUSIVE:
SPLIT INTO INSUFFICIENT_POWER / CONFLICTING_EVIDENCE

PRIMARY_OBJECTIVE:
MAXIMIZE THE NUMBER OF GENUINE SUCCESS-RATE IMPROVEMENTS
DISCOVERED, CORRECTLY IDENTIFIED, AND ADOPTED
PER UNIT OF RESEARCH RESOURCE
```
