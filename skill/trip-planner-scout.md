---
name: trip-planner-scout
description: Phase 1 互動式旅行規劃（Steps 1-7）。由 trip-planner hub skill 分派，不直接觸發。
---

# Phase 1: Scout（互動式規劃）

對話循環。Agent 推動流程但**在每個關卡（🚪）等用戶確認**。

可用工具、資料檔案格式、狀態管理規則見 `skill/trip-planner.md`。

## 機票與住宿搜尋（SerpApi）

**搜尋前提（gate）— 全部滿足才觸發，缺任何一項就先問用戶：**

### 機票搜尋 gate：
- ✅ 出發地（IATA 代碼，如 TPE）
- ✅ 目的地（IATA 代碼，如 NRT、DAD）
- ✅ 出發日期（具體日期，不是「大概五月」）
- ✅ 單程/來回確認；來回需有回程日期
- ✅ 人數（預設 1）

### 住宿搜尋 gate：
- ✅ 目的地（含區域/地段更佳，如「Da Nang beach area」比「Da Nang」精準）
- ✅ 入住日期
- ✅ 退房日期
- ✅ 人數（預設 2）

**呼叫範例：**

```bash
# 機票搜尋（來回）
echo '{
  "departure_id": "TPE",
  "arrival_id": "DAD",
  "outbound_date": "2026-05-15",
  "return_date": "2026-05-20",
  "type": 1,
  "adults": 2,
  "currency": "TWD",
  "cache_path": "trips/danang-2026-05/data/flights_cache.json"
}' | direnv exec $REPO python3 scripts/search_flights.py

# 住宿搜尋
echo '{
  "q": "Da Nang beach area",
  "check_in_date": "2026-05-15",
  "check_out_date": "2026-05-20",
  "adults": 2,
  "currency": "TWD",
  "cache_path": "trips/danang-2026-05/data/hotels_cache.json"
}' | direnv exec $REPO python3 scripts/search_hotels.py
```

**可選篩選參數：**

機票（API 層級 + 本地篩選）：
- `stops`（1=直飛, 2=≤1轉, 3=≤2轉）
- `travel_class`（1=經濟, 2=豪經, 3=商務, 4=頭等）
- `sort_by`（2=價格, 5=時長）
- `include_airlines` / `exclude_airlines`（IATA 代碼，逗號分隔，如 `"VJ,IT,MM"`）
- `max_price`（最高票價）、`max_duration`（最長飛行分鐘數）
- **`lcc_only`（本地篩選）**：只顯示廉航班機。內建亞太區主要 LCC 清單（台灣虎航 IT、樂桃 MM、VietJet VJ、酷航 TR、AirAsia AK/FD/D7、宿霧太平洋 5J、香港快運 UO、濟州航空 7C 等）
- `max_results`（預設 10）

住宿（API 層級 + 本地篩選）：
- `sort_by`（API：3=最低價, 8=最高評分, 13=最多評論）
- **`hotel_class`（API 篩選）**— 設 N = N 星以上（如 3 = 三星以上，4 = 四星以上）
- **`max_hotel_class`（本地篩選）**— 星級上限（如 `hotel_class: 3, max_hotel_class: 3` = 只看三星）
- `min_price`/`max_price`（價格範圍）
- `rating`（API 篩選：7=3.5+, 8=4.0+, 9=4.5+）
- **`min_rating`（本地篩選）**：最低評分（如 4.0），比 API 的 rating 更精確
- **`min_reviews`（本地篩選）**：最低評論數（預設 20），過濾評論太少、不可靠的飯店
- **`local_sort`（本地排序）**：`"price"` / `"rating"` / `"value"`（CP 值 = 評分²÷價格，高評分權重更大）
- `amenities`（35=含早餐, 9=泳池, 19=停車場）
- `max_results`（預設 10）

**常見篩選組合範例：**

