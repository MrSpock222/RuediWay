"""Conservative document enhancement, with optional local black/white threshold."""
import io

from fastapi import HTTPException
from PIL import Image, ImageChops, ImageFilter, ImageOps, ImageStat, UnidentifiedImageError

MAX_BYTES = 10 * 1024 * 1024


def prepare_image(data, mode="normal", max_size=2592):
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "Bild darf maximal 10 MB groß sein.")
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.width * source.height > 16_000_000:
                raise HTTPException(413, "Bild darf maximal 16 Megapixel haben.")
            picture = ImageOps.exif_transpose(source).convert("RGB")
            picture.thumbnail((max_size, max_size))
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise HTTPException(400, "Ungültiges oder zu großes Bild. JPEG oder PNG verwenden.") from exc
    gray = ImageOps.grayscale(picture)
    histogram = gray.histogram()
    count = sum(histogram)
    variation = ImageStat.Stat(gray).stddev[0]
    warning = ""
    if sum(histogram[250:]) / count > 0.98:
        warning = "Fast nur Weiß: Belichtung reduzieren oder kürzere Belichtungszeit wählen."
    elif sum(histogram[:12]) / count > 0.98:
        warning = "Fast nur Schwarz: Licht, Objektiv und Belichtung prüfen."
    elif variation < 8:
        warning = "Wenig Bilddetails: Abstand, Schärfe und Beleuchtung prüfen."
    if mode != "normal" and variation < 8:
        # Do not stretch sensor noise across the entire black/white range.
        picture = gray
    elif mode != "normal":
        enhanced = ImageOps.autocontrast(gray, cutoff=0.5)
        enhanced = enhanced.filter(ImageFilter.UnsharpMask(radius=1.2, percent=130, threshold=3))
        if mode == "bw":
            # Local threshold preserves thin black lettering under uneven lighting.
            background = enhanced.filter(ImageFilter.GaussianBlur(radius=15))
            difference = ImageChops.subtract(enhanced, background, scale=1, offset=128)
            picture = difference.point(lambda value: 0 if value < 120 else 255)
        else:
            picture = enhanced
    output = io.BytesIO()
    picture.save(output, "JPEG", quality=92)
    return output.getvalue(), warning
