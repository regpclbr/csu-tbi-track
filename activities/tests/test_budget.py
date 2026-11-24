"""An activity's budget (ASSUMPTIONS 433, NAVIGATU UAT): one required fund source, the budget lines copied from the
Activity Design approved on paper, line total = quantity x days x unit price (half-up to the centavo, Decimal only),
budget_amount = the grand total (never typed), History, Conducted locked, the old-total migration, the pages, the
reports and the AI assistant's record answer. There is no budget approval in the system."""

import importlib
import json
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.apps import apps as django_apps
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from activities import budget, services
from activities.models import Activity, ActivityAuditLog, BudgetCategory, BudgetLine, FundSource
from conftest import typed_schedule_fields

pytestmark = pytest.mark.django_db


def category(name: str = "Meals/Snacks") -> BudgetCategory:
    return BudgetCategory.objects.get(name=name)


def fund(code: str = "RAF") -> FundSource:
    return FundSource.objects.get(code=code)


def line(particulars="Lunch", quantity="30", unit="pax", days="1", unit_price="150.00", name="Meals/Snacks") -> dict:
    return {"category": category(name), "particulars": particulars, "quantity": quantity, "unit": unit, "days": days,
            "unit_price": unit_price}


def history(activity, label: str) -> list[tuple[str, str]]:
    return list(ActivityAuditLog.objects.filter(activity=activity, field_changed=label).order_by("log_id")
                .values_list("old_value", "new_value"))


def client_for(member) -> Client:
    client = Client()
    client.force_login(member)
    return client


# --- Calculations ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("quantity", "days", "price", "total"), [
    ("30", 1, "150.00", "4500.00"),
    ("1.5", 1, "33.33", "50.00"),  # 49.995 -> half-up
    ("0.25", 3, "10.10", "7.58"),  # 7.575 -> half-up
    ("0.25", 1, "0.02", "0.01"),  # 0.005 -> half-up
    ("2", 3, "0", "0.00"),
])
def test_line_total_is_quantity_days_price_half_up(quantity, days, price, total) -> None:
    assert budget.line_total(Decimal(quantity), days, Decimal(price)) == Decimal(total)


def test_totals_per_category_and_grand_total(roles, director, valid_data) -> None:
    lines = [line(), line("Snacks", "30", "pax", "2", "50.00"), line("Speaker", "1", "person", "1", "3000", "Honorarium")]
    activity = services.create_activity(director, valid_data(budget_lines=lines))
    assert activity.budget_amount == Decimal("10500.00")  # 4,500 + 3,000 + 3,000
    summary = budget.budget_summary(activity)
    assert summary["category_totals"] == [("Meals/Snacks", Decimal("7500.00")), ("Honorarium", Decimal("3000.00"))]
    assert [row["quantity"] for row in summary["rows"]] == ["30 pax × 1 day", "30 pax × 2 days", "1 person × 1 day"]
    assert BudgetLine.objects.filter(activity=activity).count() == 3


@pytest.mark.parametrize(("field", "value", "message"), [
    ("quantity", "1.255", "The quantity can have at most 2 decimal places."),
    ("unit_price", "33.335", "The unit price can have at most 2 decimal places (centavos)."),
    ("quantity", "0", "The quantity must be more than 0."),
    ("unit_price", "-1", "The unit price must be 0 or more."),
    ("quantity", "abc", "Enter a number for the quantity."),
    ("days", "1.5", "Enter the number of days as a whole number."),
    ("days", "0", "The number of days must be at least 1."),
    ("particulars", "", "Enter the particulars."),
    ("unit", "", "Enter the unit (for example pax, pack, hour, lot)."),
    ("category", "", "Choose a category."),
])
def test_each_value_is_checked_with_a_clear_message(roles, field, value, message) -> None:
    row = {**line(), field: value}
    with pytest.raises(ValidationError) as refused:
        budget.clean_lines([row])
    assert refused.value.message_dict == {f"budget-0-{field}": [message]}


