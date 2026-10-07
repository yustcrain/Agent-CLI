class Inventory:
    def __init__(self, stock):
        self.stock = dict(stock)

    def reserve(self, lines):
        for sku, quantity in lines:
            if sku not in self.stock:
                raise KeyError(sku)
            if self.stock[sku] < quantity:
                raise ValueError("insufficient stock")
            self.stock[sku] -= quantity
