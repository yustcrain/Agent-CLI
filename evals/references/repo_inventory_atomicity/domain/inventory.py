from collections import defaultdict

class Inventory:
    def __init__(self, stock):
        self.stock = dict(stock)

    def reserve(self, lines):
        requested = defaultdict(int)
        for sku, quantity in lines:
            if type(quantity) is not int or quantity <= 0:
                raise ValueError("quantity must be a positive integer")
            requested[sku] += quantity
        for sku, quantity in requested.items():
            if sku not in self.stock:
                raise KeyError(sku)
            if self.stock[sku] < quantity:
                raise ValueError("insufficient stock")
        for sku, quantity in requested.items():
            self.stock[sku] -= quantity
