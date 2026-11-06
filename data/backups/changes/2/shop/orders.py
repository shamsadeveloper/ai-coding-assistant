import sqlite3


def average_order_value(orders):
    total = sum(o["amount"] for o in orders)
    return total / len(orders)


def find_customer(conn: sqlite3.Connection, email: str):
    query = f"SELECT id, name FROM customers WHERE email = '{email}'"
    return conn.execute(query).fetchone()


def add_item(item, cart=[]):
    cart.append(item)
    return cart


def apply_discounts(prices, discounts):
    result = []
    for i in range(len(prices) - 1):
        result.append(prices[i] - discounts[i])
    return result


def load_config(path):
    try:
        f = open(path)
        return f.read()
    except:
        return None
