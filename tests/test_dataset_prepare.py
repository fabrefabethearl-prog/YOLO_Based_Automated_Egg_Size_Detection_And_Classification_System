import cv2
import numpy as np
import yaml


def egg_jpeg(mark: int) -> bytes:
    image = np.zeros((480, 640, 3), dtype=np.uint8)
    image[:] = (30, 46, 34)
    center_x = 200 + (mark % 8) * 28
    cv2.ellipse(image, (center_x, 240), (78, 108), 0, 0, 360, (236, 240, 244), -1)
    cv2.putText(image, str(mark), (16, 36), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (20, 180, 220), 2, cv2.LINE_AA)
    ok, buffer = cv2.imencode(".jpg", image)
    assert ok
    return buffer.tobytes()


def blank_jpeg(mark: int = 1) -> bytes:
    image = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.putText(image, f"n{mark}", (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (220, 220, 220), 1, cv2.LINE_AA)
    ok, buffer = cv2.imencode(".jpg", image)
    assert ok
    return buffer.tobytes()


LENGTHS = {"small": 40, "medium": 48, "large": 56, "extra large": 64, "jumbo": 72}
WIDTHS = {"small": 30, "medium": 36, "large": 42, "extra large": 48, "jumbo": 54}
SLUGS = {"small": "small", "medium": "medium", "large": "large", "extra large": "extra_large", "jumbo": "jumbo"}


def annotate_class(client, auth, key):
    slug = SLUGS[key]
    listed = client.get(f"/api/dataset/annotate/{slug}", headers=auth)
    assert listed.status_code == 200, listed.text
    length = LENGTHS[key]
    width = WIDTHS[key]
    for image in listed.json()["images"]:
        saved = client.put(
            f"/api/dataset/annotate/{slug}/{image['name']}/mark",
            json={
                "start_x": 20,
                "start_y": 40,
                "end_x": 20 + length * 4,
                "end_y": 40 + width * 4,
                "length_mm": length,
                "width_mm": width,
                "calibration": "manual",
            },
            headers=auth,
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["length_mm"] == length
        assert saved.json()["width_mm"] == width
        assert saved.json()["class"] == listed.json()["class"]


def upload_class(client, auth, key, folder, count=3, blob=None, start=1):
    files = []
    for index in range(count):
        mark = start + index
        payload = blob(mark) if blob else egg_jpeg(mark)
        files.append(("files", (f"{folder}/{folder}_{index:03d}.jpg", payload, "image/jpeg")))
    return client.post(
        "/api/dataset/class",
        files=files,
        data={"class_name": key, "folder_name": folder},
        headers=auth,
    )


def test_dataset_auto_annotation_review_and_geometry_persist(client, auth):
    uploaded = upload_class(client, auth, "small", "small", count=1)
    assert uploaded.status_code == 200, uploaded.text
    images = client.get("/api/dataset/annotate/small", headers=auth).json()["images"]
    assert len(images) == 1
    filename = images[0]["name"]

    detected = client.post(
        f"/api/dataset/annotate/small/{filename}/auto",
        headers=auth,
    )
    assert detected.status_code == 200, detected.text
    result = detected.json()
    assert result["dataset_class"] == "Small"
    assert result["reference_label"] == {
        "class_name": "Small",
        "source": "filename",
        "requires_selection": False,
    }
    assert result["model_prediction"] is None
    assert result["review_required"] is True
    assert result["unit"] == "px"
    assert result["eggs"]
    egg = result["eggs"][0]
    assert len(egg["contour"]) >= 3
    assert egg["ellipse"]["major_px"] > 0
    assert egg["length_mm"] == round(egg["length_px"] / result["pixels_per_mm"], 2)
    assert egg["width_mm"] == round(egg["width_px"] / result["pixels_per_mm"], 2)

    bbox = egg["bbox"]
    geometry = {
        **egg["ellipse"],
        "ellipse": egg["ellipse"],
        "bbox": bbox,
        "area_px": egg["area_px"],
        "perimeter_px": egg["perimeter_px"],
        "contour": egg["contour"],
        "cross_sections": egg["cross_sections"],
    }
    saved = client.put(
        f"/api/dataset/annotate/small/{filename}/mark",
        headers=auth,
        json={
            "ovals": [{
                "start_x": bbox["x"],
                "start_y": bbox["y"],
                "end_x": bbox["x"] + bbox["width"],
                "end_y": bbox["y"] + bbox["height"],
                "length_mm": 40,
                "width_mm": 30,
                "calibration": "manual",
                "geometry": geometry,
            }],
            "reference_class": "Small",
            "reference_label_source": "filename",
            "reviewed": True,
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["reference_label"]["class_name"] == "Small"
    assert saved.json()["reviewed"] is True
    assert saved.json()["ovals"][0]["geometry"]["contour"] == egg["contour"]
    loaded = client.get(
        f"/api/dataset/annotate/small/{filename}/mark",
        headers=auth,
    )
    assert loaded.status_code == 200
    assert loaded.json()["reviewed"] is True
    assert loaded.json()["ovals"][0]["geometry"]["ellipse"]["major_px"] == egg["ellipse"]["major_px"]


def test_dataset_auto_annotation_requires_manual_reference_label(client, auth):
    uploaded = upload_class(client, auth, "small", "small", count=1)
    assert uploaded.status_code == 200, uploaded.text
    from backend.services.dataset_prep import _incoming_dir

    original_name = client.get("/api/dataset/annotate/small", headers=auth).json()["images"][0]["name"]
    original = _incoming_dir("small") / original_name
    unknown_name = _incoming_dir("small") / "egg.jpg"
    original.rename(unknown_name)

    detected = client.post("/api/dataset/annotate/small/egg.jpg/auto", headers=auth)
    assert detected.status_code == 200, detected.text
    assert detected.json()["reference_label"]["requires_selection"] is True
    assert detected.json()["reference_label"]["class_name"] is None

    saved = client.put(
        "/api/dataset/annotate/small/egg.jpg/mark",
        headers=auth,
        json={
            "start_x": 120,
            "start_y": 100,
            "end_x": 280,
            "end_y": 320,
            "length_mm": 40,
            "width_mm": 30,
            "calibration": "manual",
            "reference_class": "Jumbo",
            "reference_label_source": "manual",
            "reviewed": True,
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["class"] == "Small"
    assert saved.json()["reference_label"] == {
        "class_name": "Jumbo",
        "source": "manual",
    }
    assert saved.json()["reviewed"] is True


def test_extra_large_is_available_as_annotation_reference_label(client, auth):
    uploaded = upload_class(client, auth, "extra large", "extra_large", count=1)
    assert uploaded.status_code == 200, uploaded.text
    name = client.get("/api/dataset/annotate/extra_large", headers=auth).json()["images"][0]["name"]
    saved = client.put(
        f"/api/dataset/annotate/extra_large/{name}/mark",
        json={
            "ovals": [{"start_x": 80, "start_y": 60, "end_x": 280, "end_y": 340}],
            "reference_class": "Extra Large",
            "reference_label_source": "manual",
            "reviewed": True,
        },
        headers=auth,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["class"] == "Extra Large"
    assert saved.json()["reference_label"]["class_name"] == "Extra Large"


def test_five_folders_prepare_detection_labels(client, auth, tmp_path):
    mark = 1
    for key, folder in (
        ("small", "small"),
        ("medium", "MEDIUM"),
        ("large", "large"),
        ("extra large", "extra_large"),
        ("jumbo", "Jumbo"),
    ):
        response = upload_class(client, auth, key, folder, start=mark)
        assert response.status_code == 200, response.text
        assert response.json()["class_valid"] is True
        annotate_class(client, auth, key)
        mark += 10
    from backend.services.dataset_prep import _incoming_dir

    small_name = client.get("/api/dataset/annotate/small", headers=auth).json()["images"][0]["name"]
    original_small = (_incoming_dir("small") / small_name).read_bytes()
    large_measurement_saved = client.put(
        f"/api/dataset/annotate/small/{small_name}/mark",
        json={
            "ovals": [{"start_x": 80, "start_y": 60, "end_x": 280, "end_y": 340, "length_mm": 130.2, "width_mm": 40, "calibration": "manual"}],
            "reference_class": "Small",
            "reference_label_source": "manual",
            "reviewed": True,
        },
        headers=auth,
    )
    assert large_measurement_saved.status_code == 200, large_measurement_saved.text
    assert large_measurement_saved.json()["class"] == "Small"
    assert large_measurement_saved.json()["ovals"][0]["length_mm"] == 130.2
    incoming = client.get("/api/dataset/incoming", headers=auth).json()
    assert incoming["valid"] is True
    assert incoming["classes"] == {
        "small": 3,
        "medium": 3,
        "large": 3,
        "extra large": 3,
        "jumbo": 3,
    }
    assert incoming["total_images"] == 15

    prepared = client.post("/api/dataset/prepare", json={"seed": 42}, headers=auth)
    assert prepared.status_code == 200, prepared.text
    body = prepared.json()
    assert body["ready"] is True
    assert body["task"] == "YOLO Detection"
    assert body["status"] == "Ready for training."

    root = tmp_path / "data" / "dataset"
    listed = yaml.safe_load((root / "data.yaml").read_text(encoding="utf-8"))
    assert [listed["names"][index] for index in range(5)] == ["Small", "Medium", "Large", "Extra Large", "Jumbo"]
    info = (root / "dataset_info.json").read_text(encoding="utf-8")
    assert '"0": "small"' in info
    assert '"4": "jumbo"' in info
    small = next((root / "labels" / "train").glob("small_*.txt"))
    parts = small.read_text(encoding="utf-8").split()
    assert parts[0] == "0"
    assert len(parts) == 5
    assert all(0 < float(value) <= 1 for value in parts[1:])
    matching_images = [path for path in (root / "images").rglob("*") if path.is_file() and path.read_bytes() == original_small]
    assert len(matching_images) == 1
    matching_image = matching_images[0]
    matching_label = root / "labels" / matching_image.parent.name / f"{matching_image.stem}.txt"
    assert matching_label.is_file()
    assert matching_label.read_text(encoding="utf-8").split()[0] == "0"
    jumbo = next((root / "labels").rglob("jumbo_*.txt"))
    assert jumbo.read_text(encoding="utf-8").split()[0] == "4"
    summary = client.get("/api/dataset", headers=auth).json()
    assert summary["labeled_images"] == 15
    assert summary["unlabeled_images"] == 0
    lengths = (root / "lengths.csv").read_text(encoding="utf-8")
    assert lengths.startswith("image,class,length_mm,width_mm,split\n")
    assert "Small,40.0,30.0" in lengths
    assert "Jumbo,72.0,54.0" in lengths


def test_size_order_failure_reports_class_stats_without_replacing_prepared_labels(client, auth, tmp_path):
    classes = (
        ("small", "small"),
        ("medium", "medium"),
        ("large", "large"),
        ("extra large", "extra_large"),
        ("jumbo", "jumbo"),
    )
    for index, (key, folder) in enumerate(classes):
        uploaded = upload_class(client, auth, key, folder, count=3, start=index * 10 + 1)
        assert uploaded.status_code == 200, uploaded.text
        annotate_class(client, auth, key)

    for key, length, width in (("extra_large", 72, 54), ("jumbo", 64, 48)):
        listed = client.get(f"/api/dataset/annotate/{key}", headers=auth).json()
        for image in listed["images"]:
            saved = client.put(
                f"/api/dataset/annotate/{key}/{image['name']}/mark",
                json={
                    "start_x": 20,
                    "start_y": 30,
                    "end_x": 20 + length * 2,
                    "end_y": 30 + width * 2,
                    "length_mm": length,
                    "width_mm": width,
                    "calibration": "manual",
                },
                headers=auth,
            )
            assert saved.status_code == 200, saved.text

    root = tmp_path / "data" / "dataset"
    old_label = root / "labels" / "train" / "keep.txt"
    old_image = root / "images" / "train" / "keep.jpg"
    old_info = root / "dataset_info.json"
    old_label.write_text("existing-label", encoding="utf-8")
    old_image.write_bytes(b"existing-image")
    old_info.write_text('{"ready":true,"preserved":true}', encoding="utf-8")

    response = client.post("/api/dataset/prepare", json={}, headers=auth)
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "Extra Large median area 3888.00 mm^2" in detail
    assert "Jumbo median area 3072.00 mm^2" in detail
    assert "inversion is 816.00 mm^2" in detail
    assert "ID 3, folder extra_large" in detail
    assert "ID 4, folder jumbo" in detail
    assert "extra_large_0001.jpg" in detail
    assert "jumbo_0001.jpg" in detail
    assert "Extra Large/extra_large_0001.jpg egg 1: length 72.00 mm, width 54.00 mm, area 3888.00 mm^2" in detail
    assert "Jumbo/jumbo_0001.jpg egg 1: length 64.00 mm, width 48.00 mm, area 3072.00 mm^2" in detail
    assert old_label.read_text(encoding="utf-8") == "existing-label"
    assert old_image.read_bytes() == b"existing-image"
    assert old_info.read_text(encoding="utf-8") == '{"ready":true,"preserved":true}'
    for key, length, width in (("extra_large", 72, 54), ("jumbo", 64, 48)):
        listed = client.get(f"/api/dataset/annotate/{key}", headers=auth).json()
        saved = client.get(f"/api/dataset/annotate/{key}/{listed['images'][0]['name']}/mark", headers=auth)
        assert saved.status_code == 200
        assert saved.json()["length_mm"] == length
        assert saved.json()["width_mm"] == width


def test_missing_class_blocks_preparation(client, auth):
    assert upload_class(client, auth, "small", "small").status_code == 200
    response = client.post("/api/dataset/prepare", json={}, headers=auth)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ready"] is False
    assert any("Extra Large" in item for item in body["errors"])
    refused = client.post("/api/dataset/train", headers=auth)
    assert refused.status_code == 400


def test_folder_name_must_match_the_row(client, auth):
    response = upload_class(client, auth, "small", "photos")
    assert response.status_code == 400
    assert "photos" in response.json()["detail"]


def test_corrupt_and_blank_images_are_rejected(client, auth):
    files = [
        ("files", ("small/ok.jpg", egg_jpeg(1), "image/jpeg")),
        ("files", ("small/bad.jpg", b"not-a-jpeg", "image/jpeg")),
    ]
    corrupt = client.post(
        "/api/dataset/class",
        files=files,
        data={"class_name": "small", "folder_name": "small"},
        headers=auth,
    )
    assert corrupt.status_code == 200
    assert corrupt.json()["class_valid"] is False
    assert any("corrupted" in item for item in corrupt.json()["errors"])

    blank = upload_class(client, auth, "small", "small", blob=blank_jpeg)
    assert blank.json()["class_valid"] is True
    for key, folder in (
        ("medium", "medium"),
        ("large", "large"),
        ("extra large", "extra-large"),
        ("jumbo", "jumbo"),
    ):
        assert upload_class(client, auth, key, folder, start=30).json()["class_valid"] is True
    prepared = client.post("/api/dataset/prepare", json={}, headers=auth)
    assert prepared.json()["ready"] is False
    assert any("missing oval annotation" in item for item in prepared.json()["errors"])


def test_training_runs_the_real_script_entrypoint(client, auth, monkeypatch, tmp_path):
    from backend.services import dataset_train

    dataset_train.reset_training_state()
    mark = 1
    for key in ("small", "medium", "large", "extra large", "jumbo"):
        folder = "extra_large" if key == "extra large" else key
        assert upload_class(client, auth, key, folder, start=mark).json()["class_valid"] is True
        annotate_class(client, auth, key)
        mark += 10
    assert client.post("/api/dataset/prepare", json={"seed": 7}, headers=auth).json()["ready"] is True

    popen_kwargs = {}

    class FakeProcess:
        def __init__(self, *_args, **_kwargs):
            popen_kwargs.update(_kwargs)
            self.stdout = iter(["Epoch 1/100\n", "Epoch 2/100\n"])

        def wait(self):
            return 0

    monkeypatch.setattr(dataset_train.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(dataset_train, "size_path", lambda: tmp_path / "size" / "best.pt")
    loaded = {"ok": False}
    detector = __import__("backend.services.detector", fromlist=["detector"]).detector
    monkeypatch.setattr(detector, "mock", False)
    monkeypatch.setattr(detector, "model", object())
    monkeypatch.setattr(detector, "load", lambda: loaded.__setitem__("ok", True))
    monkeypatch.setattr(detector, "is_current", lambda: True)
    started = client.post("/api/dataset/train", headers=auth)
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "running"
    finished = dataset_train.wait_for_training(5)
    assert finished["status"] == "done"
    assert finished["percent"] == 100
    assert loaded["ok"] is True
    assert finished["model"]["mapping"]["0"] == "egg"
    assert finished["model"]["size_classes"] == ["Small", "Medium", "Large", "Extra Large", "Jumbo"]
    assert popen_kwargs["encoding"] == "utf-8"
    assert popen_kwargs["errors"] == "replace"
    dataset_train.reset_training_state()


def test_annotation_rejects_bad_length_and_blocks_prepare(client, auth):
    assert upload_class(client, auth, "small", "small").status_code == 200
    listed = client.get("/api/dataset/annotate/small", headers=auth).json()
    name = listed["images"][0]["name"]
    rejected = client.put(
        f"/api/dataset/annotate/small/{name}/mark",
        json={"start_x": 10, "start_y": 10, "end_x": 80, "end_y": 70, "length_mm": -5, "width_mm": 30, "calibration": "manual"},
        headers=auth,
    )
    assert rejected.status_code == 422
    outside = client.put(
        f"/api/dataset/annotate/medium/{name}/mark",
        json={"start_x": 10, "start_y": 10, "end_x": 80, "end_y": 70, "length_mm": 40, "width_mm": 30, "calibration": "manual"},
        headers=auth,
    )
    assert outside.status_code == 404
    annotate_class(client, auth, "small")
    again = client.get(f"/api/dataset/annotate/small/{name}/mark", headers=auth)
    assert again.status_code == 200
    assert again.json()["measurement"]["pixel_length"] > 0
    removed = client.delete(f"/api/dataset/annotate/small/{name}/mark", headers=auth)
    assert removed.status_code == 200
    missing = client.get(f"/api/dataset/annotate/small/{name}/mark", headers=auth)
    assert missing.status_code == 404
    prepared = client.post("/api/dataset/prepare", json={}, headers=auth).json()
    assert prepared["ready"] is False
    assert any("missing oval annotation" in item or "Missing class" in item for item in prepared["errors"])


def test_delete_class_images_removes_only_that_folder(client, auth):
    assert upload_class(client, auth, "small", "small").status_code == 200
    assert upload_class(client, auth, "medium", "medium", start=20).status_code == 200
    annotate_class(client, auth, "small")
    removed = client.delete("/api/dataset/class/small", headers=auth)
    assert removed.status_code == 200, removed.text
    body = removed.json()
    assert body["deleted"] == 3
    assert body["classes"]["small"] == 0
    assert body["classes"]["medium"] == 3
    assert body["annotated"]["small"] == 0
    listed = client.get("/api/dataset/annotate/small", headers=auth)
    assert listed.json()["total"] == 0
    kept = client.get("/api/dataset/annotate/medium", headers=auth)
    assert kept.json()["total"] == 3
    unknown = client.delete("/api/dataset/class/peewee", headers=auth)
    assert unknown.status_code == 400


def test_one_image_keeps_several_ovals(client, auth, tmp_path):
    mark = 1
    for key, folder in (
        ("small", "small"),
        ("medium", "medium"),
        ("large", "large"),
        ("extra large", "extra_large"),
        ("jumbo", "jumbo"),
    ):
        assert upload_class(client, auth, key, folder, start=mark).status_code == 200
        annotate_class(client, auth, key)
        mark += 10
    listed = client.get("/api/dataset/annotate/small", headers=auth).json()
    name = listed["images"][0]["name"]
    saved = client.put(
        f"/api/dataset/annotate/small/{name}/mark",
        json={
            "ovals": [
                {"start_x": 20, "start_y": 30, "end_x": 180, "end_y": 150, "length_mm": 40, "width_mm": 30, "calibration": "manual"},
                {"start_x": 220, "start_y": 40, "end_x": 420, "end_y": 200, "length_mm": 41, "width_mm": 31, "calibration": "manual"},
            ]
        },
        headers=auth,
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["count"] == 2
    assert len(body["ovals"]) == 2
    assert body["ovals"][1]["length_mm"] == 41
    again = client.get(f"/api/dataset/annotate/small/{name}/mark", headers=auth)
    assert again.json()["count"] == 2
    prepared = client.post("/api/dataset/prepare", json={"seed": 3}, headers=auth)
    assert prepared.status_code == 200, prepared.text
    assert prepared.json()["ready"] is True, prepared.text
    root = tmp_path / "data" / "dataset"
    counts = []
    for path in (root / "labels").rglob("small_*.txt"):
        counts.append(len([line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]))
    assert sorted(counts) == [1, 1, 2]
    replaced = client.put(
        f"/api/dataset/annotate/small/{name}/mark",
        json={"start_x": 20, "start_y": 40, "end_x": 180, "end_y": 160, "length_mm": 40, "width_mm": 30, "calibration": "manual"},
        headers=auth,
    )
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["count"] == 1


def test_length_classifier_uses_measured_millimetres(client, auth):
    mark = 1
    for key, folder in (
        ("small", "small"),
        ("medium", "medium"),
        ("large", "large"),
        ("extra large", "extra_large"),
        ("jumbo", "jumbo"),
    ):
        assert upload_class(client, auth, key, folder, start=mark).status_code == 200
        annotate_class(client, auth, key)
        mark += 10
    prepared = client.post("/api/dataset/prepare", json={"seed": 1}, headers=auth)
    assert prepared.status_code == 200, prepared.text
    assert prepared.json()["ready"] is True, prepared.text
    from backend.services.length_model import class_for_measured_size

    assert class_for_measured_size(40, 30) == "Small"
    assert class_for_measured_size(48, 36) == "Medium"
    assert class_for_measured_size(56, 42) == "Large"
    assert class_for_measured_size(64, 48) == "Extra Large"
    assert class_for_measured_size(72, 54) == "Jumbo"
    assert class_for_measured_size(None, 30) is None


def test_pixel_annotation_saves_without_calibration_and_retains_small_class(client, auth):
    uploaded = upload_class(client, auth, "small", "small", count=1)
    assert uploaded.status_code == 200, uploaded.text
    listed = client.get("/api/dataset/annotate/small", headers=auth).json()
    name = listed["images"][0]["name"]
    from backend.services.dataset_prep import _incoming_dir

    original_path = _incoming_dir("small") / name
    original_bytes = original_path.read_bytes()
    mark_url = f"/api/dataset/annotate/small/{name}/mark"
    pixel_annotation = {
        "ovals": [{
            "start_x": 80,
            "start_y": 60,
            "end_x": 280,
            "end_y": 340,
            "length_mm": None,
            "width_mm": None,
            "calibration": "pixels",
        }],
        "reference_class": "Small",
        "reference_label_source": "manual",
        "reviewed": True,
    }

    saved = client.put(mark_url, json=pixel_annotation, headers=auth)
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["class"] == "Small"
    assert body["ovals"][0]["length_mm"] is None
    assert body["ovals"][0]["width_mm"] is None
    assert body["ovals"][0]["calibration"] == "pixels"
    assert original_path.read_bytes() == original_bytes
    assert client.get(mark_url, headers=auth).json()["class"] == "Small"

    unreliable_calibration = {
        **pixel_annotation,
        "ovals": [{**pixel_annotation["ovals"][0], "length_mm": 40, "width_mm": 30, "calibration": "unreliable"}],
    }
    saved_without_unreliable_measurement = client.put(mark_url, json=unreliable_calibration, headers=auth)
    assert saved_without_unreliable_measurement.status_code == 200, saved_without_unreliable_measurement.text
    unreliable_result = saved_without_unreliable_measurement.json()["ovals"][0]
    assert unreliable_result["length_mm"] is None
    assert unreliable_result["width_mm"] is None
    assert unreliable_result["calibration"] == "pixels"

    bad_coordinates = {**pixel_annotation, "ovals": [{**pixel_annotation["ovals"][0], "end_x": 900}]}
    invalid = client.put(mark_url, json=bad_coordinates, headers=auth)
    assert invalid.status_code == 400
    assert "outside the image" in invalid.json()["detail"]
    assert client.get(mark_url, headers=auth).json()["ovals"][0]["length_mm"] is None

    over_limit = {
        **pixel_annotation,
        "ovals": [{**pixel_annotation["ovals"][0], "length_mm": 130.2, "width_mm": 40, "calibration": "manual"}],
    }
    saved_measurement = client.put(mark_url, json=over_limit, headers=auth)
    assert saved_measurement.status_code == 200, saved_measurement.text
    assert saved_measurement.json()["ovals"][0]["length_mm"] == 130.2
    assert saved_measurement.json()["ovals"][0]["width_mm"] == 40
    persisted_measurement = client.get(mark_url, headers=auth).json()["ovals"][0]
    assert persisted_measurement["length_mm"] == 130.2
    assert persisted_measurement["width_mm"] == 40

    over_limit_scale = {
        **pixel_annotation,
        "ovals": [{**pixel_annotation["ovals"][0], "length_mm": 130.23, "width_mm": 93.02, "pixels_per_mm": 2.15, "calibration": "scale"}],
    }
    saved_scale_measurement = client.put(mark_url, json=over_limit_scale, headers=auth)
    assert saved_scale_measurement.status_code == 200, saved_scale_measurement.text
    assert saved_scale_measurement.json()["ovals"][0]["length_mm"] == 130.23
    assert saved_scale_measurement.json()["ovals"][0]["width_mm"] == 93.02
    assert saved_scale_measurement.json()["ovals"][0]["calibration"] == "scale"

    invalid_numbers = (0, -1, "not-a-number", "Infinity")
    for invalid_length in invalid_numbers:
        invalid_measurement = {
            **pixel_annotation,
            "ovals": [{**pixel_annotation["ovals"][0], "length_mm": invalid_length, "width_mm": 40, "calibration": "manual"}],
        }
        rejected_invalid = client.put(mark_url, json=invalid_measurement, headers=auth)
        assert rejected_invalid.status_code == 422, rejected_invalid.text
    assert client.get(mark_url, headers=auth).json()["ovals"][0]["length_mm"] == 130.23

    bad_label = {**pixel_annotation, "reference_class": "Unmapped"}
    rejected_label = client.put(mark_url, json=bad_label, headers=auth)
    assert rejected_label.status_code == 400
    assert "Choose Small, Medium, Large, Extra Large, or Jumbo" in rejected_label.json()["detail"]
    assert client.get(mark_url, headers=auth).json()["class"] == "Small"

    failed_save = client.put(
        f"/api/dataset/annotate/small/missing.jpg/mark",
        json=pixel_annotation,
        headers=auth,
    )
    assert failed_save.status_code == 404
    assert "not found" in failed_save.json()["detail"]
    assert client.get(mark_url, headers=auth).json()["class"] == "Small"
