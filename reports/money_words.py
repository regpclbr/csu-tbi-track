"""An amount in words, as the office writes it in an Activity Report's Running Budget (ASSUMPTIONS 469):
"Twenty-Seven Thousand Pesos" for 27000.00, "One Thousand Two Hundred Fifty Pesos and 50/100" for 1250.50.
Decimal only (never float); amounts are rounded half-up to centavos first."""

from decimal import ROUND_HALF_UP, Decimal

ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve",
        "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]
SCALES = [(10**12, "Trillion"), (10**9, "Billion"), (10**6, "Million"), (1000, "Thousand")]


def _below_thousand(number: int) -> str:
    words = []
    hundreds, rest = divmod(number, 100)
    if hundreds:
        words.append(f"{ONES[hundreds]} Hundred")
    if rest >= 20:
        tens, ones = divmod(rest, 10)
        words.append(TENS[tens] + (f"-{ONES[ones]}" if ones else ""))
    elif rest:
        words.append(ONES[rest])
    return " ".join(words)


def number_in_words(number: int) -> str:
    """A whole number in words ("Zero" for 0)."""
    if number < 0:
        raise ValueError("Amounts in words must not be negative.")
    if number == 0:
        return "Zero"
    words = []
    for scale, name in SCALES:
        count, number = divmod(number, scale)
        if count:
            words.append(f"{number_in_words(count)} {name}")
    if number:
        words.append(_below_thousand(number))
    return " ".join(words)


def pesos_in_words(amount: Decimal | int | str) -> str:
    """"Twenty-Seven Thousand Pesos", "One Peso", "Fifty Pesos and 25/100"."""
    value = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    pesos, centavos = divmod(int(value * 100), 100)
    text = f"{number_in_words(pesos)} {'Peso' if pesos == 1 else 'Pesos'}"
    if centavos:
        text += f" and {centavos:02d}/100"
    return text
