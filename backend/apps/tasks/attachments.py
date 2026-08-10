from django.core import signing

SIGNING_SALT = "tasks.attachment.download"
DOWNLOAD_MAX_AGE_SECONDS = 5 * 60


def detected_media_type(file_object) -> tuple[str, str] | None:
    """Return a safe attachment kind/MIME from file magic, never user metadata."""

    position = file_object.tell()
    try:
        header = file_object.read(32)
    finally:
        file_object.seek(position)

    if header.startswith(b"\xff\xd8\xff"):
        return "photo", "image/jpeg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "photo", "image/png"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "photo", "image/webp"
    if header[4:8] == b"ftyp" and header[8:12] in {
        b"heic",
        b"heix",
        b"hevc",
        b"hevx",
        b"heim",
        b"heis",
        b"mif1",
        b"msf1",
    }:
        return "photo", "image/heic"

    if header.startswith(b"OggS"):
        return "voice", "audio/ogg"
    if header.startswith(b"\x1aE\xdf\xa3"):
        return "voice", "audio/webm"
    if header.startswith(b"RIFF") and header[8:12] == b"WAVE":
        return "voice", "audio/wav"
    if header.startswith(b"ID3") or (
        len(header) >= 2 and header[0] == 0xFF and header[1] & 0xE0 == 0xE0
    ):
        return "voice", "audio/mpeg"
    if header[4:8] == b"ftyp":
        return "voice", "audio/mp4"
    return None


def signed_attachment_token(attachment_id: int) -> str:
    return signing.dumps(
        {"attachment_id": attachment_id},
        salt=SIGNING_SALT,
        compress=True,
    )


def attachment_id_from_token(token: str) -> int:
    payload = signing.loads(
        token,
        salt=SIGNING_SALT,
        max_age=DOWNLOAD_MAX_AGE_SECONDS,
    )
    attachment_id = payload.get("attachment_id")
    if type(attachment_id) is not int or attachment_id <= 0:
        raise signing.BadSignature("Invalid attachment id")
    return attachment_id
