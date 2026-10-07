from money import money

def total(items):
    return str(sum(money(price) * quantity for price, quantity in items))
