from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.contrib.staticfiles.storage import StaticFilesStorage
from django.utils.deconstruct import deconstructible


class PublicStaticFilesStorage(StaticFilesStorage):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault('file_permissions_mode', 0o644)
        kwargs.setdefault('directory_permissions_mode', 0o755)
        super().__init__(*args, **kwargs)


@deconstructible
class PrivateMediaStorage(FileSystemStorage):
    def __init__(self):
        super().__init__(
            location=settings.PRIVATE_MEDIA_ROOT,
            base_url='/private-media/',
        )


private_media_storage = PrivateMediaStorage()
