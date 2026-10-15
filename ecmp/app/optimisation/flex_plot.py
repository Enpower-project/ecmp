import requests
import matplotlib.pyplot as plt
import pandas as pd
import json

plt.rcParams.update({
    # "font.size": 12,
    # "axes.titlesize": 14,
    # "axes.labelsize": 12,
    # "legend.fontsize": 11,
    "figure.dpi": 300,
    "savefig.dpi": 300
})

# -------------------------
# 1. Hardcoded API request
# -------------------------

API_URL = "http://localhost:8000/flexibility"

payload = {
    "start_datetime": "2025-12-20T22:00:00",
    "end_datetime": "2025-12-21T22:00:00"
}

response = requests.post(API_URL, json=payload, timeout=30)
response.raise_for_status()

data = json.loads(response.json())  # dict

print(data['results']['aggregated_power'])
print(data['results']['community_objective'])
print(data['results']['flexibility_offer_up'])
print(data['results']['flexibility_offer_down'])

bss_power = data['results']['aggregated_bss_power']
pcc_power = data['results']['aggregated_power']
pv_agg = data['results']['aggregated_production']
energy_prices = data['results']['energy_prices']

baseline_agg = data['results']['baseline_agg']

plt.figure()

# aggregated BSS power
ts_ms = [int(p[0]) for p in bss_power]
y = [p[1] for p in bss_power]
x = pd.to_datetime(ts_ms, unit="s")
plt.plot(x, y, drawstyle="steps-post", label=f"BSS power [kW]")

# aggregated power at PCC
ts_ms = [int(p[0]) for p in pcc_power]
y = [p[1] for p in pcc_power]
x = pd.to_datetime(ts_ms, unit="s")
plt.plot(x, y, drawstyle="steps-post", label=f"Aggregated power at PCC [kW]")

# aggregated production
ts_ms = [int(p[0]) for p in pv_agg]
y = [p[1] for p in pv_agg]
x = pd.to_datetime(ts_ms, unit="s")
plt.plot(x, y, drawstyle="steps-post", label=f"Aggregated production [kW]")

# energy prices
ts_ms = [int(p[0]) for p in energy_prices]
y = [p[1]*1000.0 for p in energy_prices]
x = pd.to_datetime(ts_ms, unit="s")
plt.plot(x, y, drawstyle="steps-post", label=f"Energy prices [€/MWh]")

# baseline_agg
# ts_ms = [int(p[0]) for p in baseline_agg]
# y = [p[1] for p in baseline_agg]
# x = pd.to_datetime(ts_ms, unit="s")
# plt.plot(x, y, drawstyle="steps-post", label=f"Baseline profile [kW]")

# Highlight region
start_ts = pd.Timestamp("2025-12-21T13:00:00")
end_ts   = pd.Timestamp("2025-12-21T14:00:00")

plt.axvspan(
    start_ts,
    end_ts,
    color="gray",
    alpha=0.3,   # transparency
    zorder=0     # keep it behind the line
)

# plt.title("BSS Power")
plt.xlabel("Datetime")
plt.ylabel("P [kW]")
plt.grid()
plt.legend()
plt.tight_layout()
plt.show()

# discharge_power = data['results']['discharge_power']

# plt.figure()

# for i, series in enumerate(discharge_power):
#     ts_ms = [int(p[0]) for p in series]
#     y = [p[1] for p in series]

#     x = pd.to_datetime(ts_ms, unit="ms")  # ms -> datetime
#     plt.plot(x, y, label=f"series {i}")

# plt.title("Discharge Power")
# plt.xlabel("time")
# plt.ylabel("value")
# plt.legend()
# plt.tight_layout()
# plt.show()


# soc = data['results']['SoC']

# plt.figure()

# for i, series in enumerate(soc):
#     ts_ms = [int(p[0]) for p in series]
#     y = [p[1] for p in series]

#     x = pd.to_datetime(ts_ms, unit="ms")  # ms -> datetime
#     plt.plot(x, y, label=f"series {i}")

# plt.title("SoC")
# plt.xlabel("time")
# plt.ylabel("value")
# plt.legend()
# plt.tight_layout()
# plt.show()


# aggregated_power = data['results']['aggregated_power']

# plt.figure()

# ts_ms = [int(p[0]) for p in aggregated_power]
# y = [p[1] for p in aggregated_power]
# x = pd.to_datetime(ts_ms, unit="ms")
# plt.plot(x, y, label=f"series")

# plt.title("Aggregated Power")
# plt.xlabel("time")
# plt.ylabel("value")
# plt.legend()
# plt.tight_layout()
# plt.show()


# aggregated_production = data['results']['aggregated_production']

# plt.figure()

# ts_ms = [int(p[0]) for p in aggregated_production]
# y = [p[1] for p in aggregated_production]
# x = pd.to_datetime(ts_ms, unit="ms")
# plt.plot(x, y, label=f"series")

# plt.title("Aggregated Production")
# plt.xlabel("time")
# plt.ylabel("value")
# plt.legend()
# plt.tight_layout()
# plt.show()


# energy_prices = data['results']['energy_prices']

# plt.figure()

# ts_ms = [int(p[0]) for p in energy_prices]
# y = [p[1] for p in energy_prices]
# x = pd.to_datetime(ts_ms, unit="ms")
# plt.plot(x, y, label=f"series")

# plt.title("Energy Prices")
# plt.xlabel("time")
# plt.ylabel("value")
# plt.legend()
# plt.tight_layout()
# plt.show()
# ts_ms = [int(p[0]) for p in data]
# y = [p[1] for p in data]
# x = pd.to_datetime(ts_ms, unit="ms")

# plt.plot(x, y)
# plt.show()
# output = {
#     'start_datetime': date1,
#     'end_datetime': date2,
#     'results': {
#         'community_objective': J_star,
#         'charge_power': charge_power,
#         'discharge_power': discharge_power,
#         'SoC': soc_out,
#         'aggregated_power': aggregated_power,
#         'flexibility_offer_up': offer_up_t.value,
#         'flexibility_offer_down': offer_down_t.value
#     }
# }

# x, y = parse_response(data)

# -------------------------
# 3. Placeholder: plotting
# -------------------------

# def plot_results(x, y):
#     plt.figure()
#     plt.plot(x, y)
#     plt.xlabel("X")
#     plt.ylabel("Y")
#     plt.title("API Result")
#     plt.grid(True)
#     plt.show()


# plot_results(x, y)



# -------------------------
# 2. Placeholder: parse API response
# -------------------------
# TODO: adapt this to match your real API response structure
#
# Example expected shape:
# data = {
#     "x": [0, 1, 2, 3],
#     "y": [10.0, 12.5, 11.2, 13.0]
# }

# def parse_response(data):
#     """
#     Extract x and y arrays from API response.
#     Replace this logic with real parsing.
#     """
#     x = data["x"]
#     y = data["y"]
#     return x, y


# x, y = parse_response(data)


# -------------------------
# 3. Placeholder: plotting
# -------------------------

# def plot_results(x, y):
#     plt.figure()
#     plt.plot(x, y)
#     plt.xlabel("X")
#     plt.ylabel("Y")
#     plt.title("API Result")
#     plt.grid(True)
#     plt.show()


# plot_results(x, y)