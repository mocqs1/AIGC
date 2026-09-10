"""Unit tests for the shared still-image compressor."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from providers.image.compressor import (
    ImageTooLargeError,
    PreparedImage,
    prepare_image_bytes,
    prepare_local_image,
)


def _oversized_png_header() -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\rIHDR"
        + (6000).to_bytes(4, "big")
        + (4000).to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
    )


def _gif_header(width: int, height: int) -> bytes:
    return b"GIF89a" + width.to_bytes(2, "little") + height.to_bytes(2, "little")


class ImageCompressorTests(unittest.TestCase):
    def test_small_png_passes_through_unchanged(self) -> None:
        data = b"\x89PNG\r\n\x1a\n" + b"tiny"
        prepared = prepare_image_bytes(data, source_name="tiny.png", mime="image/png")
        self.assertEqual(prepared.data, data)
        self.assertEqual(prepared.mime, "image/png")

    def test_oversized_image_is_transcoded_to_jpeg(self) -> None:
        jpeg = b"\xff\xd8\xff\xdbprepared-jpeg"
        with mock.patch(
            "providers.image.compressor.transcode_image_bytes",
            return_value=(jpeg, "image/jpeg"),
        ) as transcode:
            prepared = prepare_image_bytes(_oversized_png_header(), source_name="phone.png")
        transcode.assert_called()
        self.assertEqual(prepared, PreparedImage(jpeg, "image/jpeg"))

    def test_quality_ladder_keeps_smallest_acceptable_jpeg(self) -> None:
        oversized = b"x" * (9 * 1024 * 1024)
        large_jpeg = b"\xff\xd8" + (b"A" * (5 * 1024 * 1024))
        small_jpeg = b"\xff\xd8" + (b"B" * (3 * 1024 * 1024))
        results = {
            (4096, 3): (large_jpeg, "image/jpeg"),
            (3072, 5): (small_jpeg, "image/jpeg"),
        }

        def transcode(data, max_edge, *, quality, source_name):
            return results.get((max_edge, quality))

        with mock.patch("providers.image.compressor.transcode_image_bytes", side_effect=transcode):
            prepared = prepare_image_bytes(oversized, source_name="huge.bin")
        self.assertEqual(prepared.data, small_jpeg)
        self.assertEqual(prepared.mime, "image/jpeg")

    def test_uncompressible_image_raises_image_too_large(self) -> None:
        with mock.patch("providers.image.compressor.transcode_image_bytes", return_value=None):
            with self.assertRaises(ImageTooLargeError) as raised:
                prepare_image_bytes(_oversized_png_header(), source_name="phone.png")
        self.assertEqual(raised.exception.provider_code, "input_image_too_large")

    def test_prepare_local_image_reads_file_bytes(self) -> None:
        jpeg = b"\xff\xd8\xff\xdbprepared-jpeg"
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "phone.png"
            source.write_bytes(_oversized_png_header())
            with mock.patch(
                "providers.image.compressor.transcode_image_bytes",
                return_value=(jpeg, "image/jpeg"),
            ):
                prepared = prepare_local_image(str(source))
        self.assertEqual(prepared.mime, "image/jpeg")
        self.assertEqual(prepared.data, jpeg)

    def test_small_gif_passes_through_unchanged(self) -> None:
        data = _gif_header(10, 10) + b"tiny"
        prepared = prepare_image_bytes(data, source_name="tiny.gif", mime="image/gif")
        self.assertEqual(prepared.data, data)
        self.assertEqual(prepared.mime, "image/gif")

    def test_oversized_gif_is_transcoded_to_jpeg(self) -> None:
        jpeg = b"\xff\xd8\xff\xdbprepared-jpeg"
        with mock.patch(
            "providers.image.compressor.transcode_image_bytes",
            return_value=(jpeg, "image/jpeg"),
        ) as transcode:
            prepared = prepare_image_bytes(_gif_header(6000, 4000), source_name="anim.gif")
        transcode.assert_called()
        self.assertEqual(prepared, PreparedImage(jpeg, "image/jpeg"))
        self.assertEqual(transcode.call_args.kwargs["source_name"], "anim.gif")


if __name__ == "__main__":
    unittest.main()
