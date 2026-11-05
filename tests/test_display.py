"""Offline tests for ui/display.py: chart choice, headline figures and formatting."""

from ui.display import bar_labels, choose_chart, format_value, headline, label, sort_column


def test_label_and_formatting():
    assert label("tickets_sold") == "Tickets sold"
    assert format_value("revenue", 62434243.0) == "$62,434,243.00"
    assert format_value("avg_ticket_price", 310.4) == "$310.40"
    assert format_value("tickets_sold", 829) == "829"
    assert format_value("tickets_sold", 345349.0) == "345,349"
    assert format_value("revenue", None) == "–"


def test_one_row_shows_headline_figures_not_a_chart():
    columns, rows = ["tickets_sold", "revenue"], [(829, 242452.0)]
    assert headline(columns, rows) == [("Tickets sold", "829"), ("Revenue", "$242,452.00")]
    assert choose_chart(columns, rows) is None


def test_headline_skips_ids_and_text():
    assert headline(["user_id", "username", "total_spent"], [(4303, "LXF16RPG", 46710.0)]) \
        == [("Total spent", "$46,710.00")]


def test_ranking_gets_a_bar_chart():
    columns = ["category", "category_group", "revenue", "tickets_sold"]
    rows = [("Pop", "Concerts", 62434243.0, 195444), ("Plays", "Shows", 25538940.0, 78545)]
    spec = choose_chart(columns, rows)
    assert (spec.kind, spec.x, spec.y, spec.money) == ("bar", "category", "revenue", True)


def test_ranking_charts_the_column_it_is_sorted_by():
    # "Top 5 categories by revenue": tickets_sold comes first but the ranking is by revenue.
    columns = ["category", "category_group", "tickets_sold", "revenue"]
    rows = [("Pop", "Concerts", 195444, 62434243.0), ("Plays", "Shows", 78545, 25538940.0)]
    sql = ("SELECT category, category_group, SUM(tickets) AS tickets_sold, "
           "ROUND(SUM(sale_total), 2) AS revenue FROM sales_detail "
           "GROUP BY category, category_group ORDER BY revenue DESC LIMIT 5")
    spec = choose_chart(columns, rows, sort_column(sql))
    assert (spec.y, spec.money) == ("revenue", True)
    assert choose_chart(columns, rows).y == "tickets_sold"      # no sort known: first number


def test_sort_column():
    assert sort_column("SELECT a FROM t ORDER BY revenue DESC LIMIT 5") == "revenue"
    assert sort_column("SELECT a FROM t\nORDER BY s.tickets_sold DESC\nLIMIT 10;") == "tickets_sold"
    assert sort_column("SELECT a FROM t ORDER BY a.category_group, a.category") == "category_group"
    assert sort_column("SELECT a FROM t ORDER BY SUM(x) DESC") is None      # an expression
    assert sort_column("SELECT 1") is None
    assert sort_column(None) is None


def test_values_over_time_get_a_line_chart():
    rows = [("2025-01", 10658), ("2025-02", 27767), ("2025-03", 32789)]
    spec = choose_chart(["sale_month", "tickets_sold"], rows)
    assert (spec.kind, spec.x, spec.y, spec.money) == ("line", "sale_month", "tickets_sold", False)


def test_year_column_is_the_axis_not_the_value():
    spec = choose_chart(["year", "revenue"], [(2025, 112023667.0), (2026, 331499.0)])
    assert (spec.kind, spec.x, spec.y) == ("line", "year", "revenue")


def test_weekday_labels_are_categories_not_time():
    spec = choose_chart(["sale_weekday", "tickets"], [("SA", 50641), ("FR", 50100)])
    assert spec.kind == "bar"


def test_ranking_with_a_date_per_row_is_a_bar_chart_not_a_timeline():
    # Live result for "highest average ticket price at Barclays": ordered by price.
    columns = ["event_id", "event", "event_date", "avg_ticket_price", "tickets_sold"]
    rows = [(8805, "Nets vs. 76ers", "2025-02-05", 1149.71, 28),
            (8801, "Nets vs. Rockets", "2025-01-20", 1007.8, 5),
            (8810, "Nets vs. Celtics", "2025-02-25", 695.92, 114)]
    spec = choose_chart(columns, rows)
    assert (spec.kind, spec.x, spec.y, spec.x_with) == ("bar", "event", "avg_ticket_price", None)


def test_repeated_names_are_labelled_with_their_date():
    columns = ["event", "event_date", "avg_ticket_price"]
    rows = [("Nets vs. Rockets", "2025-01-20", 1007.8), ("Nets vs. Rockets", "2025-12-23", 456.92)]
    spec = choose_chart(columns, rows)
    assert (spec.kind, spec.x_with) == ("bar", "event_date")
    assert bar_labels(spec, columns, rows) == ["Nets vs. Rockets (2025-01-20)",
                                               "Nets vs. Rockets (2025-12-23)"]


def test_no_chart_when_it_would_not_help():
    assert choose_chart(["event", "venue"], [("A", "X"), ("B", "Y")]) is None      # no numbers
    many = [(f"Show {i}", i) for i in range(30)]
    assert choose_chart(["show", "tickets"], many) is None                          # too many bars
    repeated = [("Nets vs. Rockets", 1007.8), ("Nets vs. Rockets", 456.92)]
    assert choose_chart(["event", "avg_ticket_price"], repeated) is None            # can't tell apart


def test_no_chart_when_the_only_number_is_a_year():
    # Used to raise StopIteration and crash the page.
    assert choose_chart(["show", "first_year"], [("A", 2025), ("B", 2026)]) is None


def test_a_year_column_is_not_plotted_as_the_value():
    spec = choose_chart(["event_name", "sale_year", "tickets"], [("A", 2025, 5), ("B", 2025, 7)])
    assert spec.y == "tickets"
