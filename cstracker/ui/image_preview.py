"""Pillow-to-Tk image presentation kept separate from network loading."""

from __future__ import annotations

import io

from PIL import Image, ImageTk


def render_preview_image(label, image_bytes, *, refs, ref_key, size, background, placeholder):
    if label is None:
        return
    if not image_bytes:
        label.configure(image="", text=placeholder)
        refs.pop(ref_key, None)
        return
    try:
        image = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
        image.thumbnail(size, Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", size, background)
        x = (size[0] - image.width) // 2
        y = (size[1] - image.height) // 2
        canvas.alpha_composite(image, (x, y))
        photo = ImageTk.PhotoImage(canvas)
        refs[ref_key] = photo
        label.configure(image=photo, text="")
    except (OSError, ValueError, TypeError):
        label.configure(image="", text=placeholder)
        refs.pop(ref_key, None)
