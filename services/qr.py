"""services/qr.py — render QR codes in memory. Nothing is written to disk."""

import io

import qrcode


def png_bytes(data: str) -> bytes:
    qr = qrcode.QRCode(version=None, box_size=8, border=4)
    qr.add_data(data)
    qr.make(fit=True)
    buf = io.BytesIO()
    qr.make_image(fill_color="black", back_color="white").save(buf, format="PNG")
    return buf.getvalue()
