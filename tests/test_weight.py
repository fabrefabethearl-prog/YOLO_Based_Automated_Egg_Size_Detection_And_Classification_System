"""Size bands, area, and a clean migration off gram columns."""

import pytest
import sqlite3

from backend.services.metrics import classification_report
from backend.services.size import area_mm2, assign_class, box_to_mm, class_for_area, fit_size_bands


class _Cat:
    def __init__(self, name, min_area, sort_order, color="#d99a2b"):
        self.name = name
        self.min_area = min_area
        self.sort_order = sort_order
        self.color = color


CATS = [
    _Cat("Small", 2250, 0, "#5a9a6e"),
    _Cat("Medium", 2420, 1, "#2d6a4f"),
    _Cat("Large", 2560, 2, "#d99a2b"),
    _Cat("Extra Large", 2730, 3, "#c4733b"),
    _Cat("Jumbo", 2900, 4, "#a23d3d"),
]


def test_area_is_length_times_width():
    assert area_mm2(56, 43.8) == pytest.approx(2452.8)
    length, width = box_to_mm(200, 100, 4)
    assert length == 50
    assert width == 25
    assert area_mm2(length, width) == 1250


def test_box_uses_longer_side_as_length():
    length, width = box_to_mm(80, 160, 2)
    assert length == 80
    assert width == 40


def test_class_bands_use_minimum_area():
    assert class_for_area(0, CATS) == "Small"
    assert class_for_area(2419, CATS) == "Small"
    assert class_for_area(2420, CATS) == "Medium"
    assert class_for_area(2560, CATS) == "Large"
    assert class_for_area(2729, CATS) == "Large"
    assert class_for_area(2730, CATS) == "Extra Large"
    assert class_for_area(2899, CATS) == "Extra Large"
    assert class_for_area(2900, CATS) == "Jumbo"
    assert class_for_area(9000, CATS) == "Jumbo"


def test_reject_stops_below_the_first_band_and_nearest_does_not():
    missing, used = assign_class(
        1000, CATS, mode="dimensions", mock=True, class_name=None, model_class_count=1, out_of_range="reject"
    )
    assert missing is None
    assert used == "range"
    nearest, used = assign_class(
        1000, CATS, mode="dimensions", mock=True, class_name=None, model_class_count=1, out_of_range="nearest"
    )
    assert nearest == "Small"
    assert used == "dimensions"
    kept, _used = assign_class(
        2500, CATS, mode="dimensions", mock=True, class_name=None, model_class_count=1, out_of_range="reject"
    )
    assert kept == "Medium"
    refused, used = assign_class(
        2500, CATS, mode="model", mock=False, class_name="other", model_class_count=4, out_of_range="nearest"
    )
    assert refused is None
    assert used == "model"


def test_fit_bands_uses_midpoints_between_medians():
    samples = [("Small", 1000)] * 6 + [("Large", 2000)] * 6
    fitted = fit_size_bands(samples, [_Cat("Small", 0, 0), _Cat("Large", 50, 1)])
    bands = {row["name"]: row for row in fitted["bands"]}
    assert bands["Small"]["min_area"] == 0
    assert bands["Large"]["min_area"] == 1500
    assert fitted["sample_count"] == 12


def test_fit_keeps_five_classes_in_order():
    samples = (
        [("Small", 1000)] * 6
        + [("Medium", 2000)] * 6
        + [("Large", 3000)] * 6
        + [("Extra Large", 4000)] * 6
        + [("Jumbo", 5000)] * 6
    )
    fitted = fit_size_bands(samples, CATS)
    bands = fitted["bands"]
    assert [row["name"] for row in bands] == ["Small", "Medium", "Large", "Extra Large", "Jumbo"]
    assert [row["min_area"] for row in bands] == [0, 1500, 2500, 3500, 4500]


def test_fit_bands_needs_ten_verified_eggs():
    with pytest.raises(ValueError):
        fit_size_bands([("Small", 1000)] * 9, CATS)


def test_macro_f1_is_zero_when_the_class_is_wrong():
    labels = ["Small", "Medium", "Large", "Extra Large", "Jumbo"]
    report = classification_report([("Large", "Small")], labels, closed=True)
    assert report["accuracy"] == 0
    assert report["macro_f1"] == 0
    assert report["labels"] == labels
    assert len(report["matrix"]) == 5
    assert all(len(row) == 5 for row in report["matrix"])


def test_classification_metrics_ignore_unsupported_pairs_in_all_totals():
    labels = ["Small", "Medium", "Large"]
    report = classification_report(
        [("Small", "Small"), ("Medium", "Small"), ("Unsupported", "Large"), ("Large", "Outside")],
        labels,
        closed=True,
    )
    assert report["evaluated_count"] == 2
    assert report["ignored_count"] == 2
    assert report["correct_count"] == 1
    assert report["incorrect_count"] == 1
    assert report["accuracy"] == 0.5
    assert sum(map(sum, report["matrix"])) == report["evaluated_count"]
    assert [row["support"] for row in report["per_class"]] == [1, 1, 0]
    assert report["per_class"][2]["precision"] is None
    assert report["per_class"][2]["recall"] is None
    assert report["macro_recall"] == 0.5
    assert report["support_adjusted_recall"] == 0.5


