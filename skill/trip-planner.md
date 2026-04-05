---
name: trip-planner
description: 規劃旅行並生成完整旅遊網站。兩階段流程——Phase 1（Scout）互動式規劃，用真實 API 資料讓用戶篩選景點、加約束、迭代路線；Phase 2（Build）渲染 HTML 網站並部署。當用戶說 /trip-planner 或描述想規劃旅行時觸發。
---

# 旅行規劃 Skill

兩階段流程：**Scout**（互動式規劃，用真實資料）→ **Build**（渲染網站 + 部署）。

**專案根目錄：** 此 skill 所在 repo 的根目錄。以下所有指令用 `$REPO` 代表，agent 執行時替換為實際路徑（通常是 `git rev-parse --show-toplevel` 的結果）。

## 核心原則

1. **API 資料一次快取，同一趟旅行不重複查詢。** 每個透過 Places API 解析的地點都寫入 `places_cache.json`。從行程刪除景點不會刪 cache——用戶可能會加回來。
2. **用真實資料規劃。** 用戶在每個決策點看到的是實際交通時間和營業時間，不是估計值。
3. **用戶掌控計畫。** Agent 提案，用戶決定——打分、刪除、重排、加約束。循環持續到用戶滿意為止。

## 可用工具（不要自己寫，直接呼叫）

以下腳本涵蓋 skill 執行所需的全部功能。**優先使用現有腳本，不要重複造輪子。**

### 景點解析與快取

| 用途 | 腳本 | 輸入 | 輸出 | 備註 |
|------|------|------|------|------|
| 批次解析景點 + 寫入 cache | `build_places_cache.py` | stdin JSON（見下方範例） | 寫入 `places_cache.json` + stdout 摘要 | **Step 3 專用**，自動 dedup、batch resolve、append-only |
| 座標 + 距離矩陣 + 分群 | `resolve_places.py` | stdin JSON: `{"places": [{"name": "...", "maps_query": "..."}]}` | stdout JSON（含 `distance_matrix` + `clusters`） | 用於 Step 5 前觀察哪些景點在同一區 |
| 匯入 Google Maps 清單 | `import_gmaps_list.py` | Google Maps 分享連結 URL | stdout JSON 或 `--merge` 寫入 itinerary | 用戶有現成清單時的捷徑，可跳過手動候選 |

`build_places_cache.py` 輸入格式：
```bash
echo '{
  "candidates": [
    {"name": "赤崁樓", "maps_query": "赤崁樓, Tainan, Taiwan"},
    {"name": "林百貨", "maps_query": "林百貨, Tainan, Taiwan"}
  ],
  "cache_path": "trips/{slug}/data/places_cache.json"
}' | direnv exec $REPO python3 scripts/build_places_cache.py
```

### 行程組裝

| 用途 | 腳本 | 輸入 | 輸出 |
|------|------|------|------|
| 從簡化輸入 + cache 組裝 itinerary | `build_itinerary.py` | stdin JSON（見下方範例） | 寫入 `itinerary.json` |

`build_itinerary.py` 是 **Phase 1 → Phase 2 的橋樑**。Agent 只需提供 name / type / time / note，腳本自動從 cache 補齊 place_id / lat / lng / maps_query / display_name。

**輸入範例：**
```bash
echo '{
  "cache_path": "trips/{slug}/data/places_cache.json",
  "output_path": "trips/{slug}/data/itinerary.json",
  "days": [
    {
      "day": 1, "date": "2026-04-17",
      "title": "奇美博物館 × 老宅義式晚餐",
      "subtitle": "仁德→中西區",
      "places": [
        {"name": "奇美博物館", "type": "spot", "time": "09:30", "note": "距高鐵站步行 15 min"},
        {"name": "奇美博物館", "type": "food", "time": "12:00", "note": "館內餐廳", "title": "奇美博物館內午餐"},
        {"name": "森根", "type": "food", "time": "18:15", "note": "老宅義式", "lat": 22.9898, "lng": 120.2088}
      ]
    }
  ]
}' | direnv exec $REPO python3 scripts/build_itinerary.py
```