| 用戶說 | 機票參數 | 住宿參數 |
|--------|---------|---------|
| 「平價」 | `lcc_only: true` | `local_sort: "price"` |
| 「四星以上」 | — | `hotel_class: 4, min_rating: 4.0` |
| 「只看三星」 | — | `hotel_class: 3, max_hotel_class: 3` |
| 「CP 值高的」 | `lcc_only: true, stops: 1` | `local_sort: "value", min_rating: 4.0` |
| 「直飛最短」 | `stops: 1, sort_by: 5` | — |
| 「最便宜」 | `sort_by: 2` | `sort_by: 3` |
| 「含早餐泳池」 | — | `amenities: "35,9"` |

**篩選策略：** Agent 應根據用戶的預算和偏好主動加篩選條件，不要回傳未篩選的原始結果。篩選後的 10 筆結果才有比直接上網搜更高的價值。

**呈現格式：**

機票：
```
✈️ 機票搜尋結果（TPE → DAD，2026-05-15，來回）

 # | 航空       | 航班     | 出發   | 抵達   | 時長    | 轉機 | 價格
 1 | VietJet   | VJ-843  | 08:30 | 10:45 | 3h15m  | 直飛 | TWD 4,250
 2 | Vietnam   | VN-579  | 14:20 | 16:40 | 3h20m  | 直飛 | TWD 5,800

💡 最低 TWD 3,800 ｜ 常見 TWD 4,000~6,500
💡 需要某班機的訂票連結？告訴我編號
📊 本月額度：已用 2 / 250
```

住宿：
```
🏨 住宿搜尋結果（Da Nang，5/15-5/20，5 晚）

 # | 飯店              | 星級 | 評分 | 每晚      | 總價       | 最低來源
 1 | Furama Resort    | ⭐5  | 4.6 | TWD 3,200 | TWD 16,000 | Booking.com
 2 | Novotel Danang   | ⭐4  | 4.4 | TWD 2,100 | TWD 10,500 | Agoda

📊 本月額度：已用 3 / 250
```

**來回機票注意事項：**
- `type: 1`（來回）搜尋結果的**價格已包含來回兩段**，不需要 ×2
- 搜尋結果只顯示**去程航班**，每班機附帶 `departure_token`
- 用戶選定去程後，可用 `departure_token` 查回程航班選項（消耗 1 次額度）
- 呈現給用戶時務必標明「價格為來回票價」

**兒童票注意事項：**
- SerpApi Google Flights 不支援兒童乘客參數，`adults` 僅計成人
- 搜尋結果的票價**不含兒童票**。帶小孩的家庭需告知：「此價格為 N 位成人的來回票價，兒童票需另計（通常為成人票價的 75-100%）」
- SerpApi Google Hotels 同樣不支援兒童人數，房間容量需用戶自行確認

**額度節約規則：**
- 同一組參數 24 小時內不重複打 API（自動 cache）
- 不主動查 booking links — 用戶指定某班機時才用 booking_token 查（另消耗 1 次）
- 來回機票查回程另消耗 1 次，所以一趟來回完整查詢 = 2 次額度
- 搜尋前先確認所有 gate 條件滿足，避免搜太廣浪費額度
- 每次搜尋結果都顯示剩餘額度

---

## Step 1: 收集需求

詢問用戶：
- **目的地** — 哪個城市？
- **天數** — 幾天幾夜？
- **月份** — 什麼時候？（影響星期幾的營業時間驗證）
- **預算等級** — 平價 / 中等 / 高檔？
- **旅行風格** — 悠閒、緊湊、混合？（影響每天景點數）
- **交通方式** — 機車？步行？開車？大眾運輸？
- **必去景點** — 有沒有一定要去的？
- **特殊需求** — 工作旅行？飲食限制？無障礙？
- **機票/住宿需求** — 需要幫忙搜機票嗎？住宿有偏好嗎？（區域、星級、預算）

用戶如果一次給了足夠資訊，跳過多餘問題。

## Step 1b: 機票/住宿搜尋（可選）

**當機票或住宿搜尋的 gate 條件全部滿足時，在進入 Step 2 之前（或平行）執行搜尋。**

