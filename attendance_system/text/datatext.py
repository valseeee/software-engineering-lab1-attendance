import csv
from datetime import datetime, timedelta

employees = [('410000001', '张三'), ('410000002', '李四')]
start_date = datetime(2026, 4, 1)
days = 7

with open('attendance.csv', 'w', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(['employee_id', 'timestamp', 'type', 'status'])
    for e_id, name in employees:
        for i in range(days):
            date = start_date + timedelta(days=i)
            # 模拟张三积极，李四偶尔迟到
            if name == '张三':
                writer.writerow([e_id, date.replace(hour=9, minute=0), 'checkin', '正常'])
                writer.writerow([e_id, date.replace(hour=17, minute=0), 'checkout', '正常'])
            else:  # 李四偶尔迟到早退
                writer.writerow([e_id, date.replace(hour=9 + i % 2, minute=0), 'checkin', '迟到' if i % 2 else '正常'])
                writer.writerow([e_id, date.replace(hour=17 - i % 2, minute=0), 'checkout', '早退' if i % 2 else '正常'])