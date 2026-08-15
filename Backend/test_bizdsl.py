import json
from dsl_transformer.nurse_shift_weekly_cap_converter import convert_nurse_shift_weekly_cap_to_solver
from solvers.nurse_shift_weekly_cap_solver import NurseShiftWeeklyCapSolver

business_dsl = json.load(open("bizdsl_001.json"))
solver_input = convert_nurse_shift_weekly_cap_to_solver(business_dsl)
solver_input.setdefault("issue_statuses", {})
result = NurseShiftWeeklyCapSolver(solver_input).solve()

print("feasible:", result.get("feasible"))
print("staff_rolling_night_counts:", result["solutions"][0].get("staff_rolling_night_counts") if result.get("feasible") else None)