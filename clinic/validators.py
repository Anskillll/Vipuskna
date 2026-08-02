from pathlib import Path
import re
from xml.etree import ElementTree

from django.core.exceptions import ValidationError
from PIL import Image, UnidentifiedImageError


MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_LOGO_BYTES = 2 * 1024 * 1024
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
ALLOWED_LOGO_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.svg'}
UKRAINIAN_PHONE_DIGIT_COUNT = 12
MAX_SERVICE_PRICE_UAH = 1_000_000
MAX_APPOINTMENT_DURATION_MINUTES = 24 * 60
MAX_SLOT_MINUTES = 8 * 60
MAX_REASON_LENGTH = 2_000
MAX_DOCTOR_DESCRIPTION_LENGTH = 300
MAX_SERVICE_DESCRIPTION_LENGTH = 5_000
MAX_PATIENT_NOTES_LENGTH = 5_000
MAX_MEDICAL_TEXT_LENGTH = 5_000
MAX_NEWS_TEXT_LENGTH = 10_000
BLOCKED_SVG_ELEMENTS = {
    'audio',
    'embed',
    'foreignobject',
    'iframe',
    'object',
    'script',
    'style',
    'video',
}


def phone_digits(value):
    text = str(value or '').strip()
    if not text or not re.fullmatch(r'\+?[\d\s().-]+', text):
        return ''
    digits = ''.join(character for character in text if character.isdigit())
    if len(digits) == 10 and digits.startswith('0'):
        digits = f'38{digits}'
    return digits


def validate_ukrainian_phone(value):
    digits = phone_digits(value)
    if (
        len(digits) != UKRAINIAN_PHONE_DIGIT_COUNT
        or not digits.startswith('380')
    ):
        raise ValidationError(
            'Введіть український номер із 10 цифр, наприклад 0501234567 або +380501234567.'
        )


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


def validate_logo_upload(value):
    extension = Path(value.name).suffix.casefold()
    if extension not in ALLOWED_LOGO_EXTENSIONS:
        raise ValidationError('Дозволені формати логотипа: PNG, JPG, WEBP, GIF або SVG.')

    if value.size > MAX_LOGO_BYTES:
        raise ValidationError(
            f'Логотип завеликий. Максимальний розмір — {format_megabytes(MAX_LOGO_BYTES)} МБ.'
        )

    if extension != '.svg':
        validate_image_upload(value)
        return

    content_type = getattr(value, 'content_type', '')
    if content_type and content_type.casefold() not in {
        'image/svg+xml',
        'text/xml',
        'application/xml',
    }:
        raise ValidationError('Файл не розпізнано як SVG-логотип.')

    file_object = getattr(value, 'file', value)
    original_position = None
    try:
        if hasattr(file_object, 'tell'):
            original_position = file_object.tell()
        content = file_object.read(MAX_LOGO_BYTES + 1)
    finally:
        if hasattr(file_object, 'seek'):
            file_object.seek(original_position or 0)

    lowered_content = content.lower()
    if b'<!doctype' in lowered_content or b'<!entity' in lowered_content:
        raise ValidationError('SVG-логотип містить заборонені XML-конструкції.')

    try:
        root = ElementTree.fromstring(content)
    except (ElementTree.ParseError, ValueError):
        raise ValidationError('Не вдалося прочитати SVG-логотип.')

    if root.tag.rsplit('}', 1)[-1].casefold() != 'svg':
        raise ValidationError('Файл не містить коректний SVG-логотип.')

    for element in root.iter():
        element_name = element.tag.rsplit('}', 1)[-1].casefold()
        if element_name in BLOCKED_SVG_ELEMENTS:
            raise ValidationError('SVG-логотип містить небезпечні або непідтримувані елементи.')

        for attribute_name, attribute_value in element.attrib.items():
            local_name = attribute_name.rsplit('}', 1)[-1].casefold()
            normalized_value = attribute_value.strip().casefold()
            if local_name.startswith('on'):
                raise ValidationError('SVG-логотип містить заборонені обробники подій.')
            if local_name in {'href', 'src'} and normalized_value and not normalized_value.startswith('#'):
                raise ValidationError('SVG-логотип не може містити зовнішні посилання.')
            if local_name == 'style':
                safe_style = re.sub(r'url\(\s*#[^)]+\)', '', normalized_value)
                if any(token in safe_style for token in ('url(', '@import', 'expression(')):
                    raise ValidationError('SVG-логотип містить небезпечні стилі.')


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