**欄位說明：**
- `name`（必填）— 用來 fuzzy match cache（match 順序：exact display_name → name 在 display_name 內 → name 在 maps_query 內 → display_name 在 name 內）
- `type`（必填）— spot / food / drink / hotel / transport / flight / work
- `time`（必填）— 24h HH:MM
- `note`（必填）— 說明、注意事項
- `title`（可選）— 顯示標題，預設 = name。同一地點多次使用時需要（如「奇美博物館內午餐」）
- `lat` + `lng`（可選）— 手動座標。**有填就跳過 cache lookup，place_id 自動設 null**。用於 Google Maps 未收錄的店

**輸出範例（自動生成）：**
```json
{
  "type": "spot",
  "title": "奇美博物館",
  "note": "距高鐵站步行 15 min",
  "maps_query": "奇美博物館, Tainan, Taiwan",  ← 自動從 cache
  "place_id": "ChIJq6qqqnp0bjQR...",           ← 自動從 cache
  "lat": 22.9346,                               ← 自動從 cache
  "lng": 120.2260,                              ← 自動從 cache
  "display_name": "Chimei Museum",              ← 自動從 cache
  "time": "09:30"
}
```

### 路線規劃與驗證

| 用途 | 腳本 | 輸入 | 輸出 |
|------|------|------|------|
| SA 路線優化（分天 + 排序） | `plan_route.py` | stdin JSON（景點、天數、約束） | stdout 前 N 組最佳方案 |
| 評估特定路線（不優化） | `score_route.py` | stdin JSON（指定順序的路線） | stdout JSON（各段交通時間 + 總計） |
| 充實行程交通資料 | `enrich_itinerary.py` | 檔案路徑引數 | 原地修改 itinerary.json（加入 travel + recommended_mode） |
| 營業時間衝突檢查 | `check_hours.py` | `trips/{slug}` 目錄引數 | stdout JSON（每個景點 ✅/⚠️/🔓/❓ 狀態） |

`enrich_itinerary.py` 行為：**已有 lat/lng 的 entry 不會被重新解析**，只計算路線交通。這代表 `build_itinerary.py` 產出的 itinerary 可以直接 enrich，不會覆蓋任何資料。

`score_route.py` 使用時機：用戶提出「我想走這個順序 A → B → C」時，**不需要重跑 SA 優化**，直接用 `score_route.py` 測量該路線的實際交通時間即可。

### 機票與住宿搜尋（SerpApi）

**預算：每月 250 次搜尋（機票 + 住宿共用），24 小時 cache 不重複計算。**

| 用途 | 腳本 | 輸入 | 輸出 | 備註 |
|------|------|------|------|------|
| 機票搜尋 | `search_flights.py` | stdin JSON（出發/目的 IATA、日期、人數） | stdout JSON（航班清單 + price_insights + usage） | 不主動查 booking links（省額度），輸出含 `booking_token` 備用 |
| 住宿搜尋 | `search_hotels.py` | stdin JSON（目的地文字、入住/退房日期、人數） | stdout JSON（飯店清單 + 各 OTA 比價 + usage） | OTA 比價（Booking/Agoda/Hotels.com）免費附帶 |

搜尋 gate 條件、參數、呈現格式等詳細說明見 `skill/trip-planner-scout.md`。

### 網站生成與部署

| 用途 | 腳本 | 輸入 | 輸出 |
|------|------|------|------|
| 渲染單趟旅行 HTML | `render_trip.py` | trip 目錄引數 | 寫入 `index.html`（同時自動呼叫 `generate_ics.py` 產生行事曆檔）。自動從 `places_cache.json` 讀取 `utc_offset_minutes` 將 transit 的 UTC 時間轉為當地時間 |
| 重建首頁 | `build_index.py` | 無 | 寫入根目錄 `index.html` |
| 部署到 GitHub Pages | `deploy.sh` | 無 | 重新渲染所有 trip → force-push 到 gh-pages |

