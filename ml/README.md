# Collecting and training the egg detector

EggScan trains one egg detector from every individually annotated box. The five source labels (Small, Medium, Large, Extra Large, Jumbo) are retained to fit the per-egg calibrated size bands. Each live detection is measured and assigned one size class from those dataset-derived bands.

## 1. Collect and label images

- Keep camera height, egg position, background, and lighting consistent when possible.
- Photograph different physical eggs and angles so the detector does not memorize one sample.
- Upload images to their verified size class and annotate every visible egg separately.
- Correct each box and verify the physical size label and length/width measurements before saving.

## 2. Class labels

| Source size class | ID |
| --- | --- |
| Small | 0 |
| Medium | 1 |
| Large | 2 |
| Extra Large | 3 |
| Jumbo | 4 |

A label line contains `class x y w h`; the four box values range from 0 to 1. Example:

```text
1 0.512340 0.498100 0.240000 0.410000
```

The detector learns to find eggs. Dataset labels and measurements fit the separate per-egg size bands, which classify each egg after detection.

## 3. Split data

Keep images and labels in matching train, validation, and test folders. Do not put photos of the same physical egg in more than one split. Add more distinct eggs in each class to improve classification across backgrounds and viewpoints.

## 4. Train and evaluate

From the project root, with the virtual environment active:

```text
python ml/train.py
python ml/evaluate.py
```

Training uses `yolov8n.pt`, image size 640, up to 100 epochs, and patience 20. It reports detector mAP and writes `size/best.pt`. The evaluator uses the test split when populated, otherwise the held-out validation split. Restart EggScan after replacing the checkpoint:

```text
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```
