"""Check throughput cost units without importing API startup dependencies."""

import ast
from pathlib import Path
import unittest

import cvxpy as cp
import numpy as np


source_path = Path(__file__).resolve().parents[1] / "app/api/flexibility.py"
source_tree = ast.parse(source_path.read_text())
function_names = {
    "build_common_constraints",
    "solve_baseline_and_offer",
    "solve_baseline_and_offer_per_service_point",
}
namespace = {"cp": cp, "np": np}
exec(compile(ast.Module(body=[
    node for node in source_tree.body
    if isinstance(node, ast.FunctionDef) and node.name in function_names
], type_ignores=[]), str(source_path), "exec"), namespace)


class FlexibilityPenaltyTests(unittest.TestCase):
    def test_one_cent_per_kwh_does_not_prevent_economic_discharge(self):
        # 1 kWh stored delivers 950 Wh at 95% efficiency. At EUR 0.20/kWh,
        # discharging saves EUR 0.19 and costs EUR 0.0095 in throughput.
        for name in sorted(function_names - {"build_common_constraints"}):
            with self.subTest(solver=name):
                result = namespace[name](
                    pv=np.zeros((1, 4)), load=np.full((1, 4), 1000.),
                    dt=0.25, L=4,
                    energy_cost_import=np.full(4, 0.0002),
                    energy_cost_export=np.zeros(4),
                    flex_up_req=np.zeros(4), flex_down_req=np.zeros(4),
                    Pch_max=np.array([2500.]), Pdis_max=np.array([2500.]),
                    SoC_min=np.zeros(1), SoC_max=np.array([5000.]),
                    SoC0=np.array([1000.]),
                    eta_ch=np.array([0.95]), eta_dis=np.array([0.95]),
                    c_thr=0.01, eps_energy=0.,
                )
                self.assertAlmostEqual(result["J_energy_star"], -0.0195, places=5)
                self.assertAlmostEqual(
                    float(np.sum(result["p_dis_baseline"]) * 0.25), 950., places=2,
                )


    def test_offers_respect_individual_directional_ratings(self):
        # Negative prices cause baseline charging; positive prices and load
        # cause discharging. Reversing either baseline must not inflate offers
        # beyond the rating, including for unequal batteries and directions.
        charge_limits = np.array([125., 1250.])
        discharge_limits = np.array([250., 2500.])
        for direction in ("up", "down"):
            with self.subTest(direction=direction):
                upward = direction == "up"
                result = namespace["solve_baseline_and_offer_per_service_point"](
                    pv=np.zeros((2, 1)), load=np.full((2, 1), 10000.),
                    dt=0.25, L=1,
                    energy_cost_import=np.array([-0.0001 if upward else 0.0002]),
                    energy_cost_export=np.array([-0.0002 if upward else 0.0001]),
                    flex_up_req=np.array([10000. if upward else 0.]),
                    flex_down_req=np.array([0. if upward else 10000.]),
                    Pch_max=charge_limits, Pdis_max=discharge_limits,
                    SoC_min=np.zeros(2), SoC_max=np.full(2, 10000.),
                    SoC0=np.full(2, 5000.),
                    eta_ch=np.full(2, 0.95), eta_dis=np.full(2, 0.95),
                    eps_energy=0.,
                )
                offers = result[f"offer_{direction}_by_service_point"]
                limits = discharge_limits if upward else charge_limits
                np.testing.assert_allclose(offers[:, 0], limits, atol=0.01, rtol=0)
                np.testing.assert_allclose(
                    result[f"offer_{direction}"], offers.sum(axis=0), atol=1e-6,
                )


if __name__ == "__main__":
    unittest.main()
