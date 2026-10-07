from report import summarize_csv
from decimal import Decimal
assert summarize_csv('name,amount\n"Lee, Amy",1.20\nBob,2.00\n"Lee, Amy",0.30\n') == {"Lee, Amy": Decimal("1.50"), "Bob": Decimal("2.00")}
assert summarize_csv('name,amount\n') == {}
assert summarize_csv('name,amount\nA,0.1\nA,0.2\n') == {"A": Decimal("0.3")}
assert summarize_csv('name,amount\nA,-2.5\nA,1.0\n') == {"A": Decimal("-1.5")}
try:
    summarize_csv('name,amount\nA,invalid\n')
except ValueError:
    pass
else:
    raise AssertionError("Invalid amount must raise ValueError")
