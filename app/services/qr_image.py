"""QR code images. Signing/verification of admission payloads lives in the ticketing service —
this module only draws whatever string it is given."""

import io
from typing import Any

import qrcode
from qrcode.constants import ERROR_CORRECT_Q


def make_qr(data: str, box_size: int = 10, border: int = 4) -> Any:
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_Q, box_size=box_size, border=border)
    qr.add_data(data)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white")


def make_png(data: str, box_size: int = 10, border: int = 4) -> bytes:
    """PNG bytes; error correction Q (~25%) survives smudged prints and phone glare."""
    buf = io.BytesIO()
    make_qr(data, box_size, border).save(buf, format="PNG")
    return buf.getvalue()
