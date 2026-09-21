"""Pinned Qwen3-VL LoRA entry point with the existing CHAMP patch-embed fix."""
import json
import sys
import torch
from transformers.models.qwen3_vl.modeling_qwen3_vl import Qwen3VLVisionPatchEmbed


def patch_embed_forward(self, hidden_states):
    # Same non-overlapping Conv3d -> linear identity used by CHAMP/.hldiag.
    # Install synchronously so every distributed worker uses it from step one.
    weight=self.proj.weight
    return torch.nn.functional.linear(hidden_states.to(weight.dtype).reshape(-1,weight[0].numel()),
                                      weight.flatten(1),self.proj.bias)


if __name__=='__main__':
    Qwen3VLVisionPatchEmbed.forward=patch_embed_forward
    from swift.llm import TrainArguments,sft_main
    config=json.load(open(sys.argv[1]))
    sft_main(TrainArguments(**config))
