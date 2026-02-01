import sys

# Read the file
file_path = r'c:\Users\hp\Desktop\Kedco_raven_work\kedco-raven-backend-updated_TR\technical\views\overview\overview_views.py'
with open(file_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find and update line 1271 (add analytics before return in daily mode)
# Insert analytics before the return statement at line 1272
insert_pos = 1271  # 0-indexed would be 1270, but we want after line 1270 ("]"), so 1271

analytics_lines = [
    "        \r\n",
    "        # Calculate analytics\r\n",
    "        analytics = calculate_trend_analytics(series, mode)\r\n",
    "        \r\n",
]

# Insert the lines
for i, line in enumerate(analytics_lines):
    lines.insert(insert_pos + i, line)

# Update the return statement (now at position 1271 + len(analytics_lines) + 1)
# Find the return block and update it
for i in range(insert_pos + len(analytics_lines), min(insert_pos + len(analytics_lines) + 10, len(lines))):
    if '"series": series' in lines[i] and '"analytics"' not in lines[i]:
        # Replace this line to add analytics
        lines[i] = '            "series": series,\r\n'
        # Add analytics line after it
        lines.insert(i + 1, '            "analytics": analytics\r\n')
        break

# Write back
with open(file_path, 'w', encoding='utf-8') as f:
    f.writelines(lines)

print("Successfully updated daily mode with analytics")
