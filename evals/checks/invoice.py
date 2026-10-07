from decimal import Decimal
from money import money
from invoice import total
assert isinstance(money("1.005"), Decimal)
assert money("1.005") == Decimal("1.01")
assert money("-1.005") == Decimal("-1.01")
assert total([]) == "0.00"
assert total([("0.10", 3), ("1.005", 2)]) == "2.32"
assert total([("2.50", 0)]) == "0.00"
assert total([("9.99", 2)]) == "19.98"