def test_amounts_are_decimal_commas_and_peso_sign_allowed_days_default_one(roles) -> None:
    [checked] = budget.clean_lines([{**line(), "quantity": 1.5, "unit_price": "₱1,234.50", "days": ""}])
    assert (checked.quantity, checked.unit_price, checked.days) == (Decimal("1.50"), Decimal("1234.50"), 1)
    assert budget.clean_lines([{**line(), "unit_price": "10.500"}])[0].unit_price == Decimal("10.50")  # same value


def test_blank_rows_are_skipped_no_lines_is_zero(roles, director, valid_data) -> None:
    blank = {"category": "", "particulars": "", "quantity": "", "unit": "", "days": "1", "unit_price": ""}
    assert budget.clean_lines([blank]) == []
    assert services.create_activity(director, valid_data(budget_lines=[blank])).budget_amount == Decimal("0.00")


def test_an_inactive_category_is_refused_unless_already_used(roles, director, valid_data) -> None:
    activity = services.create_activity(director, valid_data(budget_lines=[line(name="Printing")]))
    BudgetCategory.objects.filter(name="Printing").update(is_active=False)
    with pytest.raises(ValidationError, match="inactive"):
        services.create_activity(director, valid_data(title="New", budget_lines=[line(name="Printing")]))
    services.update_activity(director, activity, {"budget_lines": [line("Flyers", name="Printing")]})  # kept
    assert budget.saved_lines(activity)[0].particulars == "Flyers"


def test_too_many_lines_and_a_total_too_large(roles) -> None:
    with pytest.raises(ValidationError) as refused:
        budget.clean_lines([line(f"Item {n}") for n in range(budget.MAX_LINES + 1)])
    assert refused.value.message_dict == {"budget_lines": [f"A budget can have at most {budget.MAX_LINES} lines."]}
    with pytest.raises(ValidationError) as refused:
        budget.clean_lines([line(quantity="99999999.99", days="366", unit_price="9999.99")])
    assert refused.value.message_dict == {"budget-0-unit_price": ["This line's total is too large."]}


# --- Fund source ----------------------------------------------------------------------------------------------------


def test_the_fund_source_is_required_when_creating(roles, director, valid_data) -> None:
    with pytest.raises(ValidationError) as refused:
        services.create_activity(director, valid_data(fund_source=None))
    assert refused.value.message_dict["fund_source"] == [services.FUND_SOURCE_REQUIRED]


def test_an_older_activity_without_a_fund_source_stays_editable_but_never_cleared(roles, director, new_activity) -> None:
    Activity.objects.filter(pk=new_activity.pk).update(fund_source=None)
    new_activity.refresh_from_db()
    services.update_activity(director, new_activity, {"venue": "Room 101"})  # "Not set" is allowed to stay
    assert budget.budget_summary(new_activity)["fund_source"] == "Not set"
    services.update_activity(director, new_activity, {"fund_source": fund("TRF"), "fund_account": "  Research  "})
    new_activity.refresh_from_db()
    assert (new_activity.fund_source.code, new_activity.fund_account) == ("TRF", "Research")
    with pytest.raises(ValidationError, match="Choose the fund source"):
        services.update_activity(director, new_activity, {"fund_source": None})
    assert history(new_activity, "Fund Source") == [("", "TRF – Thrust Fund")]


def test_an_inactive_fund_source_is_refused_unless_kept(roles, director, new_activity, valid_data) -> None:
    FundSource.objects.filter(code="RAF").update(is_active=False)
    services.update_activity(director, new_activity, {"venue": "Room 101"})  # RAF stays
    with pytest.raises(ValidationError, match="inactive"):
        services.create_activity(director, valid_data(title="New"))


def test_the_page_form_requires_a_fund_source(roles, director, new_activity) -> None:
    from activities.forms import ActivityPageForm

    form = ActivityPageForm(data={"fund_source": ""}, activity=new_activity)
    form.is_valid()
    assert form.errors["fund_source"] == ["Choose the fund source."]


# --- History and the edit rules -------------------------------------------------------------------------------------


