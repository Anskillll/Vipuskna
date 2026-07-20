from pathlib import Path

from django.core.exceptions import ValidationError
from PIL import Image, UnidentifiedImageError


MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_COUNT = 6
MAX_IMAGE_PIXELS = 40_000_000
MAX_VIDEO_BYTES = 50 * 1024 * 1024
MAX_VIDEO_COUNT = 2
ALLOWED_VIDEO_EXTENSIONS = {'.mp4', '.webm', '.mov'}
ALLOWED_VIDEO_CONTENT_TYPES = {
    'video/mp4',
    'video/quicktime',
    'video/webm',
}


def format_megabytes(size):
    return size // (1024 * 1024)


def validate_image_upload(value):
    if value.size > MAX_IMAGE_BYTES:
        raise ValidationError(
            f'Фотографія завелика. Максимальний розмір — {format_megabytes(MAX_IMAGE_BYTES)} МБ.'
        )

    file_object = getattr(value, 'file', value)
    original_position = None
    try:
        if hasattr(file_object, 'tell'):
            original_position = file_object.tell()
        image = Image.open(file_object)
        width, height = image.size
        image.verify()
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError, ValueError):
        raise ValidationError('Не вдалося прочитати фотографію. Оберіть справний файл зображення.')
    finally:
        if hasattr(file_object, 'seek'):
            file_object.seek(original_position or 0)

    if width * height > MAX_IMAGE_PIXELS:
        raise ValidationError('Роздільна здатність фотографії завелика. Максимум — 40 мегапікселів.')


def validate_video_upload(value):
    if value.size > MAX_VIDEO_BYTES:
        raise ValidationError(
            f'Відео завелике. Максимальний розмір — {format_megabytes(MAX_VIDEO_BYTES)} МБ.'
        )

    extension = Path(value.name).suffix.casefold()
    if extension not in ALLOWED_VIDEO_EXTENSIONS:
        raise ValidationError('Дозволені формати відео: MP4, WEBM або MOV.')

    content_type = getattr(value, 'content_type', '')
    if content_type and content_type.casefold() not in ALLOWED_VIDEO_CONTENT_TYPES:
        raise ValidationError('Файл не розпізнано як підтримуване відео.')

    file_object = getattr(value, 'file', value)
    original_position = None
    try:
        if hasattr(file_object, 'tell'):
            original_position = file_object.tell()
        header = file_object.read(16)
    finally:
        if hasattr(file_object, 'seek'):
            file_object.seek(original_position or 0)

    atom_type = header[4:8] if len(header) >= 8 else b''
    is_iso_video = atom_type == b'ftyp'
    is_quicktime_video = extension == '.mov' and atom_type in {
        b'moov',
        b'mdat',
        b'wide',
        b'free',
    }
    is_webm = header.startswith(b'\x1a\x45\xdf\xa3')
    if not (is_iso_video or is_quicktime_video or is_webm):
        raise ValidationError('Файл не містить підтримуваного відео MP4, WEBM або MOV.')
