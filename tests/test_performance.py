"""Headless checks for bounded preview and chart memory use."""

from datetime import datetime, timedelta
import unittest

from cstracker.services.images import ImageRepository
from cstracker.ui.price_chart import downsample_extrema


class _Response:
    def __init__(self, payload):
        self.payload = payload
        self.headers = {"Content-Type": "image/png"}
        self.closed = False

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        for index in range(0, len(self.payload), chunk_size):
            yield self.payload[index:index + chunk_size]

    def close(self):
        self.closed = True


class _Session:
    def __init__(self, payloads):
        self.payloads = payloads
        self.calls = []
        self.responses = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        response = _Response(self.payloads[url])
        self.responses.append(response)
        return response


class ResourceBoundsTest(unittest.TestCase):
    def test_image_cache_evicts_and_closes_streams(self):
        session = _Session({"a": b"a" * 6, "b": b"b" * 6, "c": b"c" * 6})
        images = ImageRepository(session, max_cache_bytes=12, max_image_bytes=20)
        self.assertEqual(images.download("a"), b"a" * 6)
        self.assertEqual(images.download("b"), b"b" * 6)
        self.assertEqual(images.download("a"), b"a" * 6)
        images.download("c")
        self.assertEqual(session.calls, ["a", "b", "c"])
        self.assertEqual(images._cache_bytes, 12)
        self.assertTrue(all(response.closed for response in session.responses))
        images.download("b")
        self.assertEqual(session.calls, ["a", "b", "c", "b"])

    def test_oversized_image_is_not_retained(self):
        session = _Session({"large": b"x" * 21})
        images = ImageRepository(session, max_cache_bytes=12, max_image_bytes=20)
        self.assertIsNone(images.download("large"))
        self.assertEqual(images._cache_bytes, 0)
        self.assertTrue(session.responses[0].closed)

    def test_price_chart_keeps_extrema_with_bounded_points(self):
        start = datetime(2026, 1, 1)
        rows = [(start + timedelta(minutes=index), 10.0, 20.0, None, None)
                for index in range(5000)]
        rows[1234] = (rows[1234][0], 999.0, 20.0, None, None)
        rows[3210] = (rows[3210][0], 10.0, -99.0, None, None)
        sampled = downsample_extrema(rows)
        self.assertLessEqual(len(sampled), 602)
        self.assertEqual(sampled[0], rows[0])
        self.assertEqual(sampled[-1], rows[-1])
        self.assertIn(rows[1234], sampled)
        self.assertIn(rows[3210], sampled)


if __name__ == "__main__":
    unittest.main()
