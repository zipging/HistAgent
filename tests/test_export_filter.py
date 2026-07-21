from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts/export_release_checkpoint.py"
SPEC = spec_from_file_location("export_release_checkpoint", SCRIPT)
MODULE = module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_export_keeps_lora_and_histagent_modules() -> None:
    assert MODULE.keep_release_key("vision_encoder.base_model.model.blocks.0.attn.qkv.lora_A.default.weight")
    assert not MODULE.keep_release_key("vision_encoder.base_model.model.blocks.0.attn.qkv.base_layer.weight")
    assert MODULE.keep_release_key("decoder.layers.0.self_attn.in_proj_weight")
