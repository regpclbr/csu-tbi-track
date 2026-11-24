"""Data only (ASSUMPTIONS 433). 1) The fund sources (RAF, IGF, TRF, BRF) and budget categories (Meals/Snacks ... Other),
created only when missing (the same rows seed_data creates; an Admin's edit is never overwritten). 2) Every existing
activity with a budget_amount above 0 and no budget lines yet gets ONE line "Unitemized (previous total)": category
Other, quantity 1, unit "lot", 1 day, unit price = the old total, so its grand total is unchanged. Activities with a
total of 0 get no line. No activity gets a fund source (owner decision: "Not set" until someone edits the activity).
Reverse: deletes only the lines this migration made (same particulars, unit and category); the lookup rows stay.
"""

from decimal import Decimal

from django.db import migrations

FUND_SOURCES = [("RAF", "Regular Agency Fund"), ("IGF", "Internally Generated Fund"), ("TRF", "Thrust Fund"),
                ("BRF", "Business Related Fund")]
BUDGET_CATEGORIES = ["Meals/Snacks", "Honorarium", "Supplies", "Venue", "Transportation", "Printing", "Other"]
PREVIOUS_TOTAL = "Unitemized (previous total)"
UNIT = "lot"
OTHER = "Other"


def forward(apps, schema_editor):
    FundSource = apps.get_model("activities", "FundSource")
    BudgetCategory = apps.get_model("activities", "BudgetCategory")
    BudgetLine = apps.get_model("activities", "BudgetLine")
    Activity = apps.get_model("activities", "Activity")
    for order, (code, name) in enumerate(FUND_SOURCES, start=1):
        FundSource.objects.get_or_create(code=code, defaults={"name": name, "sort_order": order})
    for order, name in enumerate(BUDGET_CATEGORIES, start=1):
        BudgetCategory.objects.get_or_create(name=name, defaults={"sort_order": order})
    other = BudgetCategory.objects.get(name=OTHER)
    lines = [
        BudgetLine(activity_id=activity_id, category=other, particulars=PREVIOUS_TOTAL, quantity=Decimal("1.00"),
                   unit=UNIT, days=1, unit_price=total, line_total=total, sort_order=1)
        for activity_id, total in Activity.objects.filter(budget_amount__gt=0, budget_lines__isnull=True)
        .values_list("activity_id", "budget_amount")
    ]
    BudgetLine.objects.bulk_create(lines)


def backward(apps, schema_editor):
    BudgetLine = apps.get_model("activities", "BudgetLine")
    BudgetLine.objects.filter(particulars=PREVIOUS_TOTAL, unit=UNIT, category__name=OTHER).delete()


class Migration(migrations.Migration):
    dependencies = [("activities", "0013_budget_lines_fund_source")]

    operations = [migrations.RunPython(forward, backward)]