gate 條件見上方「機票與住宿搜尋（SerpApi）」。未滿足時不搜，告訴用戶還缺哪些資訊。

搜尋流程：
1. 確認所有 gate 條件 → 呼叫 `search_flights.py` 和/或 `search_hotels.py`
2. 用上方呈現格式向用戶展示結果
3. 用戶可以：
   - 選定航班/飯店 → 記錄在後續 itinerary（`type: "flight"` / `type: "hotel"`）
   - 要求換條件重搜（如不同日期、不同區域）→ 再次呼叫腳本（消耗額度，但 24h 內同參數免費）
   - 跳過 → 繼續 Step 2，之後再決定
4. 機票/住宿決策**不阻塞景點規劃**，用戶可以先規劃行程再回頭選機票

## Step 2: 生成候選景點清單

候選景點有三個來源，合併後一起呈現給用戶：

1. **用戶的 Google Maps 清單**（如果 Step 1 有提供）— 用 `import_gmaps_list.py` 匯入：
   ```bash
   direnv exec $REPO python3 scripts/import_gmaps_list.py "https://maps.app.goo.gl/XXXXX"
   ```
   匯入結果是名稱 + 座標，作為候選素材，不代表全部都會納入行程。
2. **用戶口頭指定的必去 / 想去景點**（如果 Step 1 有提到）
3. **Agent 根據需求額外推薦** — 補足用戶清單沒涵蓋的類型（例如用戶清單全是景點，Agent 補美食和住宿），總量生成**比所需多 30-50%** 讓用戶篩選。

Google Maps 清單是輸入素材，不是指令。**除非用戶明確說「就這些，不用再推薦了」，否則 Agent 仍應主動推薦額外候選。** 匯入後問用戶：「這些之中有哪些一定要去？哪些可以不去？需要我再推薦其他地方嗎？」

**來源標記：** 在整個 Phase 1 過程中（Step 2 ~ Step 7），任何時候向用戶列出景點，都必須標記每個景點的來源——哪些是用戶提供的（Google Maps 清單 / 口頭指定），哪些是 Agent 額外推薦的。這樣用戶才能快速辨識自己原本的選擇和 Agent 的建議。只有最終 Phase 2 生成網站時不需要標記來源。

每個候選提供：
- 名稱
- 類型（景點 / 美食 / 住宿 / 等）
- 來源標記（`📌 用戶` 或 `💡 推薦`）
- 推薦理由（一句話）
- `maps_query` — **必須包含具體店名或地標名 + 城市 + 國家**（不要用模糊街名）

## Step 3: 批次打 Places API + 寫入快取

**直接呼叫 `build_places_cache.py`**，不要自己寫 API 呼叫邏輯：

```bash
echo '{
  "candidates": [
    {"name": "赤崁樓", "maps_query": "赤崁樓, Tainan, Taiwan"},
    {"name": "度小月", "maps_query": "度小月擔仔麵 原始店, Tainan, Taiwan"}
  ],
  "cache_path": "trips/{slug}/data/places_cache.json"
}' | direnv exec $REPO python3 scripts/build_places_cache.py
```

腳本自動處理：
- 載入既有 cache → 跳過已快取的 → batch resolve 新的（8/batch + 1s 間隔）→ 寫回 cache
- 解析失敗的會列出，依以下順序 fallback：

**解析失敗 fallback 流程：**
1. **換 query 重試** — 加地址、換英文/中文名、加「餐廳」「咖啡」等類型關鍵字
2. **用戶提供地址** — 請用戶給具體地址或 Google Maps 連結
3. **網路搜尋** — 用 WebSearch 搜店名 + 城市，從 Instagram、Facebook、食記部落格找到地址/座標/營業時間
4. **手動建 cache entry** — 以上都找不到時，用找到的座標在 `places_cache.json` 手動加一筆 entry（key 用 `manual_` 前綴），`editorial_summary` 註明「Google Maps 未收錄」。在 `build_itinerary.py` 的輸入中，這類景點直接給 `lat` + `lng`，腳本會自動設 `place_id: null`（模板用座標連結）

