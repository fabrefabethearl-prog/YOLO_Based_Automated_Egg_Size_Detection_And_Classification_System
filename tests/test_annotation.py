"""Test annotation service."""

import pytest
import numpy as np


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("small.jpg", "Small"),
        ("egg_MEDIUM_01.png", "Medium"),
        ("egg-large-01.jpeg", "Large"),
        ("egg_extra_large_01.jpeg", "Extra Large"),
        ("JUMBO 5.webp", "Jumbo"),
        ("egg_small_large.jpg", None),
        ("egg_extra_large_large.jpg", None),
        ("smallish.png", None),
        ("egg.webp", None),
    ],
)
def test_filename_reference_label_requires_one_supported_token(filename, expected):
    from backend.services.annotation_labels import filename_reference_label

    assert filename_reference_label(filename) == expected


def test_annotation_service_with_mock_image():
    """Test annotation with a mock egg image."""
    from backend.services.annotation import annotate_uploaded_image
    
    # Create a simple test image with a light oval on dark background
    img = np.zeros((200, 200, 3), dtype=np.uint8)
    cv2 = __import__('cv2')
    
    # Draw a light oval (simulating an egg)
    cv2.ellipse(img, (100, 100), (60, 40), 0, 0, 360, (200, 200, 200), -1)
    
    # Test annotation
    result = annotate_uploaded_image(img, pixels_per_mm=None)
    
    # The result should have the expected structure
    assert hasattr(result, 'ok')
    assert hasattr(result, 'annotated_image')
    assert hasattr(result, 'original_image')
    assert hasattr(result, 'measurements')
    assert hasattr(result, 'unit')
    assert hasattr(result, 'definition')


def test_annotation_service_invalid_image():
    """Test annotation with invalid image."""
    from backend.services.annotation import annotate_uploaded_image
    
    # Test with None
    result = annotate_uploaded_image(None, pixels_per_mm=None)
    assert result.ok == False
    assert result.error is not None
    
    # Test with too small image
    img = np.zeros((10, 10, 3), dtype=np.uint8)
    result = annotate_uploaded_image(img, pixels_per_mm=None)
    assert result.ok == False
    assert result.error is not None


def test_annotation_format_response():
    """Test formatting annotation result as API response."""
    from backend.services.annotation import AnnotationResult, format_annotation_response
    
    result = AnnotationResult(
        ok=True,
        annotated_image="data:image/jpeg;base64,test",
        original_image="data:image/jpeg;base64,test",
        measurements={"length_px": 100, "width_px": 70},
        unit="px",
        pixels_per_mm=None,
        definition="Test definition",
        warnings=[],
        error=None,
    )
    
    response = format_annotation_response(result)
    assert response["ok"] == True
    assert response["annotated_image"] == "data:image/jpeg;base64,test"
    assert response["measurements"]["length_px"] == 100
    assert response["unit"] == "px"


if __name__ == "__main__":
    test_annotation_service_with_mock_image()
    test_annotation_service_invalid_image()
    test_annotation_format_response()
    print("All annotation tests passed!")
