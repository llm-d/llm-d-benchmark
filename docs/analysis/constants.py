"""
Library constants
"""

# Length of column bound prefix
BOUND_PREFIX_LEN = 6

# Column bound prefixes and printable string representations.
# Order is from lower bounds to upper bounds.
COLUMN_BOUND_STR = {
    '__ge__': '≥',
    '__gt__': '>',
    '__lt__': '<',
    '__le__': '≤',
}

# Reverse mapping of possible string descriptors of bounds to internal column
# prefix representation.
STR_TO_COLUMN_BOUND = {
    'ge': '__ge__',
    '>=': '__ge__',
    '≥': '__ge__',
    '__ge__': '__ge__',

    'gt': '__gt__',
    '>': '__gt__',
    '__gt__': '__gt__',

    'lt': '__lt__',
    '<': '__lt__',
    '__lt__': '__lt__',

    'le': '__le__',
    '<=': '__le__',
    '≤': '__le__',
    '__le__': '__le__',
}