def test_classification_metrics_empty_set_are_undefined():
    report = classification_report([], ["Small", "Medium"], closed=True)
    assert report["accuracy"] is None
    assert report["macro_f1"] is None
    assert report["support_adjusted_f1"] is None
    assert report["evaluated_count"] == 0
    assert report["matrix"] == [[0, 0], [0, 0]]


def test_old_database_drops_gram_columns(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE scans (
            id INTEGER PRIMARY KEY, created_at TEXT, image_path TEXT,
            length_mm FLOAT, width_mm FLOAT, est_weight_g FLOAT, actual_weight_g FLOAT,
            predicted_class TEXT, actual_class TEXT, confidence FLOAT, inference_ms FLOAT, is_mock INTEGER
        )
        """
    )
    connection.execute("INSERT INTO scans VALUES (1, '2026-01-01', 'a.jpg', 60, 44, 70, 68, 'Jumbo', 'Large', 0.9, 20, 1)")
    connection.execute("CREATE TABLE categories (id INTEGER PRIMARY KEY, name TEXT, min_weight FLOAT, color TEXT, sort_order INTEGER)")
    connection.execute("INSERT INTO categories VALUES (1, 'Large', 60, '#e07a3d', 0)")
    connection.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    connection.execute("INSERT INTO settings VALUES ('k', '0.00054')")
    connection.commit()
    connection.close()

    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + path.as_posix())
    monkeypatch.setenv("EGGSCAN_DATA_DIR", str(tmp_path / "data"))
    from backend.db import init_db

    init_db()
    check = sqlite3.connect(path)
    scan_columns = {row[1] for row in check.execute("PRAGMA table_info(scans)")}
    category_columns = {row[1] for row in check.execute("PRAGMA table_info(categories)")}
    area, actual = check.execute("SELECT area_mm2, actual_class FROM scans WHERE id = 1").fetchone()
    minimum = check.execute("SELECT min_area FROM categories WHERE name = 'Large'").fetchone()[0]
    leftover = check.execute("SELECT COUNT(*) FROM settings WHERE key = 'k'").fetchone()[0]
    check.close()
    assert "area_mm2" in scan_columns
    assert "est_weight_g" not in scan_columns
    assert "actual_weight_g" not in scan_columns
    assert "min_weight" not in category_columns
    assert area == 2640
    assert actual == "Large"
    assert minimum == 2560
    assert leftover == 0


def _gone(*codes):
    return "".join(chr(code) for code in codes)


def test_old_rows_move_onto_the_five_classes(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    first = _gone(80, 101, 119, 101, 101)
    second = _gone(80, 117, 108, 108, 101, 116)
    third = _gone(69, 120, 116, 114, 97, 32, 76, 97, 114, 103, 101)
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE scans (id INTEGER PRIMARY KEY, created_at TEXT, image_path TEXT, length_mm FLOAT, width_mm FLOAT, area_mm2 FLOAT, predicted_class TEXT, actual_class TEXT, confidence FLOAT, inference_ms FLOAT, is_mock INTEGER)")
    connection.execute("INSERT INTO scans VALUES (1, '2026-01-01', 'a.jpg', 40, 28, 1120, ?, ?, 0.9, 20, 1)", (first, first))
    connection.execute("INSERT INTO scans VALUES (2, '2026-01-01', 'b.jpg', 54, 40, 2160, ?, ?, 0.9, 20, 1)", (second, second))
    connection.execute("INSERT INTO scans VALUES (3, '2026-01-01', 'c.jpg', 60, 44, 2640, ?, 'Large', 0.9, 20, 1)", (third,))
    connection.execute("CREATE TABLE categories (id INTEGER PRIMARY KEY, name TEXT, min_area FLOAT, color TEXT, sort_order INTEGER)")
    connection.executemany("INSERT INTO categories VALUES (?, ?, ?, ?, ?)", [
        (1, "Small", 2250, "#c4a35a", 2), (2, "Medium", 2420, "#d99a2b", 3),
        (3, "Large", 2560, "#e07a3d", 4), (4, "Jumbo", 2900, "#8f3d2c", 6),
        (5, first, 0, "#888888", 0), (6, second, 2000, "#777777", 1),
        (7, third, 2710, "#666666", 5),
    ])
    connection.commit()
    connection.close()

    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + path.as_posix())
    monkeypatch.setenv("EGGSCAN_DATA_DIR", str(tmp_path / "data"))
    from backend.db import init_db

    init_db()
    init_db()
    check = sqlite3.connect(path)
    names = [row[0] for row in check.execute("SELECT name FROM categories ORDER BY sort_order").fetchall()]
    extra_large_minimum = check.execute("SELECT min_area FROM categories WHERE name = 'Extra Large'").fetchone()[0]
    rows = check.execute("SELECT id, predicted_class, actual_class FROM scans ORDER BY id").fetchall()
    notes = [row[0] for row in check.execute("SELECT message FROM logs").fetchall()]
    check.close()
    assert names == ["Small", "Medium", "Large", "Extra Large", "Jumbo"]
    assert 2560 < extra_large_minimum < 2900
    assert rows[0] == (1, "Small", None)
    assert rows[1] == (2, "Small", None)
    assert rows[2] == (3, "Large", "Large")
    assert sum(1 for note in notes if note.startswith("Size classes are now")) == 1
