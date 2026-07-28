# tmo/constants.py

# Standard daily energy forecast (GWh) by day-of-month.
# This is the built-in default used for every month unless an admin creates
# a TMODailyAllocation override for a specific month.
# Day 31 carries the same value as days 27-30.
STANDARD_DAILY_FORECAST_GWH = {
     1: 5.00,  2: 5.00,
     3: 4.30,  4: 4.30,
     5: 4.50,  6: 4.50,
     7: 4.80,  8: 4.80,  9: 4.80,
    10: 4.50,
    11: 4.30,
    12: 4.50, 13: 4.50,
    14: 5.00, 15: 5.00, 16: 5.00, 17: 5.00, 18: 5.00,
    19: 4.70, 20: 4.70,
    21: 6.00, 22: 6.00,
    23: 6.20,
    24: 6.60, 25: 6.60, 26: 6.60,
    27: 6.70, 28: 6.70, 29: 6.70, 30: 6.70, 31: 6.70,
}