很多小店（私房餐廳、新開的甜點店、預約制料理）不在 Google Maps 上但在 IG/Facebook 有頁面。**不要在 Step 1 解析失敗就放棄，先搜網路。**

**快取規則：**
- 以 `place_id` 為 key（穩定識別碼）
- **只增不刪** — 從行程移除景點不會刪 cache entry
- 後續加新景點時，先查 cache → 沒有才打 API → 打完一律寫回 cache

**API 成本：** Field mask 決定計費 tier（取最高）：
- **Pro**（$32/1000，免費 5,000/月）：`displayName`、`location`、`types`、`photos`、`formattedAddress`、`googleMapsUri`、`businessStatus`、`timeZone`、`accessibilityOptions` 等
- **Enterprise**（$35/1000，免費 1,000/月）：`regularOpeningHours`、`rating`、`websiteUri`、`internationalPhoneNumber`、`priceLevel`、`userRatingCount` 等
- **Enterprise + Atmosphere**（$40/1000，免費 1,000/月）：`reviews`、`editorialSummary`、`generativeSummary`、`serves*`、`allows*`、`goodFor*`、`paymentOptions`、`parkingOptions` 等

目前 `FULL_FIELD_MASK` 觸發最高 tier（Enterprise + Atmosphere），免費 1,000/月，實際用量 < 500/月 = **$0**。如需省成本可改用 `DEFAULT_FIELD_MASK`（只拿 3 欄位，走 Pro tier）。

## Step 4: 🚪 呈現景點清單 → 用戶打分 / 篩選

用 cache 的真實資料呈現候選清單：

```
候選景點（共 25 個，需選 ~18 個填入 3 天行程）

 # | 景點              | 類型 | 評分  | 營業時間摘要                | 網站
 1 | 赤崁樓            | 景點 | ⭐4.3 | 08:30-21:30 每日           | twtainan.net/...
 2 | 度小月（原始店）    | 美食 | ⭐4.1 | 11:00-21:00 週一公休        | duxiaoyue.com/...
 3 | 花園夜市           | 美食 | ⭐4.0 | 僅 四/六/日 18:00-01:00     | —
 4 | 神農街             | 景點 | —    | 🔓 戶外街道，全天開放        | —
 5 | 某私房小店          | 美食 | ⭐4.5 | ❓ API 無營業時間，需人工確認 | —
```

**營業時間標注規則：**
- API 有 `regular_opening_hours` → 直接顯示
- API 無營業時間，但類型為戶外/公共空間（`street`、`park`、`neighborhood` 等）→ 標 `🔓 戶外，全天開放`
- API 無營業時間，但類型為店家/景點/餐廳 → 標 `❓ API 無營業時間，需人工確認`

**請用戶：**
- ❌ 刪除不要的景點
- ➕ 新增遺漏的景點（agent 查 cache → 沒有才打 API → 寫回 cache）
- ⭐ 打分（1-5）標記優先度（可選，不打分預設 3）
- 📌 加約束條件（見下方「約束處理」）

**等用戶回覆。** 有修改就重複此步驟。

## Step 5: 路線規劃

先用 `resolve_places.py` 看分群（哪些景點在同一區 < 1.5 km）：

```bash
echo '{"places": [...]}' | direnv exec $REPO python3 scripts/resolve_places.py
```

再用 `plan_route.py` 跑 SA 優化：

```bash
echo '{
  "places": [
    {"name": "赤崁樓", "lat": 22.997, "lng": 120.202, "type": "spot"},
    ...
  ],
  "days": 3,
  "start": "飯店",
  "fixed": {
    "赤崁樓": 1,
    "花園夜市": {"day": 1, "pos": "last"},
    "安平古堡": 2
  },
  "per_day_min": 3,
  "per_day_max": 7,
  "available_modes": ["walking", "bicycling", "driving"]
}' | direnv exec $REPO python3 scripts/plan_route.py
```

