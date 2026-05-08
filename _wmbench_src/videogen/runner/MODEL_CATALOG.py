"""Model Catalog for WMBench video generation.

All entries are parsed into ModelConfig at import time — missing or
bad fields fail immediately instead of at inference time.
"""

from typing import Dict

from videogen.schemas import ModelConfig

# ── Raw definitions (parse into ModelConfig below) ──────────────

_COGVIDEOX_RAW = {
    "cogvideox-5b-i2v": {
        "wrapper_module": "videogen.models.cogvideox_inference",
        "wrapper_class": "CogVideoXWrapper",
        "model": "THUDM/CogVideoX-5b-I2V",
        "description": "CogVideoX-5B-I2V — 6s (49f @ 8fps) 720×480",
        "family": "CogVideoX",
    },
    "cogvideox1.5-5b-i2v": {
        "wrapper_module": "videogen.models.cogvideox_inference",
        "wrapper_class": "CogVideoXWrapper",
        "model": "THUDM/CogVideoX1.5-5B-I2V",
        "description": "CogVideoX1.5-5B-I2V — 10s (81f @ 16fps) 1360×768",
        "family": "CogVideoX",
    },
}

_LTX2_RAW = {
    "ltx-2-19b-distilled-fp8": {
        "wrapper_module": "videogen.models.ltx2_inference",
        "wrapper_class": "LTX2Wrapper",
        "model": "LTX-2",
        "description": "LTX-2 19B FP8 — ti2v + audio (~40GB VRAM)",
        "family": "LTX-Video",
    },
    "ltx-2-19b-dev": {
        "wrapper_module": "videogen.models.ltx2_inference",
        "wrapper_class": "LTX2Wrapper",
        "model": "LTX-2-Dev",
        "description": "LTX-2 19B Dev (full precision) — ti2v + audio (~80GB VRAM)",
        "family": "LTX-Video",
        "kwargs": {
            "enable_fp8": False,
            "checkpoint": "ltx-2-19b-dev.safetensors",
        },
    },
}

_WAN_RAW = {
    "wan2.2-ti2v-5b": {
        "wrapper_module": "videogen.models.wan_inference",
        "wrapper_class": "WanWrapper",
        "model": "Wan-AI/Wan2.2-TI2V-5B-Diffusers",
        "description": "Wan2.2-TI2V-5B — ti2v 704×1280, 81f @ 16fps (diffusers)",
        "family": "Wan",
    },
    "wan2.2-i2v-a14b": {
        "wrapper_module": "videogen.models.wan_inference",
        "wrapper_class": "WanWrapper",
        "model": "Wan-AI/Wan2.2-I2V-A14B-Diffusers",
        "description": "Wan2.2-I2V-A14B — i2v 480P/720P, 81f @ 16fps, MoE 14B active (diffusers)",
        "family": "Wan",
    },
}

_COSMOS_RAW = {
    "cosmos-predict2.5-2b": {
        "wrapper_module": "videogen.models.cosmos_inference",
        "wrapper_class": "CosmosWrapper",
        "model": "nvidia/Cosmos-Predict2.5-2B",
        "description": "Cosmos-Predict2.5-2B — Image2World, 93f @ 16fps (~5.8s)",
        "family": "Cosmos",
    },
    "cosmos-predict2.5-14b": {
        "wrapper_module": "videogen.models.cosmos_inference",
        "wrapper_class": "CosmosWrapper",
        "model": "nvidia/Cosmos-Predict2.5-14B",
        "description": "Cosmos-Predict2.5-14B — Image2World, 93f @ 16fps (~5.8s)",
        "family": "Cosmos",
    },
}

_HUNYUAN_RAW = {
    "hunyuanvideo-i2v": {
        "wrapper_module": "videogen.models.hunyuan_inference",
        "wrapper_class": "HunyuanVideoWrapper",
        "model": "hunyuanvideo-community/HunyuanVideo-I2V",
        "description": "HunyuanVideo-I2V — i2v 720×1280, 129f @ 24fps (~5.4s), 13B transformer",
        "family": "HunyuanVideo",
    },
}


def _parse_group(raw: dict) -> Dict[str, ModelConfig]:
    return {name: ModelConfig.parse(name, cfg) for name, cfg in raw.items()}


# ── Parsed registries ──────────────────────────────────────────

COGVIDEOX_MODELS = _parse_group(_COGVIDEOX_RAW)
LTX2_MODELS = _parse_group(_LTX2_RAW)
WAN_MODELS = _parse_group(_WAN_RAW)
COSMOS_MODELS = _parse_group(_COSMOS_RAW)
HUNYUAN_MODELS = _parse_group(_HUNYUAN_RAW)

AVAILABLE_MODELS: Dict[str, ModelConfig] = {
    **COGVIDEOX_MODELS,
    **COSMOS_MODELS,
    **HUNYUAN_MODELS,
    **LTX2_MODELS,
    **WAN_MODELS,
}

MODEL_FAMILIES = {
    "CogVideoX": COGVIDEOX_MODELS,
    "Cosmos": COSMOS_MODELS,
    "HunyuanVideo": HUNYUAN_MODELS,
    "LTX-Video": LTX2_MODELS,
    "Wan": WAN_MODELS,
}


def get_model_family(model_name: str) -> str:
    return AVAILABLE_MODELS[model_name].family
