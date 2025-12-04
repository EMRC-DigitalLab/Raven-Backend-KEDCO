# technical/constants.py
# Define interruption categories
LOAD_SHEDDING_TYPES = ['L/S', 'L/S GS', '330KV L/S', 'T/LS']
    
# TCN/Grid issues (not under our control)
TCN_TYPES = ['132KV E/F', '132KV CB/F', '330KV L/F', '132KV L/F', 'tcn', '330KV L/S']
    
# Combined exclusions for turnaround time
TURNAROUND_EXCLUSIONS = set(LOAD_SHEDDING_TYPES + TCN_TYPES)