`plan_route.py` 處理：
- `fixed`：指定天數（int）或天數 + 位置（dict `{"day": N, "pos": "last"}`）
- `start`：每天起點（軟偏好，不是硬約束——有 pos 約束時 pos 優先）
- SA 回傳前 N 組方案，按總交通距離排序

## 約束處理（Agent 判斷，不靠算法）

`plan_route.py` **只優化距離，不懂語意**。以下約束由 agent 在拿到 SA 結果後，用常識判斷和調整：

| 約束類型 | 範例 | Agent 怎麼做 |
|----------|------|-------------|
| 時段 | 「夜市排晚上」「早餐排早上」 | **常識判斷**：夜市當然排晚上、早餐店排早上、博物館排室內午後。不需要跑算法，直接在每天內調整順序。 |
| 先後順序 | 「先去 A 再去 B」 | 檢查 SA 結果，A 在 B 前面就不動，否則手動交換。 |
| 優先度 | 用戶打 5 星的景點被 SA 丟掉 | 告知用戶哪些高優先景點被排除，問要不要替換低優先的。 |
| 分組 | 「安平區的排同一天」 | 用 `resolve_places.py` 的 `clusters` 結果確認同區景點，檢查 SA 有沒有分到同一天。 |
| 避開正午戶外 | 「戶外景點不要排中午」 | 戶外景點排早上或傍晚，室內景點排正午。這是常識，不需要額外腳本。 |

**原則：算法給大方向（哪些景點分哪天），agent 用常識微調順序。不要把所有邏輯都丟給算法——算法可能走極端。**

## Step 6: 驗證 + 呈現路線

SA 結果 + agent 調整後：

1. **充實交通資料：**
   ```bash
   direnv exec $REPO python3 scripts/enrich_itinerary.py trips/{slug}/data/itinerary.json
   ```

2. **營業時間驗證：**
   ```bash
   direnv exec $REPO python3 scripts/check_hours.py trips/{slug}
   ```
   輸出每個景點的狀態：`✅ 到達時間在營業內`、`⚠️ 營業日但到達時間不對（早到/遲到/休息時段）`、`❌ 當天公休`、`🔓 戶外全天`、`❓ 無資料`

3. **呈現路線：**
   ```
   Day 1 — 古蹟美食巡禮（週六）
     🏨 Check-in 飯店
     🛵  5 min ｜ 1.2 km → 赤崁樓 (08:30-21:30 ✅)
     🚶  3 min ｜ 0.2 km → 度小月 (11:00-21:00 ✅)
     🛵  5 min ｜ 1.1 km → 林百貨 (11:00-21:00 ✅)
     🛵 10 min ｜ 2.9 km → 花園夜市 (18:00-01:00 ✅)

   📊 全程：機車 35 min / 步行 29 min / 總距離 12.3 km
   ```

## Step 7: 🚪 用戶回饋循環

**等用戶回覆。** 可能的回饋：

| 回饋類型 | Agent 動作 |
|----------|-----------|
| 「滿意，繼續」 | → 進入 Phase 2（見 `skill/trip-planner-build.md`） |
| 「Day 1 太趕」 | 移動景點到其他天，重跑 enrich，回 Step 6 |
| 「把 X 換成 Y」 | 查 cache → 沒有則打 API 寫回 cache → 替換後重跑 Step 5-6 |
| 「加一個景點 Z」 | 查 cache → 沒有則打 API 寫回 cache → 加入候選 → 重跑 Step 5-6 |
| 「刪掉 X」 | 從 itinerary 移除（cache 保留）→ 重跑 Step 5-6 |
| 「X 改到第 3 天下午」 | 更新約束 → 重跑 Step 5-6 |
| 「整體順序 OK 但交通方式想改」 | 改 available_modes → 只重跑 enrich → 回 Step 6 |
| 「我想走 A → B → C 這個順序」 | 用 `score_route.py` 測量該路線，不需重跑 SA |

**新增景點 → 查 cache → 沒有才打 API → 一律寫回 cache。**

循環持續到用戶明確確認路線。確認後更新 `trip_state.json`：`phase` 改 `"build"`，進入 Phase 2。
