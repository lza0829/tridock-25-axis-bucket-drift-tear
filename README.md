# Tridock（到货三联台）

骨架：到货预报装载 → 实收入账 → 供应商回执装载 → 三方键对齐与差额处置 → 到货日结案封印。

- 前端 http://127.0.0.1:5900
- API http://127.0.0.1:10900

## 比较轴架构（全局只贯彻一种）

三腿（预报 expect / 实收 receive / 回执 confirm）数量拧着时，全系统用**同一条比较轴**
决定「哪些键进主差额、怎样算匹配」。当前实现两种架构，全局设置 `compare_axis` 二选一，
默认 **预报-实收轴**；比较轴、差额分桶、匹配判定、批次快照 axis、结案写闸、总览差额计数、
差额列表页全部联动，不允许只挑一条腿数量做结案对照却不改分桶轴。

| | 预报-实收轴 `expect_receive`（默认） | 实收-回执轴 `receive_confirm` |
| --- | --- | --- |
| 主差额（bucket=`main`） | expect ≠ receive（或轴腿缺失） | receive ≠ confirm（或轴腿缺失） |
| 匹配成功 | expect == receive（两腿在场且相等） | receive == confirm（两腿在场且相等） |
| 第三腿 | confirm 仅作旁证，写入匹配快照旁路字段 `side_dev`，**不单独开主差额** | expect 偏离单独进 `expect_drift` **旁证桶**，不与主差额混桶 |

统一规则：

- **旁证偏差** `side_dev = 旁证腿 − 实收`（实收为物理基准腿），缺腿不可算则为 NULL。
- **缺腿不当 0**：某腿无记录时数量存 `NULL`、`has_*` 标记置 0；轴腿缺失即进主差额并带
  缺腿标记，绝不静默对齐吞掉偏差。
- **两桶不混**：主差额与旁证桶同表分桶存放（`variance_rows.bucket`），所有主差额查询
  （结案写闸、总览未决计数、差额列表主区）都显式过滤 `bucket='main'`；旁证桶 `fate='info'`，
  只作证据留存，不需处置、不挡结案、拒绝改命运。
- **结案写闸**：只数该批 `bucket='main' AND fate='pending'` 的未决主差额。
- **批次快照 axis 可回溯**：每个批次落库时快照当时轴（`reconcile_batches.axis`），
  匹配行/差额行随之封存；事后切换全局轴只影响之后新跑的批次，已 sealed 批的轴与
  分桶集合不可撬动（重跑被拒、命运被拒、快照原样）。
- **并发两路对账**：`reconcile_batches(dock_day) WHERE status='open'` 部分唯一索引 +
  `BEGIN IMMEDIATE` 序列化 + 同事务删旧建新，同一 dock_day 任意时刻至多一套 open 主差额，
  不会落下两套互相矛盾的口径。

切换轴：设置页或 `POST /api/settings/axis`（`{"axis": "receive_confirm"}`）。

## 核对（两架构分别验证）

```bash
cd backend
python -m tests.test_reconcile_axis   # 10 项口径核对，无需 pytest
```

覆盖：三腿互不相同时两轴分叉出不同主差额集合；旁证桶与主桶不混；缺腿 NULL+标记；
结案写闸只数主差额；sealed 批不被轴切换撬动；并发两路对账只落一套 open 主差额。

## 启动

```powershell
cd backend
py -3.11 -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\uvicorn app.main:app --host 127.0.0.1 --port 10900 --app-dir .
```

```powershell
cd frontend
npm install
npm run dev -- --host 127.0.0.1 --port 5900
```