def test_history_of_budget_changes(roles, director, valid_data) -> None:
    activity = services.create_activity(director, valid_data(budget_lines=[line()]))
    assert history(activity, "Budget") == [] and history(activity, "Budget Amount") == []  # creation is its own row
    services.update_activity(director, activity, {"budget_lines": [line(), line("Snacks", unit_price="40.00")]})
    [(old, new)] = history(activity, "Budget")
    assert old == "Lunch (Meals/Snacks): 30 pax × 1 day × ₱150.00 = ₱4,500.00"
    assert new == old + "; Snacks (Meals/Snacks): 30 pax × 1 day × ₱40.00 = ₱1,200.00"
    assert history(activity, "Budget Amount") == [("₱4,500.00", "₱5,700.00")]
    services.update_activity(director, activity, {"budget_lines": [line(), line("Snacks", unit_price="40.00")]})
    assert len(history(activity, "Budget")) == 1  # nothing changed: no row
    activity.refresh_from_db()
    assert activity.budget_amount == Decimal("5700.00")


def test_a_conducted_activity_is_read_only(roles, director, new_activity, move_to) -> None:
    new_activity.start_date = new_activity.end_date = date.today()
    move_to(new_activity, "Conducted")
    with pytest.raises(services.ActivityError, match="read-only"):
        services.update_activity(director, new_activity, {"budget_lines": [line()]})


def test_no_approval_status_notification_or_attention(roles, director, valid_data) -> None:
    from activities import attention
    from notifications.models import Notification

    activity = services.create_activity(director, valid_data(budget_lines=[line()]))
    assert not any("budget" in item.text.lower() for item in attention.activity_attention(activity))
    assert not Notification.objects.filter(message__icontains="budget").exists()


# --- The old-total migration ----------------------------------------------------------------------------------------


def test_the_migration_keeps_an_old_total_as_one_line(roles, director, valid_data) -> None:
    old = services.create_activity(director, valid_data(title="Old"))
    free = services.create_activity(director, valid_data(title="Free"))
    Activity.objects.filter(pk=old.pk).update(budget_amount=Decimal("12345.67"), fund_source=None)
    migration = importlib.import_module("activities.migrations.0014_budget_lookups_previous_totals")
    migration.forward(django_apps, None)
    migration.forward(django_apps, None)  # safe to run twice: one line only
    [made] = BudgetLine.objects.filter(activity=old)
    assert (made.particulars, made.category.name, made.quantity, made.unit, made.days) == (
        budget.PREVIOUS_TOTAL, "Other", Decimal("1.00"), "lot", 1)
    assert made.unit_price == made.line_total == Decimal("12345.67")
    assert not BudgetLine.objects.filter(activity=free).exists()  # a total of 0 gets no line
    old.refresh_from_db()
    assert old.fund_source is None and budget.budget_summary(old)["total"] == Decimal("12345.67")  # still "Not set"
    migration.backward(django_apps, None)
    assert not BudgetLine.objects.filter(activity=old).exists()


# --- The pages ------------------------------------------------------------------------------------------------------


def page_post(values: dict, rows: list[dict]) -> dict:
    data = {
        "title": values["title"], "description": values["description"], "activity_type": values["activity_type"].pk,
        "participant_categories": [c.pk for c in values["participant_categories"]], **typed_schedule_fields(values),
        "venue": values["venue"], "max_slots": values["max_slots"], "fund_source": values["fund_source"].pk,
        "fund_account": "Research Services – Representation Expenses",
    }
    for key, row in enumerate(rows):
        for field, value in row.items():
            data[f"budget-{key * 3}-{field}"] = value.pk if isinstance(value, BudgetCategory) else value
    return data


def test_the_new_form_saves_the_lines_and_the_overview_shows_them(roles, director, valid_data) -> None:
    client = client_for(director)
    html = client.get(reverse("activities:create")).content.decode()
    assert 'name="fund_source"' in html and 'name="budget-0-particulars"' in html and "Add a line" in html
    data = page_post(valid_data(title="With budget"), [line(), line("Speaker", "1", "person", "1", "3000", "Honorarium")])
    assert client.post(reverse("activities:create"), data).status_code == 302
    activity = Activity.objects.get(title="With budget")
    assert activity.budget_amount == Decimal("7500.00") and activity.fund_account.startswith("Research Services")
    overview = client.get(reverse("activities:detail", args=[activity.pk])).content.decode()
    for text in ("Fund source", "RAF – Regular Agency Fund", "Particulars", "Unit Cost", "Total Amount",
                 "30 pax × 1 day", "₱4,500.00", "₱7,500.00"):
        assert text in overview, text


