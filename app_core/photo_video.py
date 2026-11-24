"""Generate a short clip with the built-in Wan nodes on a local ComfyUI server."""

import base64
import http.client
import json
import secrets
from urllib.parse import urlencode
from uuid import UUID, uuid4


MAX_PHOTO_BYTES = 10 * 1024 * 1024
MAX_VIDEO_BYTES = 24 * 1024 * 1024


def _request(path, *, body=None, content_type="application/json", limit=1024 * 1024):
    # A fixed loopback connection avoids proxies and redirects to other machines.
    connection = http.client.HTTPConnection("127.0.0.1", 8188, timeout=15)
    try:
        headers = {"Content-Type": content_type} if body is not None else {}
        connection.request("POST" if body is not None else "GET", path, body, headers)
        with connection.getresponse() as response:
            data = response.read(limit + 1)
            if len(data) > limit:
                raise ValueError("The ComfyUI response is too large. Check its output folder for the clip.")
            if response.status != 200:
                if path == "/prompt" and response.status == 400:
                    raise ValueError("ComfyUI rejected the video workflow. Update ComfyUI and check the three Wan model files.")
                raise ValueError(f"ComfyUI returned HTTP {response.status}.")
            return data
    except (OSError, http.client.HTTPException) as error:
        raise ValueError("Could not reach ComfyUI at 127.0.0.1:8188. Check that it is running.") from error
    finally:
        connection.close()


def _workflow(image, prompt, prefix, portrait):
    def node(kind, **inputs):
        return {"class_type": kind, "inputs": inputs}

    width, height = (352, 640) if portrait else (640, 352)
    return {
        "1": node("LoadImage", image=image),
        "2": node("UNETLoader", unet_name="wan2.2_ti2v_5B_fp16.safetensors", weight_dtype="default"),
        "3": node("CLIPLoader", clip_name="umt5_xxl_fp8_e4m3fn_scaled.safetensors", type="wan", device="default"),
        "4": node("VAELoader", vae_name="wan2.2_vae.safetensors"),
        "5": node("CLIPTextEncode", clip=["3", 0], text=prompt),
        "6": node("CLIPTextEncode", clip=["3", 0], text="blur, flicker, distortion"),
        "7": node("Wan22ImageToVideoLatent", vae=["4", 0], start_image=["1", 0],
                  width=width, height=height, length=49, batch_size=1),
        "8": node("ModelSamplingSD3", model=["2", 0], shift=8.0),
        "9": node("KSampler", model=["8", 0], positive=["5", 0], negative=["6", 0],
                  latent_image=["7", 0], seed=secrets.randbits(48), steps=20, cfg=5.0,
                  sampler_name="uni_pc", scheduler="simple", denoise=1.0),
        "10": node("VAEDecode", samples=["9", 0], vae=["4", 0]),
        "11": node("CreateVideo", images=["10", 0], fps=24),
        "12": node("SaveVideo", video=["11", 0], filename_prefix=prefix, format="mp4", **{"format.codec": "h264"}),
    }


def start(encoded, prompt, portrait=False):
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 2000:
        raise ValueError("Describe the motion using 1 to 2,000 characters.")
    if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_PHOTO_BYTES + 2) // 3):
        raise ValueError("Choose a JPEG or PNG up to 10 MB.")
    try:
        data = base64.b64decode(encoded, validate=True)
    except ValueError:
        raise ValueError("Could not read the photo.") from None
    if len(data) > MAX_PHOTO_BYTES:
        raise ValueError("Choose a JPEG or PNG up to 10 MB.")
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        extension, mime = "png", "image/png"
    elif data.startswith(b"\xff\xd8\xff"):
        extension, mime = "jpg", "image/jpeg"
    else:
        raise ValueError("Choose a JPEG or PNG photo.")

    job = str(uuid4())
    boundary = uuid4().hex
    filename = f"{job}.{extension}"
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{filename}"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
    uploaded = json.loads(_request("/upload/image", body=body, content_type=f"multipart/form-data; boundary={boundary}"))
    if uploaded.get("name") != filename or uploaded.get("subfolder"):
        raise ValueError("ComfyUI saved the photo at an unexpected location.")
    graph = _workflow(filename, prompt.strip(), f"ai-assist/{job}", portrait is True)
    queued = json.loads(_request("/prompt", body=json.dumps({"prompt": graph, "prompt_id": job}).encode()))
    return str(UUID(queued["prompt_id"]))


def status(job):
    job = str(UUID(job))
    item = json.loads(_request(f"/history/{job}")).get(job)
    if item is None:
        return {"ready": False}
    if item.get("status", {}).get("status_str") == "error":
        raise ValueError("Video generation failed. Check the ComfyUI log for the cause.")
    output = item.get("outputs", {}).get("12", {})
    for files in output.values():
        if not isinstance(files, list):
            continue
        for file in files:
            if not isinstance(file, dict):
                continue
            name = file.get("filename", "")
            if file.get("type") == "output" and file.get("subfolder") == "ai-assist" and name.lower().endswith(".mp4"):
                query = urlencode({"filename": name, "subfolder": file.get("subfolder", ""), "type": "output"})
                video = _request(f"/view?{query}", limit=MAX_VIDEO_BYTES)
                if video[4:8] != b"ftyp":
                    raise ValueError("ComfyUI returned an invalid MP4.")
                return {"ready": True, "video": base64.b64encode(video).decode("ascii"), "name": name}
    raise ValueError("ComfyUI finished without an MP4. Check its log and output folder.")