### 底層函式（已在腳本內部使用，一般不需直接呼叫）

- `directions.resolve_place(query, field_mask=None)` — 支援 `FULL_FIELD_MASK`（50 欄位）或預設 3 欄位
- `directions.resolve_places_batched(queries, field_mask=None)` — 8/batch + 1s 間隔
- `directions.FULL_FIELD_MASK` — 完整欄位常數，觸發 Enterprise + Atmosphere SKU
- **這些函式已經寫好，不要重寫。** `build_places_cache.py` 和 `resolve_places.py` 已經包裝了它們。

### 所有腳本的呼叫方式

```bash
# 一律用 direnv exec，不要 cd
direnv exec $REPO python3 scripts/<腳本名>.py [引數]
```

## 資料檔案

### `trips/{slug}/data/places_cache.json`（per-trip API 快取）

以 `place_id` 為 key，每個地點一筆。**只增不刪。**

```json
{
  "ChIJbYl7d2F2bjQRnFdvyMBuZfI": {
    "maps_query": "赤崁樓, Tainan, Taiwan",
    "display_name": "赤崁樓",
    "types": ["tourist_attraction"],
    "primary_type": "tourist_attraction",
    "lat": 22.997,
    "lng": 120.202,
    "formatted_address": "...",
    "short_address": "...",
    "google_maps_uri": "...",
    "website": "...",
    "rating": 4.3,
    "rating_count": 12847,
    "regular_opening_hours": { "weekdayDescriptions": ["Monday: 8:30 AM – 9:30 PM", "..."] },
    "business_status": "OPERATIONAL",
    "editorial_summary": "...",
    "fetched_at": "2026-04-04T17:30:00Z"
  }
}
```

完整欄位共 50 個（含 `serves_*`、`payment_options`、`reviews` 等），不適用的欄位值為 `null`，一律保留不篩除。

### 其他檔案（每趟旅行 data/ 下共 8 個）

- `trip.json` — 標題、日期、城市、slug
- `itinerary.json` — 每日路線，含 places[]、travel[]、recommended_mode
- `reservations.json` — 訂位/預約項目（`render_trip.py` 讀取此檔，不是 checklist.json）
- `todo.json` — 行前確認項目
- `info.json` — 實用資訊（預算、簽證、交通、天氣等）
- `packing.json` — 行李清單（從 `template/data/packing.json` 複製再客製）
- `places_cache.json` — Places API 快取（Phase 1 自動生成）
- `flights_cache.json` — 機票搜尋快取（SerpApi，24h 有效）
- `hotels_cache.json` — 住宿搜尋快取（SerpApi，24h 有效）

---

## 狀態管理

每趟旅行在 `trips/{slug}/data/trip_state.json` 追蹤規劃進度：

```json
{
  "phase": "scout",
  "current_step": 5,
  "completed_steps": [1, 2, 3, 4],
  "candidates_count": 32,
  "cached_count": 18,
  "user_approved_route": false,
  "updated_at": "2026-04-05T14:30:00Z"
}
```

**使用規則：**
- 每次對話開始：檢查 `trip_state.json` 是否存在。存在則從 `current_step` 繼續；不存在則從 Step 1 開始。
- 每完成一個 Step：更新 `current_step`、`completed_steps`、`updated_at`。
- Phase 1 完成（用戶確認路線）→ `phase` 改 `"build"`。
- Phase 2 完成（deploy）→ `phase` 改 `"done"`。
- `trip_state.json` 不需要加入 `.gitignore`，它是有用的規劃紀錄。

## Phase 分派

進入 Phase 1（互動式規劃）時，讀取 `skill/trip-planner-scout.md`。
進入 Phase 2（網站生成）時，讀取 `skill/trip-planner-build.md`。

在每個 Step 完成時，更新 `trips/{slug}/data/trip_state.json`。