def test_a_bad_line_is_shown_under_its_cell_and_nothing_is_saved(roles, director, valid_data) -> None:
    data = page_post(valid_data(title="Bad budget"), [line(unit_price="10.555")])
    html = client_for(director).post(reverse("activities:create"), data).content.decode()
    assert "The unit price can have at most 2 decimal places (centavos)." in html
    assert "Please correct the budget lines marked below." in html
    assert 'value="10.555"' in html and not Activity.objects.filter(title="Bad budget").exists()


def test_the_edit_form_shows_the_saved_lines_and_saves_changes(roles, director, valid_data) -> None:
    activity = services.create_activity(director, valid_data(budget_lines=[line()]))
    client = client_for(director)
    html = client.get(reverse("activities:edit", args=[activity.pk])).content.decode()
    assert 'value="Lunch"' in html and 'value="150.00"' in html
    data = page_post(valid_data(), [line(unit_price="200.00")])
    assert client.post(reverse("activities:edit", args=[activity.pk]), data).status_code == 302
    activity.refresh_from_db()
    assert activity.budget_amount == Decimal("6000.00")


def test_the_preview_uses_the_service(roles, director) -> None:
    data = {"budget-0-category": category().pk, "budget-0-particulars": "Lunch", "budget-0-quantity": "1.5",
            "budget-0-unit": "pax", "budget-0-days": "1", "budget-0-unit_price": "33.33",
            "budget-4-category": category().pk, "budget-4-particulars": "Bad", "budget-4-quantity": "1",
            "budget-4-unit": "pax", "budget-4-days": "1", "budget-4-unit_price": "1.001"}
    html = client_for(director).post(reverse("activities:budget_preview"), data).content.decode()
    assert 'id="budget-row-total-0" hx-swap-oob="true">₱50.00' in html
    assert "Grand total" in html and "1 line not counted yet" in html


def test_the_api_takes_lines_and_refuses_three_decimals(roles, director, valid_data, client) -> None:
    client.force_login(director)
    values = valid_data()
    payload = {"title": "API budget", "activity_type": values["activity_type"].pk,
               "participant_categories": [c.pk for c in values["participant_categories"]],
               "start_date": values["start_date"].isoformat(), "end_date": values["end_date"].isoformat(),
               "start_time": "08:00", "end_time": "17:00", "venue": "Hall", "max_slots": 10,
               "registration_deadline": values["registration_deadline"].isoformat(), "fund_source": fund("IGF").pk,
               "budget_lines": [{"category": category().pk, "particulars": "Lunch", "quantity": 0.25, "unit": "pax",
                                 "days": 3, "unit_price": "10.10"}]}
    response = client.post("/activities/api/", json.dumps(payload), content_type="application/json")
    assert response.status_code == 201
    data = response.json()["activity"]
    assert data["budget_amount"] == "7.58" and data["budget"]["fund_source"] == "IGF – Internally Generated Fund"
    payload["budget_lines"][0]["quantity"] = "0.255"
    payload["title"] = "Refused"
    response = client.post("/activities/api/", json.dumps(payload), content_type="application/json")
    assert response.status_code == 400
    assert response.json()["errors"]["budget-0-quantity"] == ["The quantity can have at most 2 decimal places."]
    payload["budget_amount"] = "100"
    response = client.post("/activities/api/", json.dumps(payload), content_type="application/json")
    assert response.json()["errors"] == {"budget_amount": [services.BUDGET_TYPED]}


# --- Reports --------------------------------------------------------------------------------------------------------


