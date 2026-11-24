"""An activity's budget (ASSUMPTIONS 433, NAVIGATU UAT): the lines copied from the Activity Design approved on paper,
the fund source and the account. There is NO budget approval in the system.

The rules live only here:
- `clean_lines()`: the one check of the lines (category active, particulars, quantity > 0 with at most 2 decimal places,
  unit, days a whole number >= 1, unit price >= 0 with at most 2 decimal places, at most MAX_LINES lines, totals that
  fit the column). Errors as ValidationError keys "budget-<row>-<field>" (the form puts them under the row's fields) and
  "budget_lines" (the whole table).
- `line_total()`: quantity x days x unit price, rounded half-up to the centavo. Decimal only, never float.
- `save_lines()`: replaces an activity's lines (called by activities.services.create / update, inside their
  transaction), sets ACTIVITY.budget_amount to the grand total and writes History ("Budget", "Budget Amount").
- `budget_summary()` / `budget_table()`: what the Overview, the API, the assistant and (Prompt 6) the Activity Report's
  "Running Budget" show.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.core.exceptions import ValidationError

from .formatting import format_money
from .models import Activity, BudgetCategory, BudgetLine, FundSource

CENTAVO = Decimal("0.01")
MAX_LINES = 50
MAX_AMOUNT = Decimal("9999999999.99")  # DECIMAL(12, 2)
MAX_QUANTITY = Decimal("99999999.99")  # DECIMAL(10, 2)
MAX_DAYS = 366
FIELDS = ("category", "particulars", "quantity", "unit", "days", "unit_price")
LINES_KEY = "budget_lines"  # the pseudo-field the activity services accept (a list of dicts with FIELDS)
BUDGET_LABEL = "Budget"  # History: the lines, old -> new
BUDGET_AMOUNT_LABEL = "Budget Amount"  # History: the grand total, old -> new
PREVIOUS_TOTAL = "Unitemized (previous total)"  # the one line migration 0014 made from an old total
NOT_SET = "Not set"  # an activity from before ASSUMPTIONS 433 without a fund source


def row_key(index: int, field: str) -> str:
    return f"budget-{index}-{field}"


@dataclass(frozen=True)
class LineValues:
    """One checked line (not saved yet)."""

    category: BudgetCategory
    particulars: str
    quantity: Decimal
    unit: str
    days: int
    unit_price: Decimal

    @property
    def line_total(self) -> Decimal:
        return line_total(self.quantity, self.days, self.unit_price)


def line_total(quantity: Decimal, days: int, unit_price: Decimal) -> Decimal:
    """quantity x days x unit price, rounded half-up to the centavo (1.5 x 1 x 33.33 = 49.995 -> 50.00)."""
    return (Decimal(quantity) * Decimal(days) * Decimal(unit_price)).quantize(CENTAVO, rounding=ROUND_HALF_UP)


def _decimal(value, label: str, places_message: str) -> Decimal:
    """A typed amount as Decimal (commas and a peso sign allowed), never through float arithmetic."""
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError(f"Enter the {label}.")
    if isinstance(value, bool):
        raise ValueError(f"Enter a number for the {label}.")
    text = str(value).strip().replace(",", "").replace("₱", "").strip()  # a JSON float becomes its shortest text
    try:
        number = Decimal(text)
    except InvalidOperation:
        raise ValueError(f"Enter a number for the {label}.") from None
    if not number.is_finite():
        raise ValueError(f"Enter a number for the {label}.")
    if number != number.quantize(CENTAVO, rounding=ROUND_HALF_UP):
        raise ValueError(places_message)
    return number.quantize(CENTAVO)


def _days(value) -> int:
    if value is None or (isinstance(value, str) and not value.strip()):
        return 1  # default: one day
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation:
        raise ValueError("Enter the number of days as a whole number.") from None
    if not number.is_finite() or number != number.to_integral_value():
        raise ValueError("Enter the number of days as a whole number.")
    days = int(number)
    if days < 1:
        raise ValueError("The number of days must be at least 1.")
    if days > MAX_DAYS:
        raise ValueError(f"The number of days can be at most {MAX_DAYS}.")
    return days


def _category(value, allowed_inactive: set[int]) -> BudgetCategory:
    if isinstance(value, BudgetCategory):
        category = value
    else:
        text = str(value if value is not None else "").strip()
        category = BudgetCategory.objects.filter(pk=int(text)).first() if text.isdigit() else None
    if category is None:
        raise ValueError("Choose a category.")
    if not category.is_active and category.pk not in allowed_inactive:
        raise ValueError(f'The category "{category.name}" is inactive. Choose an active one.')
    return category


def is_blank(row: dict) -> bool:
    """A row the person left empty (the form's spare rows): no particulars, quantity, unit or price typed."""
    return not any(str(row.get(field) or "").strip() for field in ("particulars", "quantity", "unit", "unit_price"))


def clean_lines(raw_lines, allowed_inactive: set[int] | None = None) -> list[LineValues]:
    """Check every line (a list of dicts with FIELDS; values may be text from the form, JSON numbers or Python values).
    Rows the person left empty are skipped. `allowed_inactive`: category IDs already on the activity (an inactive one
    may stay). Raises ValidationError with every problem at once."""
    if raw_lines is None:
        return []
    if not isinstance(raw_lines, (list, tuple)):
        raise ValidationError({LINES_KEY: ["The budget lines must be a list."]})
    allowed_inactive = allowed_inactive or set()
    errors: dict[str, list[str]] = {}
    lines: list[LineValues] = []
    for index, row in enumerate(raw_lines):
        if not isinstance(row, dict):
            errors[row_key(index, "particulars")] = ["Each budget line must be an object with its fields."]
            continue
        if is_blank(row):
            continue
        values = {}
        checks = {
            "category": lambda: _category(row.get("category"), allowed_inactive),
            "particulars": lambda: _text(row.get("particulars"), "particulars", 255),
            "quantity": lambda: _quantity(row.get("quantity")),
            "unit": lambda: _text(row.get("unit"), "unit (for example pax, pack, hour, lot)", 30),
            "days": lambda: _days(row.get("days")),
            "unit_price": lambda: _price(row.get("unit_price")),
        }
        for field, check in checks.items():
            try:
                values[field] = check()
            except ValueError as error:
                errors[row_key(index, field)] = [str(error)]
        if len(values) == len(FIELDS):
            line = LineValues(**values)
            if line.line_total > MAX_AMOUNT:
                errors[row_key(index, "unit_price")] = ["This line's total is too large."]
            else:
                lines.append(line)
    if len(lines) > MAX_LINES:
        errors[LINES_KEY] = [f"A budget can have at most {MAX_LINES} lines."]
    elif not errors and sum((line.line_total for line in lines), Decimal("0")) > MAX_AMOUNT:
        errors[LINES_KEY] = ["The grand total is too large."]
    if errors:
        raise ValidationError(errors)
    return lines


def _text(value, label: str, limit: int) -> str:
    text = " ".join(str(value if value is not None else "").split())
    if not text:
        raise ValueError(f"Enter the {label}.")
    if len(text) > limit:
        raise ValueError(f"The {label.split(' (')[0]} can be at most {limit} characters.")
    return text


def _quantity(value) -> Decimal:
    number = _decimal(value, "quantity", "The quantity can have at most 2 decimal places.")
    if number <= 0:
        raise ValueError("The quantity must be more than 0.")
    if number > MAX_QUANTITY:
        raise ValueError("The quantity is too large.")
    return number


def _price(value) -> Decimal:
    number = _decimal(value, "unit price", "The unit price can have at most 2 decimal places (centavos).")
    if number < 0:
        raise ValueError("The unit price must be 0 or more.")
    if number > MAX_AMOUNT:
        raise ValueError("The unit price is too large.")
    return number


def grand_total(lines) -> Decimal:
    """The sum of the line totals (LineValues or saved BUDGET_LINE rows)."""
    return sum((line.line_total for line in lines), Decimal("0.00")).quantize(CENTAVO)


def quantity_text(quantity: Decimal) -> str:
    """30.00 -> "30", 1.50 -> "1.5" (never scientific notation)."""
    text = f"{Decimal(quantity):f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def days_text(days: int) -> str:
    return f"{days} day" if days == 1 else f"{days} days"


def quantity_label(line) -> str:
    """The table's Quantity: "30 pax × 1 day"."""
    return f"{quantity_text(line.quantity)} {line.unit} × {days_text(line.days)}"


def line_text(line) -> str:
    """One line in History: 'Lunch (Meals/Snacks): 30 pax × 1 day × ₱150.00 = ₱4,500.00'."""
    return (f"{line.particulars} ({line.category.name}): {quantity_label(line)} × {format_money(line.unit_price)} = "
            f"{format_money(line.line_total)}")


def lines_text(lines) -> str:
    return "; ".join(line_text(line) for line in lines)


def saved_lines(activity: Activity) -> list[BudgetLine]:
    return list(BudgetLine.objects.filter(activity=activity).select_related("category").order_by("sort_order", "pk"))


def used_category_ids(activity: Activity | None) -> set[int]:
    if activity is None or not activity.pk:
        return set()
    return set(BudgetLine.objects.filter(activity=activity).values_list("category_id", flat=True))


def save_lines(activity: Activity, lines: list[LineValues], member, write_history: bool = True) -> bool:
    """Replace the activity's lines with `lines` (already checked by clean_lines) and set budget_amount to their grand
    total. Inside the caller's transaction (activities.services). History (`write_history`, False when the activity is
    being created: its first row is its creation), only when something changed: "Budget" (the lines, old -> new) and
    "Budget Amount" (the total). Returns True when the lines changed."""
    from .services import _write_audit  # activities.services imports this module

    old = saved_lines(activity)
    old_text, new_text = lines_text(old), lines_text(lines)
    old_total, new_total = activity.budget_amount, grand_total(lines)
    if old_text == new_text and old_total == new_total:
        return False
    BudgetLine.objects.filter(activity=activity).delete()
    BudgetLine.objects.bulk_create(
        BudgetLine(activity=activity, category=line.category, particulars=line.particulars, quantity=line.quantity,
                   unit=line.unit, days=line.days, unit_price=line.unit_price, line_total=line.line_total,
                   sort_order=order)
        for order, line in enumerate(lines, start=1)
    )
    activity.budget_amount = new_total
    activity.save(update_fields=["budget_amount", "updated_at"])
    if write_history:
        changes = []
        if old_text != new_text:
            changes.append((BUDGET_LABEL, old_text, new_text))
        if old_total != new_total:
            changes.append((BUDGET_AMOUNT_LABEL, old_total, new_total))
        _write_audit(activity, member, changes)
    return old_text != new_text


# --- Reading: the Overview, the API, the assistant, the reports ---------------------------------------------------


def fund_source_text(activity: Activity) -> str:
    return str(activity.fund_source) if activity.fund_source_id else NOT_SET


def budget_table(activity: Activity, lines: list[BudgetLine] | None = None) -> dict:
    """The "Running Budget" table of NAVIGATU's Activity Report (Prompt 6 reuses it): rows of Particulars / Quantity /
    Unit Cost / Total Amount, plus the grand total. Amounts are Decimal (formatting is the caller's)."""
    lines = saved_lines(activity) if lines is None else lines
    rows = [{"particulars": line.particulars, "category": line.category.name, "quantity": quantity_label(line),
             "unit_cost": line.unit_price, "total_amount": line.line_total} for line in lines]
    return {"rows": rows, "total": grand_total(lines)}


def category_totals(lines) -> list[tuple[str, Decimal]]:
    """(category, total) in the categories' own order, only categories with lines."""
    totals: dict[int, list] = {}
    for line in lines:
        entry = totals.setdefault(line.category.pk, [line.category, Decimal("0.00")])
        entry[1] += line.line_total
    ordered = sorted(totals.values(), key=lambda item: (item[0].sort_order, item[0].name))
    return [(category.name, total) for category, total in ordered]


def budget_summary(activity: Activity) -> dict:
    """Everything about one activity's budget: fund source (or "Not set"), account, the table, totals per category and
    the grand total (= ACTIVITY.budget_amount)."""
    lines = saved_lines(activity)
    table = budget_table(activity, lines)
    return {
        "fund_source": fund_source_text(activity),
        "fund_source_set": activity.fund_source_id is not None,
        "fund_account": activity.fund_account,
        "rows": table["rows"],
        "category_totals": category_totals(lines),
        "total": table["total"],
    }


def budget_json(activity: Activity) -> dict:
    """The API's "budget" (Decimal amounts become strings in config.json.JsonResponse)."""
    summary = budget_summary(activity)
    lines = saved_lines(activity)
    return {
        "fund_source_id": activity.fund_source_id,
        "fund_source": summary["fund_source"],
        "fund_account": summary["fund_account"],
        "lines": [{"category_id": line.category_id, "category": line.category.name, "particulars": line.particulars,
                   "quantity": line.quantity, "unit": line.unit, "days": line.days, "unit_price": line.unit_price,
                   "line_total": line.line_total} for line in lines],
        "category_totals": [{"category": name, "total": total} for name, total in summary["category_totals"]],
        "total": summary["total"],
    }


def preview(raw_lines, allowed_inactive: set[int] | None = None) -> dict:
    """The form's totals while typing (activities:budget_preview, display only): per row its total or nothing, the
    totals per category and the grand total, from clean_lines' own rules. Rows with problems are left out of the totals
    and their messages are returned."""
    totals, errors, good = {}, {}, []
    for index, row in enumerate(raw_lines):
        if not isinstance(row, dict) or is_blank(row):
            continue
        try:
            [line] = clean_lines([row], allowed_inactive)
        except ValidationError as error:
            for key, messages in error.message_dict.items():
                errors[key.replace("budget-0-", f"budget-{index}-")] = messages
            continue
        totals[index] = line.line_total
        good.append(line)
    return {"row_totals": totals, "errors": errors, "category_totals": category_totals(good), "total": grand_total(good)}


def active_fund_sources():
    return FundSource.objects.filter(is_active=True).order_by("sort_order", "code")


def active_categories():
    return BudgetCategory.objects.filter(is_active=True).order_by("sort_order", "name")
