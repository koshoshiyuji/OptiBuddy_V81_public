"""
Backend/i18n/rideshare_matching_planner_messages.py — RideshareMatchingPlanner 専用メッセージ辞書

2026-08-16 再修正: 最終登録時（force_apply実行）のapply_domain_files()が、
write_files_for_dynamic_check()の再読込対象外だったi18nファイルについて、
ジョブ内部に残っていたStage2生成時点の古い診断（キー名が
issue.solve_failed.* / issue.unassigned_passenger.* 等）で本ファイルを
上書きしてしまうインフラ上の既知ギャップが発生した。

本ファイルは、solver.py（issue.no_passengers.* 等の入力チェック文言、
issue.unmatched_passenger.* 等の実行時issue、solver.planLabel等）と
ui_converter.py（kpi.*/alert.*/passenger.status.*/table.section.*/
table.column.* のUI表示文言）の両方が実際にt()で参照するキーを
全数突合の上、1つのファイルに統合したもの。
"""

from __future__ import annotations

from i18n import make_translator

_JA = {
    # --- solver.py: solve() 冒頭の入力チェック ---
    "issue.no_passengers.title": "乗客データがありません",
    "issue.no_passengers.message": "乗客データが空です。少なくとも1名の乗客データを登録してください。",

    "issue.no_drivers.title": "運転手データがありません",
    "issue.no_drivers.message": "運転手データが空です。少なくとも1名の運転手データを登録してください。",

    "issue.ce_limit.title": "CPLEXの無料版で扱える件数を超えています",
    "issue.ce_limit.message": "乗客数または運転手数を減らすか、正規ライセンスのご利用をご検討ください。",

    "issue.infeasible.title": "マッチングが成立しませんでした",
    "issue.infeasible.message": "条件を満たす相乗りマッチングが見つかりませんでした。乗客・運転手の条件を見直してください。",

    # --- solver.py: _detect_issues() ---
    "issue.unmatched_passenger.title": "未割当乗客: {name}",
    "issue.unmatched_passenger.message": "乗客「{name}」はどの運転手にもマッチングできませんでした。希望時間帯や乗車時間の上限（detour_factor）を確認してください。",

    "issue.seat_overflow.title": "座席数超過の疑い: {name}",
    "issue.seat_overflow.message": "運転手「{name}」の同時乗車人数が {count} 名で、座席数 {seats} 席を超えている可能性があります。",

    # --- solver.py: ラベル ---
    "solver.passengerLabel": "乗客",
    "solver.planLabel": "相乗りプランA",

    # --- ui_converter.py: KPIカード ---
    "kpi.coverage.label": "マッチング率",
    "kpi.coverage.sub": "{assigned}/{total} 名",
    "kpi.unassigned.label": "未割当乗客数",
    "kpi.unassigned.unit": "名",
    "kpi.total_km.label": "総走行距離（推定）",
    "kpi.total_km.unit": "km",
    "kpi.n_drivers.label": "稼働運転手数",
    "kpi.n_drivers.unit": "名",

    # --- ui_converter.py: アラート ---
    "alert.infeasible": "マッチングが成立しませんでした。条件を緩和してください。",
    "alert.unassigned_exist": "{count}名の乗客がマッチングできませんでした。",

    # --- ui_converter.py: 乗客ステータス ---
    "passenger.status.assigned": "マッチング済",
    "passenger.status.unassigned": "未割当",

    # --- ui_converter.py: テーブルセクション ---
    "table.section.passengers.title": "乗客マッチング一覧",
    "table.section.drivers.title": "運転手別ルート一覧",
    "table.column.passenger_name": "乗客名",
    "table.column.status": "状態",
    "table.column.driver_name": "担当運転手",
    "table.column.driver_name_col": "運転手名",
    "table.column.pickup_time": "乗車時刻",
    "table.column.dropoff_time": "降車時刻",
    "table.column.ride_time_min": "乗車時間",
    "table.column.direct_time_min": "直接移動時間",
    "table.column.n_passengers": "担当乗客数",
    "table.column.capacity": "座席数",
    "table.column.travel_km": "走行距離",
    "table.column.active_min": "稼働時間",
    "table.column.route": "乗車順序",
}

_EN = {
    # --- solver.py: input checks at the top of solve() ---
    "issue.no_passengers.title": "No Passenger Data",
    "issue.no_passengers.message": "Passenger data is empty. Please register at least one passenger.",

    "issue.no_drivers.title": "No Driver Data",
    "issue.no_drivers.message": "Driver data is empty. Please register at least one driver.",

    "issue.ce_limit.title": "Model size exceeds CPLEX Community Edition limit",
    "issue.ce_limit.message": "Please reduce the number of passengers or drivers, or consider using a full CPLEX license.",

    "issue.infeasible.title": "No Feasible Matching Found",
    "issue.infeasible.message": "Could not find a rideshare matching satisfying all constraints. Please review passenger and driver conditions.",

    # --- solver.py: _detect_issues() ---
    "issue.unmatched_passenger.title": "Unmatched Passenger: {name}",
    "issue.unmatched_passenger.message": "Passenger \"{name}\" could not be matched to any driver. Please check the time window and the ride-time cap (detour_factor).",

    "issue.seat_overflow.title": "Possible Seat Overflow: {name}",
    "issue.seat_overflow.message": "Driver \"{name}\" may be carrying {count} concurrent passengers, exceeding the {seats}-seat capacity.",

    # --- solver.py: labels ---
    "solver.passengerLabel": "passenger",
    "solver.planLabel": "Rideshare Plan A",

    # --- ui_converter.py: KPI cards ---
    "kpi.coverage.label": "Match Rate",
    "kpi.coverage.sub": "{assigned}/{total} passengers",
    "kpi.unassigned.label": "Unmatched Passengers",
    "kpi.unassigned.unit": "pax",
    "kpi.total_km.label": "Total Travel Distance (est.)",
    "kpi.total_km.unit": "km",
    "kpi.n_drivers.label": "Active Drivers",
    "kpi.n_drivers.unit": "drivers",

    # --- ui_converter.py: alerts ---
    "alert.infeasible": "No matching was established. Please relax constraints.",
    "alert.unassigned_exist": "{count} passenger(s) could not be matched.",

    # --- ui_converter.py: passenger status ---
    "passenger.status.assigned": "Matched",
    "passenger.status.unassigned": "Unmatched",

    # --- ui_converter.py: table sections ---
    "table.section.passengers.title": "Passenger Matching List",
    "table.section.drivers.title": "Driver Route List",
    "table.column.passenger_name": "Passenger",
    "table.column.status": "Status",
    "table.column.driver_name": "Driver",
    "table.column.driver_name_col": "Driver Name",
    "table.column.pickup_time": "Pickup Time",
    "table.column.dropoff_time": "Dropoff Time",
    "table.column.ride_time_min": "Ride Time",
    "table.column.direct_time_min": "Direct Time",
    "table.column.n_passengers": "Passengers",
    "table.column.capacity": "Seats",
    "table.column.travel_km": "Travel (km)",
    "table.column.active_min": "Active (min)",
    "table.column.route": "Route Order",
}

t = make_translator(_JA, _EN, "rideshare_matching_planner")