def test_budget_per_period_and_fund_source(roles, director, valid_data, move_to) -> None:
    from reports import content, metrics

    def conducted(title, code, lines):
        activity = services.create_activity(director, valid_data(title=title, fund_source=fund(code), budget_lines=lines))
        activity.start_date = activity.end_date = date.today()
        Activity.objects.filter(pk=activity.pk).update(start_date=date.today(), end_date=date.today(),
                                                      registration_deadline=valid_data()["registration_deadline"] - timedelta(days=60))
        activity.refresh_from_db()
        move_to(activity, "Conducted")
        return activity

    conducted("A", "RAF", [line()])
    conducted("B", "RAF", [line(unit_price="100.00")])
    older = conducted("C", "IGF", [line("Speaker", "1", "person", "1", "2500.50", "Honorarium")])
    Activity.objects.filter(pk=older.pk).update(fund_source=None)
    filters = metrics.ReportFilters(start=date.today() - timedelta(days=1), end=date.today(), activity_type_id=None)
    assert metrics.period_budget_total(filters) == Decimal("10000.50")
    rows = {row["fund_source"]: (row["activities"], row["total"]) for row in metrics.budget_by_fund_source(filters)}
    assert rows["RAF – Regular Agency Fund"] == (2, Decimal("7500.00"))
    assert rows["IGF – Internally Generated Fund"] == (0, Decimal("0.00"))
    assert rows["Not set"] == (1, Decimal("2500.50"))
    assert "budget" in content.REPORT_TYPES["accomplishment"].default_sections
    built = content.build_content("accomplishment", list(content.REPORT_TYPES["accomplishment"].default_sections), filters,
                                  generated_by="Test", generated_at=valid_data()["registration_deadline"])
    section = built.section("budget")
    assert section.tables[0].total == ["Total", 3, Decimal("10000.50")]
    assert section.tables[0].columns[2].kind == content.MONEY


# --- The AI assistant -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("question", [
    "How much is the budget for the Pitching Workshop?",
    "What is the fund source of Pitching Workshop?",
    "how much will pitching workshop cost",
])
def test_budget_questions_are_answered_from_the_records(roles, director, valid_data, question) -> None:
    from django.utils import timezone

    from assistant import routing

    activity = services.create_activity(director, valid_data(budget_lines=[line()]))
    decided = routing.route(question, timezone.now())
    assert decided.path == routing.BUDGET and decided.activity_ids == [activity.pk]


def test_the_activity_design_content_still_goes_to_the_document_search(roles) -> None:
    from django.utils import timezone

    from assistant import routing

    decided = routing.route("What does the Activity Design say about the budget?", timezone.now())
    assert decided.path == routing.DOCUMENT_CONTENT


def test_the_budget_answer_and_the_record_tool(roles, director, valid_data) -> None:
    from assistant import services as assistant_services
    from assistant import tools

    activity = services.create_activity(director, valid_data(budget_lines=[line(), line("Speaker", "1", "person", "1",
                                                                                         "3000", "Honorarium")]))
    chat = assistant_services.create_chat(director)
    answer = assistant_services.ask(director, chat, "How much is the budget of the Pitching Workshop?")
    text = answer.message.content
    assert f"[activity:{activity.pk}]" in text and "Fund source: RAF – Regular Agency Fund" in text
    assert "- Lunch (Meals/Snacks): 30 pax × 1 day × ₱150.00 = ₱4,500.00" in text
    assert "**Total: ₱7,500.00**" in text and text.endswith("(Source: the system's records.)")
    result = tools.RECORD_FUNCTIONS[tools.BUDGET_TOOL]({"activity_id": activity.pk})
    assert result["grand_total"] == "7500.00" and result["fund_source"] == "RAF – Regular Agency Fund"
    assert result["lines"][0]["quantity"] == "30.00" and result["lines"][0]["line_total"] == "4500.00"
    assert tools.BUDGET_TOOL in {spec.name for spec in tools.TOOL_SPECS}


def test_no_activity_named_asks_which(roles) -> None:
    from assistant.listings import budget_answer

    assert budget_answer(None).text.startswith("Which activity?")
    assert budget_answer([]).text.startswith("No activity has that title.")
