"""HY-Motion text-to-motion for one clip, run by forge/motion_text.py in HY-Motion's own Python
with its checkout on the path and the Hugging Face libraries offline.

  python -B hymotion_entry.py --checkout DIR --model latest.ckpt --config config.yml
         --text-encoder QWEN_DIR --clip CLIP_DIR --prompt-file prompt.txt --seed N --steps N
         --guidance X --duration SECONDS --out motion.npz --record hymotion.json

The text is encoded first (the language model and the sentence encoder), the encoders are
freed, then the motion model is loaded from the checkpoint (learned parameters must match; the
body model's buffers come from the checkout's bundled assets), generates with the seed, and
its arrays (keypoints3d without the root translation, transl, rot6d, ...) are saved with a
record of what ran. No prompt rewriting and no duration estimate (no other model is asked).
The process exits after one clip, so the card is free again.
"""

import argparse
import gc
import hashlib
import json
import os
import sys
import time


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    for name in ("checkout", "model", "config", "text-encoder", "clip", "prompt-file", "out", "record"):
        ap.add_argument("--" + name, required=True)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--steps", type=int, default=50)
    ap.add_argument("--guidance", type=float, default=5.0)
    ap.add_argument("--duration", type=float, required=True)
    a = ap.parse_args()
    t0 = time.perf_counter()
    with open(a.prompt_file, encoding="utf-8") as f:
        text = f.read().strip()
    sys.path.insert(0, a.checkout)
    import numpy as np
    import torch
    import yaml
    from hymotion.network.text_encoders import text_encoder as module
    from hymotion.utils.loaders import load_object

    with open(a.config, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    args = config["train_pipeline_args"]
    # The encoders from the configured folders, never downloaded.
    module.LLM_ENCODER_LAYOUT["qwen3"]["module_path"] = a.text_encoder
    module.SENTENCE_EMB_LAYOUT["clipl"]["module_path"] = a.clip
    t1 = time.perf_counter()
    encoder = module.HYTextModel(**args["text_encoder_cfg"]).cuda().eval()
    with torch.inference_mode():
        vector, context, length = encoder.encode([text])
    hidden = {"text_vec_raw": vector.cpu(), "text_ctxt_raw": context.cpu(), "text_ctxt_raw_length": length.cpu()}
    del encoder, vector, context, length
    gc.collect()
    torch.cuda.empty_cache()
    encode_seconds = time.perf_counter() - t1

    args["test_cfg"]["mean_std_dir"] = os.path.join(a.checkout, "stats")
    previous = os.getcwd()
    try:
        os.chdir(a.checkout)  # the body model reads its bundled assets relative to the checkout
        pipeline = load_object(config["train_pipeline"], args, network_module=config["network_module"],
                               network_module_args=config["network_module_args"])
    finally:
        os.chdir(previous)
    state = torch.load(a.model, map_location="cpu", weights_only=False)["model_state_dict"]
    result = pipeline.load_state_dict(state, strict=False)
    unexpected = list(result.unexpected_keys)
    missing = [k for k in result.missing_keys if not k.startswith("body_model.")]
    if unexpected or missing:
        raise SystemExit("checkpoint mismatch: missing %s, unexpected %s" % (missing[:8], unexpected[:8]))
    del state
    pipeline.cuda().eval()
    pipeline.validation_steps = a.steps
    hidden = {k: v.cuda() for k, v in hidden.items()}
    torch.cuda.reset_peak_memory_stats()
    t2 = time.perf_counter()
    with torch.inference_mode():
        out = pipeline.generate(text, [a.seed], a.duration, cfg_scale=a.guidance, use_special_game_feat=False,
                                hidden_state_dict=hidden)
    torch.cuda.synchronize()
    arrays = {k: v.cpu().numpy() for k, v in out.items() if torch.is_tensor(v)}
    if not all(np.isfinite(x).all() for x in arrays.values()):
        raise SystemExit("the generated motion has values that are not finite")
    np.savez_compressed(a.out, **arrays)
    fps = int(args.get("output_mesh_fps", 30))
    record = {"format": "forge-hymotion-record", "version": 1, "model": os.path.basename(os.path.dirname(a.model)),
              "text": text, "seed": a.seed, "requested_steps": a.steps, "guidance_scale": a.guidance,
              "duration_seconds": a.duration, "fps": fps,
              "frames": int(arrays["keypoints3d"].shape[1]) if "keypoints3d" in arrays else None,
              "prompt_rewrite": False, "duration_estimate": False,
              "checkpoint_sha256": digest(a.model), "config_sha256": digest(a.config),
              "stats": {n: digest(os.path.join(a.checkout, "stats", n)) for n in ("Mean.npy", "Std.npy")},
              "torch": torch.__version__, "cuda": torch.version.cuda,
              "encode_seconds": round(encode_seconds, 3), "inference_seconds": round(time.perf_counter() - t2, 3),
              "seconds": round(time.perf_counter() - t0, 3),
              "peak_allocated_bytes": int(torch.cuda.max_memory_allocated()),
              "arrays": {k: list(v.shape) for k, v in arrays.items()}}
    with open(a.record, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=1)
    print("HYMOTION done: %s frames at %d fps" % (record["frames"], fps), flush=True)


if __name__ == "__main__":
    main()
