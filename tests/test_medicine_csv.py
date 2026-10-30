import datetime

from app.views import MEDICINE_CSV_FIELDS, parse_medicine_csv


def make_csv(*rows):
    return ",".join(MEDICINE_CSV_FIELDS) + "\n" + "\n".join(rows)


def test_medicine_csv_parses_multiple_batches_and_defaults_optional_numbers():
    expiry = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
    content = make_csv(
        f"Tablet,Cold,Maker,123,B-1,{expiry},2.50,4.00,8,5,5",
        f"Syrup,Other,,,B-2,{expiry},10,15,,,",
    )

    medicines, errors = parse_medicine_csv(content)

    assert errors == []
    assert len(medicines) == 2
    assert medicines[0]["name"] == "Tablet"
    assert medicines[0]["quantity"] == 8
    assert medicines[1]["quantity"] == 0
    assert medicines[1]["reorder_level"] == 10


def test_medicine_csv_rejects_incorrect_headers():
    medicines, errors = parse_medicine_csv("name,batch_number\nTablet,B-1")

    assert medicines == []
    assert errors and "columns exactly once" in errors[0]


def test_medicine_csv_rejects_malformed_quoting_without_partial_results():
    expiry = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
    content = make_csv(
        f"Valid,Other,,,B-1,{expiry},2,4,1,1,0",
        '"Unclosed,,,,B-2,2030-01-01,2,4,1,1,0',
    )

    medicines, errors = parse_medicine_csv(content)

    assert medicines == []
    assert errors and "Could not read CSV" in errors[0]


def test_medicine_csv_rejects_invalid_rows_without_returning_partial_import():
    expiry = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
    content = make_csv(
        f"Valid,Other,,,B-1,{expiry},2,4,1,1,0",
        f"Invalid,Other,,,B-2,{expiry},5,4,1,1,0",
    )

    medicines, errors = parse_medicine_csv(content)

    assert medicines == []
    assert any("Row 3" in error and "selling price" in error for error in errors)


def test_medicine_csv_rejects_expired_dates():
    future = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
    past = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    content = make_csv(f"Expired,Other,,123,B-1,{past},2,4,1,1,0")

    medicines, errors = parse_medicine_csv(content)

    assert medicines == []
    assert any("expiry date must be after today" in error for error in errors)


def test_medicine_csv_rejects_barcode_assigned_to_different_medicines():
    expiry = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
    content = make_csv(
        f"Tablet,Cold,,123,B-1,{expiry},2,4,1,1,0",
        f"Syrup,Cold,,123,B-2,{expiry},2,4,1,1,0",
    )

    medicines, errors = parse_medicine_csv(content)

    assert medicines == []
    assert any("barcode is assigned to different medicines" in error for error in errors)


def test_medicine_csv_allows_multiple_batches_for_the_same_medicine():
    expiry = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
    content = make_csv(
        f"Tablet,Cold,Maker,123,B-1,{expiry},2,4,1,1,0",
        f"tablet,cold,maker,123,B-2,{expiry},2,4,1,1,0",
    )

    medicines, errors = parse_medicine_csv(content)

    assert errors == []
    assert len(medicines) == 2
    assert medicines[0]["identity"] == medicines[1]["identity"]


def test_medicine_csv_requires_category_and_rejects_excess_decimal_places():
    expiry = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
    missing_category = make_csv(f"Tablet,,,,B-1,{expiry},2,4,1,1,0")
    medicines, errors = parse_medicine_csv(missing_category)
    assert medicines == []
    assert any("category" in error.lower() for error in errors)

    bad_price = make_csv(f"Tablet,Cold,,,B-1,{expiry},2.001,4,1,1,0")
    medicines, errors = parse_medicine_csv(bad_price)
    assert medicines == []
    assert any("two decimal places" in error.lower() for error in errors)
