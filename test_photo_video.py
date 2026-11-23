import base64
import json
import unittest
from unittest.mock import patch
from uuid import uuid4

from app_core import photo_video


class PhotoVideoTests(unittest.TestCase):
    def test_queues_a_photo_and_returns_the_generated_mp4(self):
        job = str(uuid4())
        video = b"\x00\x00\x00\x20ftypisom"
        filename = "generated_00001_.mp4"
        output = {"filename": filename, "subfolder": "ai-assist", "type": "output"}

        def comfy(path, **request):
            if path == "/upload/image":
                self.assertIn(b"Content-Type: image/png", request["body"])
                name = request["body"].split(b'filename="')[1].split(b'"')[0].decode()
                return json.dumps({"name": name, "subfolder": ""}).encode()
            if path == "/prompt":
                graph = json.loads(request["body"])["prompt"]
                self.assertTrue(graph["1"]["inputs"]["image"].endswith(".png"))
                self.assertEqual(graph["5"]["inputs"]["text"], "The toy car moves.")
                self.assertEqual(graph["7"]["inputs"]["start_image"], ["1", 0])
                self.assertEqual(graph["7"]["inputs"]["height"], 640)
                self.assertEqual(graph["12"]["inputs"]["format.codec"], "h264")
                return json.dumps({"prompt_id": job}).encode()
            if path == f"/history/{job}":
                return json.dumps({job: {"outputs": {"12": {"images": [output]}}}}).encode()
            self.assertEqual(path, f"/view?filename={filename}&subfolder=ai-assist&type=output")
            return video

        with patch.object(photo_video, "_request", side_effect=comfy) as request:
            encoded = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode()
            self.assertEqual(photo_video.start(encoded, "The toy car moves.", True), job)
            self.assertEqual(base64.b64decode(photo_video.status(job)["video"]), video)
            self.assertEqual(request.call_count, 4)

    def test_rejects_invalid_image_before_connecting(self):
        with patch.object(photo_video, "_request") as request:
            with self.assertRaises(ValueError):
                photo_video.start("bad data", "Move the camera.")
            request.assert_not_called()

    def test_reports_pending_and_failed_jobs(self):
        job = str(uuid4())
        with patch.object(photo_video, "_request", return_value=b"{}"):
            self.assertEqual(photo_video.status(job), {"ready": False})
        failed = json.dumps({job: {"status": {"status_str": "error"}}}).encode()
        with patch.object(photo_video, "_request", return_value=failed):
            with self.assertRaisesRegex(ValueError, "Video generation failed"):
                photo_video.status(job)
