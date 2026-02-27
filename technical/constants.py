# technical/constants.py
# Define interruption categories
LOAD_SHEDDING_TYPES = ['L/S', 'L/S GS', '330KV L/S', 'T/LS']

# TCN/Grid issues (not under our control)
# Includes both lowercase 'tcn' and title-case 'TCN', plus 132KV MTCE (TCN maintenance)
TCN_TYPES = ['TCN', 'tcn', '132KV E/F', '132KV CB/F', '132KV MTCE', '330KV L/F', '132KV L/F', '330KV L/S']

# Combined exclusions for turnaround time:
#   Turnaround Time = Interruption Duration - (L/S + TCN faults)
#   All types below are excluded from turnaround calculations:
#     L/S, T/LS, L/S GS, 330KV L/S  — load shedding
#     TCN, tcn                        — generic TCN label
#     132KV E/F, 132KV CB/F           — 132KV faults
#     132KV MTCE                      — 132KV TCN maintenance
#     330KV L/F                       — 330KV line fault
#     132KV L/F                       — 132KV line fault
TURNAROUND_EXCLUSIONS = set(LOAD_SHEDDING_TYPES + TCN_TYPES)